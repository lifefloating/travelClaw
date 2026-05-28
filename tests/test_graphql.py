from travelclaw_ta_geo.settings import Settings
from travelclaw_ta_geo.tripadvisor.graphql import TripadvisorGraphQL


class DummyClient:
    pass


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
