import gzip
import json

from travelclaw_ta_geo.output.package import PackageBuilder
from travelclaw_ta_geo.settings import Settings


def test_package_manifest_lists_geo_and_media_blob(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "geo.ndjson").write_text('{"record_id":"g1","source":"tripadvisor","name":"Kyoto","center":{"lat":1,"lng":2}}\n', encoding="utf-8")
    (run_dir / "media.ndjson").write_text(
        '{"record_id":"m1","source":"tripadvisor","path":"media/1/a.jpg","mime_type":"image/jpeg"}\n',
        encoding="utf-8",
    )
    media_dir = run_dir / "media" / "1"
    media_dir.mkdir(parents=True)
    (media_dir / "a.jpg").write_bytes(b"fake")

    result = PackageBuilder(Settings()).build(run_dir)
    manifest = json.loads((result.package_dir / "manifest.json").read_text(encoding="utf-8"))
    paths = {item["path"]: item for item in manifest["files"]}
    assert paths["geo.ndjson.gz"]["kind"] == "geo"
    assert paths["geo.ndjson.gz"]["row_count"] == 1
    assert paths["media.ndjson.gz"]["kind"] == "media"
    assert paths["media/1/a.jpg"]["kind"] == "media_blob"
    assert (result.package_dir / "_READY").exists()
    with gzip.open(result.package_dir / "geo.ndjson.gz", "rt", encoding="utf-8") as handle:
        assert json.loads(handle.readline())["name"] == "Kyoto"

