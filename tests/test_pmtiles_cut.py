"""Offline tests for scripts/pmtiles_cut.py: a small PMTiles fixture built here, the app's cover rule, the writer's layout,
and the verify step catching a damaged file. Run: python3 -m pytest tests/"""
import gzip, json, os, random, struct, sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import pmtiles_cut as pc  # noqa: E402


# ---------------------------------------------------------------- an independent fixture writer (plain loops, no shared code)

def _varint(out, v):
    while v >= 0x80:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)


def _dir(entries):
    out = bytearray()
    _varint(out, len(entries))
    last = 0
    for t, _, _, _ in entries:
        _varint(out, t - last)
        last = t
    for _, r, _, _ in entries:
        _varint(out, r)
    for _, _, l, _ in entries:
        _varint(out, l)
    for _, _, _, o in entries:
        _varint(out, o + 1)       # never the "follows" shorthand: the reader must handle both
    return gzip.compress(bytes(out), mtime=0)


def make_planet(path, maxz=7, leaf_size=50, seed=3):
    """A planet-like archive: every tile z0..maxz over a box round Idaho and Utah, plus z0..3 world-wide; repeated contents
    (one offset shared by many tiles, and runs), a root of leaf pointers, metadata with attribution. Returns {tile id: bytes}."""
    rnd = random.Random(seed)
    want = set()
    for z in range(maxz + 1):
        x0, x1, y0, y1 = pc.tile_range((-120.0, 36.0, -108.0, 50.0), z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                want.add(pc.zxy_to_id(z, x, y))
        if z <= 3:
            for x in range(1 << z):
                for y in range(1 << z):
                    want.add(pc.zxy_to_id(z, x, y))
    tiles = {}
    ocean = b"\x1f\x8bOCEAN" + bytes(20)
    for t in sorted(want):
        if t % 7 == 0:
            tiles[t] = ocean                                   # one content, many tiles (and some runs)
        else:
            tiles[t] = bytes(rnd.randrange(256) for _ in range(rnd.randrange(5, 60)))
    # data: contents in first-appearance order; entries with runs
    data, where, entries = bytearray(), {}, []
    for t in sorted(tiles):
        b = tiles[t]
        if b not in where:
            where[b] = len(data)
            data += b
        o = where[b]
        if entries and entries[-1][0] + entries[-1][1] == t and entries[-1][3] == o:
            entries[-1][1] += 1
        else:
            entries.append([t, 1, len(b), o])
    leaves, ptrs = bytearray(), []
    for i in range(0, len(entries), leaf_size):
        c = _dir(entries[i:i + leaf_size])
        ptrs.append((entries[i][0], 0, len(c), len(leaves)))
        leaves += c
    root = _dir(ptrs)
    meta = gzip.compress(json.dumps({"attribution": "(c) OpenStreetMap", "name": "fixture"}).encode(), mtime=0)
    root_off, data_off = 127, 127 + len(root)
    meta_off = data_off + len(data)
    leaf_off = meta_off + len(meta)
    h = bytearray(127)
    h[0:8] = b"PMTiles\x03"
    h[8:96] = struct.pack("<11Q", root_off, len(root), meta_off, len(meta), leaf_off, len(leaves), data_off, len(data),
                          len(tiles), len(entries), len(where))
    h[96:102] = bytes([1, 2, 2, 1, 0, maxz])
    h[102:118] = struct.pack("<4i", -1800000000, -850511287, 1800000000, 850511287)
    with open(path, "wb") as f:
        f.write(h + root + data + meta + leaves)
    return tiles


@pytest.fixture(scope="module")
def planet(tmp_path_factory):
    p = str(tmp_path_factory.mktemp("planet") / "planet.pmtiles")
    tiles = make_planet(p)
    return p, tiles


def read_all(path):
    """{tile id: bytes} of a PMTiles file, read with this test's own loops."""
    b = open(path, "rb").read()
    h = struct.unpack("<11Q", b[8:96])
    ro, rl, mo, ml, lo, ll, do, dl = h[:8]

    def parse(raw):
        raw = gzip.decompress(raw)
        p = [0]

        def v():
            x = s = 0
            while True:
                c = raw[p[0]]
                p[0] += 1
                x |= (c & 0x7F) << s
                s += 7
                if not c & 0x80:
                    return x
        n = v()
        ids, last = [], 0
        for _ in range(n):
            last += v()
            ids.append(last)
        run = [v() for _ in range(n)]
        ln = [v() for _ in range(n)]
        off = []
        for i in range(n):
            x = v()
            off.append(off[i - 1] + ln[i - 1] if x == 0 and i > 0 else x - 1)
        return list(zip(ids, run, ln, off))
    out = {}

    def walk(es):
        for t, r, l, o in es:
            if r == 0:
                walk(parse(b[lo + o:lo + o + l]))
            else:
                for k in range(r):
                    out[t + k] = b[do + o:do + o + l]
    walk(parse(b[ro:ro + rl]))
    return out


# ---------------------------------------------------------------- tile ids

def test_tile_ids_match_the_app():
    assert [pc.zxy_to_id(*t) for t in [(0, 0, 0), (1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0), (2, 0, 0)]] == [0, 1, 2, 3, 4, 5]
    rnd = random.Random(1)
    for _ in range(300):
        z = rnd.randrange(0, 16)
        x, y = rnd.randrange(1 << z), rnd.randrange(1 << z)
        assert pc.id_to_zxy(pc.zxy_to_id(z, x, y)) == (z, x, y)
    # an aligned block's descendants are one id range at every deeper zoom
    for z0, x, y in [(3, 2, 5), (5, 17, 11)]:
        h = pc.zxy_to_id(z0, x, y) - pc.BASE[z0]
        for d in (1, 2, 3):
            ids = sorted(pc.zxy_to_id(z0 + d, (x << d) + i, (y << d) + j) for i in range(1 << d) for j in range(1 << d))
            assert ids == list(range(pc.BASE[z0 + d] + (h << 2 * d), pc.BASE[z0 + d] + ((h + 1) << 2 * d)))


# ---------------------------------------------------------------- cover parity (cover.rs tests, ported)

def _old_hits(poly, r):
    """cover.rs tests::old_hits, the tile-by-tile test the cover must reproduce (one ring)."""
    def pip(p):
        inside, j = False, len(poly) - 1
        for i in range(len(poly)):
            a, b = poly[i], poly[j]
            if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]:
                inside = not inside
            j = i
        return inside
    corners = [(r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])]
    if any(r[0] <= p[0] <= r[2] and r[1] <= p[1] <= r[3] for p in poly):
        return True
    if any(pip(c) for c in corners):
        return True
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        for k in range(4):
            if pc._seg_hits(a, b, corners[k], corners[(k + 1) % 4]):
                return True
    return False


