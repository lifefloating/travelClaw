from pathlib import Path

from travelclaw_ta_geo.seeds import load_seeds


def test_load_sample_seeds() -> None:
    seeds = load_seeds(Path("seeds/destinations.sample.csv"))
    assert len(seeds) == 22
    assert seeds[0].name_en == "Kyoto"
    assert seeds[0].country_code == "JP"
    assert seeds[0].has_center


def test_load_supplemental_seeds_with_parent_destination() -> None:
    seeds = load_seeds(Path("seeds/destinations.supplemental.csv"))
    assert len(seeds) == 55
    assert seeds[0].name_en == "Zermatt"
    assert seeds[0].parent_destination == "Swiss Alps"
    assert seeds[0].tripadvisor_geo_id == "188113"
