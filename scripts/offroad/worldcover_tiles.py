#!/usr/bin/env python3
"""List the ESA WorldCover 2021 v200 tiles that meet a bbox, from the bucket's own grid file.

usage: worldcover_tiles.py GRID.geojson W,S,E,N   -> one tile name (e.g. N42W117) per line
The grid file is https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/esa_worldcover_grid.geojson
(3 x 3 degree cells, property `ll_tile`). Only cells the grid lists exist as map tiles (open ocean has none).
"""
import json, sys


def cells(grid, bbox):
    w, s, e, n = bbox
    out = []
    for f in grid["features"]:
        name = f["properties"]["ll_tile"]
        xs, ys = [], []
        for ring in _rings(f["geometry"]):
            for x, y in ring:
                xs.append(x)
                ys.append(y)
        # touching edges do not count: a cell must share area with the bbox
        if min(xs) < e and max(xs) > w and min(ys) < n and max(ys) > s:
            out.append(name)
    return sorted(set(out))


def _rings(g):
    if g["type"] == "Polygon":
        return g["coordinates"]
    if g["type"] == "MultiPolygon":
        return [r for p in g["coordinates"] for r in p]
    return []


if __name__ == "__main__":
    bbox = [float(v) for v in sys.argv[2].split(",")]
    with open(sys.argv[1]) as fh:
        for name in cells(json.load(fh), bbox):
            print(name)