def _brute(poly, zmin, zmax):
    b = pc.rings_bounds([poly])
    out = set()
    for z in range(zmin, zmax + 1):
        x0, x1, y0, y1 = pc.tile_range(b, z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                if _old_hits(poly, pc.tile_bbox(z, x, y)):
                    out.add((z, x, y))
    return out


SHAPES = [
    [(-114.0, 42.0), (-113.0, 42.0), (-113.5, 43.0)],
    # a concave outline with a notch, closed as GeoJSON closes its rings
    [(-117.0, 42.0), (-111.0, 42.0), (-111.0, 49.0), (-114.0, 45.0), (-117.0, 49.0), (-117.0, 42.0)],
    # edges along tile lines
    [(-112.5, 40.979898), (-112.148438, 40.979898), (-112.148438, 41.244772), (-112.5, 41.244772)],
]


@pytest.mark.parametrize("i", range(len(SHAPES)))
def test_cover_is_the_tile_by_tile_set(i):
    zmax = 12 if i != 1 else 10
    assert pc.cover_tiles([SHAPES[i]], 0, zmax) == _brute(SHAPES[i], 0, zmax)


def test_parts_make_their_union_and_a_hole_is_left_out():
    west = [(-117.0, 42.0), (-115.0, 42.0), (-115.0, 44.0), (-117.0, 44.0)]
    east = [(-113.0, 42.0), (-111.0, 42.0), (-111.0, 44.0), (-113.0, 44.0)]
    w, e, u = pc.cover_tiles([west], 6, 10), pc.cover_tiles([east], 6, 10), pc.cover_tiles([west, east], 6, 10)
    assert u == w | e
    gx = int((-114.0 + 180.0) / 360.0 * 1024.0)
    assert not any(t[0] == 10 and t[1] == gx for t in u)
    outer = [(-117.0, 42.0), (-111.0, 42.0), (-111.0, 48.0), (-117.0, 48.0)]
    hole = [(-115.0, 44.0), (-113.0, 44.0), (-113.0, 46.0), (-115.0, 46.0)]
    o = pc.Outline([outer, hole])
    assert o.contains((-116.0, 43.0)) and not o.contains((-114.0, 45.0))
    with_hole = pc.cover_tiles([outer, hole], 6, 10)
    z10 = pc.zxy_to_id(10, int((-114.0 + 180) / 360 * 1024), int(pc.lat_to_y(45.0, 10)))
    assert pc.id_to_zxy(z10) not in with_hole                      # the middle of the hole
    assert pc.cover_tiles([outer], 6, 10) > with_hole


def test_numpy_and_plain_paths_agree(monkeypatch):
    import math
    ring = [(-113.5 + 1.7 * math.cos(a / 400 * 2 * math.pi) * (1 + 0.2 * math.sin(a / 13)),
             44.0 + 1.3 * math.sin(a / 400 * 2 * math.pi)) for a in range(400)]
    hole = [(-113.5 + 0.3 * math.cos(a / 40 * 2 * math.pi), 44.0 + 0.2 * math.sin(a / 40 * 2 * math.pi)) for a in range(40)]
    rings = [ring, hole]
    monkeypatch.setattr(pc.Outline, "VEC", 10 ** 9)
    plain = pc.Outline(rings).cover(0, 11, pc.rings_bounds(rings))
    monkeypatch.setattr(pc.Outline, "VEC", 0)
    vec = pc.Outline(rings).cover(0, 11, pc.rings_bounds(rings))
    assert plain == vec
    # the banded contains() equals the plain even-odd loop
    o = pc.Outline(rings)
    rnd = random.Random(5)
    for _ in range(2000):
        p = (rnd.uniform(-116, -111), rnd.uniform(42, 46))
        inside = False
        for (b, a) in o.edges:
            if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]:
                inside = not inside
        assert o.contains(p) == inside


