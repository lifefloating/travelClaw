from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from travelclaw_ta_geo.settings import Settings


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_gzip_ndjson(path: Path) -> int:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def file_kind(relative_path: str) -> str:
    if relative_path == "geo.ndjson.gz":
        return "geo"
    if relative_path == "places.ndjson.gz":
        return "places"
    if relative_path == "media.ndjson.gz":
        return "media"
    if relative_path == "annotations.ndjson.gz":
        return "annotations"
    if relative_path.startswith("media/"):
        return "media_blob"
    raise ValueError(f"unsupported package file path: {relative_path}")


def build_manifest(settings: Settings, package_dir: Path, delivered_at: str, notes: str = "") -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file() or path.name in {"manifest.json", "_READY"}:
            continue
        relative = path.relative_to(package_dir).as_posix()
        entry: dict[str, Any] = {
            "path": relative,
            "kind": file_kind(relative),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        if relative.endswith(".ndjson.gz"):
            entry["row_count"] = count_gzip_ndjson(path)
        files.append(entry)

    manifest: dict[str, Any] = {
        "vendor_id": settings.vendor_id,
        "focus": settings.focus,
        "source": settings.source,
        "delivered_at": delivered_at,
        "package_version": 1,
        "license": settings.license,
        "attribution": settings.attribution,
        "contact": {"name": settings.contact_name, "email": settings.contact_email},
        "files": files,
    }
    if notes:
        manifest["notes"] = notes
    return manifest


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")

