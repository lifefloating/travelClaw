from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ta_base_url: str = "https://www.tripadvisor.com"
    ta_accept_language: str = "en-US,en;q=0.9"
    ta_proxies: str = ""
    ta_detail_concurrency: int = Field(default=4, ge=1, le=32)
    ta_graphql_concurrency: int = Field(default=4, ge=1, le=32)
    ta_image_concurrency: int = Field(default=32, ge=1, le=64)
    ta_image_requests_per_second: float = Field(default=32.0, gt=0, le=200)
    ta_image_request_jitter_seconds: float = Field(default=0.0, ge=0, le=10)
    ta_image_use_proxy: bool = False
    ta_r2_concurrency: int = Field(default=10, ge=1, le=64)
    ta_requests_per_second: float = Field(default=1.0, gt=0, le=20)
    ta_request_jitter_seconds: float = Field(default=0.3, ge=0, le=10)
    ta_max_retries: int = Field(default=3, ge=0, le=10)
    ta_timeout_seconds: int = Field(default=30, ge=5, le=180)
    ta_gallery_query_id: str = "e451fc43b6a61cab"
    # mediaAlbumPage returns an EMPTY list when limit >= 100 (a TripAdvisor edge
    # bug), so each page silently "topped out" at offset 100 and we only ever got
    # ~100 images per geo. Any value <= 99 paginates correctly; 50 is the sweet
    # spot (full coverage, moderate per-page payload). See docs/research/
    # tripadvisor_gallery_pagination_findings.md §1-§2.
    ta_gallery_page_limit: int = Field(default=50, ge=1, le=99)
    # The album exposes only ~2550 photos/geo regardless of totalMediaCount (a
    # multi-million UI aggregate that is never reachable). We stop on consecutive
    # empty pages, but cap the offset as a backstop against pointless empty paging.
    ta_gallery_offset_ceiling: int = Field(default=3000, ge=100, le=100000)
    # Number of consecutive empty pages tolerated before declaring the album done.
    ta_gallery_empty_page_stop: int = Field(default=2, ge=1, le=10)
    # OPT-IN review-photo supplement (A3). The official album tops out at ~2550;
    # to approach larger targets we can merge user-uploaded review photos. This is
    # INERT until a real reviewListPage preRegisteredQueryId is supplied (capture
    # it from a live Reviews page via F12 -> /data/graphql/ids). See findings §8.
    ta_review_photos_query_id: str = ""
    ta_review_page_limit: int = Field(default=20, ge=1, le=20)
    ta_review_offset_ceiling: int = Field(default=20000, ge=0, le=200000)
    ta_locale: str = "en-US"
    ta_timezone: str = "America/New_York"
    ta_html_browser_fallback: bool = True
    ta_headless: bool = True
    ta_real_chrome: bool = False
    ta_disable_resources: bool = False
    ta_browser_user_data_dir: str = "data/browser/tripadvisor"
    ta_browser_sticky_after_block: bool = True
    ta_browser_block_statuses: str = "403,429,503"

    # VPS orchestration. Default root is /data/city_geo per ops spec; everything
    # (raw, packages, status, persistent state, browser profiles) lives under it.
    data_root: str = "/data/city_geo"
    ta_worker_count: int = Field(default=4, ge=1, le=32)
    ta_image_max_bytes: int = Field(default=25 * 1024 * 1024, ge=1024 * 1024)
    ta_image_max_side: int = Field(default=8000, ge=256)
    ta_download_side: int = Field(default=8000, ge=256, le=8000)
    ta_image_dedupe_distance: int = Field(default=8, ge=0, le=64)
    ta_max_images_per_geo: int = Field(default=10000, ge=0, le=100000)

    r2_upload_enabled: bool = False
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_endpoint_url: str = ""
    r2_bucket: str = "r2-qiqi"
    r2_prefix: str = "qiqi"
    r2_region: str = "auto"

    vendor_id: str = "qiqi"
    focus: str = "geo"
    source: str = "tripadvisor"
    license: str = "proprietary"
    attribution: str = "\u00a9 2026 frank"
    contact_name: str = "frank"
    contact_email: str = "imshuazi@126.com"

    @property
    def base_url(self) -> str:
        return self.ta_base_url.rstrip("/")

    @property
    def layout(self):
        # Imported lazily to keep settings import-light and avoid a cycle.
        from travelclaw_ta_geo.paths import DataLayout

        return DataLayout(Path(self.data_root))

    def for_worker(self, worker_index: int) -> Settings:
        """Clone settings with a worker-private browser profile dir so parallel
        worker processes never open the same StealthySession user_data_dir
        (which would lock). Pass the result down to a single-process crawl."""
        worker_dir = self.layout.worker_profile(worker_index)
        return self.model_copy(update={"ta_browser_user_data_dir": str(worker_dir)})

    @property
    def proxies(self) -> list[str]:
        return [item.strip() for item in self.ta_proxies.split(",") if item.strip()]

    @property
    def browser_block_statuses(self) -> set[int]:
        out: set[int] = set()
        for item in self.ta_browser_block_statuses.split(","):
            item = item.strip()
            if item.isdigit():
                out.add(int(item))
        return out or {403, 429, 503}

    @property
    def r2_configured(self) -> bool:
        return all(
            [
                self.r2_access_key_id,
                self.r2_secret_access_key,
                self.r2_endpoint_url,
                self.r2_bucket,
            ]
        )

    def r2_upload_allowed(self, upload_requested: bool) -> bool:
        return bool(upload_requested and self.r2_upload_enabled and self.r2_configured)


def ensure_dir(path: str | Path) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target
