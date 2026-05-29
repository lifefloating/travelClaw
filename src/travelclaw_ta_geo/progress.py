from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class Stage(str, Enum):
    QUEUED = "queued"
    DISCOVERING = "discovering"
    FETCHING_DETAIL = "fetching_detail"
    GALLERY = "gallery"
    DOWNLOADING = "downloading"
    PACKAGING = "packaging"
    UPLOADING = "uploading"
    CLEANUP = "cleanup"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


_TERMINAL = {Stage.DONE, Stage.FAILED, Stage.SKIPPED}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class CityStatus:
    city_key: str
    name: str = ""
    geo_id: int | None = None
    stage: str = Stage.QUEUED.value
    images_total: int = 0
    images_done: int = 0
    geo_rows: int = 0
    media_rows: int = 0
    error_rows: int = 0
    worker: int | None = None
    message: str = ""
    r2_timestamp: str = ""
    started_at: str = ""
    updated_at: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.stage in {s.value for s in _TERMINAL}


class StatusWriter:
    """Atomic per-city status writer. Each update rewrites <status>/<city_key>.json
    via a temp file + os.replace so the monitor never reads a half-written file."""

    def __init__(self, status_path: Path, status: CityStatus) -> None:
        self._path = status_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._status = status
        if not self._status.started_at:
            self._status.started_at = _utc_now()
        self._flush()

    @property
    def status(self) -> CityStatus:
        return self._status

    def update(self, **fields: Any) -> None:
        for key, value in fields.items():
            if isinstance(value, Stage):
                value = value.value
            setattr(self._status, key, value)
        self._flush()

    def set_stage(self, stage: Stage, message: str = "") -> None:
        self._status.stage = stage.value
        if message:
            self._status.message = message
        self._flush()

    def bump_images(self, *, done: int | None = None, total: int | None = None) -> None:
        if done is not None:
            self._status.images_done = done
        if total is not None:
            self._status.images_total = total
        self._flush()

    def _flush(self) -> None:
        self._status.updated_at = _utc_now()
        payload = json.dumps(asdict(self._status), ensure_ascii=False, indent=2)
        fd, tmp_name = tempfile.mkstemp(dir=str(self._path.parent), prefix=".status_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp_name, self._path)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise


def read_status(path: Path) -> CityStatus | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    known = set(CityStatus.__dataclass_fields__)
    return CityStatus(**{k: v for k, v in raw.items() if k in known})


def read_all_statuses(status_dir: Path) -> list[CityStatus]:
    if not status_dir.exists():
        return []
    out: list[CityStatus] = []
    for path in sorted(status_dir.glob("*.json")):
        status = read_status(path)
        if status is not None:
            out.append(status)
    return out
