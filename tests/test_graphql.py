from travelclaw_ta_geo.settings import Settings
from travelclaw_ta_geo.tripadvisor.graphql import TripadvisorGraphQL


class DummyClient:
    pass


def test_extract_media_from_nested_gallery_payload() -> None:
    gql = TripadvisorGraphQL(Settings(), DummyClient())  # type: ignore[arg-type]
    response = [
        {
            "data": {
                "album": {
                    "totalMediaCount": 2,
                    "items": [
                        {
                            "id": 123,
                            "caption": "Hero",
                            "photoSizeDynamic": {
                                "maxWidth": 3000,
                                "maxHeight": 2000,
                                "urlTemplate": "https://dynamic-media-cdn.tripadvisor.com/media/photo-o/test.jpg?w={width}&h={height}&s=1",
                            },
                        }
                    ],
                }
            }
        }
    ]
    items = gql._extract_media(response, 298564)
    assert len(items) == 1
    assert items[0].media_id == "123"
    assert "w=2000" in items[0].url
    assert gql._extract_total_count(response) == 2

