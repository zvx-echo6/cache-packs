#!/usr/bin/env python3
"""ATTRIBUTION.json for one off-road region build: every source, its licence and the statement it requires.

usage: attribution.py --mapterhorn attribution.json --urls download_urls.json --inputs IN_DIR --out ATTRIBUTION.json
The elevation entry is generated from Mapterhorn's own https://download.mapterhorn.com/attribution.json (all of its
sources; that file has no per-area coverage, so the list is not cut to the region) and download_urls.json (the
planet.pmtiles version the extract was cut from). Source dates come from the downloads in IN_DIR. PAD-US, USFS and BLM
are listed only when their downloads are in IN_DIR (a build with LAND_ACCESS=none uses none of them).
"""
import argparse, datetime, glob, json, os, re


def usfs_pubdate(xml_path):
    """Publication date from a USFS EDW FGDC metadata file (<pubdate>YYYYMMDD</pubdate>), or None."""
    try:
        with open(xml_path, errors="replace") as fh:
            m = re.search(r"<pubdate>\s*([0-9]{8})\s*</pubdate>", fh.read())
    except OSError:
        return None
    return f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else None


def osm_source(path):
    out = {}
    try:
        with open(path) as fh:
            for line in fh:
                k, _, v = line.strip().partition(" ")
                if k:
                    out[k] = v
    except OSError:
        pass
    return out


def mapterhorn(attr_path, urls_path):
    with open(attr_path) as fh:
        attr = json.load(fh)
    planet = None
    with open(urls_path) as fh:
        for it in json.load(fh).get("items", []):
            if it.get("name") == "planet.pmtiles":
                planet = {"url": it["url"], "md5sum": it.get("md5sum"), "size": it.get("size")}
    return {
        "id": "elevation",
        "name": "Mapterhorn terrain tiles (z12, lossless Terrarium WebP), PMTiles area extract of planet.pmtiles",
        "attribution": "© Mapterhorn",
        "attribution_url": "https://mapterhorn.com/attribution",
        "licence": "Per source; see the list below and https://mapterhorn.com/attribution",
        "used_for": ["elevation"],
        "planet": planet,
        "sources": [{k: s.get(k) for k in ("source", "name", "producer", "license", "website", "license_pdf", "access_year")} for s in attr],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapterhorn", required=True)
    ap.add_argument("--urls", required=True)
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    i = a.inputs
    tiles = []
    try:
        with open(os.path.join(i, "worldcover", "tiles.txt")) as fh:
            tiles = [t.strip() for t in fh if t.strip()]
    except OSError:
        pass
    osm = osm_source(os.path.join(i, "osm", "source.txt"))
    doc = {
        "format": 1,
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": [
            {"id": "worldcover", "name": "ESA WorldCover 10 m 2021 v200", "licence": "CC BY 4.0",
             "attribution": "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium",
             "url": "https://esa-worldcover.org", "tiles": tiles, "used_for": ["class", "friction"],
             "modifications": "Resampled (nearest) to the z12 web-mercator grid; friction is a per-class lookup."},
            {"id": "osm", "name": "OpenStreetMap", "licence": "ODbL 1.0", "attribution": "© OpenStreetMap contributors",
             "url": "https://www.openstreetmap.org/copyright", "extract": osm, "used_for": ["trails", "trailheads", "parking"]},
            *us_land(i),
            mapterhorn(a.mapterhorn, a.urls),
        ],
    }
    with open(a.out, "w") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
        fh.write("\n")


def us_land(i):
    """The US land-access sources whose downloads are in IN_DIR."""
    usfs = {os.path.basename(p)[:-4]: usfs_pubdate(p) for p in sorted(glob.glob(os.path.join(i, "usfs", "*.xml")))}
    have = {"padus": os.path.exists(os.path.join(i, "padus.geojsonl")), "usfs": os.path.isdir(os.path.join(i, "usfs")),
            "blm": os.path.exists(os.path.join(i, "blm.geojsonl"))}
    out = [
        {"id": "padus", "name": "USGS PAD-US 4.1 (Manager Name feature service)", "licence": "Public domain (US Government data)",
         "attribution": "USGS Gap Analysis Project, PAD-US 4.1 (doi:10.5066/P96WBCHS)", "used_for": ["barriers", "wilderness"]},
        {"id": "usfs", "name": "USDA Forest Service Enterprise Data Warehouse: MVUM roads and trails, NFS trails, road core",
         "licence": "Public domain (US Government data); provided without warranty by the USDA Forest Service",
         "attribution": "USDA Forest Service, Enterprise Data Warehouse", "published": usfs, "used_for": ["mvum", "trails"]},
        {"id": "blm", "name": "BLM National GTLF public datasets", "licence": "BLM data provided as is, with no warranty",
         "attribution": "Source: Bureau of Land Management (BLM National GTLF public datasets). Not approved or endorsed by the BLM.",
         "modifications": "Selected by the region bbox, classed into road, track or foot trail (rules/offroad/agency_line_classes.tsv) and rasterised into the trails layer.",
         "used_for": ["trails"]},
    ]
    return [s for s in out if have[s["id"]]]


if __name__ == "__main__":
    main()
