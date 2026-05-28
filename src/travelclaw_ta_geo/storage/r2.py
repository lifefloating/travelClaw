from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from boto3.s3.transfer import TransferConfig

from travelclaw_ta_geo.output.manifest import is_junk_file
from travelclaw_ta_geo.settings import Settings


@dataclass(frozen=True)
class UploadResult:
    uploaded_keys: list[str]
    ready_key: str


class R2Uploader:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def enabled(self, upload_requested: bool) -> bool:
        return self.settings.r2_upload_allowed(upload_requested)

    def upload_package(self, package_dir: Path, timestamp: str | None = None) -> UploadResult:
        if not self.settings.r2_configured:
            raise RuntimeError("R2 config is incomplete")
        timestamp = timestamp or package_dir.name
        prefix = "/".join(
            item.strip("/")
            for item in [self.settings.r2_prefix, self.settings.focus, self.settings.source, timestamp]
            if item.strip("/")
        )
        client = self._client()
        config = TransferConfig(max_concurrency=self.settings.ta_r2_concurrency, use_threads=True)

        regular_files = [
            path
            for path in sorted(package_dir.rglob("*"))
            if path.is_file() and path.name not in {"_READY"} and not is_junk_file(path)
        ]
        uploaded: list[str] = []
        with ThreadPoolExecutor(max_workers=self.settings.ta_r2_concurrency) as pool:
            futures = {
                pool.submit(self._upload_file, client, config, package_dir, path, prefix): path for path in regular_files
            }
            for future in as_completed(futures):
                uploaded.append(future.result())

        ready_key = f"{prefix}/_READY"
        client.put_object(Bucket=self.settings.r2_bucket, Key=ready_key, Body=b"")
        return UploadResult(uploaded_keys=sorted(uploaded), ready_key=ready_key)

    def _client(self):
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            endpoint_url=self.settings.r2_endpoint_url,
            aws_access_key_id=self.settings.r2_access_key_id,
            aws_secret_access_key=self.settings.r2_secret_access_key,
            region_name=self.settings.r2_region,
            config=Config(max_pool_connections=self.settings.ta_r2_concurrency),
        )

    def _upload_file(self, client, config: TransferConfig, package_dir: Path, path: Path, prefix: str) -> str:
        relative = path.relative_to(package_dir).as_posix()
        key = f"{prefix}/{relative}"
        client.upload_file(str(path), self.settings.r2_bucket, key, Config=config)
        return key

