#!/usr/bin/env python3
"""Adds download sizes to the region catalogue.

For every record of the chosen shards (default us, ca, mx) that has a bbox, works out what the Cache app would download for it,
part by part, and writes it into the record as "sizes":

  "sizes": {
    "routing": <bytes>,                          road routing tiles the region's outline touches (gzip size)
    "places": <bytes>,                           same for places, only while places/current.json is published
    "map": {"<layer id>": [<bytes>, <zoom>]},    map layers: most detail that fits one download (see below)
    "offroad": {"packs": {"<pack id>": <bytes>}, "terrain": <bytes>},
    "routing_build": "<routing build the sizes came from>",
    "built": "<UTC time>"
  }

A part that cannot be worked out is left out of the record (never null, never 0 for "unknown").

The numbers follow the app's own arithmetic, so a phone that reads them gets what it would have worked out itself:
  routing/places  geo.rs + index.rs (Valhalla tiles at levels 0, 1, 2 that the region's polygon touches; the gzip column)
  map             download.rs + core_layer_engine.dart (the region's bounding box, antimeridian box cut to its larger side;
                  PMTiles directory sizes; the layer's top zoom first, then down at most five steps, first one that fits
                  800 MiB and the layer's own limits)
  off-road        offroad_catalogue.dart (packsCovering) + regions_state.dart (terrainBox, z12 elevation only)

Map sizes are exact: they are sums over the PMTiles directory entries that fall inside the box's tile ids (a run-length entry
counts its length once for every tile id it covers; tiles not in the directory count 0). Nothing is aggregated or sampled.

Input is a directory that holds regions/<shard>.json (a fresh build, or shards downloaded from the published catalogue). The
shards are rewritten in place; nothing else in them changes. Reads only the project's own published files.

  python3 -I regions/region_sizes.py DIR [--shards us,ca,mx] [--ids us-id,us-id-jerome-county]
"""
import argparse, collections, concurrent.futures as cf, datetime, gzip, json, math, os, struct, sys, threading, time, urllib.error, urllib.request, zlib
import numpy as np, shapely
from shapely.geometry import Polygon

UA = "cache-packs-regions/1.0 (https://github.com/zvx-echo6/cache-packs)"
DATA = "https://data.cached.info"
LAYER_CATALOGUE = "https://cache.echo6.co/catalogue.json"
DEFAULT_POLYGON_BASE = "https://cache.echo6.co/regions/polygons/"
MAX_DOWNLOAD_BYTES = 800 * 1024 * 1024      # core_layer_engine.dart: maxDownloadBytes
MAX_ENUMERATED = 6_000_000                  # download.rs: most tiles of one cover; above it the app gives up on the layer
STEPS_DOWN = 5                              # core_layer_engine.dart: _stepDown goes at most five zooms below the top
TERRAIN_ZOOM = 12                           # the only zoom of elevation the off-road router reads
EDGE_EPS = 1e-9                             # geo.rs: a cell is shrunk this much so an edge on its side belongs to one side only
DIRECT_COVER = 40_000                       # covers up to this many tiles get their ids worked out one by one; larger ones by quadtree
CACHE_ENTRIES = 40_000_000                  # PMTiles directory entries kept in memory (16 bytes each)

# The elevation layer, as packages/cache_search/layers/mapterhorn.json in the app describes its offline source. Hillshade and
# contours download as "elevation".
ELEVATION = {"id": "elevation", "offline": {"allowed": True, "source": {
    "type": "pmtiles", "url": "https://download.mapterhorn.com/planet.pmtiles", "minzoom": 0, "maxzoom": 12}}}

SAMPLE_IDS = ["us-id", "us-id-jerome-county", "us-id-twin-falls-county", "us-id-twin-falls"]
SAMPLE_NAMES = ["Magic Valley", "Southern Idaho", "Eastern Idaho"]


def log(*a):
    print(*a, flush=True)


def fetch(url, rng=None, missing_ok=False):
    """GET with retries. A byte range is (first, last). A 404 gives None when missing_ok."""
    h = {"User-Agent": UA}
    if rng:
        h["Range"] = f"bytes={rng[0]}-{rng[1]}"
    err = None
    for i in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 and missing_ok:
                return None
            err = e
            if e.code not in (429, 500, 502, 503, 504):
                break
        except Exception as e:  # network trouble: try again
            err = e
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"cannot read {url}: {err}")


