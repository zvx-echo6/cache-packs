#!/usr/bin/env python3
"""Cut one region's tiles out of a PMTiles v3 archive (the Protomaps planet) into a PMTiles file of its own.

The tiles are the region outline's cover, z0 to z15 by default, worked out by the same rule as the Cache app
(packages/cache_search/native/cache_layers/src/cover.rs, branch feat/faster-region-downloads): a tile is in when its
rectangle touches the outline, the rings are read even-odd (parts make their union, holes are left out), and only tiles
inside the outline's box (download.rs `tile_range`) are kept. Tile ids, directories and the header follow the app's
pmtiles.rs. Tile bytes are copied from the source byte for byte; nothing is decoded or re-encoded.

The output is spec layout: header, root directory (inside the first 16 KiB), metadata, leaf directories, tile data in
tile-id order (clustered). The metadata blob is the source's, unchanged (attribution included); the header carries the
region's bounds, zooms and centre.

  pmtiles_cut.py cut --outline region.geojson --source URL_OR_PATH --out region.pmtiles [--maxzoom 15]
  pmtiles_cut.py verify --file region.pmtiles --source URL_OR_PATH --outline region.geojson [--samples 64]

Needs numpy. Nothing here installs anything or writes anywhere but the output path.
"""
import argparse, bisect, concurrent.futures as cf, gzip, hashlib, json, math, os, random, shutil, struct, sys, threading, time
import urllib.request

import numpy as np

HEADER_LEN = 127
FIRST = 16384                      # the app reads the first 16 KiB and expects the root inside it
COMP_NONE, COMP_GZIP = 1, 2
UA = {"User-Agent": "cache-packs-basemap-regions"}
BASE = [((1 << (2 * z)) - 1) // 3 for z in range(32)]   # first tile id of each zoom
GAP = 256 << 10                    # merge source reads closer than this
GROUP = 16 << 20                   # and at most this long
WORKERS = 8


class CutError(Exception):
    pass


# ---------------------------------------------------------------- tile ids (pmtiles.rs zxy_to_id / id_to_zxy)

def zxy_to_id(z, x, y):
    n = 1 << z
    tx, ty, d, s = x, y, 0, n // 2
    while s > 0:
        rx = 1 if tx & s else 0
        ry = 1 if ty & s else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                tx, ty = n - 1 - tx, n - 1 - ty
            tx, ty = ty, tx
        s //= 2
    return BASE[z] + d


def zoom_of_id(tid):
    return bisect.bisect_right(BASE, tid) - 1


def id_to_zxy(tid):
    z = zoom_of_id(tid)
    t, n = tid - BASE[z], 1 << z
    x = y = 0
    s = 1
    while s < n:
        rx = 1 & (t // 2)
        ry = 1 & (t ^ rx)
        if ry == 0:
            if rx == 1:
                x, y = s - 1 - x, s - 1 - y
            x, y = y, x
        x += s * rx
        y += s * ry
        t //= 4
        s *= 2
    return z, x, y


# ---------------------------------------------------------------- tile geometry (core.rs tile_bbox, download.rs tile_range)

def tile_bbox(z, x, y):
    n = float(1 << z)
    lon = lambda v: v / n * 360.0 - 180.0
    lat = lambda v: math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * v / n))))
    return (lon(float(x)), lat(float(y) + 1.0), lon(float(x) + 1.0), lat(float(y)))


def lon_to_x(lon, z):
    return (lon + 180.0) / 360.0 * float(1 << z)


def lat_to_y(lat, z):
    lat = math.radians(min(max(lat, -85.0511287), 85.0511287))
    return (1.0 - math.log(math.tan(lat) + 1.0 / math.cos(lat)) / math.pi) / 2.0 * float(1 << z)


def tile_range(b, z):
    """(x0, x1, y0, y1) at zoom z that box b [w, s, e, n] touches (download.rs, saturating casts included)."""
    n = 1 << z
    sat = lambda v: max(0, int(v)) if v == v else 0      # Rust `as u64` saturates at 0, NaN -> 0
    x0 = sat(math.floor(lon_to_x(b[0], z)))
    x1 = max(min(max(sat(math.ceil(lon_to_x(b[2], z))) - 1, 0), n - 1), x0)
    y0 = sat(math.floor(lat_to_y(b[3], z)))
    y1 = max(min(max(sat(math.ceil(lat_to_y(b[1], z))) - 1, 0), n - 1), y0)
    return x0, x1, y0, y1


# ---------------------------------------------------------------- outlines (region_catalogue.dart mapOutline)

