from pathlib import Path

from travelclaw_ta_geo.seeds import load_seeds


def test_load_sample_seeds() -> None:
    seeds = load_seeds(Path("seeds/destinations.sample.csv"))
    assert len(seeds) == 20
    assert seeds[0].name_en == "Kyoto"
    assert seeds[0].country_code == "JP"
    assert seeds[0].has_center

