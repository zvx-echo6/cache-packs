#!/usr/bin/env python3
"""USFS MVUM roads and trails for the off-road file, with null trail shapes filled from the NFS trail centrelines.

usage: prep_mvum.py --usfs DIR --bbox w,s,e,n --out mvum.geojsonl
DIR holds the EDW downloads Trans_MVUM_Road.gdb.zip, Trans_MVUM_Trail.gdb.zip and Trans_Trail_NFS_Publish.gdb.zip
(GDAL's OpenFileGDB reads the zips directly). Writes one GeoJSON feature per line in EPSG:4326 with the fields the
router reads (lower case) plus `is_trail`, `fid` (USFS GLOBALID) and `backfilled`; a feature is kept when its bbox
meets --bbox. Counts go to <out dir>/mvum.counts.

Backfill rule (the one navi documented in README-mvum-ingest.md): a trail with no shape takes the geometry of the
NFS trail segments whose TRAIL_NO equals its ID and TRAIL_NAME equals its NAME, within the forest's ADMIN_ORG
4-character prefix. The forest -> prefix map is learned from the trails that do have a shape; a forest with no
learnable prefix (or no in-prefix match) falls back to number and name alone. The segments are written as one
MultiLineString, unmerged: the router rasterises each part, so merging touching parts changes no cell.
"""
import argparse, json, os
from collections import Counter, defaultdict
from osgeo import ogr, osr

FIELDS_ROAD = ["SYMBOL", "SEASONAL", "OPERATIONALMAINTLEVEL", "E_BIKE_CLASS1", "E_BIKE_CLASS1_DUR", "ATV", "ATV_DATESOPEN",
               "HIGHCLEARANCEVEHICLE", "HIGHCLEARANCEVEHICLE_DATESOPEN"]
FIELDS_TRAIL = [f for f in FIELDS_ROAD if f != "OPERATIONALMAINTLEVEL"]

ogr.UseExceptions()


def to_wgs84():
    src, dst = osr.SpatialReference(), osr.SpatialReference()
    src.ImportFromEPSG(4269)
    dst.ImportFromEPSG(4326)
    for s in (src, dst):
        s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return osr.CoordinateTransformation(src, dst)


def layer(path):
    ds = ogr.Open(path)
    return ds, ds.GetLayer(0)


def field(f, name):
    i = f.GetFieldIndex(name)
    if i < 0 or not f.IsFieldSetAndNotNull(i):
        return None
    v = f.GetField(i)
    return str(v) if v is not None else None


def parts(g):
    """MultiLineString coordinate lists of an OGR geometry (empty list for none)."""
    if g is None or g.IsEmpty():
        return []
    g = ogr.ForceToMultiLineString(g)
    out = []
    for i in range(g.GetGeometryCount()):
        ls = g.GetGeometryRef(i)
        pts = [(ls.GetX(j), ls.GetY(j)) for j in range(ls.GetPointCount())]
        if len(pts) >= 2:
            out.append(pts)
    return out


def meets(lines, bbox):
    w, s, e, n = bbox
    xs = [x for l in lines for x, _ in l]
    ys = [y for l in lines for _, y in l]
    return bool(xs) and max(xs) >= w and min(xs) <= e and max(ys) >= s and min(ys) <= n


def nfs_index(path, tr, wanted):
    """(no, name) -> {prefix4} for every NFS segment with a shape; geometries only for the wanted keys."""
    prefixes, geoms = defaultdict(set), defaultdict(list)
    ds, lyr = layer(path)
    for f in lyr:
        no, nm, org = field(f, "TRAIL_NO"), field(f, "TRAIL_NAME"), field(f, "ADMIN_ORG")
        g = f.GetGeometryRef()
        if no is None or nm is None or org is None or g is None or g.IsEmpty():
            continue
        prefixes[(no, nm)].add(org[:4])
        if (no, nm) in wanted:
            g = g.Clone()
            g.Transform(tr)
            geoms[(no, nm, org[:4])].append(parts(g))
    return prefixes, geoms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--usfs", required=True)
    ap.add_argument("--bbox", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    bbox = [float(v) for v in a.bbox.split(",")]
    tr = to_wgs84()
    cnt = Counter()
    out_dir = os.path.dirname(a.out) or "."

    def write(fh, props, lines):
        fh.write(json.dumps({"type": "Feature", "properties": props,
                             "geometry": {"type": "MultiLineString", "coordinates": [[[round(x, 7), round(y, 7)] for x, y in l] for l in lines]}},
                            separators=(",", ":")) + "\n")

    with open(a.out, "w") as fh:
        # roads
        ds, lyr = layer(os.path.join(a.usfs, "Trans_MVUM_Road.gdb.zip"))
        for f in lyr:
            cnt["roads_total"] += 1
            g = f.GetGeometryRef()
            if g is None or g.IsEmpty():
                cnt["roads_null_shape"] += 1
                continue
            g = g.Clone()
            g.Transform(tr)
            lines = parts(g)
            if not meets(lines, bbox):
                continue
            props = {k.lower(): field(f, k) for k in FIELDS_ROAD}
            props.update(is_trail=0, fid=field(f, "GLOBALID"), backfilled=0)
            write(fh, props, lines)
            cnt["roads_written"] += 1
        del ds
        # trails: first pass keeps shaped trails and remembers the null ones
        ds, lyr = layer(os.path.join(a.usfs, "Trans_MVUM_Trail.gdb.zip"))
        nulls, shaped_keys = [], []
        for f in lyr:
            cnt["trails_total"] += 1
            props = {k.lower(): field(f, k) for k in FIELDS_TRAIL}
            props.update(is_trail=1, fid=field(f, "GLOBALID"), backfilled=0)
            key = (field(f, "ID"), field(f, "NAME"), field(f, "FORESTNAME"))
            g = f.GetGeometryRef()
            if g is None or g.IsEmpty():
                cnt["trails_null_shape"] += 1
                nulls.append((key, props))
                continue
            shaped_keys.append(key)
            g = g.Clone()
            g.Transform(tr)
            lines = parts(g)
            if meets(lines, bbox):
                write(fh, props, lines)
                cnt["trails_written"] += 1
        del ds
        wanted = {(k[0], k[1]) for k, _ in nulls if k[0] and k[1]}
        prefixes, geoms = nfs_index(os.path.join(a.usfs, "Trans_Trail_NFS_Publish.gdb.zip"), tr, wanted)
        fmap = defaultdict(set)
        for no, nm, forest in shaped_keys:
            if no and nm and forest and (no, nm) in prefixes:
                fmap[forest] |= prefixes[(no, nm)]
        for (no, nm, forest), props in nulls:
            if not (no and nm):
                cnt["trails_backfill_no_key"] += 1
                continue
            found = []
            for p in fmap.get(forest, ()):
                for g in geoms.get((no, nm, p), []):
                    found.extend(g)
            if not found:  # fallback: number and name in any forest
                for p in prefixes.get((no, nm), ()):
                    for g in geoms.get((no, nm, p), []):
                        found.extend(g)
            if not found:
                cnt["trails_backfill_no_match"] += 1
                continue
            cnt["trails_backfilled"] += 1
            props["backfilled"] = 1
            if meets(found, bbox):
                write(fh, props, found)
                cnt["trails_written"] += 1
                cnt["trails_written_backfilled"] += 1
    with open(os.path.join(out_dir, "mvum.counts"), "w") as fh:
        fh.write(", ".join(f"{k} {v}" for k, v in sorted(cnt.items())) + "\n")


if __name__ == "__main__":
    main()