def test_cover_ranges_expand_to_the_listed_tiles():
    ring = SHAPES[1]
    o = pc.Outline([ring])
    rs = o.cover(3, 9, pc.rings_bounds([ring]))
    assert all(a < b for a, b in rs) and all(rs[i][1] < rs[i + 1][0] for i in range(len(rs) - 1))
    assert {pc.id_to_zxy(t) for a, b in rs for t in range(a, b)} == _brute(ring, 3, 9)


def test_outline_rings_reads_geojson_like_the_app():
    mp = {"type": "Feature", "geometry": {"type": "MultiPolygon", "coordinates": [
        [[[170, 50], [179, 50], [179, 52], [170, 50]]],
        [[[-179, 50], [-170, 50], [-170, 51], [-179, 50]], [[-178, 50.2], [-177, 50.2], [-177, 50.4]]]]}}
    assert len(pc.outline_rings(mp)) == 3
    # a box across the antimeridian keeps its larger side only (a tie keeps the west side, -180..east)
    kept = pc.outline_rings(mp, bbox=[170, 50, -170, 52])
    assert len(kept) == 2 and kept[0][0] == (-179.0, 50.0)
    kept = pc.outline_rings(mp, bbox=[165, 50, -175, 52])
    assert len(kept) == 1 and kept[0][0] == (170.0, 50.0)
    kept = pc.outline_rings(mp, bbox=[175, 50, -160, 52])
    assert len(kept) == 2 and kept[0][0] == (-179.0, 50.0)
    with pytest.raises(pc.CutError):
        pc.outline_rings({"type": "LineString", "coordinates": [[0, 0], [1, 1]]})


# ---------------------------------------------------------------- the cutter on the fixture

