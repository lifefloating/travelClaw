from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from travelclaw_ta_geo.seeds import DestinationSeed


@dataclass(frozen=True)
class DiscoveryResult:
    seed: DestinationSeed
    url: str
    geo_id: int
    discovered_by: str


@dataclass
class GeoPage:
    seed: DestinationSeed
    url: str
    geo_id: int
    title: str = ""
    canonical_url: str = ""
    name: str = ""
    description: str = ""
    breadcrumbs: list[str] = field(default_factory=list)
    sections_seen: list[str] = field(default_factory=list)
    review_count_text: str = ""
    gallery_query_id: str = ""
    raw_meta: dict[str, Any] = field(default_factory=dict)

    @property
    def record_id(self) -> str:
        return f"qiqi:tripadvisor:geo:{self.geo_id}"


@dataclass(frozen=True)
class MediaCandidate:
    media_id: str
    url: str
    source_url: str
    width: int | None = None
    height: int | None = None
    caption: str = ""
    source_kind: str = "geo_gallery"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        return self.media_id or self.url

