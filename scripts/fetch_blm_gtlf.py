#!/usr/bin/env python3
"""Download the BLM National GTLF public datasets through the documented ArcGIS REST query API.

Pages with resultOffset within the service's maxRecordCount, filtered to the region bbox (env BBOX = w,s,e,n),
and writes GeoJSON-seq with a `subset` attribute naming the BLM feature service each feature came from.
"""
import json, os, sys, time, urllib.parse, urllib.request

BASE = "https://services1.arcgis.com/KbxwQRRfWyEYLgp4/ArcGIS/rest/services"
# (subset name, service, layer id) from each FeatureServer's own listing on the BLM hub
SUBSETS = [
    ("motorized_road", "BLM_Natl_GTLF_Public_Motorized_Roads", 3),
    ("limited_motorized_road", "BLM_Natl_GTLF_Limited_Public_Motorized_Roads", 0),
    ("motorized_trail", "BLM_Natl_GTLF_Public_Motorized_Trails", 4),
    ("limited_motorized_trail", "BLM_Natl_GTLF_Limited_Public_Motorized_Trails", 1),
    ("nonmechanized_trail", "BLM_Natl_GTLF_Public_Nonmechanized_Trails", 5),
    ("nonmotorized_trail", "BLM_Natl_GTLF_Public_Nonmotorized_Trails", 6),
    ("managed_trail", "BLM_Natl_GTLF_Public_Managed_Trails", 2),
    ("not_assessed_trail", "BLM_Natl_GTLF_Public_Not_Assessed_Trails", 7),
]
FIELDS = ("FLTP_CODE,ADMIN_ST,ROUTE_PRMRY_NM,ROUTE_SCNDRY_SPCL_DSGNTN_NM,ROUTE_SPCL_DSGNTN_TYPE,OBSRVE_SRFCE_TYPE,"
          "OBSRVE_ROUTE_USE_CLASS,PLAN_ROUTE_DSGNTN_AUTH,PLAN_ASSET_CLASS,PLAN_OHV_ROUTE_DSGNTN,OHV_ROUTE_DSGNTN_LIM,"
          "PLAN_MODE_TRNSPRT,PLAN_ALLOW_MODE_TRNSPRT,PLAN_ACCESS_RSTRCT,PLAN_SEASON_RSTRCT_CODE,GIS_MILES")
PAGE = 1000  # below every service's maxRecordCount (2000)


def get(url, data):
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode())
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.load(r)
            if "error" in d:
                raise RuntimeError(d["error"])
            return d
        except Exception as e:
            print(f"retry {attempt}: {e}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise SystemExit("query failed")


def main(out):
    national = os.environ.get("NATIONAL") == "1"
    w, s, e, n = os.environ["BBOX"].split(",")
    total = 0
    with open(out, "w") as fh:
        for subset, svc, lid in SUBSETS:
            url = f"{BASE}/{svc}/FeatureServer/{lid}/query"
            offset, count = 0, 0
            while True:
                q = {"where": "1=1", "outFields": FIELDS, "outSR": "4326", "orderByFields": "OBJECTID",
                     "resultOffset": offset, "resultRecordCount": PAGE, "f": "geojson"}
                if not national:
                    q.update({"geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
                              "inSR": "4326", "spatialRel": "esriSpatialRelIntersects"})
                d = get(url, q)
                feats = d.get("features", [])
                for f in feats:
                    f["properties"] = {k: v for k, v in (f.get("properties") or {}).items() if v not in (None, "")}
                    f["properties"]["subset"] = subset
                    fh.write(json.dumps(f, separators=(",", ":")) + "\n")
                count += len(feats)
                if not feats or len(feats) < PAGE and not d.get("exceededTransferLimit"):
                    break
                offset += len(feats)
            print(f"{subset}: {count} features", file=sys.stderr)
            total += count
    print(f"total {total}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1])
