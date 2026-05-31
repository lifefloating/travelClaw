from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from travelclaw_ta_geo.output.package import PackageBuilder, PackageResult, utc_timestamp
from travelclaw_ta_geo.output.writers import NdjsonWriter
from travelclaw_ta_geo.seeds import DestinationSeed, load_seeds
from travelclaw_ta_geo.settings import Settings
from travelclaw_ta_geo.state import CrawlState
from travelclaw_ta_geo.storage.r2 import R2Uploader, UploadResult
from travelclaw_ta_geo.tripadvisor.detail import TripadvisorDetailParser
from travelclaw_ta_geo.tripadvisor.discovery import TripadvisorDiscovery
from travelclaw_ta_geo.tripadvisor.graphql import TripadvisorGraphQL
from travelclaw_ta_geo.tripadvisor.http import TripadvisorHttpClient
from travelclaw_ta_geo.tripadvisor.media import DownloadedMedia, MediaDownloader, MediaDownloadError
from travelclaw_ta_geo.tripadvisor.models import GeoPage, MediaCandidate


@dataclass(frozen=True)
class ProcessedGeo:
    seed: DestinationSeed
    page: GeoPage
    geo_record: dict[str, Any]
    media_candidates: list[MediaCandidate]
    gallery_meta: dict[str, Any]
    captured_at: str
    warnings: list[str]


@dataclass(frozen=True)
class CrawlRunResult:
    run_dir: Path
    package: PackageResult
    upload: UploadResult | None
    geo_rows: int
    media_rows: int
    error_rows: int


class TripadvisorGeoCrawler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = TripadvisorHttpClient(settings)
        self.discovery = TripadvisorDiscovery(settings, self.client)
        self.detail = TripadvisorDetailParser(settings, self.client)
        self.graphql = TripadvisorGraphQL(settings, self.client)
        self.media = MediaDownloader(settings, self.client)

    def crawl(
        self,
        *,
        seed_path: Path,
        output_dir: Path,
        limit_geos: int | None,
        max_images_per_geo: int,
        upload_requested: bool,
        resume: bool = True,
    ) -> CrawlRunResult:
        run_id, _delivered_at = utc_timestamp()
        run_dir = output_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        seeds = load_seeds(seed_path)
        if limit_geos is not None and limit_geos > 0:
            seeds = seeds[:limit_geos]
        max_images_per_geo = min(max_images_per_geo, self.settings.ta_max_images_per_geo)

        geo_writer = NdjsonWriter(run_dir / "geo.ndjson")
        media_writer = NdjsonWriter(run_dir / "media.ndjson", lazy=True)
        error_writer = NdjsonWriter(run_dir / "errors.ndjson", lazy=True)
        state = CrawlState(run_dir / "state.sqlite")
        geo_rows = media_rows = error_rows = 0

        try:
            pending = [seed for seed in seeds if not (resume and state.is_done(seed.key))]
            with ThreadPoolExecutor(max_workers=self.settings.ta_detail_concurrency) as pool:
                futures = {pool.submit(self._process_seed, seed, max_images_per_geo): seed for seed in pending}
                for future in as_completed(futures):
                    seed = futures[future]
                    try:
                        processed = future.result()
                    except Exception as exc:
                        error_writer.write(self._error_row(seed, "geo_failed", str(exc)))
                        error_rows += 1
                        continue

                    geo_writer.write(processed.geo_record)
                    geo_rows += 1
                    for warning in processed.warnings:
                        error_writer.write(self._error_row(seed, "geo_warning", warning, processed.page.url))
                        error_rows += 1

                    for result in self.media.download_many(
                        processed.media_candidates,
                        run_dir=run_dir,
                        geo_id=processed.page.geo_id,
                        geo_record_id=processed.page.record_id,
                        captured_at=processed.captured_at,
                        max_items=max_images_per_geo,
                    ):
                        if isinstance(result, DownloadedMedia):
                            media_writer.write(result.line)
                            media_rows += 1
                        elif isinstance(result, MediaDownloadError):
                            error_writer.write(
                                self._error_row(
                                    seed,
                                    "media_failed",
                                    result.message,
                                    result.candidate.source_url,
                                )
                            )
                            error_rows += 1
                    state.mark_done(seed.key, processed.page.geo_id, processed.page.url, processed.captured_at)
        finally:
            geo_writer.close()
            media_writer.close()
            error_writer.close()
            state.close()
            self.client.close()

        if geo_rows == 0:
            raise RuntimeError(f"crawl produced zero geo rows; inspect {run_dir / 'errors.ndjson'}")

        package = PackageBuilder(self.settings).build(
            run_dir,
            notes=(
                "Tripadvisor geo crawl. Images are limited to geo gallery candidates only; "
                f"max_images_per_geo={max_images_per_geo}."
            ),
        )
        upload = None
        uploader = R2Uploader(self.settings)
        if uploader.enabled(upload_requested):
            upload = uploader.upload_package(package.package_dir, timestamp=package.timestamp)
        return CrawlRunResult(
            run_dir=run_dir,
            package=package,
            upload=upload,
            geo_rows=geo_rows,
            media_rows=media_rows,
            error_rows=error_rows,
        )

    def process_seed(self, seed: DestinationSeed, max_images_per_geo: int) -> ProcessedGeo:
        """Public entry point reused by the per-city runner. Delegates to the
        existing internal pipeline so the crawl/anti-bot mechanism is unchanged."""
        return self._process_seed(seed, max_images_per_geo)

    def _process_seed(self, seed: DestinationSeed, max_images_per_geo: int) -> ProcessedGeo:
        captured_at = utc_now()
        discovery = self.discovery.discover(seed)
        page, _html = self.detail.fetch_and_parse(discovery)
        warnings: list[str] = []
        media_candidates: list[MediaCandidate] = []
        gallery_meta: dict[str, Any] = {}
        if max_images_per_geo > 0:
            try:
                media_candidates, gallery_meta = self.graphql.gallery_media(page, max_images_per_geo)
            except Exception as exc:
                warnings.append(f"gallery GraphQL failed for g{page.geo_id}: {exc}")
        geo_record = build_geo_record(page, captured_at, self.settings, gallery_meta)
        return ProcessedGeo(
            seed=seed,
            page=page,
            geo_record=geo_record,
            media_candidates=media_candidates,
            gallery_meta=gallery_meta,
            captured_at=captured_at,
            warnings=warnings,
        )

    @staticmethod
    def _error_row(seed: DestinationSeed, error_type: str, message: str, source_url: str = "") -> dict[str, Any]:
        return {
            "captured_at": utc_now(),
            "error_type": error_type,
            "seed_key": seed.key,
            "seed": {
                "name_cn": seed.name_cn,
                "name_en": seed.name_en,
                "latitude": seed.latitude,
                "longitude": seed.longitude,
                "country_code": seed.country_code,
            },
            "source_url": source_url,
            "message": message,
        }


