#!/usr/bin/env python3
"""One-time analysis (B0): recover the WorldCover class -> friction table from paired tiles.

usage: b0_class_friction.py CLASS_DIR FRICTION_DIR > counts.tsv
Pairs ESA_WorldCover_10m_2021_v200_<tile>_Map.tif with <tile>_Friction.tif (same grid), and prints the joint
histogram as `class<TAB>friction<TAB>pixels`. A class with exactly one friction value is a pure lookup. The
friction tiles were navi's (read-only analysis input); they are never a build input.
"""
import glob, os, sys
import numpy as np
from osgeo import gdal


def pairs(class_dir, fr_dir):
    for f in sorted(glob.glob(os.path.join(fr_dir, "*_Friction.tif"))):
        c = os.path.join(class_dir, os.path.basename(f).replace("_Friction.tif", "_Map.tif"))
        if os.path.exists(c):
            yield c, f


def main(class_dir, fr_dir):
    hist = np.zeros((256, 256), dtype=np.int64)
    n = 0
    for c, f in pairs(class_dir, fr_dir):
        dc, df = gdal.Open(c), gdal.Open(f)
        if (dc.RasterXSize, dc.RasterYSize, dc.GetGeoTransform()) != (df.RasterXSize, df.RasterYSize, df.GetGeoTransform()):
            sys.exit(f"grid differs: {c} {f}")
        bc, bf = dc.GetRasterBand(1), df.GetRasterBand(1)
        step = 3000
        for y in range(0, dc.RasterYSize, step):
            h = min(step, dc.RasterYSize - y)
            a = bc.ReadAsArray(0, y, dc.RasterXSize, h).astype(np.int64)
            b = bf.ReadAsArray(0, y, dc.RasterXSize, h).astype(np.int64)
            hist += np.bincount((a * 256 + b).ravel(), minlength=65536).reshape(256, 256)
        n += 1
        print(f"{os.path.basename(c)}", file=sys.stderr)
    print(f"{n} tile pairs", file=sys.stderr)
    for cl, fr in zip(*np.nonzero(hist)):
        print(f"{cl}\t{fr}\t{hist[cl, fr]}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
