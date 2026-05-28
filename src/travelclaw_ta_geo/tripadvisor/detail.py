from __future__ import annotations

import re

from travelclaw_ta_geo.settings import Settings
from travelclaw_ta_geo.tripadvisor.http import TripadvisorHttpClient
from travelclaw_ta_geo.tripadvisor.models import DiscoveryResult, GeoPage
from travelclaw_ta_geo.tripadvisor.parsing import (
    clean_text,
    extract_breadcrumbs,
    extract_canonical,
    extract_gallery_query_id,
    extract_json_ld,
    extract_meta,
    extract_title,
    text_from_html,
)


class TripadvisorDetailParser:
    def __init__(self, settings: Settings, client: TripadvisorHttpClient) -> None:
        self.settings = settings
        self.client = client

    def fetch_and_parse(self, discovery: DiscoveryResult) -> tuple[GeoPage, str]:
        try:
            html = self.client.get_html(discovery.url, referer=self.settings.base_url + "/")
        except Exception:
            page = GeoPage(
                seed=discovery.seed,
                url=discovery.url,
                geo_id=discovery.geo_id,
                canonical_url=discovery.url,
                name=discovery.seed.name_en or discovery.seed.name_cn,
                gallery_query_id=self.settings.ta_gallery_query_id,
                raw_meta={"detail_html": "unavailable"},
            )
            return page, ""
        json_ld = extract_json_ld(html)
        title = extract_title(html)
        canonical = extract_canonical(html) or discovery.url
        description = extract_meta(html, "description") or extract_meta(html, "og:description")
        name = self._extract_name(html, title, discovery.seed.name_en)
        page = GeoPage(
            seed=discovery.seed,
            url=discovery.url,
            geo_id=discovery.geo_id,
            title=title,
            canonical_url=canonical,
            name=name,
            description=description,
            breadcrumbs=extract_breadcrumbs(json_ld),
            sections_seen=self._sections_seen(html),
            review_count_text=self._review_count_text(html),
            gallery_query_id=extract_gallery_query_id(html, self.settings.ta_gallery_query_id),
            raw_meta={
                "meta_description": description,
                "og_image": extract_meta(html, "og:image"),
                "json_ld_types": sorted({str(item.get("@type")) for item in json_ld if item.get("@type")}),
            },
        )
        return page, html

    @staticmethod
    def _extract_name(html: str, title: str, fallback: str) -> str:
        h1 = re.search(r"<h1[^>]*>(?P<body>.*?)</h1>", html, re.I | re.S)
        if h1:
            text = text_from_html(h1.group("body"))
            if text:
                return text
        if title:
            for delimiter in [":", " - Tripadvisor", " | Tripadvisor"]:
                if delimiter in title:
                    value = title.split(delimiter, 1)[0]
                    if value:
                        return clean_text(value)
            return clean_text(title)
        return fallback

    @staticmethod
    def _sections_seen(html: str) -> list[str]:
        text = text_from_html(html).lower()
        sections = []
        probes = {
            "essential": "essential",
            "travel_advice": "travel advice",
            "faq": "frequently asked questions",
            "ai_itineraries": "itinerar",
            "sponsored": "sponsored",
        }
        for key, needle in probes.items():
            if needle in text:
                sections.append(key)
        return sections

    @staticmethod
    def _review_count_text(html: str) -> str:
        text = text_from_html(html)
        match = re.search(r"([0-9][0-9,\.]*)\s+(?:reviews|contributions)", text, re.I)
        return match.group(1) if match else ""

