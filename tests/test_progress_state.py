from __future__ import annotations

from travelclaw_ta_geo.progress import CityStatus, Stage, StatusWriter, read_all_statuses, read_status
from travelclaw_ta_geo.state import PersistentState


def test_status_roundtrip(tmp_path):
    path = tmp_path / "298564.json"
    writer = StatusWriter(path, CityStatus(city_key="298564", name="Kyoto", geo_id=298564))
    writer.set_stage(Stage.DOWNLOADING)
    writer.bump_images(done=3, total=10)

    loaded = read_status(path)
    assert loaded is not None
    assert loaded.city_key == "298564"
    assert loaded.stage == Stage.DOWNLOADING.value
    assert loaded.images_done == 3
    assert loaded.images_total == 10
    assert not loaded.is_terminal

    writer.set_stage(Stage.DONE)
    assert read_status(path).is_terminal


def test_read_all_statuses_sorted(tmp_path):
    StatusWriter(tmp_path / "b.json", CityStatus(city_key="b"))
    StatusWriter(tmp_path / "a.json", CityStatus(city_key="a"))
    statuses = read_all_statuses(tmp_path)
    assert [s.city_key for s in statuses] == ["a", "b"]


def test_persistent_state_skip(tmp_path):
    db = tmp_path / "state.sqlite"
    state = PersistentState(db)
    assert not state.is_city_done("298564")
    state.mark_city_done(
        "298564",
        geo_id=298564,
        name="Kyoto",
        r2_timestamp="2026-05-29T030000Z",
        media_count=42,
        completed_at="2026-05-29T03:00:00Z",
    )
    assert state.is_city_done("298564")
    state.record_media(298564, "media-1", "ff00ff00", "2026-05-29T03:00:00Z")
    state.record_media(298564, "media-1", "ff00ff00", "2026-05-29T03:00:00Z")  # idempotent
    count = state.conn.execute("SELECT COUNT(*) FROM media_index").fetchone()[0]
    assert count == 1
    state.close()
