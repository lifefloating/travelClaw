from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DataLayout:
    """Resolved on-disk layout under a single data root (default /data/city_geo).

    raw/     per-city working dir: geo.ndjson, media.ndjson, errors.ndjson, media/<geo_id>/*
    data/    per-city delivery package staged for R2 upload
    status/  per-city status JSON, polled by the monitor
    state/   cross-run persistent sqlite (incremental skip)
    browser/ base + per-worker browser profiles (cf_clearance lives here)
    logs/    per-city / orchestrator logs
    """

    root: Path

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def status(self) -> Path:
        return self.root / "status"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def browser(self) -> Path:
        return self.root / "browser"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def browser_base(self) -> Path:
        return self.browser / "base"

    @property
    def state_db(self) -> Path:
        return self.state / "state.sqlite"

    def worker_profile(self, worker_index: int) -> Path:
        return self.browser / f"worker_{worker_index}"

    def city_raw(self, city_key: str) -> Path:
        return self.raw / city_key

    def city_data(self, city_key: str) -> Path:
        return self.data / city_key

    def city_status(self, city_key: str) -> Path:
        return self.status / f"{city_key}.json"

    def ensure_base_dirs(self) -> None:
        for path in (self.raw, self.data, self.status, self.state, self.browser, self.logs):
            path.mkdir(parents=True, exist_ok=True)