def fetch_json(url, missing_ok=False):
    b = fetch(url, missing_ok=missing_ok)
    return None if b is None else json.loads(b)


# ---- Hilbert tile ids (pmtiles.rs zxy_to_id) and the ids a box of tiles covers ----

def xy2d(k, x, y):
    """Position of cell (x, y) along the Hilbert curve of a 2^k x 2^k grid (the loop of pmtiles.rs zxy_to_id)."""
    n = 1 << k
    x = np.asarray(x, dtype=np.int64).copy()
    y = np.asarray(y, dtype=np.int64).copy()
    d = np.zeros_like(x)
    s = n >> 1
    while s > 0:
        rx = ((x & s) > 0).astype(np.int64)
        ry = ((y & s) > 0).astype(np.int64)
        d += s * s * ((3 * rx) ^ ry)
        m = ry == 0
        flip = m & (rx == 1)
        x = np.where(flip, n - 1 - x, x)
        y = np.where(flip, n - 1 - y, y)
        x, y = np.where(m, y, x), np.where(m, x, y)
        s >>= 1
    return d


def level_start(z):
    """First tile id of zoom z."""
    return ((1 << (2 * z)) - 1) // 3


def merge(lo, hi):
    """Sorts id intervals (inclusive, disjoint) and joins neighbours."""
    if len(lo) == 0:
        return lo, hi
    o = np.argsort(lo, kind="stable")
    lo, hi = lo[o], hi[o]
    new = np.empty(len(lo), dtype=bool)
    new[0] = True
    new[1:] = lo[1:] != hi[:-1] + 1
    starts = np.flatnonzero(new)
    ends = np.append(starts[1:], len(lo)) - 1
    return lo[starts], hi[ends]


def cover_intervals(z, x0, x1, y0, y1):
    """The tile ids of the cover (every tile x0..x1, y0..y1 of zoom z) as inclusive id intervals."""
    acc = level_start(z)
    count = (x1 - x0 + 1) * (y1 - y0 + 1)
    if count <= DIRECT_COVER:
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1, dtype=np.int64), np.arange(y0, y1 + 1, dtype=np.int64))
        ids = xy2d(z, xs.ravel(), ys.ravel()) + acc
        return merge(ids, ids.copy())
    # Quadtree: a square cell that lies wholly inside the box is one run of ids (the curve visits a cell's descendants
    # together), so only the cells along the box's edge are split further.
    los, his = [], []
    X = np.array([0], dtype=np.int64)
    Y = np.array([0], dtype=np.int64)
    for k in range(z + 1):
        s = z - k
        nx0, nx1 = X << s, ((X + 1) << s) - 1
        ny0, ny1 = Y << s, ((Y + 1) << s) - 1
        inside = (nx0 >= x0) & (nx1 <= x1) & (ny0 >= y0) & (ny1 <= y1)
        outside = (nx0 > x1) | (nx1 < x0) | (ny0 > y1) | (ny1 < y0)
        if inside.any():
            d = xy2d(k, X[inside], Y[inside])
            los.append(acc + (d << (2 * s)))
            his.append(acc + ((d + 1) << (2 * s)) - 1)
        part = ~inside & ~outside
        X, Y = X[part], Y[part]
        if len(X) == 0:
            break
        X = np.concatenate([X * 2, X * 2 + 1, X * 2, X * 2 + 1])
        Y = np.concatenate([Y * 2, Y * 2, Y * 2 + 1, Y * 2 + 1])
    return merge(np.concatenate(los), np.concatenate(his))


# ---- PMTiles v3 (directory only) ----

def varints(data):
    """Every varint of a byte string, as uint64s."""
    a = np.frombuffer(data, dtype=np.uint8)
    ends = np.flatnonzero(a < 128)
    starts = np.empty_like(ends)
    starts[0] = 0
    starts[1:] = ends[:-1] + 1
    group = np.repeat(np.arange(len(ends)), ends - starts + 1)
    shift = (np.arange(len(a)) - starts[group]).astype(np.uint64) * np.uint64(7)
    return np.add.reduceat((a & 0x7F).astype(np.uint64) << shift, starts)


