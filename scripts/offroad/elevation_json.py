#!/usr/bin/env python3
"""The elevation a region's pack expects: Mapterhorn's z12 tiles over BBOX (not republished), as JSON on stdout.

usage: elevation_json.py PLANET_JSON EXTRACT.pmtiles w,s,e,n
PLANET_JSON is Mapterhorn's download_urls.json, or a region's ATTRIBUTION.json (its elevation entry's "planet").
"""
import hashlib, json, os, struct, sys


def planet_of(path):
    doc = json.load(open(path))
    for i in doc.get("items", []):
        if i.get("name") == "planet.pmtiles":
            return i
    for s in doc.get("sources", []):
        if s.get("id") == "elevation" and s.get("planet"):
            return dict(name="planet.pmtiles", **s["planet"])
    return None


def main():
    src, pm, bbox = sys.argv[1:4]
    with open(pm, "rb") as fh:
        head = fh.read(127)
        fh.seek(0)
        h = hashlib.sha256()
        while b := fh.read(8 << 20):
            h.update(b)
    if head[:7] != b"PMTiles" or head[7] != 3:
        raise SystemExit(f"{pm}: not a PMTiles v3 file")
    w, s, e, n = map(float, bbox.split(","))
    print(json.dumps({
        "source": "Mapterhorn planet.pmtiles (z12, 512 px lossless Terrarium WebP); not included in this release",
        "planet": planet_of(src), "bbox": [w, s, e, n], "minzoom": 12, "maxzoom": 12,
        "tiles": struct.unpack_from("<Q", head, 72)[0],
        "extract_bytes": os.path.getsize(pm), "extract_sha256": h.hexdigest(),
        "how": f"pmtiles extract --bbox={bbox} --minzoom=12 --maxzoom=12 https://download.mapterhorn.com/planet.pmtiles elevation.pmtiles",
        "attribution": "© Mapterhorn (https://mapterhorn.com/attribution)",
    }, indent=1))


if __name__ == "__main__":
    main()
