"""Offline test of scripts/basemap_regions.py: a dry run over local region lists and the fixture planet, and the key guard."""
import json, os, subprocess, sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)
import basemap_regions as br  # noqa: E402
import pmtiles_cut as pc  # noqa: E402
from test_pmtiles_cut import make_planet  # noqa: E402


def _lists(root):
    regions = root / "regions"
    (regions / "polygons").mkdir(parents=True)
    recs = [
        {"id": "us-id-small", "kind": "county", "country": "US", "region_code": "US-ID", "bbox": [-114, 42, -113, 43],
         "polygon": "polygons/us-id-small.geojson"},
        {"id": "us-ut-small", "kind": "locality", "country": "US", "region_code": "US-UT", "bbox": [-112.5, 40, -111, 41],
         "polygon": "polygons/us-ut-small.geojson"},
        {"id": "us-metro-x", "kind": "metro", "country": "US", "bbox": [-114, 41, -111, 43], "polygon": "polygons/us-metro-x.geojson"},
        {"id": "us-no-outline", "kind": "county", "country": "US", "region_code": "US-ID"},
    ]
    polys = {"us-id-small": [[[-114, 42], [-113, 42], [-113.5, 43], [-114, 42]]],
             "us-ut-small": [[[-112.5, 40], [-111, 40], [-111, 41], [-112.5, 41], [-112.5, 40]]],
             "us-metro-x": [[[-114, 41], [-111, 41], [-111, 43], [-114, 41]]]}
    for k, c in polys.items():
        (regions / "polygons" / f"{k}.geojson").write_text(json.dumps({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": c}}))
    (regions / "us.json").write_text(json.dumps({"format": 1, "shard": "us", "regions": recs}))
    (regions / "index.json").write_text(json.dumps({"polygon_base": (regions / "polygons").as_uri() + "/"}))
    return regions.as_uri() + "/", polys


def test_select_groups_and_selectors(tmp_path):
    base, _ = _lists(tmp_path)
    picked, _ = br.select(base, "country:us")
    assert [(g, r["id"]) for g, r in picked] == [("us-id", "us-id-small"), ("us-other", "us-metro-x"), ("us-ut", "us-ut-small")]
    assert [r["id"] for _, r in br.select(base, "state:us-ut")[0]] == ["us-ut-small"]
    assert [r["id"] for _, r in br.select(base, "ids:us-metro-x")[0]] == ["us-metro-x"]
    with pytest.raises(pc.CutError):
        br.select(base, "ids:us-no-outline")
    with pytest.raises(pc.CutError):
        br.parse_selector("state:us-id; rm -rf /")


def test_dry_run_cuts_verifies_and_writes_a_manifest(tmp_path):
    base, polys = _lists(tmp_path)
    pub = tmp_path / "pub"
    (pub / "basemap").mkdir(parents=True)
    planet = pub / "basemap" / "protomaps-planet-20261103.pmtiles"
    tiles = make_planet(str(planet))
    work = tmp_path / "work"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AWS_", "R2_"))}
    r = subprocess.run([sys.executable, os.path.join(HERE, "..", "scripts", "basemap_regions.py"), "--regions-base", base, "run",
                        "--selector", "country:us", "--group", "us-id", "--planet-version", "20261103",
                        "--public-url", str(pub), "--work", str(work), "--maxzoom", "7", "--dry-run"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    m = json.loads((work / "manifest-us-id.json").read_text())
    assert m["complete"] and m["dry_run"] and [e["id"] for e in m["regions"]] == ["us-id-small"]
    e = m["regions"][0]
    assert e["key"] == "basemap-regions/20261103/us-id-small.pmtiles" and len(e["sha256"]) == 64 and not e["uploaded"]
    rings = pc.outline_rings({"type": "Polygon", "coordinates": polys["us-id-small"]})
    want = {pc.zxy_to_id(*t) for t in pc.cover_tiles(rings, 0, 7)} & set(tiles)
    assert e["tiles"] == len(want)
    assert not (work / "us-id-small.pmtiles").exists()          # the local file is deleted after verify


def test_r2_refuses_any_other_key(monkeypatch):
    monkeypatch.setenv("R2_BUCKET", "b")
    monkeypatch.setenv("R2_ENDPOINT", "https://example.invalid")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    r2 = br.R2("20261103")
    assert r2._path("basemap-regions/20261103/us-id.pmtiles") == "r2:b/basemap-regions/20261103/us-id.pmtiles"
    assert r2._path("basemap-regions/20261103/manifests/us-id.json")
    for bad in ["basemap/protomaps-planet-20261103.pmtiles", "catalogue.json", "regions/us.json",
                "basemap-regions/20261025/us-id.pmtiles", "basemap-regions/20261103/../catalogue.json",
                "basemap-regions/20261103/x/y.pmtiles", "basemap-regions/20261103/us-id.json"]:
        with pytest.raises(pc.CutError):
            r2._path(bad)
    assert not hasattr(r2, "delete")
