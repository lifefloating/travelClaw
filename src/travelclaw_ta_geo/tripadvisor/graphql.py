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
        """Harvest geo gallery photos via mediaAlbumPage, optionally topped up with
        review photos. Returns up to max_images deduped candidates plus a summary.

        See docs/research/tripadvisor_gallery_pagination_findings.md for the why:
        the album caps at ~2550 photos/geo and limit>=100 returns empty, so we
        page with limit<=99 and stop on consecutive empty pages."""
        candidates: list[MediaCandidate] = []
        seen: set[str] = set()

        gallery_summary = self._album_media(page, max_images=max_images, candidates=candidates, seen=seen)

        review_summary: dict[str, Any] = {}
        if len(candidates) < max_images:
            review_summary = self._review_photos_media(
                page,
                max_images=max_images - len(candidates),
                candidates=candidates,
                seen=seen,
            )

        summary: dict[str, Any] = {
            "source": "album+review" if review_summary else "album",
            "album": gallery_summary,
            "candidates_returned": len(candidates),
        }
        if review_summary:
            summary["review_photos"] = review_summary
        return candidates, summary

    def _album_media(
        self,
        page: GeoPage,
        *,
        max_images: int,
        candidates: list[MediaCandidate],
        seen: set[str],
    ) -> dict[str, Any]:
        """Page the official mediaAlbumPage album, appending new photos in place.

        Termination: consecutive empty pages (the album's real end signal) or the
        offset ceiling — NOT totalMediaCount, which is a multi-million UI aggregate
        the album can never page to."""
        query_id = page.gallery_query_id or self.settings.ta_gallery_query_id
        limit = min(self.settings.ta_gallery_page_limit, max(1, max_images))
        ceiling = self.settings.ta_gallery_offset_ceiling
        empty_stop = self.settings.ta_gallery_empty_page_stop

        offset = 0
        total_count: int | None = None
        pages_fetched = 0
        items_seen = 0
        empty_streak = 0
        error: str | None = None

        while len(candidates) < max_images and offset <= ceiling:
            payload = [
                {
                    "variables": {
                        "locationId": page.geo_id,
                        "albumId": 101,
                        "client": "t",
                        "dataStrategy": "geo",
                        "filter": {
                            "mediaGroup": "ALL_INCLUDING_RESTRICTED",
                            # PHOTO only (user wants no video). PHOTO_360 kept as a
                            # still image; VIDEO dropped per findings §6.
                            "mediaTypes": ["PHOTO", "PHOTO_360"],
                        },
                        "subAlbumId": 101,
                        "offset": offset,
                        "limit": limit,
                    },
                    "extensions": {"preRegisteredQueryId": query_id},
                }
            ]
            try:
                response = self.client.post_graphql(payload, referer=page.canonical_url or page.url)
                extracted = self._extract_media(response, page.geo_id)
            except Exception as exc:
                # A single failed page (after the HTTP layer's own retries) must NOT
                # discard the photos already harvested. Stop paging and return what
                # we have; the error is recorded in the summary for the geo row.
                error = f"album page offset={offset} failed: {exc}"
                break
            total_count = total_count if total_count is not None else self._extract_total_count(response)
            pages_fetched += 1
            items_seen += len(extracted)

            # Append in place; termination is driven by empty pages, not new_count.
            self._append_new(extracted, candidates, seen, max_images)

            if not extracted:
                empty_streak += 1
                if empty_streak >= empty_stop:
                    break
            else:
                empty_streak = 0
            offset += limit

        summary: dict[str, Any] = {
            "query_id": query_id,
            "limit": limit,
            "offset_ceiling": ceiling,
            "last_offset": offset,
            "pages_fetched": pages_fetched,
            "total_media_count": total_count,
            "items_seen": items_seen,
            "items_added": len(candidates),
        }
        if error:
            summary["error"] = error
        return summary

    @staticmethod
    def _append_new(
        extracted: list[MediaCandidate],
        candidates: list[MediaCandidate],
        seen: set[str],
        max_images: int,
    ) -> int:
        """Append candidates not already seen (deduped by dedupe_key), respecting
        the max_images budget. Returns how many were newly added."""
        new_count = 0
        for item in extracted:
            if item.dedupe_key in seen:
                continue
            seen.add(item.dedupe_key)
            candidates.append(item)
            new_count += 1
            if len(candidates) >= max_images:
                break
        return new_count

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

    def _review_photos_media(
        self,
        page: GeoPage,
        *,
        max_images: int,
        candidates: list[MediaCandidate],
        seen: set[str],
    ) -> dict[str, Any]:
        """OPT-IN supplement (A3): top up with user-uploaded review photos when the
        official album (~2550/geo) falls short of max_images.

        INERT unless ta_review_photos_query_id is configured. The reviewListPage
        preRegisteredQueryId is account/version-specific and must be captured from a
        live Reviews page (F12 -> POST /data/graphql/ids); see findings §8. Once set,
        we page reviews (limit<=20), pull each review's embedded photos, normalize to
        the largest size, and merge into `candidates` deduped against the album.

        Returns {} when disabled so the gallery summary stays clean."""
        query_id = self.settings.ta_review_photos_query_id.strip()
        if max_images <= 0 or not query_id:
            return {}

        limit = min(self.settings.ta_review_page_limit, 20)
        ceiling = self.settings.ta_review_offset_ceiling
        offset = 0
        pages_fetched = 0
        photos_seen = 0
        empty_streak = 0
        added_before = len(candidates)
        error: str | None = None

        while len(candidates) < max_images and offset <= ceiling:
            payload = [
                {
                    "variables": {
                        "locationId": page.geo_id,
                        "offset": offset,
                        "limit": limit,
                        "filters": [],
                        "prefs": None,
                        "initialPrefs": None,
                        "filterCacheKey": None,
                        "prefsCacheKey": None,
                        "needKeywords": False,
                        "keywordVariant": "location_keywords_v2_llr_order_30_en",
                        "language": self.settings.ta_locale.split("-", 1)[0],
                        "sortType": "SERVER_DETERMINED",
                        "photosPerReviewLimit": 7,
                    },
                    "extensions": {"preRegisteredQueryId": query_id},
                }
            ]
            try:
                response = self.client.post_graphql(payload, referer=page.canonical_url or page.url)
                extracted = self._extract_review_photos(response, page.geo_id)
            except Exception as exc:
                # A bad/stale review query id (or a transient failure that survived
                # the HTTP retries) must degrade gracefully: keep the album photos
                # already collected rather than failing the whole geo.
                error = f"review page offset={offset} failed: {exc}"
                break
            pages_fetched += 1
            photos_seen += len(extracted)

            new_count = self._append_new(extracted, candidates, seen, max_images)
            if new_count == 0:
                empty_streak += 1
                if empty_streak >= self.settings.ta_gallery_empty_page_stop:
                    break
            else:
                empty_streak = 0
            offset += limit

        summary: dict[str, Any] = {
            "query_id": query_id,
            "limit": limit,
            "offset_ceiling": ceiling,
            "last_offset": offset,
            "pages_fetched": pages_fetched,
            "photos_seen": photos_seen,
            "items_added": len(candidates) - added_before,
        }
        if error:
            summary["error"] = error
        return summary

    def _extract_review_photos(self, response: Any, geo_id: int) -> list[MediaCandidate]:
        """Pull photo nodes embedded in a reviewListPage response. Review photos
        carry the same multi-size `sizes[]` shape as album photos, so we reuse the
        largest-size picker and tag them source_kind='review_photo'."""
        items: list[MediaCandidate] = []
        seen_ids: set[str] = set()
        max_side = self.settings.ta_download_side
        for node in iter_dicts(response):
            sizes = node.get("sizes")
            if not isinstance(sizes, list) or not sizes:
                continue
            # Only treat a node as a photo when it actually exposes a usable image.
            picked = self._pick_size(sizes, max_side)
            if picked is None:
                continue
            url = picked.get("url")
            if not isinstance(url, str) or "tripadvisor.com" not in url:
                continue
            media_id = self._media_id_from_node(node, url)
            if media_id in seen_ids:
                continue
            seen_ids.add(media_id)
            items.append(
                MediaCandidate(
                    media_id=f"review_photo:{media_id}",
                    url=url,
                    source_url=url,
                    width=_int_or_none(picked.get("width")),
                    height=_int_or_none(picked.get("height")),
                    caption=self._caption_from_node(node),
                    source_kind="review_photo",
                    raw={
                        "geo_id": geo_id,
                        "upload_date_time": node.get("uploadDateTime"),
                        "size_count": len(sizes),
                    },
                )
            )
        return items


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