class Dir:
    """One decoded directory: tile id of each entry's first tile, run length (0 = pointer to a leaf), length, and leaf offsets."""
    __slots__ = ("ids", "runs", "lens", "offs")

    def __init__(self, raw, compression):
        if compression == 2:
            raw = gzip.decompress(raw)
        v = varints(raw)
        n = int(v[0])
        self.ids = np.cumsum(v[1:1 + n].astype(np.int64))
        self.runs = v[1 + n:1 + 2 * n].astype(np.int64)
        self.lens = v[1 + 2 * n:1 + 3 * n].astype(np.int64)
        self.offs = None
        if (self.runs == 0).any():       # only the entries that point at leaves need their offset
            raw_off = v[1 + 3 * n:1 + 4 * n].astype(np.int64)
            offs = np.zeros(n, dtype=np.int64)
            for i in range(n):
                offs[i] = raw_off[i] - 1 if raw_off[i] > 0 else (offs[i - 1] + self.lens[i - 1] if i else 0)
            self.offs = offs

    def __len__(self):
        return len(self.ids)


def count_before(t, lo, hi, before):
    """How many ids below t the (sorted, disjoint) intervals hold; `before` is the count of ids in the intervals ahead of each."""
    j = np.searchsorted(lo, t, side="right") - 1
    jj = np.maximum(j, 0)
    inside = np.minimum(t - lo[jj], hi[jj] - lo[jj] + 1)
    return np.where(j >= 0, before[jj] + inside, 0)


class PMTiles:
    """Reads the directories of a remote PMTiles v3 archive with range requests (the tiles themselves are never fetched)."""

    def __init__(self, url, pool):
        self.url = url
        self.pool = pool
        self.lock = threading.Lock()
        self.cache = collections.OrderedDict()   # (offset, length) -> Dir
        self.cached_entries = 0
        self.inflight = {}
        self.requests = 0
        head = fetch(url, (0, 16383))
        if head[:7] != b"PMTiles" or head[7] != 3:
            raise RuntimeError(f"{url} is not a PMTiles v3 file")
        (self.root_off, self.root_len, _mo, _ml, self.leaf_off, _ll, _do, _dl, _a, _e, _c) = struct.unpack("<11Q", head[8:96])
        self.internal = head[96 + 1]
        if self.internal not in (1, 2):
            raise RuntimeError(f"{url}: directory compression {self.internal} is not supported")
        raw = head[self.root_off:self.root_off + self.root_len] if self.root_off + self.root_len <= len(head) else fetch(url, (self.root_off, self.root_off + self.root_len - 1))
        self.root = Dir(raw, self.internal)

    def leaf(self, off, ln):
        key = (off, ln)
        with self.lock:
            d = self.cache.get(key)
            if d is not None:
                self.cache.move_to_end(key)
                return d
            fut = self.inflight.get(key)
            mine = fut is None
            if mine:
                fut = self.inflight[key] = cf.Future()
        if not mine:
            return fut.result()
        try:
            a = self.leaf_off + off
            d = Dir(fetch(self.url, (a, a + ln - 1)), self.internal)
        except BaseException as e:
            with self.lock:
                del self.inflight[key]
            fut.set_exception(e)
            raise
        with self.lock:
            self.requests += 1
            self.cache[key] = d
            self.cached_entries += len(d)
            while self.cached_entries > CACHE_ENTRIES and len(self.cache) > 1:
                _, old = self.cache.popitem(last=False)
                self.cached_entries -= len(old)
            del self.inflight[key]
        fut.set_result(d)
        return d

    def entries(self, d, lo, hi):
        """Tile entries (ids, runs, lens) of directory d (and its leaves) that may overlap the id intervals lo..hi."""
        n = len(d)
        nxt = np.append(d.ids[1:], np.int64(1 << 62))
        end = np.where(d.runs > 0, d.ids + d.runs, nxt)       # a leaf pointer holds ids up to the next entry
        j = np.searchsorted(lo, end, side="left") - 1          # last interval that starts before the entry ends
        hit = (j >= 0) & (hi[np.maximum(j, 0)] >= d.ids)
        t = hit & (d.runs > 0)
        parts = [(d.ids[t], d.runs[t], d.lens[t])]
        pointers = np.flatnonzero(hit & (d.runs == 0))
        if len(pointers):
            futs = [self.pool.submit(self.leaf, int(d.offs[i]), int(d.lens[i])) for i in pointers]
            for f in futs:
                parts.append(self.entries(f.result(), lo, hi))
        return tuple(np.concatenate([p[k] for p in parts]) for k in range(3))

    def bytes_in(self, lo, hi):
        """Bytes of every tile whose id is in the intervals: each entry's length once per tile id it covers."""
        if len(lo) == 0:
            return 0
        ids, runs, lens = self.entries(self.root, lo, hi)
        if len(ids) == 0:
            return 0
        span = hi - lo + 1
        before = np.cumsum(span) - span
        n = count_before(ids + runs, lo, hi, before) - count_before(ids, lo, hi, before)
        return int((n * lens).sum())