def outline_rings(geojson, bbox=None):
    """Every ring of every part as [(lon, lat), ...], the way the app reads a region's geometry. `bbox` is the record's box;
    when it crosses the antimeridian (west > east) only the parts on the side the app cuts the box to are kept."""
    g = geojson
    if g.get("type") == "Feature":
        g = g["geometry"]
    t, coords = g.get("type"), g.get("coordinates")
    if not isinstance(coords, list):
        raise CutError("the outline has no coordinates")
    parts = [coords] if t == "Polygon" else coords if t == "MultiPolygon" else None
    if parts is None:
        raise CutError(f"the outline must be a Polygon or MultiPolygon, not {t}")
    west = east = None
    if bbox is not None and bbox[0] > bbox[2]:
        west, east = bbox[0], bbox[2]
        if east + 180 >= 180 - west:
            west = -180.0
        else:
            east = 180.0
    rings = []
    for part in parts:
        own = []
        for ring in part:
            pts = [(float(p[0]), float(p[1])) for p in ring]
            if len(pts) >= 3:
                own.append(pts)
        if not own:
            continue
        if west is not None and not all(west <= p[0] <= east for p in own[0]):
            continue
        rings.extend(own)
    if not rings:
        raise CutError("the outline has no usable ring")
    return rings


def rings_bounds(rings):
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return (min(xs), min(ys), max(xs), max(ys))


# ---------------------------------------------------------------- cover (cover.rs)

def _seg_hits(a, b, c, d):
    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    d1, d2, d3, d4 = cross(c, d, a), cross(c, d, b), cross(a, b, c), cross(a, b, d)
    return ((d1 > 0.0) != (d2 > 0.0)) and ((d3 > 0.0) != (d4 > 0.0))


IN, OUT = "in", "out"