def build_geo_record(page: GeoPage, captured_at: str, settings: Settings, gallery_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    seed = page.seed
    if not seed.has_center:
        raise ValueError(f"seed has no valid center: {seed.name_en or seed.name_cn}")
    source_url = page.canonical_url or page.url
    name = seed.name_en or page.name or seed.name_cn
    name_i18n = {}
    if seed.name_en:
        name_i18n["en"] = seed.name_en
    elif name:
        name_i18n["en"] = name
    if seed.name_cn:
        name_i18n["zh-CN"] = seed.name_cn
    raw_seed = {
        "name_cn": seed.name_cn,
        "name_en": seed.name_en,
        "kind": seed.kind,
        "country_code": seed.country_code,
    }
    if seed.parent_destination:
        raw_seed["parent_destination"] = seed.parent_destination
    return {
        "record_id": page.record_id,
        "source": settings.source,
        "source_id": f"g{page.geo_id}",
        "source_url": source_url,
        "captured_at": captured_at,
        "name": name,
        "name_i18n": name_i18n,
        "kind_hint": f"tripadvisor:{seed.kind or 'geo'}",
        "country_code": seed.country_code,
        "center": {"lat": seed.latitude, "lng": seed.longitude},
        "raw": {
            "title": page.title,
            "meta": page.raw_meta,
            "breadcrumbs": page.breadcrumbs,
            "description": page.description,
            "review_count_text": page.review_count_text,
            "sections_seen": page.sections_seen,
            "tripadvisor_ids": {"geo_id": page.geo_id},
            "gallery": gallery_meta or {},
            "seed": raw_seed,
        },
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
