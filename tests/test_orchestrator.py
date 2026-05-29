from __future__ import annotations

from pathlib import Path

from travelclaw_ta_geo.orchestrator import SelectOptions, select_seeds
from travelclaw_ta_geo.paths import DataLayout

SEED_CSV = Path("seeds/destinations.sample.csv")


def test_select_by_geo_id():
    seeds = select_seeds(SelectOptions(seed_path=SEED_CSV, cities=["g298564"]))
    assert len(seeds) == 1
    assert seeds[0].name_en == "Kyoto"


def test_select_by_geo_id_without_prefix():
    seeds = select_seeds(SelectOptions(seed_path=SEED_CSV, cities=["293974"]))
    assert len(seeds) == 1
    assert seeds[0].name_en == "Istanbul"


def test_select_by_name():
    seeds = select_seeds(SelectOptions(seed_path=SEED_CSV, cities=["Paris"]))
    assert any(s.name_en == "Paris" for s in seeds)


def test_select_multiple_and_limit():
    seeds = select_seeds(SelectOptions(seed_path=SEED_CSV, cities=["g298564", "g293974"]))
    assert {s.name_en for s in seeds} == {"Kyoto", "Istanbul"}

    limited = select_seeds(SelectOptions(seed_path=SEED_CSV, limit_geos=3))
    assert len(limited) == 3


def test_layout_paths(tmp_path):
    layout = DataLayout(tmp_path)
    assert layout.city_raw("298564") == tmp_path / "raw" / "298564"
    assert layout.city_data("298564") == tmp_path / "data" / "298564"
    assert layout.city_status("298564") == tmp_path / "status" / "298564.json"
    assert layout.worker_profile(2) == tmp_path / "browser" / "worker_2"
    assert layout.state_db == tmp_path / "state" / "state.sqlite"
    layout.ensure_base_dirs()
    for sub in ("raw", "data", "status", "state", "browser", "logs"):
        assert (tmp_path / sub).is_dir()