# ---- map tile cover (download.rs tile_range, RegionRecord.bounds) ----

def lon_to_x(lon, z):
    return (lon + 180.0) / 360.0 * (1 << z)


def lat_to_y(lat, z):
    lat = math.radians(max(-85.0511287, min(85.0511287, lat)))
    return (1.0 - math.log(math.tan(lat) + 1.0 / math.cos(lat)) / math.pi) / 2.0 * (1 << z)


def tile_range(b, z):
    n = 1 << z
    x0 = max(math.floor(lon_to_x(b[0], z)), 0)
    x1 = max(min(max(math.ceil(lon_to_x(b[2], z)), 0) - 1, n - 1), x0)
    y0 = max(math.floor(lat_to_y(b[3], z)), 0)
    y1 = max(min(max(math.ceil(lat_to_y(b[1], z)), 0) - 1, n - 1), y0)
    return x0, x1, y0, y1


def region_bounds(bbox):
    """RegionRecord.bounds: a box across the antimeridian (west > east) is cut to its larger side. [west, south, east, north]."""
    w, s, e, n = bbox
    if w > e:
        if e + 180 >= 180 - w:
            w = -180.0
        else:
            e = 180.0
    return [w, s, e, n]


def valid_box(b):
    """AreaReq.bounds: a box the layer engine accepts."""
    return all(math.isfinite(v) for v in b) and b[0] >= -180 and b[2] <= 180 and b[1] >= -90 and b[3] <= 90 and b[0] < b[2] and b[1] < b[3]


def levels_bytes(arch, b, zlo, zhi):
    """Bytes of the cover of box b over zooms zlo..zhi."""
    los, his = [], []
    for z in range(zlo, zhi + 1):
        lo, hi = cover_intervals(z, *tile_range(b, z))
        los.append(lo)
        his.append(hi)
    lo, hi = merge(np.concatenate(los), np.concatenate(his))
    return arch.bytes_in(lo, hi)


def map_size(arch, layer, bounds):
    """[bytes, zoom] the app would pick for a layer, or None when it would not allow the download."""
    off = layer["offline"]
    src = off["source"]
    zmin, ztop = int(src["minzoom"]), int(src["maxzoom"])
    cap = MAX_DOWNLOAD_BYTES
    if off.get("max_bytes_per_download") is not None:
        cap = min(cap, int(off["max_bytes_per_download"]))
    max_tiles = off.get("max_tiles_per_download")
    floor = max(zmin, ztop - STEPS_DOWN)
    count, counts = 0, {}
    for z in range(zmin, ztop + 1):
        x0, x1, y0, y1 = tile_range(bounds, z)
        count += (x1 - x0 + 1) * (y1 - y0 + 1)
        counts[z] = count
    if counts[ztop] > MAX_ENUMERATED:
        return None   # the app cannot list that many tiles: it gives up on the layer instead of stepping down
    # The bytes of the cover grow with the zoom, so the first zoom (from the top) that fits is the highest one that does:
    # add the zooms up from the bottom and stop when one no longer fits.
    total = levels_bytes(arch, bounds, zmin, floor - 1) if floor > zmin else 0
    best = None
    for z in range(floor, ztop + 1):
        total += levels_bytes(arch, bounds, z, z)
        if total > cap or (max_tiles is not None and counts[z] > int(max_tiles)):
            break
        best = [total, z]
    return best


# ---- routing / places (geo.rs, index.rs) ----

