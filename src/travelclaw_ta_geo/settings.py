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
    ta_image_concurrency: int = Field(default=16, ge=1, le=64)
    ta_r2_concurrency: int = Field(default=16, ge=1, le=64)
    ta_requests_per_second: float = Field(default=1.0, gt=0, le=20)
    ta_request_jitter_seconds: float = Field(default=0.3, ge=0, le=10)
    ta_max_retries: int = Field(default=3, ge=0, le=10)
    ta_timeout_seconds: int = Field(default=30, ge=5, le=180)
    ta_gallery_query_id: str = "e451fc43b6a61cab"
    ta_locale: str = "en-US"
    ta_timezone: str = "America/New_York"
    ta_html_browser_fallback: bool = False
    ta_headless: bool = True
    ta_real_chrome: bool = False
    ta_disable_resources: bool = False
    ta_image_max_bytes: int = Field(default=25 * 1024 * 1024, ge=1024 * 1024)
    ta_image_max_side: int = Field(default=8000, ge=256)
    ta_download_side: int = Field(default=2000, ge=256, le=8000)
    ta_image_dedupe_distance: int = Field(default=8, ge=0, le=64)

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
    def proxies(self) -> list[str]:
        return [item.strip() for item in self.ta_proxies.split(",") if item.strip()]

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
