#!/usr/bin/env python3
"""USFS and BLM road and trail lines with a `burn` value for the trails raster (rules/offroad/agency_line_classes.tsv).

usage: prep_agency.py --rules DIR --usfs DIR --blm blm.geojsonl --bbox w,s,e,n --out agency_lines.geojsonl
Sources: USFS EDW Trans_RoadCore_FS.gdb.zip and Trans_Trail_NFS_Publish.gdb.zip (file downloads), BLM National GTLF
GeoJSON-seq from scripts/fetch_blm_gtlf.py (documented ArcGIS REST query API). Output is EPSG:4326, one feature per
line, properties `burn` and `source`. Counts go to <out dir>/agency_lines.counts.
"""
import argparse, json, os
from collections import Counter
from osgeo import ogr, osr

ogr.UseExceptions()
USFS = {"usfs_road": ("Trans_RoadCore_FS.gdb.zip", "OPER_MAINT_LEVEL"), "usfs_trail": ("Trans_Trail_NFS_Publish.gdb.zip", "TRAIL_TYPE")}


def code(v):
    """The coded part of a USFS domain value ("2 - HIGH CLEARANCE VEHICLES" -> "2"), upper case; None -> ""."""
    if v is None:
        return ""
    return str(v).split(" - ", 1)[0].strip().upper()


def read_rules(path):
    rules = {}
    header = None
    with open(path) as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            p = line.split("\t")
            if header is None:
                header = p
                if header != ["source", "field", "code", "burn"]:
                    raise SystemExit(f"{path}: bad header {header}")
                continue
            rules[(p[0], p[1], p[2].upper())] = int(p[3])
    return rules


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rules", required=True)
    ap.add_argument("--usfs", required=True)
    ap.add_argument("--blm", required=True)
    ap.add_argument("--bbox", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    w, s, e, n = [float(v) for v in a.bbox.split(",")]
    rules = read_rules(os.path.join(a.rules, "agency_line_classes.tsv"))
    src, dst = osr.SpatialReference(), osr.SpatialReference()
    src.ImportFromEPSG(4269)
    dst.ImportFromEPSG(4326)
    for r in (src, dst):
        r.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr = osr.CoordinateTransformation(src, dst)
    cnt = Counter()
    with open(a.out, "w") as fh:
        def write(source, burn, geom_json):
            fh.write(json.dumps({"type": "Feature", "properties": {"burn": burn, "source": source}, "geometry": geom_json},
                                separators=(",", ":")) + "\n")
            cnt[f"{source}_{burn}"] += 1

        for source, (fname, fld) in USFS.items():
            ds = ogr.Open(os.path.join(a.usfs, fname))
            lyr = ds.GetLayer(0)
            lyr.SetSpatialFilterRect(w, s, e, n)  # the layer is NAD83 lon/lat; the bbox is close enough for a pre-filter
            for f in lyr:
                b = rules.get((source, fld, code(f.GetField(fld))))
                g = f.GetGeometryRef()
                if b is None or g is None or g.IsEmpty():
                    cnt[f"{source}_skipped"] += 1
                    continue
                g = g.Clone()
                g.Transform(tr)
                write(source, b, json.loads(g.ExportToJson()))
            del ds
        with open(a.blm) as src_fh:
            for line in src_fh:
                f = json.loads(line)
                p = f.get("properties") or {}
                b = rules.get(("blm", "subset", str(p.get("subset", "")).upper()))
                if b is None or not f.get("geometry"):
                    cnt["blm_skipped"] += 1
                    continue
                write("blm", b, f["geometry"])
    with open(os.path.join(os.path.dirname(a.out) or ".", "agency_lines.counts"), "w") as fh:
        fh.write(", ".join(f"{k} {v}" for k, v in sorted(cnt.items())) + "\n")


if __name__ == "__main__":
    main()
