from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from travelclaw_ta_geo.crawler import TripadvisorGeoCrawler, build_geo_record, utc_now
from travelclaw_ta_geo.output.package import PackageBuilder
from travelclaw_ta_geo.output.writers import NdjsonWriter
from travelclaw_ta_geo.paths import DataLayout
from travelclaw_ta_geo.progress import CityStatus, Stage, StatusWriter
from travelclaw_ta_geo.seeds import DestinationSeed
from travelclaw_ta_geo.settings import Settings
from travelclaw_ta_geo.state import PersistentState
from travelclaw_ta_geo.storage.r2 import R2Uploader
from travelclaw_ta_geo.tripadvisor.media import DownloadedMedia, MediaDownloadError


@dataclass(frozen=True)
class CityOptions:
    max_images_per_geo: int
    upload: bool = False
    force: bool = False
    dry_run: bool = False
    worker_index: int | None = None


@dataclass(frozen=True)
class CityOutcome:
    city_key: str
    geo_id: int | None
    stage: str
    geo_rows: int
    media_rows: int
    error_rows: int
    r2_timestamp: str
    package_dir: Path | None
    message: str


def run_city(
    settings: Settings,
    seed: DestinationSeed,
    options: CityOptions,
    *,
    layout: DataLayout | None = None,
    persistent: PersistentState | None = None,
) -> CityOutcome:
    """End-to-end for a single city: crawl -> package -> upload -> delete images.

    Reuses TripadvisorGeoCrawler's components verbatim (discovery / detail /
    graphql / media) so the proven anti-bot path is unchanged. Everything else
    here is orchestration: per-stage status, R2 upload with a collision-free
    timestamp, and disk reclamation after a successful upload.
    """
    layout = layout or settings.layout
    city_key = seed.key

    raw_dir = layout.city_raw(city_key)
    pkg_dir = layout.city_data(city_key)
    status_path = layout.city_status(city_key)

    status = CityStatus(
        city_key=city_key,
        name=seed.name_en or seed.name_cn,
        worker=options.worker_index,
    )
    writer = StatusWriter(status_path, status)

    owns_persistent = persistent is None
    if persistent is None and not options.dry_run:
        persistent = PersistentState(layout.state_db)

    if persistent is not None and not options.force and persistent.is_city_done(city_key):
        writer.set_stage(Stage.SKIPPED, "already completed in a prior run")
        if owns_persistent:
            persistent.close()
        return CityOutcome(city_key, None, Stage.SKIPPED.value, 0, 0, 0, "", None, "skipped")

    # Fresh working dirs for this attempt.
    if raw_dir.exists():
        shutil.rmtree(raw_dir, ignore_errors=True)
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir, ignore_errors=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    crawler = TripadvisorGeoCrawler(settings)
    geo_writer = NdjsonWriter(raw_dir / "geo.ndjson")
    media_writer = NdjsonWriter(raw_dir / "media.ndjson", lazy=True)
    error_writer = NdjsonWriter(raw_dir / "errors.ndjson", lazy=True)

    geo_rows = media_rows = error_rows = 0
    r2_timestamp = ""
    uploaded = False

    try:
        max_images = min(options.max_images_per_geo, settings.ta_max_images_per_geo)
        captured_at = utc_now()

        writer.set_stage(Stage.DISCOVERING)
        discovery = crawler.discovery.discover(seed)
        writer.update(geo_id=discovery.geo_id)

        writer.set_stage(Stage.FETCHING_DETAIL)
        page, _html = crawler.detail.fetch_and_parse(discovery)

        media_candidates = []
        gallery_meta: dict = {}
        if max_images > 0:
            writer.set_stage(Stage.GALLERY)
            try:
                media_candidates, gallery_meta = crawler.graphql.gallery_media(page, max_images)
            except Exception as exc:
                msg = f"gallery GraphQL failed for g{page.geo_id}: {exc}"
                error_writer.write(_error_row(seed, "geo_warning", msg, page.url))
                error_rows += 1

        geo_record = build_geo_record(page, captured_at, settings, gallery_meta)
        geo_writer.write(geo_record)
        geo_rows = 1

        total = min(len(media_candidates), max_images)
        writer.set_stage(Stage.DOWNLOADING)
        writer.bump_images(done=0, total=total)
        step = max(1, total // 100)
        for result in crawler.media.download_many(
            media_candidates,
            run_dir=raw_dir,
            geo_id=page.geo_id,
            geo_record_id=page.record_id,
            captured_at=captured_at,
            max_items=max_images,
        ):
            if isinstance(result, DownloadedMedia):
                media_writer.write(result.line)
                media_rows += 1
                if persistent is not None:
                    persistent.record_media(
                        page.geo_id,
                        str(result.line.get("source_id", "")),
                        str(result.line.get("phash", "")),
                        captured_at,
                    )
                if media_rows % step == 0 or media_rows == total:
                    writer.bump_images(done=media_rows)
            elif isinstance(result, MediaDownloadError):
                error_writer.write(
                    _error_row(seed, "media_failed", result.message, result.candidate.source_url)
                )
                error_rows += 1
        writer.bump_images(done=media_rows)
    except Exception as exc:
        error_writer.write(_error_row(seed, "geo_failed", str(exc)))
        error_rows += 1
        _close_all(geo_writer, media_writer, error_writer, crawler)
        writer.update(geo_rows=geo_rows, media_rows=media_rows, error_rows=error_rows)
        writer.set_stage(Stage.FAILED, str(exc))
        if owns_persistent and persistent is not None:
            persistent.close()
        return CityOutcome(city_key, status.geo_id, Stage.FAILED.value, geo_rows, media_rows, error_rows, "", None, str(exc))

    _close_all(geo_writer, media_writer, error_writer, crawler)
    writer.update(geo_rows=geo_rows, media_rows=media_rows, error_rows=error_rows)

    # Package.
    writer.set_stage(Stage.PACKAGING)
    package = PackageBuilder(settings).build(
        raw_dir,
        package_dir=pkg_dir,
        notes=(
            f"Tripadvisor geo crawl for {seed.name_en or seed.name_cn} (g{status.geo_id}). "
            f"Single-city delivery; max_images_per_geo={max_images}."
        ),
    )

    # Upload.
    uploader = R2Uploader(settings)
    if options.upload and not options.dry_run and uploader.enabled(upload_requested=True):
        writer.set_stage(Stage.UPLOADING)
        r2_timestamp = _alloc_timestamp(layout)
        uploader.upload_package(package.package_dir, timestamp=r2_timestamp)
        uploaded = True
        writer.update(r2_timestamp=r2_timestamp)

    # Reclaim disk. Raw media is redundant once packaged; package media is
    # redundant once uploaded to R2 (the authoritative copy).
    writer.set_stage(Stage.CLEANUP)
    if not options.dry_run:
        shutil.rmtree(raw_dir / "media", ignore_errors=True)
    if uploaded:
        shutil.rmtree(package.package_dir / "media", ignore_errors=True)

    if persistent is not None and not options.dry_run:
        persistent.mark_city_done(
            city_key,
            geo_id=status.geo_id or 0,
            name=status.name,
            r2_timestamp=r2_timestamp,
            media_count=media_rows,
            completed_at=utc_now(),
        )
    if owns_persistent and persistent is not None:
        persistent.close()

    writer.set_stage(Stage.DONE)
    return CityOutcome(
        city_key,
        status.geo_id,
        Stage.DONE.value,
        geo_rows,
        media_rows,
        error_rows,
        r2_timestamp,
        package.package_dir,
        "ok",
    )


def _close_all(geo_writer, media_writer, error_writer, crawler) -> None:
    geo_writer.close()
    media_writer.close()
    error_writer.close()
    crawler.client.close()


def _error_row(seed: DestinationSeed, error_type: str, message: str, source_url: str = "") -> dict:
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


@contextmanager
def _file_lock(lock_path: Path) -> Iterator[None]:
    import fcntl

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _alloc_timestamp(layout: DataLayout) -> str:
    """Allocate a unique UTC second-resolution timestamp across processes.

    The delivery spec fixes the R2 path to qiqi/geo/tripadvisor/<timestamp>/ and
    forbids any suffix, so two cities finishing in the same second would collide
    on one immutable delivery prefix. A cross-process file lock + last-issued
    record guarantees each city gets its own second (bumping forward if needed)."""
    layout.state.mkdir(parents=True, exist_ok=True)
    lock_path = layout.state / ".timestamp.lock"
    last_path = layout.state / ".last_timestamp"
    with _file_lock(lock_path):
        now = int(datetime.now(timezone.utc).timestamp())
        try:
            last = int(last_path.read_text().strip())
        except (OSError, ValueError):
            last = 0
        issued = now if now > last else last + 1
        last_path.write_text(str(issued))
    return datetime.fromtimestamp(issued, tz=timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
