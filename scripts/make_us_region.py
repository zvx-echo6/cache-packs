#!/usr/bin/env python3
"""Build data/regions/us.geojson: lower 48 + Alaska + Hawaii (US Census cartographic boundary states) buffered by 50 km.

Input: GeoJSON of the Census states (converted from cb_<year>_us_state_5m.zip). Output: one MultiPolygon in WGS84,
simplified (~1 km), with rings that cross the antimeridian split at 180.
Needs shapely and pyproj. Usage: make_us_region.py states.geojson out.geojson
"""
import json, sys
from shapely.geometry import shape, mapping, box
from shapely.ops import transform, unary_union
from shapely import affinity
import pyproj
import shapely

def shift(a):
    a = a.copy()
    a[a[:, 0] < 0, 0] += 360
    return a

KEEP = {"AL","AZ","AR","CA","CO","CT","DE","DC","FL","GA","ID","IL","IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT",
        "NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","AK","HI"}
BUFFER_M = 50_000

def buffered(geom):
    out = []
    parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    for p in parts:
        c = p.centroid
        fwd = pyproj.Transformer.from_crs("EPSG:4326", f"+proj=aeqd +lat_0={c.y} +lon_0={c.x} +datum=WGS84", always_xy=True).transform
        inv = pyproj.Transformer.from_crs(f"+proj=aeqd +lat_0={c.y} +lon_0={c.x} +datum=WGS84", "EPSG:4326", always_xy=True).transform
        b = transform(inv, transform(fwd, p).buffer(BUFFER_M, 8))
        if b.bounds[2] - b.bounds[0] > 180:              # crosses the antimeridian: split at 180
            s = shapely.transform(b, shift)               # move the negative side to 180..360
            west = s.intersection(box(0, -90, 180, 90))
            east = affinity.translate(s.intersection(box(180, -90, 360, 90)), xoff=-360)
            out += [west, east]
        else:
            out.append(b)
    return out

def main(src, dst):
    fc = json.load(open(src))
    geoms = []
    for f in fc["features"]:
        if f["properties"].get("STUSPS") in KEEP:
            geoms += buffered(shape(f["geometry"]))
    u = unary_union([g for g in geoms if not g.is_empty]).simplify(0.01)
    json.dump({"type": "Feature", "properties": {"name": "US + 50 km"}, "geometry": mapping(u)}, open(dst, "w"),
              separators=(",", ":"))
    print("polygons:", len(u.geoms) if u.geom_type == "MultiPolygon" else 1, "bounds:", [round(v, 2) for v in u.bounds])

if __name__ == "__main__":
    main(*sys.argv[1:3])
