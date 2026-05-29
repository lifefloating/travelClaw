from travelclaw_ta_geo.seeds import DestinationSeed
from travelclaw_ta_geo.settings import Settings
from travelclaw_ta_geo.tripadvisor.graphql import TripadvisorGraphQL
from travelclaw_ta_geo.tripadvisor.models import GeoPage


class DummyClient:
    pass


def _photo_node(media_id: int, *, largest: int = 2000) -> dict:
    """A Media_PhotoResult with the multi-resolution sizes[] TripAdvisor returns."""
    return {
        "__typename": "Media_PhotoResult",
        "id": media_id,
        "caption": f"Photo {media_id}",
        "sizes": [
            {"url": f"https://dynamic-media-cdn.tripadvisor.com/media/photo-l/{media_id}-100.jpg", "width": 100, "height": 67},
            {
                "url": f"https://dynamic-media-cdn.tripadvisor.com/media/photo-l/{media_id}-{largest}.jpg",
                "width": largest,
                "height": int(largest * 0.66),
            },
        ],
    }


def _album_response(nodes: list[dict], total: int = 2448) -> list:
    return [{"data": {"mediaAlbumPage": [{"totalMediaCount": total, "mediaList": [{"data": n} for n in nodes]}]}}]


class AlbumPagingClient:
    """Serves album pages keyed by request offset, recording the variables of every
    POST so tests can assert on limit / mediaTypes / offset progression. Offsets in
    fail_at raise, to exercise partial-harvest retention."""

    def __init__(self, pages_by_offset: dict[int, list[dict]], fail_at: set[int] | None = None) -> None:
        self.pages_by_offset = pages_by_offset
        self.fail_at = fail_at or set()
        self.requests: list[dict] = []

    def post_graphql(self, payload, referer: str):  # type: ignore[no-untyped-def]
        variables = payload[0]["variables"]
        self.requests.append(variables)
        if variables["offset"] in self.fail_at:
            raise RuntimeError("simulated GraphQL failure")
        nodes = self.pages_by_offset.get(variables["offset"], [])
        return _album_response(nodes)


def _page(geo_id: int = 298564) -> GeoPage:
    return GeoPage(
        seed=DestinationSeed(name_cn="", name_en="Kyoto", latitude=0, longitude=0),
        url=f"https://www.tripadvisor.com/Tourism-g{geo_id}-Kyoto-Vacations.html",
        canonical_url=f"https://www.tripadvisor.com/Tourism-g{geo_id}-Kyoto-Vacations.html",
        geo_id=geo_id,
    )


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
    # Largest size within the (default 8000) budget wins — resolution must be preserved.
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


def test_gallery_media_pages_with_safe_limit_and_excludes_video() -> None:
    # Two full pages of 50, then an empty page -> stop. (limit must be <= 99: the
    # >=100 empty-list bug is the whole reason for this fix.)
    client = AlbumPagingClient(
        {
            0: [_photo_node(i) for i in range(50)],
            50: [_photo_node(i) for i in range(50, 100)],
            100: [],
        }
    )
    gql = TripadvisorGraphQL(Settings(), client)  # type: ignore[arg-type]

    items, meta = gql.gallery_media(_page(), max_images=10000)

    assert len(items) == 100
    assert {item.source_kind for item in items} == {"geo_gallery"}
    # Request hygiene: safe limit, no VIDEO, and offsets advanced by limit.
    first = client.requests[0]
    assert first["limit"] == 50
    assert first["limit"] <= 99
    assert first["filter"]["mediaTypes"] == ["PHOTO", "PHOTO_360"]
    assert "VIDEO" not in first["filter"]["mediaTypes"]
    assert [r["offset"] for r in client.requests][:3] == [0, 50, 100]
    assert meta["source"] == "album"
    assert meta["album"]["items_added"] == 100
    assert meta["album"]["total_media_count"] == 2448
    assert "review_photos" not in meta


