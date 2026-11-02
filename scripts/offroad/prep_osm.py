#!/usr/bin/env python3
"""OSM vectors for the off-road file: burn classes for the trails raster, trailhead (entry) points and parking.

Inputs are osmium GeoJSON-seq exports (see build-offroad-region.sh prep-osm). Outputs in --out:
  osm_lines.geojsonl   highway lines with a `burn` property (rules/offroad/osm_trail_classes.tsv)
  entry_points.tsv     lat, lon, highway, name: first and last node of each way in rules/offroad/entry_point_classes.tsv,
                       one point per location
  parking.tsv          lat, lon, name, parking_type, access: one point per OSM object (polygon centroid, line
                       centroid or node), blocked access (private, no, permit) dropped
  prep_osm.counts      one line of counts
Runs in the GDAL image (osgeo.ogr for centroids).
"""
import argparse, json, os
from osgeo import ogr

BLOCKED = {"private", "no", "permit"}
GEOM_RANK = {"Point": 0, "LineString": 1, "MultiLineString": 1, "Polygon": 2, "MultiPolygon": 2}


def read_rules(path, cols):
    rows = []
    with open(path) as fh:
        header = None
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if header is None:
                header = parts
                if header[:len(cols)] != cols:
                    raise SystemExit(f"{path}: header {header} is not {cols}")
                continue
            rows.append(parts)
    return rows


def in_bbox(lat, lon, bbox):
    w, s, e, n = bbox
    return s <= lat <= n and w <= lon <= e


def clean(v):
    return (v or "").replace("\t", " ").replace("\n", " ").strip()


def lines_and_entries(highways, burn, entry, bbox, out):
    n_lines = n_entry = 0
    seen = set()
    with open(highways) as src, open(os.path.join(out, "osm_lines.geojsonl"), "w") as lo, \
            open(os.path.join(out, "entry_points.tsv"), "w") as eo:
        eo.write("lat\tlon\thighway\tname\n")
        for line in src:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            f = json.loads(line)
            g = f.get("geometry") or {}
            if g.get("type") != "LineString" or len(g.get("coordinates", [])) < 2:
                continue
            p = f.get("properties") or {}
            hw = p.get("highway")
            b = burn.get(hw)
            if b is not None:
                lo.write(json.dumps({"type": "Feature", "properties": {"burn": b}, "geometry": g}, separators=(",", ":")) + "\n")
                n_lines += 1
            if hw in entry:
                c = g["coordinates"]
                for lon, lat in (c[0], c[-1]):
                    if not in_bbox(lat, lon, bbox):
                        continue
                    key = (round(lat, 7), round(lon, 7))
                    if key in seen:  # a node shared by several ways is one point (the first way's class and name)
                        continue
                    seen.add(key)
                    eo.write(f"{lat:.7f}\t{lon:.7f}\t{hw}\t{clean(p.get('name'))}\n")
                    n_entry += 1
    return n_lines, n_entry


def parking(path, bbox, out):
    best = {}
    with open(path) as src:
        for line in src:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            f = json.loads(line)
            p = f.get("properties") or {}
            g = f.get("geometry")
            if not g or p.get("amenity") != "parking":
                continue
            key = (p.get("@type"), p.get("@id"))
            rank = GEOM_RANK.get(g.get("type"), -1)
            if key in best and best[key][0] >= rank:
                continue  # osmium writes a closed way as both line and area: keep the area
            best[key] = (rank, g, p)
    n = dropped = 0
    with open(os.path.join(out, "parking.tsv"), "w") as po:
        po.write("lat\tlon\tname\tparking_type\taccess\n")
        for _, g, p in best.values():
            access = clean(p.get("access"))
            if access in BLOCKED:
                dropped += 1
                continue
            c = ogr.CreateGeometryFromJson(json.dumps(g)).Centroid()
            lon, lat = c.GetX(), c.GetY()
            if not in_bbox(lat, lon, bbox):
                continue
            po.write(f"{lat:.7f}\t{lon:.7f}\t{clean(p.get('name'))}\t{clean(p.get('parking'))}\t{access}\n")
            n += 1
    return n, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", required=True)
    ap.add_argument("--bbox", required=True, help="w,s,e,n")
    ap.add_argument("--highways", required=True)
    ap.add_argument("--parking", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    bbox = [float(v) for v in a.bbox.split(",")]
    burn = {h: int(b) for h, b in read_rules(os.path.join(a.rules, "osm_trail_classes.tsv"), ["highway", "burn"])}
    entry = {r[0] for r in read_rules(os.path.join(a.rules, "entry_point_classes.tsv"), ["highway"])}
    n_lines, n_entry = lines_and_entries(a.highways, burn, entry, bbox, a.out)
    n_park, n_blocked = parking(a.parking, bbox, a.out)
    with open(os.path.join(a.out, "prep_osm.counts"), "w") as fh:
        fh.write(f"lines {n_lines}, entry points {n_entry}, parking {n_park} (blocked dropped {n_blocked})\n")


if __name__ == "__main__":
    main()
