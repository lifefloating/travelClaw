from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DestinationSeed:
    name_cn: str
    name_en: str
    latitude: float
    longitude: float
    kind: str = ""
    country_code: str = ""
    tripadvisor_url: str = ""
    tripadvisor_geo_id: str = ""
    parent_destination: str = ""

    @property
    def key(self) -> str:
        text = self.tripadvisor_geo_id or self.name_en or self.name_cn
        return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")

    @property
    def has_center(self) -> bool:
        return -90 <= self.latitude <= 90 and -180 <= self.longitude <= 180


def load_seeds(path: str | Path) -> list[DestinationSeed]:
    seed_path = Path(path)
    if not seed_path.exists():
        raise FileNotFoundError(f"seed file not found: {seed_path}")
    if seed_path.suffix.lower() in {".jsonl", ".ndjson"}:
        return list(_load_jsonl(seed_path))
    return list(_load_csv(seed_path))


def _load_csv(path: Path) -> Iterable[DestinationSeed]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            yield _seed_from_mapping(row)


def _load_jsonl(path: Path) -> Iterable[DestinationSeed]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            yield _seed_from_mapping(json.loads(line))


def _seed_from_mapping(row: dict[str, object]) -> DestinationSeed:
    def text(name: str) -> str:
        value = row.get(name, "")
        return "" if value is None else str(value).strip()

    return DestinationSeed(
        name_cn=text("name_cn"),
        name_en=text("name_en"),
        latitude=float(row.get("latitude") or row.get("lat") or 0),
        longitude=float(row.get("longitude") or row.get("lng") or row.get("longtitude") or 0),
        kind=text("kind"),
        country_code=text("country_code").upper(),
        tripadvisor_url=text("tripadvisor_url"),
        tripadvisor_geo_id=text("tripadvisor_geo_id"),
        parent_destination=text("parent_destination") or text("parent") or text("parent_name_en"),
    )