def test_gallery_media_stops_on_consecutive_empty_pages_not_total_count() -> None:
    # totalMediaCount is a multi-million UI aggregate; the real end signal is two
    # consecutive empty pages. A single empty page must NOT stop paging.
    client = AlbumPagingClient(
        {
            0: [_photo_node(i) for i in range(50)],
            50: [],  # transient empty page
            100: [_photo_node(i) for i in range(50, 80)],
            150: [],
            200: [],  # second consecutive empty -> stop
        }
    )
    gql = TripadvisorGraphQL(Settings(), client)  # type: ignore[arg-type]

    items, _meta = gql.gallery_media(_page(), max_images=10000)

    assert len(items) == 80
    offsets = [r["offset"] for r in client.requests]
    assert offsets == [0, 50, 100, 150, 200]


def test_review_supplement_disabled_without_query_id() -> None:
    # Album short of target, but no review query id configured -> deliver what the
    # album gave and don't attempt reviews.
    client = AlbumPagingClient({0: [_photo_node(i) for i in range(10)], 10: []})
    gql = TripadvisorGraphQL(Settings(), client)  # type: ignore[arg-type]

    items, meta = gql.gallery_media(_page(), max_images=500)

    assert len(items) == 10
    assert meta["source"] == "album"
    assert "review_photos" not in meta


class AlbumThenReviewClient:
    """Album responses for the mediaAlbumPage query id, review responses for the
    configured review query id — distinguished by preRegisteredQueryId."""

    def __init__(self, album_qid: str, review_qid: str) -> None:
        self.album_qid = album_qid
        self.review_qid = review_qid
        self.requests: list[dict] = []

    def post_graphql(self, payload, referer: str):  # type: ignore[no-untyped-def]
        qid = payload[0]["extensions"]["preRegisteredQueryId"]
        variables = payload[0]["variables"]
        self.requests.append({"qid": qid, "offset": variables["offset"]})
        if qid == self.album_qid:
            if variables["offset"] == 0:
                return _album_response([_photo_node(i) for i in (1, 2, 3)])
            return _album_response([])
        # review query id: page 0 has 100,101 plus a duplicate 100 (must dedupe
        # within the review stream); page 20 repeats 101 (already seen) then stops.
        if variables["offset"] == 0:
            return _album_response([_photo_node(100), _photo_node(101), _photo_node(100)])
        if variables["offset"] == 20:
            return _album_response([_photo_node(101)])
        return _album_response([])


def test_review_supplement_merges_and_dedupes_when_query_id_set() -> None:
    settings = Settings(ta_review_photos_query_id="review-qid-xyz")
    client = AlbumThenReviewClient(album_qid=settings.ta_gallery_query_id, review_qid="review-qid-xyz")
    gql = TripadvisorGraphQL(settings, client)  # type: ignore[arg-type]

    items, meta = gql.gallery_media(_page(), max_images=500)

    # Album: ids 1,2,3 (source_kind geo_gallery). Review adds 100,101 once each
    # (the duplicate 100 and the repeated 101 are deduped by dedupe_key).
    media_ids = {item.media_id for item in items}
    assert media_ids == {"1", "2", "3", "review_photo:100", "review_photo:101"}
    assert len(items) == 5
    kinds = {item.source_kind for item in items}
    assert kinds == {"geo_gallery", "review_photo"}
    assert meta["source"] == "album+review"
    assert meta["review_photos"]["items_added"] == 2
    # The review request used the configured query id, not the album one.
    review_reqs = [r for r in client.requests if r["qid"] == "review-qid-xyz"]
    assert review_reqs and review_reqs[0]["offset"] == 0


def test_album_keeps_partial_results_when_a_page_fails() -> None:
    # Page 0 succeeds (50 photos), page at offset=50 raises. The harvested photos
    # must survive: gallery_media never propagates the error, it records it.
    client = AlbumPagingClient(
        {0: [_photo_node(i) for i in range(1, 51)]},
        fail_at={50},
    )
    gql = TripadvisorGraphQL(Settings(), client)  # type: ignore[arg-type]

    items, meta = gql.gallery_media(_page(), max_images=10000)

    assert len(items) == 50  # first page kept despite the second page failing
    assert meta["album"]["error"] is not None and "offset=50" in meta["album"]["error"]
    assert meta["album"]["items_added"] == 50