class Outline:
    """cover.rs Outline. Plain Python for short edge lists and numpy for long ones; both are the same IEEE arithmetic in the
    same order as the Rust, so the answers are the same (tests check the two paths against each other)."""

    VEC = 48            # edge lists longer than this are tested with numpy

    def __init__(self, rings):
        self.edges = []
        for r in rings:
            for i in range(len(r)):
                self.edges.append((r[i], r[(i + 1) % len(r)]))
        e = np.array([(a[0], a[1], b[0], b[1]) for a, b in self.edges], dtype=np.float64).reshape(-1, 4)
        self.ax, self.ay, self.bx, self.by = e[:, 0].copy(), e[:, 1].copy(), e[:, 2].copy(), e[:, 3].copy()
        self.minx, self.maxx = np.minimum(self.ax, self.bx), np.maximum(self.ax, self.bx)
        self.miny, self.maxy = np.minimum(self.ay, self.by), np.maximum(self.ay, self.by)
        self.box = list(zip(self.minx.tolist(), self.maxx.tolist(), self.miny.tolist(), self.maxy.tolist()))
        self._band_index()

    # contains(): even-odd over every ring. Only edges whose y-span holds p.y can toggle, so they are found through bands of
    # y; the band lookup returns a superset (floor of a monotone map), and the exact test is the Rust one.
    def _band_index(self):
        n = len(self.edges)
        self.y0 = float(self.miny.min())
        span = float(self.maxy.max()) - self.y0
        self.nb = max(1, min(4096, n))
        self.bh = span / self.nb if span > 0 else 1.0
        lo = np.clip(np.floor((self.miny - self.y0) / self.bh), 0, self.nb - 1).astype(np.int64)
        hi = np.clip(np.floor((self.maxy - self.y0) / self.bh), 0, self.nb - 1).astype(np.int64)
        cnt = hi - lo + 1
        idx = np.repeat(np.arange(n, dtype=np.int64), cnt)
        band = np.repeat(lo, cnt) + (np.arange(cnt.sum()) - np.repeat(np.cumsum(cnt) - cnt, cnt))
        order = np.argsort(band, kind="stable")
        self.band_edges = idx[order]
        self.band_start = np.searchsorted(band[order], np.arange(self.nb + 1))

    def contains(self, p):
        px, py = p
        if not (self.y0 <= py):
            return False
        k = int(min(max(math.floor((py - self.y0) / self.bh), 0), self.nb - 1))
        cand = self.band_edges[self.band_start[k]:self.band_start[k + 1]]
        # Rust: for &(b, a) in &self.edges  -> b is the edge's start, a its end
        bx, by, ax, ay = self.ax[cand], self.ay[cand], self.bx[cand], self.by[cand]
        m = (ay > py) != (by > py)
        if not m.any():
            return False
        bx, by, ax, ay = bx[m], by[m], ax[m], ay[m]
        with np.errstate(divide="ignore", invalid="ignore"):
            t = px < (bx - ax) * (py - ay) / (by - ay) + ax
        return bool(int(t.sum()) & 1)

    def classify(self, r, near):
        """IN, OUT, or the list of edge indices near rectangle r (it touches the outline)."""
        if len(near) > self.VEC:
            nr = np.asarray(near, dtype=np.int64)
            mk = (self.maxx[nr] >= r[0]) & (self.minx[nr] <= r[2]) & (self.maxy[nr] >= r[1]) & (self.miny[nr] <= r[3])
            mine = nr[mk].tolist()
        else:
            bx = self.box
            mine = [i for i in near if bx[i][1] >= r[0] and bx[i][0] <= r[2] and bx[i][3] >= r[1] and bx[i][2] <= r[3]]
        corners = ((r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3]))
        if not mine:
            return IN if any(self.contains(c) for c in corners) else OUT
        if len(mine) > self.VEC:
            hits = self._hits_np(np.asarray(mine, dtype=np.int64), r, corners)
        else:
            hits = False
            for i in mine:
                a, b = self.edges[i]
                if (r[0] <= a[0] <= r[2] and r[1] <= a[1] <= r[3]) or any(_seg_hits(a, b, corners[k], corners[(k + 1) % 4]) for k in range(4)):
                    hits = True
                    break
        if hits or any(self.contains(c) for c in corners):
            return mine
        return OUT

    def _hits_np(self, ix, r, corners):
        ax, ay, bx, by = self.ax[ix], self.ay[ix], self.bx[ix], self.by[ix]
        if ((ax >= r[0]) & (ax <= r[2]) & (ay >= r[1]) & (ay <= r[3])).any():
            return True
        for k in range(4):
            c, d = corners[k], corners[(k + 1) % 4]
            d1 = (d[0] - c[0]) * (ay - c[1]) - (d[1] - c[1]) * (ax - c[0])
            d2 = (d[0] - c[0]) * (by - c[1]) - (d[1] - c[1]) * (bx - c[0])
            d3 = (bx - ax) * (c[1] - ay) - (by - ay) * (c[0] - ax)
            d4 = (bx - ax) * (d[1] - ay) - (by - ay) * (d[0] - ax)
            if (((d1 > 0.0) != (d2 > 0.0)) & ((d3 > 0.0) != (d4 > 0.0))).any():
                return True
        return False

    def cover(self, zmin, zmax, box):
        """The tiles from zmin to zmax that touch the outline and lie in tile_range(box, z), as sorted, merged half-open
        tile-id ranges [(start, end), ...]. A tile wholly inside is not split further: its descendants at a zoom are one
        contiguous id range (Hilbert order), so a state at z15 is a few thousand ranges, not millions of ids."""
        ranges = [tile_range(box, z) for z in range(zmax + 1)]
        keep = lambda z, x, y: ranges[z][0] <= x <= ranges[z][1] and ranges[z][2] <= y <= ranges[z][3]
        out = []
        c = self.classify(tile_bbox(0, 0, 0), list(range(len(self.edges))))
        level = [] if c is OUT else [(0, 0, c)]
        for z in range(zmax + 1):
            nxt = []
            for x, y, cl in level:
                if cl is IN:
                    self._block(out, z, x, y, zmin, zmax, ranges)
                    continue
                if z >= zmin and keep(z, x, y):
                    t = zxy_to_id(z, x, y)
                    out.append((t, t + 1))
                if z == zmax:
                    continue
                for cx, cy in ((2 * x, 2 * y), (2 * x + 1, 2 * y), (2 * x, 2 * y + 1), (2 * x + 1, 2 * y + 1)):
                    cc = self.classify(tile_bbox(z + 1, cx, cy), cl)
                    if cc is not OUT:
                        nxt.append((cx, cy, cc))
            level = nxt
        return merge_ranges(out)

    @staticmethod
    def _block(out, z0, x, y, zmin, zmax, ranges):
        h = zxy_to_id(z0, x, y) - BASE[z0]
        for z in range(max(z0, zmin), zmax + 1):
            d = z - z0
            x0, x1, y0, y1 = ranges[z]
            bx0, bx1, by0, by1 = x << d, ((x + 1) << d) - 1, y << d, ((y + 1) << d) - 1
            if x0 <= bx0 and bx1 <= x1 and y0 <= by0 and by1 <= y1:
                out.append((BASE[z] + (h << (2 * d)), BASE[z] + ((h + 1) << (2 * d))))
            else:   # a corner case the box check trims: list the block's tiles one by one
                for tx in range(max(bx0, x0), min(bx1, x1) + 1):
                    for ty in range(max(by0, y0), min(by1, y1) + 1):
                        t = zxy_to_id(z, tx, ty)
                        out.append((t, t + 1))


def merge_ranges(rs):
    rs.sort()
    out = []
    for a, b in rs:
        if out and a <= out[-1][1]:
            if b > out[-1][1]:
                out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def cover_tiles(rings, zmin, zmax):
    """The app's cover of these rings as a set of (z, x, y) (small areas only; for tests and checks)."""
    o = Outline(rings)
    s = set()
    for a, b in o.cover(zmin, zmax, rings_bounds(rings)):
        for t in range(a, b):
            s.add(id_to_zxy(t))
    return s


# ---------------------------------------------------------------- byte sources

