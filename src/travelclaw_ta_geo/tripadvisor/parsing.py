from __future__ import annotations

import html
import json
import mimetypes
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urljoin

GEO_URL_RE = re.compile(r"(?:^|-)g(?P<geo_id>\d+)(?:-|$)")
DETAIL_URL_RE = re.compile(r"(?:^|-)d(?P<detail_id>\d+)(?:-|$)")
TOURISM_HREF_RE = re.compile(r"""href=["'](?P<href>[^"']*Tourism-g\d+[^"']*?\.html(?:\?[^"']*)?)["']""")
SCRIPT_JSONLD_RE = re.compile(
    r"""<script[^>]+type=["']application/ld\+json["'][^>]*>(?P<body>.*?)</script>""",
    re.IGNORECASE | re.DOTALL,
)
META_RE_TEMPLATE = r"""<meta[^>]+(?:name|property)=["']{name}["'][^>]+content=["'](?P<content>[^"']*)["'][^>]*>"""
CANONICAL_RE = re.compile(r"""<link[^>]+rel=["']canonical["'][^>]+href=["'](?P<href>[^"']+)["']""", re.I)
TITLE_RE = re.compile(r"<title[^>]*>(?P<title>.*?)</title>", re.I | re.S)


def absolute_tripadvisor_url(base_url: str, href: str) -> str:
    cleaned = html.unescape(href).replace("\\/", "/")
    return urljoin(base_url.rstrip("/") + "/", cleaned)


def extract_geo_id(value: str) -> int | None:
    match = GEO_URL_RE.search(value)
    return int(match.group("geo_id")) if match else None


def extract_detail_id(value: str) -> int | None:
    match = DETAIL_URL_RE.search(value)
    return int(match.group("detail_id")) if match else None


def extract_tourism_links(html_text: str, base_url: str) -> list[str]:
    seen: set[str] = set()
    links: list[str] = []
    for match in TOURISM_HREF_RE.finditer(html_text):
        url = absolute_tripadvisor_url(base_url, match.group("href"))
        if url not in seen:
            seen.add(url)
            links.append(url)
    if links:
        return links
    for raw in re.findall(r"https?://www\.tripadvisor\.com/Tourism-g\d+[^\"'\\<\s]+?\.html", html_text):
        url = html.unescape(raw).replace("\\/", "/")
        if url not in seen:
            seen.add(url)
            links.append(url)
    return links


def text_from_html(value: str) -> str:
    value = re.sub(r"<script\b.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style\b.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return clean_text(value)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def extract_title(html_text: str) -> str:
    match = TITLE_RE.search(html_text)
    return clean_text(match.group("title")) if match else ""


def extract_meta(html_text: str, name: str) -> str:
    pattern = re.compile(META_RE_TEMPLATE.format(name=re.escape(name)), re.I | re.S)
    match = pattern.search(html_text)
    if match:
        return clean_text(match.group("content"))
    alternate = re.compile(
        r"""<meta[^>]+content=["'](?P<content>[^"']*)["'][^>]+(?:name|property)=["']"""
        + re.escape(name)
        + r"""["'][^>]*>""",
        re.I | re.S,
    )
    match = alternate.search(html_text)
    return clean_text(match.group("content")) if match else ""


def extract_canonical(html_text: str) -> str:
    match = CANONICAL_RE.search(html_text)
    return html.unescape(match.group("href")) if match else ""


def extract_json_ld(html_text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for match in SCRIPT_JSONLD_RE.finditer(html_text):
        body = html.unescape(match.group("body")).strip()
        if not body:
            continue
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            items.extend(item for item in parsed if isinstance(item, dict))
        elif isinstance(parsed, dict):
            graph = parsed.get("@graph")
            if isinstance(graph, list):
                items.extend(item for item in graph if isinstance(item, dict))
            items.append(parsed)
    return items


def extract_breadcrumbs(json_ld: Iterable[dict[str, Any]]) -> list[str]:
    crumbs: list[str] = []
    for item in json_ld:
        if item.get("@type") != "BreadcrumbList":
            continue
        elements = item.get("itemListElement") or []
        for element in elements:
            if not isinstance(element, dict):
                continue
            raw_name = element.get("name")
            if not raw_name:
                nested = element.get("item")
                if isinstance(nested, dict):
                    raw_name = nested.get("name")
            text = clean_text(str(raw_name)) if raw_name else ""
            if text:
                crumbs.append(text)
    return crumbs


def extract_gallery_query_id(html_text: str, fallback: str) -> str:
    haystack = html.unescape(html_text).replace('\\"', '"').replace("\\/", "/")
    marker = 'dataStrategy":"geo"'
    index = haystack.find(marker)
    if index == -1:
        return fallback
    window = haystack[max(0, index - 5000) : index + 5000]
    ids = re.findall(r'preRegisteredQueryId"\s*:\s*"([a-f0-9]{12,32})"', window)
    return ids[-1] if ids else fallback


def normalize_image_url(url: str, width: int | None = None, height: int | None = None, default_side: int = 2000) -> str:
    target_width = min(width or default_side, default_side)
    target_height = min(height or default_side, default_side)
    normalized = html.unescape(url).replace("\\/", "/")
    normalized = normalized.replace("{width}", str(target_width)).replace("{height}", str(target_height))
    return normalized


def mime_to_extension(mime_type: str) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    return mapping.get(mime_type.lower()) or mimetypes.guess_extension(mime_type) or ".img"


def iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_dicts(child)
    elif isinstance(value, list):
        for item in value:
            yield from iter_dicts(item)