def test_cut_copies_exactly_the_cover(planet, tmp_path):
    src, tiles = planet
    arc = pc.Archive(pc.FileSource(src))
    rings = [SHAPES[1], [(-113.5, 40.0), (-112.0, 40.0), (-112.0, 41.0), (-113.5, 41.0)]]   # two parts
    out = str(tmp_path / "r.pmtiles")
    facts = pc.cut_region(arc, rings, out, 0, 7, samples="all")
    got = read_all(out)
    cover = {pc.zxy_to_id(*t) for t in pc.cover_tiles(rings, 0, 7)}
    want = {t: b for t, b in tiles.items() if t in cover}
    assert got == want                                            # same tiles, byte for byte, nothing else
    assert facts["tiles"] == len(want) and facts["bytes"] == os.path.getsize(out)
    assert facts["sha256"] == pc.sha256_file(out)
    h = pc.parse_header(open(out, "rb").read(127))
    assert h["min_zoom"] == 0 and h["max_zoom"] == 7 and h["clustered"]
    assert h["tile_contents"] < len(want)                         # the repeated content is stored once
    b = pc.rings_bounds(rings)
    assert [round(v, 6) for v in h["bounds"]] == [round(v, 6) for v in b]
    assert b[0] <= h["center"][0] <= b[2] and b[1] <= h["center"][1] <= b[3]
    # metadata copied unchanged
    assert pc.Archive(pc.FileSource(out)).metadata()["attribution"] == "(c) OpenStreetMap"
    # same input, same bytes
    out2 = str(tmp_path / "r2.pmtiles")
    assert pc.cut_region(arc, rings, out2, 0, 7, samples=5)["sha256"] == facts["sha256"]


def test_writer_layout_with_leaf_directories(planet, tmp_path):
    src, tiles = planet
    arc = pc.Archive(pc.FileSource(src))
    rings = [SHAPES[1]]
    rows = arc.collect(pc.Outline(rings).cover(0, 7, pc.rings_bounds(rings)))
    out = str(tmp_path / "leaves.pmtiles")
    pc.write_pmtiles(arc, rows, out, 0, 7, pc.rings_bounds(rings), root_max=60, leaf_size=16)
    b = open(out, "rb").read()
    h = pc.parse_header(b[:127])
    assert h["root_offset"] == 127 and h["root_offset"] + h["root_len"] <= 16384
    assert h["meta_offset"] == 127 + h["root_len"] and h["leaf_offset"] == h["meta_offset"] + h["meta_len"]
    assert h["leaf_len"] > 0 and h["data_offset"] >= 16384 and h["data_offset"] + h["data_len"] == len(b)
    ids, run, ln, off = pc.parse_dir(gzip.decompress(b[127:127 + h["root_len"]]))
    assert (run == 0).all() and len(ids) > 1 and list(ids) == sorted(ids)
    assert read_all(out) == {t: tiles[t] for t in {pc.zxy_to_id(*z) for z in pc.cover_tiles(rings, 0, 7)} if t in tiles}
    pc.verify(out, arc, rows, 0, 7, samples="all")
    # and without leaves when the root fits
    out2 = str(tmp_path / "flat.pmtiles")
    pc.write_pmtiles(arc, rows, out2, 0, 7, pc.rings_bounds(rings))
    assert pc.parse_header(open(out2, "rb").read(127))["leaf_len"] == 0
    assert read_all(out2) == read_all(out)


def test_directory_round_trip():
    import numpy as np
    ids = np.array([5, 6, 9, 300, 70000, 2 ** 40])
    run = np.array([1, 2, 0, 5, 1, 3])
    ln = np.array([10, 20, 30, 1, 2 ** 31, 7])
    off = np.array([0, 10, 30, 0, 61, 2 ** 37])
    a = pc.parse_dir(pc.serialize_dir(ids, run, ln, off))
    for x, y in zip(a, (ids, run, ln, off)):
        assert list(x) == list(y)


# ---------------------------------------------------------------- verify catches damage

def _cut(planet, tmp_path):
    src, _ = planet
    arc = pc.Archive(pc.FileSource(src))
    rings = [SHAPES[1]]
    rows = arc.collect(pc.Outline(rings).cover(0, 7, pc.rings_bounds(rings)))
    out = str(tmp_path / "c.pmtiles")
    pc.write_pmtiles(arc, rows, out, 0, 7, pc.rings_bounds(rings))
    return arc, rows, out


