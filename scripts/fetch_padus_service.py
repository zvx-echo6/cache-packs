#!/usr/bin/env python3
"""Download PAD-US from USGS's own ArcGIS feature service (documented REST query API) into GeoJSON-seq.

Service: PAD-US Manager Name Feature Service (layer PADUS4_1ManagerName), built from the PAD-US Combined
(Proclamation, Marine, Fee, Designation, Easement) feature class and linked from the USGS PAD-US Web Services page.
National by default; with REGION != us (env NATIONAL=0) the BBOX env var (w,s,e,n) filters the query.
Pages by OBJECTID within maxRecordCount (2000), several pages in flight at a time.
"""
import json, os, sys, threading, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

URL = os.environ.get("PADUS_SERVICE", "https://services.arcgis.com/v01gqwM5QqNysAAi/arcgis/rest/services/Manager_Name_PADUS/FeatureServer/0") + "/query"
FIELDS = "Unit_Nm,Mang_Name,Mang_Type,Des_Tp,Own_Type,Own_Name,Pub_Access,GAP_Sts,GIS_Acres,State_Nm,Category"
PAGE = 500


# The service allows 60 "large geometry" requests per minute; start at most one request every 1.4 s overall.
_lock, _next = threading.Lock(), [0.0]


def pace():
    with _lock:
        now = time.time()
        wait = max(0.0, _next[0] - now)
        _next[0] = max(now, _next[0]) + 1.4
    time.sleep(wait)


def call(params):
    data = urllib.parse.urlencode(params).encode()
    for attempt in range(12):
        try:
            pace()
            with urllib.request.urlopen(urllib.request.Request(URL, data=data), timeout=180) as r:
                d = json.load(r)
            if "error" in d:
                raise RuntimeError(d["error"])
            return d
        except Exception as e:
            print(f"retry {attempt}: {str(e)[:200]}", file=sys.stderr)
            time.sleep(20 if "429" in str(e) else 5 * (attempt + 1))
    raise SystemExit("query failed")


def main(out):
    base = {"where": "1=1"}
    if os.environ.get("NATIONAL") != "1":
        w, s, e, n = os.environ["BBOX"].split(",")
        base.update({"geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope", "inSR": "4326",
                     "spatialRel": "esriSpatialRelIntersects"})
    total = call({**base, "returnCountOnly": "true", "f": "json"})["count"]
    print(f"{total} features to fetch", file=sys.stderr)

    def page(offset):
        d = call({**base, "outFields": FIELDS, "outSR": "4326", "orderByFields": "OBJECTID", "geometryPrecision": "6",
                  "resultOffset": offset, "resultRecordCount": PAGE, "f": "geojson"})
        lines = []
        for f in d.get("features", []):
            if not f.get("geometry"):
                continue
            f["properties"] = {k: v for k, v in (f.get("properties") or {}).items() if v not in (None, "")}
            lines.append(json.dumps(f, separators=(",", ":")))
        return len(d.get("features", [])), lines

    got = 0
    with open(out, "w") as fh, ThreadPoolExecutor(max_workers=4) as ex:
        for i, (n, lines) in enumerate(ex.map(page, range(0, total, PAGE))):
            got += n
            if lines:
                fh.write("\n".join(lines) + "\n")
            if i % 100 == 0:
                print(f"{got}/{total}", file=sys.stderr)
    print(f"fetched {got} of {total}", file=sys.stderr)
    if got < total:
        raise SystemExit("incomplete download")


if __name__ == "__main__":
    main(sys.argv[1])