LEVEL_SIZE = (4.0, 1.0, 0.25)


def tile_index_text(base, current):
    raw = fetch(f"{base}/{current['index']}")
    return gzip.decompress(raw).decode()


def load_tile_index(base, current):
    """{(level, id): gzip bytes} from a published index (`<path>\\t<bytes>\\t<sha256>\\t<gzip bytes>`)."""
    out = {}
    for line in tile_index_text(base, current).splitlines():
        f = line.rstrip("\r").split("\t")
        if len(f) < 4:
            continue
        p = f[0].split("/")
        out[(int(p[0]), int("".join(p[1:])))] = int(f[3])
    return out


def parse_polygons(g, out, depth=0):
    """geo.rs Shape::parse: Feature, FeatureCollection, Polygon, MultiPolygon -> shapely polygons."""
    if depth > 4:
        raise ValueError("geometry is nested too deeply")
    t = g.get("type")
    if t == "Feature":
        parse_polygons(g["geometry"], out, depth + 1)
    elif t == "FeatureCollection":
        for f in g["features"]:
            parse_polygons(f, out, depth + 1)
    elif t == "Polygon":
        add_polygon(g.get("coordinates"), out)
    elif t == "MultiPolygon":
        for c in g.get("coordinates") or []:
            add_polygon(c, out)
    else:
        raise ValueError(f"geometry must be a Polygon or MultiPolygon, not {t}")


def add_polygon(rings, out):
    clean = []
    for r in rings or []:
        pts = [(float(p[0]), float(p[1])) for p in r if len(p) >= 2 and math.isfinite(p[0]) and math.isfinite(p[1])]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts.pop()
        clean.append(pts)
    if not clean or len(clean[0]) < 3:
        return                                   # without its outer ring a polygon is nothing
    p = Polygon(clean[0], [r for r in clean[1:] if len(r) >= 3])
    out.append(p)


def col_row(level, lon, lat):
    s = LEVEL_SIZE[level]
    cols, rows = round(360.0 / s), round(180.0 / s)
    c = min(max(math.floor((min(max(lon, -180.0), 180.0) + 180.0) / s), 0), cols - 1)
    r = min(max(math.floor((min(max(lat, -90.0), 90.0) + 90.0) / s), 0), rows - 1)
    return c, r


def shape_tile_ids(polys, level):
    """geo.rs Shape::tiles: ids of the tiles of a level whose cell and the outline overlap (an outline that only runs along a
    cell's side does not take the cell beyond it)."""
    s = LEVEL_SIZE[level]
    cols = round(360.0 / s)
    out = set()
    for p in polys:
        w, so, e, n = p.bounds
        c0, r0 = col_row(level, w, so)
        c1, r1 = col_row(level, e, n)
        cc, rr = np.meshgrid(np.arange(c0, c1 + 1), np.arange(r0, r1 + 1))
        cc, rr = cc.ravel(), rr.ravel()
        west = -180.0 + cc * s
        south = -90.0 + rr * s
        boxes = shapely.box(west + EDGE_EPS, south + EDGE_EPS, west + s - EDGE_EPS, south + s - EDGE_EPS)
        shapely.prepare(p)
        try:
            hit = shapely.intersects(p, boxes)
        except shapely.errors.GEOSException:
            q = shapely.make_valid(p)
            shapely.prepare(q)
            hit = shapely.intersects(q, boxes)
        out.update((rr[hit] * cols + cc[hit]).tolist())
    return out


def tiles_bytes(index, polys):
    return sum(index.get((lv, i), 0) for lv in range(3) for i in shape_tile_ids(polys, lv))


# ---- off-road (offroad_catalogue.dart, regions_state.dart) ----

def packs_covering(packs, b):
    """The smallest pack whose box holds all of the region; when none does, every pack whose box meets it."""
    w, s, e, n = b
    whole = [p for p in packs if p["bbox"][0] <= w and p["bbox"][1] <= s and p["bbox"][2] >= e and p["bbox"][3] >= n]
    if whole:
        return [min(whole, key=lambda p: (p["bbox"][2] - p["bbox"][0]) * (p["bbox"][3] - p["bbox"][1]))]
    return [p for p in packs if p["bbox"][0] < e and p["bbox"][2] > w and p["bbox"][1] < n and p["bbox"][3] > s]


