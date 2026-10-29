#!/usr/bin/env python3
"""One-time calibration (B0): compare rebuilt inputs with navi's (read-only analysis, never a build input).

  b0_compare.py entry NAVI.tsv NEW.tsv      navi rows: lat lon highway (no header); new: entry_points.tsv
      counts per highway class, and the share of navi points within 1 m of a new point
  b0_compare.py raster NAVI.tif NEW.tif     cell agreement over the overlap (same pixel size and anchor),
      on cells that are non-zero in either, with the (navi, new) value matrix
"""
import math, sys
from collections import Counter, defaultdict


def entry(navi_path, new_path):
    navi, new = Counter(), Counter()
    cell = defaultdict(list)
    q = 1e-5  # ~1.1 m in latitude
    with open(new_path) as fh:
        next(fh)
        for line in fh:
            la, lo, hw = line.split("\t")[:3]
            la, lo = float(la), float(lo)
            new[hw] += 1
            cell[(int(la // q), int(lo // q))].append((la, lo))
    near = total = 0
    with open(navi_path) as fh:
        for line in fh:
            la, lo, hw = line.rstrip("\n").split("\t")
            la, lo = float(la), float(lo)
            navi[hw] += 1
            total += 1
            ci, cj = int(la // q), int(lo // q)
            k = math.cos(math.radians(la))
            hit = False
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    for (a, b) in cell.get((ci + di, cj + dj), ()):
                        if math.hypot((a - la) * 111320, (b - lo) * 111320 * k) <= 1.0:
                            hit = True
                            break
            near += hit
    print("highway\tnavi\tnew\tratio")
    for hw in sorted(set(navi) | set(new), key=lambda h: -navi[h]):
        print(f"{hw}\t{navi[hw]}\t{new[hw]}\t{new[hw] / navi[hw]:.3f}" if navi[hw] else f"{hw}\t0\t{new[hw]}\t-")
    print(f"total\t{sum(navi.values())}\t{sum(new.values())}")
    print(f"navi points within 1 m of a new point: {near}/{total} = {100 * near / total:.2f}%")


def raster(navi_path, new_path):
    import numpy as np
    from osgeo import gdal
    gdal.UseExceptions()
    a, b = gdal.Open(navi_path), gdal.Open(new_path)
    ga, gb = a.GetGeoTransform(), b.GetGeoTransform()
    if abs(ga[1] - gb[1]) > 1e-12 or abs(ga[5] - gb[5]) > 1e-12:
        sys.exit("pixel sizes differ")
    # overlap in pixel offsets of each raster
    ox = round((gb[0] - ga[0]) / ga[1])
    oy = round((gb[3] - ga[3]) / ga[5])
    if abs(ga[0] + ox * ga[1] - gb[0]) > 1e-9 or abs(ga[3] + oy * ga[5] - gb[3]) > 1e-9:
        sys.exit("grids are not aligned")
    ax0, ay0 = max(0, ox), max(0, oy)
    ax1, ay1 = min(a.RasterXSize, ox + b.RasterXSize), min(a.RasterYSize, oy + b.RasterYSize)
    m = np.zeros((256, 256), dtype=np.int64)
    step = 2000
    for y in range(ay0, ay1, step):
        h = min(step, ay1 - y)
        va = a.GetRasterBand(1).ReadAsArray(ax0, y, ax1 - ax0, h).astype(np.int64)
        vb = b.GetRasterBand(1).ReadAsArray(ax0 - ox, y - oy, ax1 - ax0, h).astype(np.int64)
        m += np.bincount((va * 256 + vb).ravel(), minlength=65536).reshape(256, 256)
    nz = m.sum() - m[0, 0]
    same = sum(m[i, i] for i in range(1, 256))
    print(f"overlap {ax1 - ax0} x {ay1 - ay0} px; cells non-zero in either: {nz}; equal: {same} ({100 * same / max(nz, 1):.2f}%)")
    print("navi\tnew\tcells")
    for i, j in zip(*np.nonzero(m)):
        if i or j:
            print(f"{i}\t{j}\t{m[i, j]}")


if __name__ == "__main__":
    {"entry": entry, "raster": raster}[sys.argv[1]](sys.argv[2], sys.argv[3])
