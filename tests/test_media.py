from io import BytesIO

from PIL import Image

from travelclaw_ta_geo.settings import Settings
from travelclaw_ta_geo.tripadvisor.media import DownloadedMedia, MediaDownloader, MediaDownloadError, _hamming
from travelclaw_ta_geo.tripadvisor.models import MediaCandidate


def test_imagehash_phash_is_hex_and_hamming_compatible() -> None:
    image = Image.new("RGB", (32, 32), color=(120, 80, 40))

    phash = MediaDownloader._phash_from_pil(image)

    assert len(phash) == 16
    assert int(phash, 16) >= 0
    assert _hamming(phash, phash) == 0


def test_duplicate_phash_is_silently_skipped(tmp_path) -> None:
    image = Image.new("RGB", (64, 64), color=(20, 120, 200))
    buffer = BytesIO()
    image.save(buffer, format="JPEG")

    class FakeClient:
        def download_bytes(self, url: str):
            return buffer.getvalue(), "image/jpeg"

    candidates = [
        MediaCandidate(media_id="one", url="https://media-cdn.tripadvisor.com/one.jpg", source_url="one"),
        MediaCandidate(media_id="two", url="https://media-cdn.tripadvisor.com/two.jpg", source_url="two"),
    ]
    downloader = MediaDownloader(
        Settings(ta_image_concurrency=2, ta_image_dedupe_distance=8),
        FakeClient(),
    )

    results = list(
        downloader.download_many(
            candidates,
            run_dir=tmp_path,
            geo_id=298564,
            geo_record_id="geo-record",
            captured_at="2026-05-29T00:00:00Z",
            max_items=2,
        )
    )

    assert len(results) == 1
    assert isinstance(results[0], DownloadedMedia)
    assert not any(isinstance(result, MediaDownloadError) for result in results)
    assert len(list((tmp_path / "media" / "298564").glob("*.jpg"))) == 1
