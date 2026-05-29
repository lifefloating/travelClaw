from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

from travelclaw_ta_geo.settings import Settings
from travelclaw_ta_geo.tripadvisor.http import TripadvisorHttpClient
from travelclaw_ta_geo.tripadvisor.models import GeoPage, MediaCandidate
from travelclaw_ta_geo.tripadvisor.parsing import absolute_tripadvisor_url, clean_text, iter_dicts, normalize_image_url

LOCATION_PHOTOS_RANGE_RE = re.compile(r"\b(?P<start>\d[\d,]*)\s*-\s*(?P<end>\d[\d,]*)\s+of\s+(?P<total>\d[\d,]*)\b")
TOURISM_TO_PHOTOS_RE = re.compile(r"/Tourism-g(?P<geo_id>\d+)-(?P<slug>.+?)-Vacations\.html")
PHOTO_VARIANT_RE = re.compile(r"/media/photo-[^/]+/")


class _LocationPhotosParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[dict[str, str]] = []
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "a":
            href = attr_map.get("href", "").strip()
            if href:
                self.hrefs.append(href)
            return
        if tag.lower() != "img":
            return

        urls: list[str] = []
        for key in ("src", "data-src", "data-lazyurl", "data-original"):
            value = attr_map.get(key, "").strip()
            if value:
                urls.append(value)
        srcset = attr_map.get("srcset", "").strip()
        if srcset:
            urls.extend(part.strip().split(" ", 1)[0] for part in srcset.split(",") if part.strip())

        for url in urls:
            self.images.append(
                {
                    "url": url,
                    "alt": attr_map.get("alt", ""),
                    "class": attr_map.get("class", ""),
                }
            )


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

        if len(candidates) < max_images:
            graphql_summary = dict(last_response_summary)
            location_items, location_summary = self._location_photos_media(
                page,
                max_images=max_images - len(candidates),
                seen=seen,
            )
            candidates.extend(location_items)
            if location_summary:
                last_response_summary = {
                    "source": "graphql+location_photos" if graphql_summary else "location_photos",
                    "graphql": graphql_summary,
                    "location_photos": location_summary,
                    "candidates_returned": len(candidates),
                }
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

    def _location_photos_media(
        self,
        page: GeoPage,
        *,
        max_images: int,
        seen: set[str],
    ) -> tuple[list[MediaCandidate], dict[str, Any]]:
        first_url = self._location_photos_url(page)
        if max_images <= 0 or not first_url:
            return [], {}

        candidates: list[MediaCandidate] = []
        current_url = first_url
        visited: set[str] = set()
        total_available: int | None = None
        pages_fetched = 0
        items_seen = 0

        while current_url and current_url not in visited and len(candidates) < max_images:
            visited.add(current_url)
            html_text = self.client.get_html(current_url, referer=page.canonical_url or page.url)
            parser = self._parse_location_photos_html(html_text)
            pages_fetched += 1
            total_available = total_available or self._extract_location_photos_total(html_text)

            extracted = self._extract_location_photo_media(
                parser,
                geo_id=page.geo_id,
                source_page_url=current_url,
            )
            items_seen += len(extracted)
            new_count = 0
            for item in extracted:
                if item.dedupe_key in seen:
                    continue
                seen.add(item.dedupe_key)
                candidates.append(item)
                new_count += 1
                if len(candidates) >= max_images:
                    break

            if len(candidates) >= max_images or new_count == 0:
                break
            current_url = self._next_location_photos_url(parser, current_url, page.geo_id)

        return candidates, {
            "first_url": first_url,
            "pages_fetched": pages_fetched,
            "items_seen": items_seen,
            "items_added": len(candidates),
            "total_photo_count": total_available,
        }

    def _location_photos_url(self, page: GeoPage) -> str:
        for candidate in (page.canonical_url, page.url):
            if not candidate:
                continue
            parsed = urlparse(candidate)
            path = parsed.path
            if "/LocationPhotos-" in path:
                return candidate
            match = TOURISM_TO_PHOTOS_RE.search(path)
            if match and int(match.group("geo_id")) == page.geo_id:
                slug = match.group("slug")
                return f"{self.settings.base_url}/LocationPhotos-g{page.geo_id}-{slug}.html"
        return ""

    @staticmethod
    def _parse_location_photos_html(html_text: str) -> _LocationPhotosParser:
        parser = _LocationPhotosParser()
        parser.feed(html_text)
        return parser

    def _extract_location_photo_media(
        self,
        parser: _LocationPhotosParser,
        *,
        geo_id: int,
        source_page_url: str,
    ) -> list[MediaCandidate]:
        items: list[MediaCandidate] = []
        seen_ids: set[str] = set()
        for image in parser.images:
            candidate = self._location_photo_candidate(image, geo_id, source_page_url)
            if candidate is None or candidate.media_id in seen_ids:
                continue
            seen_ids.add(candidate.media_id)
            items.append(candidate)
        return items

    def _location_photo_candidate(
        self,
        image: dict[str, str],
        geo_id: int,
        source_page_url: str,
    ) -> MediaCandidate | None:
        raw_url = image.get("url", "")
        if "/media/photo-" not in raw_url or "tripadvisor.com/media/photo-" not in raw_url:
            return None
        url = normalize_image_url(raw_url, default_side=self.settings.ta_download_side)
        url = PHOTO_VARIANT_RE.sub("/media/photo-o/", url, count=1)
        media_id = self._location_photo_id(url)
        if not media_id:
            return None
        return MediaCandidate(
            media_id=f"location_photo:{media_id}",
            url=url,
            source_url=url,
            caption=clean_text(image.get("alt", "")),
            source_kind="location_photos",
            raw={
                "geo_id": geo_id,
                "source_page_url": source_page_url,
                "original_url": raw_url,
                "class": image.get("class", ""),
            },
        )

    @staticmethod
    def _location_photo_id(url: str) -> str:
        path = urlparse(url).path
        marker = "/media/photo-"
        index = path.find(marker)
        if index == -1:
            return ""
        parts = path[index + len(marker) :].split("/", 1)
        if len(parts) != 2:
            return ""
        return parts[1].rsplit(".", 1)[0].replace("/", "-")

    @staticmethod
    def _extract_location_photos_total(html_text: str) -> int | None:
        text = clean_text(re.sub(r"<[^>]+>", " ", html_text))
        match = LOCATION_PHOTOS_RANGE_RE.search(text)
        if not match:
            return None
        return _int_or_none(match.group("total").replace(",", ""))

    def _next_location_photos_url(
        self,
        parser: _LocationPhotosParser,
        current_url: str,
        geo_id: int,
    ) -> str:
        next_page = self._location_photos_page_number(current_url) + 1
        wanted = f"-w{next_page}-"
        for href in parser.hrefs:
            if f"LocationPhotos-g{geo_id}" in href and wanted in href:
                return absolute_tripadvisor_url(self.settings.base_url, href)
        return ""

    @staticmethod
    def _location_photos_page_number(url: str) -> int:
        match = re.search(r"-w(?P<page>\d+)-", url)
        return int(match.group("page")) if match else 1


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
