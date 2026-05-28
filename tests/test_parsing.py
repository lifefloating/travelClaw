from travelclaw_ta_geo.tripadvisor.parsing import (
    extract_gallery_query_id,
    extract_geo_id,
    extract_tourism_links,
    normalize_image_url,
)


def test_extract_geo_id_from_tourism_url() -> None:
    url = "https://www.tripadvisor.com/Tourism-g298564-Kyoto_Kyoto_Prefecture_Kinki-Vacations.html"
    assert extract_geo_id(url) == 298564


def test_extract_tourism_links_dedupes_and_absolutizes() -> None:
    html = """
    <a href="/Tourism-g298564-Kyoto_Kyoto_Prefecture_Kinki-Vacations.html">Kyoto</a>
    <a href="/Tourism-g298564-Kyoto_Kyoto_Prefecture_Kinki-Vacations.html">Kyoto duplicate</a>
    """
    assert extract_tourism_links(html, "https://www.tripadvisor.com") == [
        "https://www.tripadvisor.com/Tourism-g298564-Kyoto_Kyoto_Prefecture_Kinki-Vacations.html"
    ]


def test_extract_gallery_query_id_near_geo_strategy() -> None:
    html = '{"extensions":{"preRegisteredQueryId":"abc123abc123"},"variables":{"dataStrategy":"geo"}}'
    assert extract_gallery_query_id(html, "fallback") == "abc123abc123"


def test_normalize_image_url_template() -> None:
    url = "https://dynamic-media-cdn.tripadvisor.com/media/photo.jpg?w={width}&h={height}&s=1"
    assert normalize_image_url(url, width=4000, height=3000, default_side=2000).endswith("w=2000&h=2000&s=1")