def terrain_box(r, p):
    return [max(r[0], p[0]), max(r[1], p[1]), min(r[2], p[2]), min(r[3], p[3])]


# ---- one record ----

class Sizer:
    def __init__(self, a, pool):
        self.a = a
        self.base_dir = a.dir
        index_path = os.path.join(a.dir, "regions", "index.json")
        self.polygon_base = DEFAULT_POLYGON_BASE
        if os.path.exists(index_path):
            self.polygon_base = json.load(open(index_path)).get("polygon_base") or DEFAULT_POLYGON_BASE
        self.routing_cur = fetch_json(f"{DATA}/routing/current.json")
        self.routing = load_tile_index(f"{DATA}/routing", self.routing_cur)
        self.places_cur = fetch_json(f"{DATA}/places/current.json", missing_ok=True)
        self.places = load_tile_index(f"{DATA}/places", self.places_cur) if self.places_cur else None
        log(f"routing build {self.routing_cur['build']}: {len(self.routing)} tiles;",
            f"places build {self.places_cur['build']}: {len(self.places)} tiles" if self.places_cur else "places not published")
        cat = fetch_json(LAYER_CATALOGUE)
        self.layers = []
        for l in cat["layers"]:
            off = l.get("offline") or {}
            if (l.get("source") or {}).get("type") != "pmtiles" or off.get("allowed") is False or l["id"] == "elevation":
                continue
            self.layers.append({"id": l["id"], "offline": {**off, "source": off.get("source") or l["source"]}})
        self.layers.append(ELEVATION)
        self.archives = {}
        for l in self.layers:
            url = l["offline"]["source"]["url"]
            if url not in self.archives:
                self.archives[url] = PMTiles(url, pool)
        self.elevation = self.archives[ELEVATION["offline"]["source"]["url"]]
        off = fetch_json(f"{DATA}/offroad/current.json")
        self.packs = [{"id": r["id"], "bbox": [float(v) for v in r["bbox"]], "bytes": int(r["files"]["offroad.sqlite"]["bytes"])}
                      for r in off.get("regions", []) if r.get("files", {}).get("offroad.sqlite") and r.get("bbox")]
        log("map layers:", ", ".join(l["id"] for l in self.layers), "| off-road packs:", len(self.packs))

    def polygons(self, rid):
        local = os.path.join(self.base_dir, "regions", "polygons", rid + ".geojson")
        if os.path.exists(local):
            g = json.load(open(local))
        elif self.polygon_base.startswith("http"):
            g = json.loads(fetch(self.polygon_base.rstrip("/") + "/" + rid + ".geojson"))
        else:
            raise RuntimeError("no polygon file")
        out = []
        parse_polygons(g, out)
        if not out:
            raise ValueError("geometry has no polygon with at least 3 points")
        return out

    def size(self, rec):
        """The "sizes" of one record and a list of what could not be worked out."""
        rid = rec["id"]
        s, notes = {}, []
        try:
            polys = self.polygons(rid)
            s["routing"] = tiles_bytes(self.routing, polys)
            if self.places is not None:
                s["places"] = tiles_bytes(self.places, polys)
        except Exception as e:
            notes.append(f"{rid}: polygon not read, no routing/places size ({e})")
        b = region_bounds(rec["bbox"])
        if valid_box(b):
            m = {}
            for l in self.layers:
                try:
                    r = map_size(self.archives[l["offline"]["source"]["url"]], l, b)
                except Exception as e:
                    notes.append(f"{rid}: map layer {l['id']} failed ({e})")
                    continue
                if r is not None:
                    m[l["id"]] = r
            if m:
                s["map"] = m
            packs = packs_covering(self.packs, b)
            if packs:
                terrain, ok = 0, True
                for p in packs:
                    tb = terrain_box(b, p["bbox"])
                    if not valid_box(tb):
                        ok = False
                        continue
                    try:
                        terrain += levels_bytes(self.elevation, tb, TERRAIN_ZOOM, TERRAIN_ZOOM)
                    except Exception as e:
                        ok = False
                        notes.append(f"{rid}: terrain failed ({e})")
                o = {"packs": {p["id"]: p["bytes"] for p in packs}}
                if ok:
                    o["terrain"] = terrain
                s["offroad"] = o
        else:
            notes.append(f"{rid}: bbox is not a box the layer engine accepts, no map/off-road size")
        if "routing" in s:
            s["routing_build"] = self.routing_cur["build"]
        s["built"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # key order of the schema
        order = ["routing", "places", "map", "offroad", "routing_build", "built"]
        return {k: s[k] for k in order if k in s}, notes


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def show(rec):
    s = rec.get("sizes", {})
    parts = [f"routing {human(s['routing'])}" if "routing" in s else "routing -"]
    if "places" in s:
        parts.append(f"places {human(s['places'])}")
    parts.append("map " + (", ".join(f"{k} {human(v[0])}@z{v[1]}" for k, v in s.get("map", {}).items()) or "-"))
    o = s.get("offroad")
    parts.append("offroad " + (", ".join(f"{k} {human(v)}" for k, v in o["packs"].items()) + (f" + terrain {human(o['terrain'])}" if "terrain" in o else " + terrain -") if o else "-"))
    log(f"  {rec['id']} ({rec['name']}): " + "; ".join(parts))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", help="directory that holds regions/<shard>.json")
    ap.add_argument("--shards", default="us,ca,mx", help="comma separated shard names to size")
    ap.add_argument("--ids", default="", help="test filter: comma separated record ids to size (others stay as they are)")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    t0 = time.time()
    only = {i for i in a.ids.split(",") if i}
    with cf.ThreadPoolExecutor(16) as io_pool:      # directory reads (leaf directories are fetched in parallel)
        sizer = Sizer(a, io_pool)
        counts = collections.Counter()
        notes, samples = [], {}
        loaded = {}
        for sh in [s for s in a.shards.split(",") if s]:
            p = os.path.join(a.dir, "regions", sh + ".json")
            if not os.path.exists(p):
                log(f"shard {sh}: no file in {a.dir}/regions, skipped")
                continue
            shard = loaded[p] = json.load(open(p))
            todo = [r for r in shard["regions"] if r.get("bbox") and (not only or r["id"] in only)]
            log(f"shard {sh}: {len(todo)} of {len(shard['regions'])} records to size")
            done = 0

            def work(r):
                return r, *sizer.size(r)

            with cf.ThreadPoolExecutor(a.workers) as ex:
                for r, s, n in ex.map(work, todo):
                    r["sizes"] = s
                    notes += n
                    counts["records"] += 1
                    counts[sh] += 1
                    for k in ("routing", "places", "map", "offroad"):
                        if k in s:
                            counts[k] += 1
                    for layer in s.get("map", {}):
                        counts["map:" + layer] += 1
                    done += 1
                    if done % 500 == 0:
                        log(f"  {sh}: {done}/{len(todo)} done, {round(time.time() - t0)} s")
                    samples[r["id"]] = r
                    if r.get("kind") in ("sub_state", "valley", "multi_state", "metro_alias"):
                        samples.setdefault("name:" + r["name"], r)
        # A polygon that cannot be read costs the record its routing size. A few are tolerated (logged below); many mean the
        # polygon host was in trouble, and shards written now would be worse than the ones they replace.
        no_polygon = sum(1 for n in notes if "polygon not read" in n)
        if no_polygon > max(5, counts["records"] // 100):
            log(f"{no_polygon} of {counts['records']} polygons could not be read; nothing written")
            for n in notes[:20]:
                log("  " + n)
            sys.exit(1)
        for p, shard in loaded.items():
            json.dump(shard, open(p, "w"), separators=(",", ":"), ensure_ascii=False)
    log(f"sized {counts['records']} records in {round(time.time() - t0)} s:", {k: v for k, v in sorted(counts.items())})
    if notes:
        log(f"{len(notes)} parts skipped:")
        for n in notes[:50]:
            log("  " + n)
    log("sample:")
    for rid in SAMPLE_IDS:
        if rid in samples:
            show(samples[rid])
    for nm in SAMPLE_NAMES:
        for k, r in samples.items():
            if k.startswith("name:") and nm.lower() in r["name"].lower():
                show(r)
    log("directory reads:", sum(x.requests for x in sizer.archives.values()))


if __name__ == "__main__":
    main()
