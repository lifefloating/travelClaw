from travelclaw_ta_geo.seeds import DestinationSeed
from travelclaw_ta_geo.settings import Settings
from travelclaw_ta_geo.tripadvisor.graphql import TripadvisorGraphQL
from travelclaw_ta_geo.tripadvisor.models import GeoPage


class DummyClient:
    pass


class GalleryFallbackClient:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.html_urls: list[str] = []

    def post_graphql(self, payload, referer: str):  # type: ignore[no-untyped-def]
        return [{"data": {"album": {"totalMediaCount": 50016, "items": []}}}]

    def get_html(self, url: str, referer: str | None = None) -> str:
        self.html_urls.append(url)
        return self.pages[url]


def test_extract_media_picks_largest_size_within_budget() -> None:
    gql = TripadvisorGraphQL(Settings(), DummyClient())  # type: ignore[arg-type]
    response = [
        {
            "data": {
                "album": {
                    "totalMediaCount": 2,
                    "items": [
                        {
                            "__typename": "Media_PhotoResult",
                            "id": 123,
                            "caption": "Hero",
                            "uploadDateTime": "2026-05-28T00:00:00Z",
                            "supplierCategory": "USER",
                            "sizes": [
                                {
                                    "url": "https://dynamic-media-cdn.tripadvisor.com/media/photo-l/test-100.jpg",
                                    "width": 100,
                                    "height": 67,
                                },
                                {
                                    "url": "https://dynamic-media-cdn.tripadvisor.com/media/photo-l/test-2000.jpg",
                                    "width": 2000,
                                    "height": 1334,
                                },
                                {
                                    "url": "https://dynamic-media-cdn.tripadvisor.com/media/photo-o/test-3000.jpg",
                                    "width": 3000,
                                    "height": 2000,
                                },
                            ],
                        }
                    ],
                }
            }
        }
    ]
    items = gql._extract_media(response, 298564)
    assert len(items) == 1
    assert items[0].media_id == "123"
    assert items[0].caption == "Hero"
    assert items[0].url.endswith("test-3000.jpg")
    assert items[0].width == 3000
    assert items[0].height == 2000
    assert gql._extract_total_count(response) == 2


def test_extract_media_skips_zero_size_origin_entry() -> None:
    gql = TripadvisorGraphQL(Settings(), DummyClient())  # type: ignore[arg-type]
    response = [
        {
            "__typename": "Media_PhotoResult",
            "id": 7,
            "sizes": [
                {"url": "https://dynamic-media-cdn.tripadvisor.com/media/photo-o/zero.jpg", "width": 0, "height": 0},
                {
                    "url": "https://dynamic-media-cdn.tripadvisor.com/media/photo-l/large.jpg",
                    "width": 1280,
                    "height": 853,
                },
            ],
        }
    ]
    items = gql._extract_media(response, 1)
    assert len(items) == 1
    assert items[0].url.endswith("large.jpg")
    assert items[0].width == 1280


def test_extract_location_photo_media_dedupes_thumbnails_and_uses_original_variant() -> None:
    gql = TripadvisorGraphQL(Settings(), DummyClient())  # type: ignore[arg-type]
    parser = gql._parse_location_photos_html(
        """
        <div>1-6 of 50,016</div>
        <a href="/LocationPhotos-g298564-w2-Kyoto_Kyoto_Prefecture_Kinki.html">next</a>
        <img class="taLnk big_photo"
             src="https://media-cdn.tripadvisor.com/media/photo-s/03/9b/2d/dc/kyoto.jpg"
             alt="Kyoto, Japan: Photo provided by ©4Corners">
        <img src="https://media-cdn.tripadvisor.com/media/photo-t/03/9b/2d/dc/kyoto.jpg"
             alt="Photo thumbnail">
        """
    )

    items = gql._extract_location_photo_media(
        parser,
        geo_id=298564,
        source_page_url="https://www.tripadvisor.com/LocationPhotos-g298564-Kyoto.html",
    )

    assert len(items) == 1
    assert items[0].media_id == "location_photo:03-9b-2d-dc-kyoto"
    assert items[0].url == "https://media-cdn.tripadvisor.com/media/photo-o/03/9b/2d/dc/kyoto.jpg"
    assert items[0].caption == "Kyoto, Japan: Photo provided by ©4Corners"
    assert items[0].source_kind == "location_photos"


def test_gallery_media_falls_back_to_location_photos_pages_when_graphql_is_short() -> None:
    first_url = "https://www.tripadvisor.com/LocationPhotos-g298564-Kyoto_Kyoto_Prefecture_Kinki.html"
    second_url = "https://www.tripadvisor.com/LocationPhotos-g298564-w2-Kyoto_Kyoto_Prefecture_Kinki.html"
    client = GalleryFallbackClient(
        {
            first_url: _location_photos_page_html(1, 6, next_href="/LocationPhotos-g298564-w2-Kyoto_Kyoto_Prefecture_Kinki.html"),
            second_url: _location_photos_page_html(7, 12),
        }
    )
    gql = TripadvisorGraphQL(Settings(), client)  # type: ignore[arg-type]
    page = GeoPage(
        seed=DestinationSeed(name_cn="", name_en="Kyoto", latitude=0, longitude=0),
        url="https://www.tripadvisor.com/Tourism-g298564-Kyoto_Kyoto_Prefecture_Kinki-Vacations.html",
        canonical_url="https://www.tripadvisor.com/Tourism-g298564-Kyoto_Kyoto_Prefecture_Kinki-Vacations.html",
        geo_id=298564,
    )

    items, meta = gql.gallery_media(page, max_images=8)

    assert len(items) == 8
    assert client.html_urls == [first_url, second_url]
    assert {item.source_kind for item in items} == {"location_photos"}
    assert meta["location_photos"]["total_photo_count"] == 50016
    assert meta["location_photos"]["pages_fetched"] == 2


def _location_photos_page_html(start: int, end: int, next_href: str = "") -> str:
    next_link = f'<a href="{next_href}">next</a>' if next_href else ""
    images = "\n".join(
        f'<img class="taLnk big_photo" src="https://media-cdn.tripadvisor.com/media/photo-s/aa/bb/cc/{idx:02d}/caption.jpg" alt="Photo {idx}">'
        for idx in range(start, end + 1)
    )
    return f"<html><body><div>{start}-{end} of 50,016</div>{next_link}{images}</body></html>"
