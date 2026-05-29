from PIL import Image

from travelclaw_ta_geo.tripadvisor.media import MediaDownloader, _hamming


def test_imagehash_phash_is_hex_and_hamming_compatible() -> None:
    image = Image.new("RGB", (32, 32), color=(120, 80, 40))

    phash = MediaDownloader._phash_from_pil(image)

    assert len(phash) == 16
    assert int(phash, 16) >= 0
    assert _hamming(phash, phash) == 0