def test_verify_catches_a_changed_tile_byte(planet, tmp_path):
    arc, rows, out = _cut(planet, tmp_path)
    pc.verify(out, arc, rows, 0, 7, samples="all")
    h = pc.parse_header(open(out, "rb").read(127))
    with open(out, "r+b") as f:
        f.seek(h["data_offset"] + h["data_len"] // 2)
        c = f.read(1)
        f.seek(-1, 1)
        f.write(bytes([c[0] ^ 0xFF]))
    with pytest.raises(pc.CutError, match="differ from the source"):
        pc.verify(out, arc, rows, 0, 7, samples="all")


def test_verify_catches_a_bad_header_and_a_short_file(planet, tmp_path):
    arc, rows, out = _cut(planet, tmp_path)
    raw = open(out, "rb").read()
    open(out, "wb").write(b"XMTiles" + raw[7:])
    with pytest.raises(pc.CutError, match="not a PMTiles"):
        pc.verify(out, arc, rows, 0, 7)
    open(out, "wb").write(raw[:-10])
    with pytest.raises(pc.CutError):
        pc.verify(out, arc, rows, 0, 7)


def test_verify_catches_missing_or_extra_tiles(planet, tmp_path):
    arc, rows, out = _cut(planet, tmp_path)
    with pytest.raises(pc.CutError, match="not the tiles of the cover"):
        pc.verify(out, arc, rows[1:], 0, 7)
    with pytest.raises(pc.CutError, match="zooms"):
        pc.verify(out, arc, rows, 0, 8)


# ---------------------------------------------------------------- per-region cost in a job

def test_a_small_region_costs_its_tile_reads_plus_a_constant(planet, tmp_path, monkeypatch):
    """One archive per job: once its directory pages are cached, a region's requests are its own merged tile reads plus
    its merged verify reads; verify no longer makes one request per sampled tile."""
    monkeypatch.setattr(pc, "GAP", 0)                 # no merging across gaps, so the fixture's reads are not all one
    src, _ = planet
    fs = pc.FileSource(src)
    arc = pc.Archive(fs)
    a = [(-114.3, 42.6), (-114.0, 42.6), (-114.15, 42.8)]
    b = [(-113.9, 42.6), (-113.6, 42.6), (-113.75, 42.8)]           # a neighbour
    first = pc.cut_region(arc, [a], str(tmp_path / "a.pmtiles"), 0, 7, samples="all")
    assert first["requests"]["directory"] > 0
    for ring, name in ((a, "a2"), (b, "b")):
        before = fs.requests
        f = pc.cut_region(arc, [ring], str(tmp_path / f"{name}.pmtiles"), 0, 7, samples="all")
        rq = f["requests"]
        assert fs.requests - before == rq["directory"] + rq["tiles"] + rq["verify"]     # nothing else is read
        assert rq["directory"] <= 8                                   # at most a page per zoom that a neighbour did not need
        assert rq["verify"] <= rq["tiles"] < f["verified"]["sampled"]   # merged, not one per tile
    assert pc.cut_region(arc, [a], str(tmp_path / "a3.pmtiles"), 0, 7, samples="all")["requests"]["directory"] == 0


def test_the_page_cache_is_bounded(planet):
    src, _ = planet
    arc = pc.Archive(pc.FileSource(src), max_entries=120)
    rings = [SHAPES[1]]
    arc.collect(pc.Outline(rings).cover(0, 7, pc.rings_bounds(rings)))
    assert arc._cached_entries <= 120 and len(arc._cache) >= 1


def test_disk_budget_waits_and_refuses():
    import threading, time
    d = pc.DiskBudget(".", capacity=100)
    d.reserve(70)
    got = []
    t = threading.Thread(target=lambda: (d.reserve(50), got.append(1)))
    t.start()
    time.sleep(0.1)
    assert not got                                    # waits while the space is held
    d.release(70)
    t.join(2)
    assert got and d.held == 50
    with pytest.raises(pc.CutError):
        d.reserve(101)                                # could never fit
