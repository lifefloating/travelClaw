from travelclaw_ta_geo.crawler import build_geo_record
from travelclaw_ta_geo.seeds import DestinationSeed
from travelclaw_ta_geo.settings import Settings
from travelclaw_ta_geo.tripadvisor.models import GeoPage


def test_build_geo_record_uses_seed_center() -> None:
    seed = DestinationSeed("京都", "Kyoto", 35.0116, 135.7583, "city", "JP")
    page = GeoPage(seed=seed, url="https://www.tripadvisor.com/Tourism-g298564-x.html", geo_id=298564, name="Kyoto")
    record = build_geo_record(page, "2026-05-28T00:00:00Z", Settings(), {})
    assert record["record_id"] == "qiqi:tripadvisor:geo:298564"
    assert record["source"] == "tripadvisor"
    assert record["source_id"] == "g298564"
    assert record["center"] == {"lat": 35.0116, "lng": 135.7583}


def test_build_geo_record_prefers_clean_seed_name_and_keeps_parent() -> None:
    seed = DestinationSeed(
        "采尔马特",
        "Zermatt",
        46.0207,
        7.7491,
        "town",
        "CH",
        parent_destination="Swiss Alps",
    )
    page = GeoPage(
        seed=seed,
        url="https://www.tripadvisor.com/Tourism-g188113-Zermatt_Canton_of_Valais.html",
        geo_id=188113,
        name="Plan Your Trip to Zermatt: Best of Zermatt Tourism",
    )
    record = build_geo_record(page, "2026-05-28T00:00:00Z", Settings(), {})

    assert record["name"] == "Zermatt"
    assert record["name_i18n"] == {"en": "Zermatt", "zh-CN": "采尔马特"}
    assert record["raw"]["seed"]["parent_destination"] == "Swiss Alps"
