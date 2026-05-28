from __future__ import annotations

import hashlib
from typing import Any

from travelclaw_ta_geo.settings import Settings
from travelclaw_ta_geo.tripadvisor.http import TripadvisorHttpClient
from travelclaw_ta_geo.tripadvisor.models import GeoPage, MediaCandidate
from travelclaw_ta_geo.tripadvisor.parsing import clean_text, iter_dicts


class TripadvisorGraphQL:
    def __init__(self, settings: Settings, client: TripadvisorHttpClient) -> None:
        self.settings = settings
        self.client = client

    def gallery_media(self, page: GeoPage, max_images: int) -> tuple[list[MediaCandidate], dict[str, Any]]:
        candidates: list[MediaCandidate] = []
        seen: set[str] = set()
        offset = 0
        limit = min(100, max(1, max_images))
        total_count: int | None = None
        last_response_summary: dict[str, Any] = {}

        while len(candidates) < max_images:
            payload = [
                {
                    "variables": {
                        "locationId": page.geo_id,
                        "albumId": 101,
                        "client": "t",
                        "dataStrategy": "geo",
                        "filter": {
                            "mediaGroup": "ALL_INCLUDING_RESTRICTED",
                            "mediaTypes": ["PHOTO", "PHOTO_360", "VIDEO"],
                        },
                        "subAlbumId": 101,
                        "offset": offset,
                        "limit": limit,
                    },
                    "extensions": {"preRegisteredQueryId": page.gallery_query_id or self.settings.ta_gallery_query_id},
                }
            ]
            response = self.client.post_graphql(payload, referer=page.canonical_url or page.url)
            extracted = self._extract_media(response, page.geo_id)
            total_count = total_count or self._extract_total_count(response)
            last_response_summary = {
                "query_id": page.gallery_query_id or self.settings.ta_gallery_query_id,
                "offset": offset,
                "limit": limit,
                "total_media_count": total_count,
                "items_seen": len(extracted),
            }
            new_count = 0
            for item in extracted:
                if item.dedupe_key in seen:
                    continue
                seen.add(item.dedupe_key)
                candidates.append(item)
                new_count += 1
                if len(candidates) >= max_images:
                    break
            if new_count == 0 or len(extracted) < limit:
                break
            offset += limit
        return candidates, last_response_summary

    def _extract_media(self, response: Any, geo_id: int) -> list[MediaCandidate]:
        """One MediaCandidate per Media_PhotoResult, downloading the largest size
        within the ta_download_side budget. Each node's `sizes[]` is the same image
        at multiple resolutions, so we must NOT flatten them into separate items."""
        items: list[MediaCandidate] = []
        seen_ids: set[str] = set()
        max_side = self.settings.ta_download_side
        for node in iter_dicts(response):
            if node.get("__typename") != "Media_PhotoResult":
                continue
            sizes = node.get("sizes")
            if not isinstance(sizes, list):
                continue
            picked = self._pick_size(sizes, max_side)
            if picked is None:
                continue
            url = picked.get("url")
            if not isinstance(url, str) or not url.startswith("http"):
                continue
            if "tripadvisor.com" not in url:
                continue
            media_id = self._media_id_from_node(node, url)
            if media_id in seen_ids:
                continue
            seen_ids.add(media_id)
            width = _int_or_none(picked.get("width"))
            height = _int_or_none(picked.get("height"))
            items.append(
                MediaCandidate(
                    media_id=media_id,
                    url=url,
                    source_url=url,
                    width=width,
                    height=height,
                    caption=self._caption_from_node(node),
                    raw={
                        "geo_id": geo_id,
                        "upload_date_time": node.get("uploadDateTime"),
                        "supplier_category": node.get("supplierCategory"),
                        "size_count": len(sizes),
                    },
                )
            )
        return items

    @staticmethod
    def _pick_size(sizes: list[Any], max_side: int) -> dict[str, Any] | None:
        valid: list[dict[str, Any]] = []
        for entry in sizes:
            if not isinstance(entry, dict):
                continue
            url = entry.get("url")
            if not isinstance(url, str) or not url.startswith("http"):
                continue
            w = _int_or_none(entry.get("width")) or 0
            h = _int_or_none(entry.get("height")) or 0
            # Skip the photo-o origin entry: reports 0x0 but serves a 100x100 thumb-crop.
            if max(w, h) == 0:
                continue
            valid.append(entry)
        if not valid:
            return None
        within = [
            e for e in valid
            if max(_int_or_none(e.get("width")) or 0, _int_or_none(e.get("height")) or 0) <= max_side
        ]
        pool = within or valid
        return max(
            pool,
            key=lambda e: max(_int_or_none(e.get("width")) or 0, _int_or_none(e.get("height")) or 0),
        )

    @staticmethod
    def _media_id_from_node(node: dict[str, Any], url: str) -> str:
        for key in ("id", "mediaId", "photoId", "photoID"):
            value = node.get(key)
            if value:
                return str(value)
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _caption_from_node(node: dict[str, Any]) -> str:
        for key in ("caption", "title", "name"):
            value = node.get(key)
            if isinstance(value, str) and value:
                return clean_text(value)
        return ""

    @staticmethod
    def _extract_total_count(response: Any) -> int | None:
        for node in iter_dicts(response):
            for key in ("totalMediaCount", "totalCount", "count"):
                value = _int_or_none(node.get(key))
                if value is not None and value >= 0:
                    return value
        return None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