class HttpSource:
    def __init__(self, url, tries=8):
        self.url, self.tries = url, tries
        self.fetched = 0
        self.requests = 0
        self._lock = threading.Lock()

    def read(self, off, length, exact=True):
        if length == 0:
            return b""
        err = None
        for t in range(self.tries):
            try:
                req = urllib.request.Request(self.url, headers={**UA, "Range": f"bytes={off}-{off + length - 1}"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    if r.status != 206:
                        raise IOError(f"HTTP {r.status} for a range request")
                    b = r.read()
                if len(b) != length and (exact or not b or len(b) > length):
                    raise IOError(f"short read {len(b)} of {length}")
                with self._lock:
                    self.fetched += length
                    self.requests += 1
                return b
            except Exception as e:      # retried; the last error is raised
                err = e
                time.sleep(min(30, 2 * (t + 1)))
        raise CutError(f"reading {length} bytes at {off} failed: {err}")


class FileSource:
    def __init__(self, path):
        self.path = path
        self.fetched = 0
        self.requests = 0

    def read(self, off, length, exact=True):
        with open(self.path, "rb") as f:
            f.seek(off)
            b = f.read(length)
        if len(b) != length and (exact or not b):
            raise CutError(f"short read {len(b)} of {length} at {off} in {self.path}")
        self.fetched += length
        self.requests += 1
        return b


def open_source(s):
    return HttpSource(s) if s.startswith(("http://", "https://")) else FileSource(s)


# ---------------------------------------------------------------- header and directories (pmtiles.rs)

HKEYS = ("root_offset", "root_len", "meta_offset", "meta_len", "leaf_offset", "leaf_len", "data_offset", "data_len",
         "addressed_tiles", "tile_entries", "tile_contents")


def parse_header(b):
    if len(b) < HEADER_LEN or b[:7] != b"PMTiles":
        raise CutError("not a PMTiles file")
    if b[7] != 3:
        raise CutError(f"PMTiles version {b[7]} (only 3)")
    h = dict(zip(HKEYS, struct.unpack("<11Q", b[8:96])))
    h.update(clustered=b[96] == 1, internal_compression=b[97], tile_compression=b[98], tile_type=b[99],
             min_zoom=b[100], max_zoom=b[101], bounds=[v / 1e7 for v in struct.unpack("<4i", b[102:118])],
             center_zoom=b[118], center=[v / 1e7 for v in struct.unpack("<2i", b[119:127])])
    return h


def serialize_header(h):
    b = bytearray(HEADER_LEN)
    b[0:7] = b"PMTiles"
    b[7] = 3
    b[8:96] = struct.pack("<11Q", *[int(h[k]) for k in HKEYS])
    b[96] = 1 if h["clustered"] else 0
    b[97], b[98], b[99], b[100], b[101] = h["internal_compression"], h["tile_compression"], h["tile_type"], h["min_zoom"], h["max_zoom"]
    b[102:118] = struct.pack("<4i", *[int(round(v * 1e7)) for v in h["bounds"]])
    b[118] = h["center_zoom"]
    b[119:127] = struct.pack("<2i", *[int(round(v * 1e7)) for v in h["center"]])
    return bytes(b)


def decompress(raw, comp):
    if comp in (0, COMP_NONE):
        return raw
    if comp == COMP_GZIP:
        return gzip.decompress(raw)
    raise CutError(f"PMTiles compression {comp} (only none and gzip)")


def compress(raw, comp):
    if comp in (0, COMP_NONE):
        return raw
    if comp == COMP_GZIP:
        return gzip.compress(raw, compresslevel=6, mtime=0)
    raise CutError(f"PMTiles compression {comp} (only none and gzip)")


def _varints(buf):
    b = np.frombuffer(buf, dtype=np.uint8)
    if len(b) == 0 or b[-1] & 0x80:
        raise CutError("PMTiles directory is cut short")
    ends = np.flatnonzero((b & 0x80) == 0)
    starts = np.r_[0, ends[:-1] + 1]
    width = ends - starts + 1
    if width.max() > 10:
        raise CutError("bad varint in PMTiles directory")
    pos = np.arange(len(b)) - np.repeat(starts, width)
    vals = (b & 0x7F).astype(np.uint64) << (7 * pos).astype(np.uint64)
    return np.add.reduceat(vals, starts).astype(np.int64)


def parse_dir(raw):
    """(ids, run, length, offset) arrays; run 0 marks a leaf pointer."""
    v = _varints(raw)
    n = int(v[0])
    if len(v) < 1 + 4 * n:
        raise CutError("PMTiles directory is cut short")
    ids = np.cumsum(v[1:1 + n])
    run = v[1 + n:1 + 2 * n]
    ln = v[1 + 2 * n:1 + 3 * n]
    o = v[1 + 3 * n:1 + 4 * n]
    # offset 0 (after the first entry) means "right after the previous entry"
    idx = np.arange(n)
    k = np.maximum.accumulate(np.where((o > 0) | (idx == 0), idx, 0))
    cs = np.r_[0, np.cumsum(ln)[:-1]]
    off = np.maximum(o[k] - 1, 0) + cs - cs[k]
    return ids, run, ln, off


def _put_varints(v):
    v = np.asarray(v, dtype=np.uint64)
    if len(v) == 0:
        return b""
    nb = np.ones(len(v), dtype=np.int64)
    t = v >> np.uint64(7)
    while t.any():
        nb += (t > 0)
        t >>= np.uint64(7)
    pos = np.cumsum(nb) - nb
    out = np.zeros(int(nb.sum()), dtype=np.uint8)
    for k in range(int(nb.max())):
        m = nb > k
        byte = ((v[m] >> np.uint64(7 * k)) & np.uint64(0x7F)).astype(np.uint8)
        byte |= np.where(nb[m] > k + 1, 0x80, 0).astype(np.uint8)
        out[pos[m] + k] = byte
    return out.tobytes()


def serialize_dir(ids, run, ln, off):
    ids, run, ln, off = (np.asarray(a, dtype=np.int64) for a in (ids, run, ln, off))
    n = len(ids)
    delta = np.diff(np.r_[0, ids])
    follows = np.r_[False, off[1:] == off[:-1] + ln[:-1]]
    ov = np.where(follows, 0, off + 1)
    return _put_varints(np.r_[n]) + _put_varints(delta) + _put_varints(run) + _put_varints(ln) + _put_varints(ov)


class Archive:
    """A PMTiles source read by ranges: header, root, leaf pages (kept, compressed, for the life of the object)."""

    def __init__(self, src):
        self.src = src
        first = src.read(0, FIRST, exact=False)     # a file smaller than 16 KiB is fine
        self.header = h = parse_header(first)
        if h["root_offset"] + h["root_len"] <= len(first):
            raw = first[h["root_offset"]:h["root_offset"] + h["root_len"]]
        else:
            raw = src.read(h["root_offset"], h["root_len"])
        self.root = parse_dir(decompress(raw, h["internal_compression"]))
        self._pages = {}
        self._lock = threading.Lock()
        self._meta = None

    def metadata_raw(self):
        if self._meta is None:
            h = self.header
            self._meta = self.src.read(h["meta_offset"], h["meta_len"]) if h["meta_len"] else b""
        return self._meta

    def metadata(self):
        raw = self.metadata_raw()
        return json.loads(decompress(raw, self.header["internal_compression"])) if raw else {}

    def page(self, off, ln):
        key = (off, ln)
        with self._lock:
            raw = self._pages.get(key)
        if raw is None:
            raw = self.src.read(self.header["leaf_offset"] + off, ln)
            with self._lock:
                self._pages[key] = raw
        return parse_dir(decompress(raw, self.header["internal_compression"]))

    def prefetch(self, keys):
        todo = [k for k in set(keys) if k not in self._pages]
        if len(todo) > 1:
            with cf.ThreadPoolExecutor(WORKERS) as ex:
                list(ex.map(lambda k: self.page(*k), todo))

    def lookup(self, tid):
        """(offset in tile data, length) of one tile, or None: pmtiles.rs find/lookup, one id at a time."""
        ids, run, ln, off = self.root
        for _ in range(4):
            i = int(np.searchsorted(ids, tid, side="right"))
            if i == 0:
                return None
            i -= 1
            if run[i] == 0:
                ids, run, ln, off = self.page(int(off[i]), int(ln[i]))
                continue
            return (int(off[i]), int(ln[i])) if tid < ids[i] + run[i] else None
        raise CutError("PMTiles directories nest too deep")

    def collect(self, ranges):
        """Every stored tile inside the half-open id ranges, as rows (first id, count, data offset, length), sorted.
        Runs are kept as runs; a run cut by a range edge is cut too."""
        if not ranges:
            return np.zeros((0, 4), dtype=np.int64)
        A = np.array([a for a, _ in ranges], dtype=np.int64)
        B = np.array([b for _, b in ranges], dtype=np.int64)
        rows = self._collect(self.root, A, B, 1 << 62, 0)
        if not rows:
            return np.zeros((0, 4), dtype=np.int64)
        r = np.concatenate(rows)
        return r[np.argsort(r[:, 0], kind="stable")]

    def _collect(self, d, A, B, upper, depth):
        if depth > 3:
            raise CutError("PMTiles directories nest too deep")
        ids, run, ln, off = d
        n = len(ids)
        if n == 0 or len(A) == 0:
            return []
        i0 = np.maximum(np.searchsorted(ids, A, side="right") - 1, 0)
        i1 = np.searchsorted(ids, B, side="left")
        cnt = np.maximum(i1 - i0, 0)
        ri = np.repeat(np.arange(len(A)), cnt)
        ei = np.repeat(i0, cnt) + (np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt))
        a, b = A[ri], B[ri]
        out = []
        tile = run[ei] > 0
        if tile.any():
            e = ei[tile]
            s = np.maximum(ids[e], a[tile])
            t = np.minimum(ids[e] + run[e], b[tile])
            k = t > s
            out.append(np.stack([s[k], (t - s)[k], off[e][k], ln[e][k]], axis=1))
        leaf = ~tile
        if leaf.any():
            e = ei[leaf]
            lo = ids[e]
            hi = np.where(e + 1 < n, ids[np.minimum(e + 1, n - 1)], upper)
            s = np.maximum(lo, a[leaf])
            t = np.minimum(hi, b[leaf])
            k = t > s
            e, s, t, hi = e[k], s[k], t[k], hi[k]
            self.prefetch([(int(off[j]), int(ln[j])) for j in np.unique(e)])
            order = np.argsort(e, kind="stable")
            e, s, t, hi = e[order], s[order], t[order], hi[order]
            bounds = np.flatnonzero(np.r_[True, e[1:] != e[:-1], True])
            for g in range(len(bounds) - 1):
                p, q = bounds[g], bounds[g + 1]
                j = int(e[p])
                sub = self.page(int(off[j]), int(ln[j]))
                out += self._collect(sub, s[p:q], t[p:q], int(hi[p]), depth + 1)
        return out


# ---------------------------------------------------------------- writer

def plan_layout(rows):
    """From collected rows: the output entries (ids, run, length, offset in the new data) and the copy list (source offset,
    length, destination offset) in destination order. Contents are deduplicated by source offset and placed in the order
    they first appear by tile id (clustered)."""
    tid, cnt, soff, ln = rows[:, 0], rows[:, 1], rows[:, 2], rows[:, 3]
    if len(rows) and ((tid[1:] < tid[:-1] + cnt[:-1]).any()):
        raise CutError("collected tiles overlap")
    uo, first, inv = np.unique(soff, return_index=True, return_inverse=True)
    if (ln != ln[first][inv]).any():
        raise CutError("one source offset with two lengths")
    order = np.argsort(first, kind="stable")
    rank = np.empty_like(order)
    rank[order] = np.arange(len(order))
    clen = ln[first][order]
    cdst = np.cumsum(clen) - clen
    dst = cdst[rank[inv]]
    brk = np.r_[True, (tid[1:] != tid[:-1] + cnt[:-1]) | (dst[1:] != dst[:-1]) | (ln[1:] != ln[:-1])]
    starts = np.flatnonzero(brk)
    run = np.add.reduceat(cnt, starts) if len(starts) else np.zeros(0, dtype=np.int64)
    if len(run) and run.max() > 0xFFFFFFFF:
        raise CutError("a run longer than PMTiles allows")
    entries = (tid[starts], run, ln[starts], dst[starts])
    copies = (uo[order], clen, cdst)
    return entries, copies


def build_dirs(ids, run, ln, off, comp, root_max=FIRST - HEADER_LEN, leaf_size=4096):
    root = compress(serialize_dir(ids, run, ln, off), comp)
    if len(root) <= root_max:
        return root, b""
    size = leaf_size
    while True:
        leaves, p = [], []
        pos = 0
        for i in range(0, len(ids), size):
            c = compress(serialize_dir(ids[i:i + size], run[i:i + size], ln[i:i + size], off[i:i + size]), comp)
            p.append((int(ids[i]), pos, len(c)))
            leaves.append(c)
            pos += len(c)
        r = compress(serialize_dir([a for a, _, _ in p], [0] * len(p), [c for _, _, c in p], [b for _, b, _ in p]), comp)
        if len(r) <= root_max:
            return r, b"".join(leaves)
        size *= 2


def center_of(bounds, maxz):
    w, s, e, n = bounds
    width = max(e - w, (n - s), 1e-9)
    return [(w + e) / 2, (s + n) / 2], int(min(maxz, max(0, math.floor(math.log2(360.0 / width)))))


def write_pmtiles(arc, rows, path, minz, maxz, bounds, root_max=FIRST - HEADER_LEN, free_margin=1 << 30, leaf_size=4096):
    """Writes the cut. Returns facts about it. Fails before writing when the disk cannot hold it."""
    entries, copies = plan_layout(rows)
    ids, run, ln, off = entries
    sh = arc.header
    root, leaves = build_dirs(ids, run, ln, off, sh["internal_compression"], root_max, leaf_size)
    meta = arc.metadata_raw()
    root_off = HEADER_LEN
    meta_off = root_off + len(root)
    leaf_off = meta_off + len(meta)
    data_off = max(FIRST, leaf_off + len(leaves))
    so, clen, cdst = copies
    data_len = int(clen.sum())
    total = data_off + data_len
    d = os.path.dirname(os.path.abspath(path))
    free = shutil.disk_usage(d).free
    if total + free_margin > free:
        raise CutError(f"{total} bytes to write and {free} free on {d}")
    center, cz = center_of(bounds, maxz)
    h = {"root_offset": root_off, "root_len": len(root), "meta_offset": meta_off, "meta_len": len(meta),
         "leaf_offset": leaf_off, "leaf_len": len(leaves), "data_offset": data_off, "data_len": data_len,
         "addressed_tiles": int(run.sum()), "tile_entries": len(ids), "tile_contents": len(so), "clustered": True,
         "internal_compression": sh["internal_compression"], "tile_compression": sh["tile_compression"],
         "tile_type": sh["tile_type"], "min_zoom": minz, "max_zoom": maxz,
         "bounds": [max(-180.0, bounds[0]), max(-85.0511287, bounds[1]), min(180.0, bounds[2]), min(85.0511287, bounds[3])],
         "center_zoom": cz, "center": center}
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(serialize_header(h))
        f.write(root)
        f.write(meta)
        f.write(leaves)
        f.truncate(total)
    # copy the tile bytes: merge nearby source ranges, read in parallel, write each content at its place
    srt = np.argsort(so, kind="stable")
    s_off, s_len, s_dst = so[srt], clen[srt], cdst[srt] + data_off
    groups, i, N = [], 0, len(s_off)
    while i < N:
        j, end = i, int(s_off[i] + s_len[i])
        while j + 1 < N and s_off[j + 1] - end <= GAP and s_off[j + 1] + s_len[j + 1] - s_off[i] <= GROUP:
            j += 1
            end = max(end, int(s_off[j] + s_len[j]))
        groups.append((i, j + 1, int(s_off[i]), end - int(s_off[i])))
        i = j + 1
    fd = os.open(tmp, os.O_WRONLY)
    try:
        def job(g):
            a, b, start, length = g
            buf = arc.src.read(sh["data_offset"] + start, length)
            for k in range(a, b):
                p = int(s_off[k]) - start
                piece = buf[p:p + int(s_len[k])]
                if len(piece) != int(s_len[k]):
                    raise CutError("short tile read")
                os.pwrite(fd, piece, int(s_dst[k]))
            return length
        with cf.ThreadPoolExecutor(WORKERS) as ex:
            fetched = sum(ex.map(job, groups))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    return {"bytes": total, "tiles": h["addressed_tiles"], "entries": h["tile_entries"], "contents": h["tile_contents"],
            "data_bytes": data_len, "read_bytes": fetched, "reads": len(groups)}


def sha256_file(path):
    hs = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(4 << 20), b""):
            hs.update(blk)
    return hs.hexdigest()


