from __future__ import annotations

import gzip
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from travelclaw_ta_geo.output.manifest import build_manifest, write_manifest
from travelclaw_ta_geo.settings import Settings


@dataclass(frozen=True)
class PackageResult:
    package_dir: Path
    timestamp: str
    delivered_at: str
    manifest_path: Path


def utc_timestamp() -> tuple[str, str]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return now.strftime("%Y-%m-%dT%H%M%SZ"), now.strftime("%Y-%m-%dT%H:%M:%SZ")


class PackageBuilder:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build(self, run_dir: Path, package_dir: Path | None = None, notes: str = "") -> PackageResult:
        timestamp, delivered_at = utc_timestamp()
        target = package_dir or (run_dir / "package")
        if target.exists() and any(target.iterdir()):
            raise FileExistsError(f"package directory is not empty: {target}")
        target.mkdir(parents=True, exist_ok=True)

        self._gzip_if_present(run_dir / "geo.ndjson", target / "geo.ndjson.gz", required=True)
        self._gzip_if_present(run_dir / "media.ndjson", target / "media.ndjson.gz", required=False)
        self._gzip_if_present(run_dir / "places.ndjson", target / "places.ndjson.gz", required=False)
        self._gzip_if_present(run_dir / "annotations.ndjson", target / "annotations.ndjson.gz", required=False)

        source_media = run_dir / "media"
        if source_media.exists():
            shutil.copytree(source_media, target / "media", dirs_exist_ok=True)

        manifest = build_manifest(self.settings, target, delivered_at, notes=notes)
        manifest_path = target / "manifest.json"
        write_manifest(manifest_path, manifest)
        (target / "_READY").write_bytes(b"")
        return PackageResult(package_dir=target, timestamp=timestamp, delivered_at=delivered_at, manifest_path=manifest_path)

    @staticmethod
    def _gzip_if_present(source: Path, target: Path, *, required: bool) -> None:
        if not source.exists():
            if required:
                raise FileNotFoundError(f"required NDJSON file missing: {source}")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as src, gzip.open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)