# ---------------------------------------------------------------- verify

def read_entries(path):
    """Header and every tile entry of a PMTiles file on disk, read on its own (not through the writer's arrays)."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        first = f.read(FIRST)
        h = parse_header(first)
        if h["root_offset"] + h["root_len"] > min(size, FIRST):
            raise CutError("root directory is not inside the first 16 KiB")
        for o, l, what in ((h["meta_offset"], h["meta_len"], "metadata"), (h["leaf_offset"], h["leaf_len"], "leaf directories"),
                           (h["data_offset"], h["data_len"], "tile data")):
            if o + l > size:
                raise CutError(f"{what} run past the end of the file")
        if h["data_offset"] + h["data_len"] != size:
            raise CutError("tile data does not end at the end of the file")
        comp = h["internal_compression"]
        rows = []

        def walk(d, depth):
            if depth > 3:
                raise CutError("directories nest too deep")
            ids, run, ln, off = d
            for i in range(len(ids)):
                if run[i] == 0:
                    f.seek(h["leaf_offset"] + int(off[i]))
                    walk(parse_dir(decompress(f.read(int(ln[i])), comp)), depth + 1)
                else:
                    rows.append((int(ids[i]), int(run[i]), int(ln[i]), int(off[i])))
        walk(parse_dir(decompress(first[h["root_offset"]:h["root_offset"] + h["root_len"]], comp)), 0)
        f.seek(h["meta_offset"])
        meta_raw = f.read(h["meta_len"])
    return h, np.array(rows, dtype=np.int64).reshape(-1, 4), meta_raw


def verify(path, arc, expect_rows, minz, maxz, samples=64, seed=None):
    """Re-reads a written cut and checks it: header and layout, that its tiles are exactly the collected ones, that the
    metadata is the source's, and `samples` random tiles byte-equal to the source (looked up there one by one; "all" checks
    every tile). Raises CutError on the first failure; returns what was checked."""
    h, rows, meta_raw = read_entries(path)
    if h["min_zoom"] != minz or h["max_zoom"] != maxz:
        raise CutError("zooms in the header are not the ones cut")
    if not h["clustered"] or h["tile_compression"] != arc.header["tile_compression"] or h["tile_type"] != arc.header["tile_type"]:
        raise CutError("header flags differ from the source")
    if meta_raw != arc.metadata_raw():
        raise CutError("metadata differs from the source")
    ids, run, ln, off = rows[:, 0], rows[:, 1], rows[:, 2], rows[:, 3]
    if len(rows) != h["tile_entries"] or int(run.sum()) != h["addressed_tiles"]:
        raise CutError("header tile counts do not match the directories")
    if len(rows) and ((ids[1:] < ids[:-1] + run[:-1]).any() or (off + ln > h["data_len"]).any()):
        raise CutError("directory entries overlap or point past the tile data")
    uo, first = np.unique(off, return_index=True)
    if len(uo) != h["tile_contents"]:
        raise CutError("header content count does not match the directories")
    fo = off[np.sort(first)]          # first appearances in tile order must be laid out back to back (clustered)
    fl = ln[np.sort(first)]
    if len(fo) and (fo[0] != 0 or (fo[1:] != fo[:-1] + fl[:-1]).any() or int(fo[-1] + fl[-1]) != h["data_len"]):
        raise CutError("tile data is not clustered or has gaps")
    # the tiles are exactly the collected ones: same ids, same lengths
    def runs(tid, cnt, l):
        if len(tid) == 0:
            return np.zeros((0, 3), dtype=np.int64)
        brk = np.r_[True, (tid[1:] != tid[:-1] + cnt[:-1]) | (l[1:] != l[:-1])]
        st = np.flatnonzero(brk)
        return np.stack([tid[st], np.add.reduceat(cnt, st), l[st]], axis=1)
    if not np.array_equal(runs(ids, run, ln), runs(expect_rows[:, 0], expect_rows[:, 1], expect_rows[:, 3])):
        raise CutError("the file's tiles are not the tiles of the cover")
    total = int(run.sum())
    rng = random.Random(seed)
    picks = range(total) if samples == "all" else sorted(rng.randrange(total) for _ in range(min(int(samples), total))) if total else []
    cum = np.cumsum(run)
    bad = 0
    with open(path, "rb") as f:
        for k in picks:
            i = int(np.searchsorted(cum, k, side="right"))
            tid = int(ids[i] + (k - (cum[i] - run[i])))
            f.seek(h["data_offset"] + int(off[i]))
            mine = f.read(int(ln[i]))
            loc = arc.lookup(tid)
            if loc is None or loc[1] != len(mine) or arc.src.read(arc.header["data_offset"] + loc[0], loc[1]) != mine:
                bad += 1
    if bad:
        raise CutError(f"{bad} of {len(picks)} sampled tiles differ from the source")
    return {"tiles": total, "entries": len(rows), "contents": int(len(uo)), "sampled": len(picks)}


# ---------------------------------------------------------------- one region

def cut_region(arc, rings, out_path, minz=0, maxz=15, samples=64, root_max=FIRST - HEADER_LEN):
    t0 = time.time()
    box = rings_bounds(rings)
    o = Outline(rings)
    ranges = o.cover(minz, maxz, box)
    cover_tiles_n = sum(b - a for a, b in ranges)
    t1 = time.time()
    rows = arc.collect(ranges)
    if len(rows) == 0:
        raise CutError("the source has no tiles in this outline")
    t2 = time.time()
    facts = write_pmtiles(arc, rows, out_path, minz, maxz, box, root_max=root_max)
    t3 = time.time()
    checked = verify(out_path, arc, rows, minz, maxz, samples=samples)
    facts.update(sha256=sha256_file(out_path), cover_tiles=cover_tiles_n, cover_ranges=len(ranges), verified=checked,
                 seconds={"cover": round(t1 - t0, 2), "directory": round(t2 - t1, 2), "write": round(t3 - t2, 2),
                          "verify": round(time.time() - t3, 2), "total": round(time.time() - t0, 2)},
                 bounds=list(box), minzoom=minz, maxzoom=maxz)
    if facts["bytes"] != os.path.getsize(out_path):
        raise CutError("file size differs from the plan")
    return facts


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cut")
    c.add_argument("--outline", required=True)
    c.add_argument("--source", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--minzoom", type=int, default=0)
    c.add_argument("--maxzoom", type=int, default=15)
    c.add_argument("--samples", default="64")
    v = sub.add_parser("verify")
    v.add_argument("--file", required=True)
    v.add_argument("--outline", required=True)
    v.add_argument("--source", required=True)
    v.add_argument("--minzoom", type=int, default=0)
    v.add_argument("--maxzoom", type=int, default=15)
    v.add_argument("--samples", default="64")
    a = ap.parse_args()
    arc = Archive(open_source(a.source))
    rings = outline_rings(json.load(open(a.outline)))
    samples = a.samples if a.samples == "all" else int(a.samples)
    if a.cmd == "cut":
        print(json.dumps(cut_region(arc, rings, a.out, a.minzoom, a.maxzoom, samples)))
    else:
        rows = arc.collect(Outline(rings).cover(a.minzoom, a.maxzoom, rings_bounds(rings)))
        print(json.dumps(verify(a.file, arc, rows, a.minzoom, a.maxzoom, samples)))


if __name__ == "__main__":
    try:
        main()
    except CutError as e:
        sys.exit(f"error: {e}")
