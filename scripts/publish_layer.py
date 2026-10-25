#!/usr/bin/env python3
"""Upload a built PMTiles file to R2, write its layer definition (format 1) and merge it into catalogue.json.

R2 credentials come from the environment (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY), plus
R2_ENDPOINT, R2_BUCKET and R2_PUBLIC_URL. Nothing here prints them.
"""
import argparse, datetime, json, os, subprocess, sys, tempfile

def aws(*args):
    cmd = ["aws", "s3", *args, "--endpoint-url", os.environ["R2_ENDPOINT"], "--region", "auto", "--only-show-errors"]
    return subprocess.run(cmd, check=True)

def put(path, key, ctype, cache):
    aws("cp", path, f"s3://{os.environ['R2_BUCKET']}/{key}", "--content-type", ctype, "--cache-control", cache)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--id", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--description", required=True)
    p.add_argument("--pmtiles", required=True)
    p.add_argument("--minzoom", type=int, required=True)
    p.add_argument("--maxzoom", type=int, required=True)
    p.add_argument("--attribution", required=True)
    p.add_argument("--license", required=True)
    p.add_argument("--interval-days", type=int, required=True)
    p.add_argument("--style-hints", required=True, help="JSON list of {name, attributes}")
    p.add_argument("--role", required=True, help="app colour role: basemap, public_lands, trails, roads, mvum, blm_routes, ...")
    p.add_argument("--recipe", required=True, help="recipe file name under recipes/")
    p.add_argument("--extra", action="append", default=[], help="path:content-type, uploaded next to the PMTiles")
    a = p.parse_args()

    base = os.environ["R2_PUBLIC_URL"].rstrip("/")
    bbox = [float(x) for x in os.environ["BBOX"].split(",")]
    version = os.environ["VERSION"]
    folder = a.id
    key = f"{folder}/{a.id}.pmtiles"
    put(a.pmtiles, key, "application/vnd.pmtiles", "public, max-age=86400")
    extras = []
    for e in a.extra:
        path, ctype = e.rsplit(":", 1)
        k = f"{folder}/{os.path.basename(path)}"
        put(path, k, ctype, "public, max-age=86400")
        extras.append(f"{base}/{k}")

    definition = {
        "format": 1, "id": a.id, "title": a.title, "description": a.description,
        "kind": "vector", "transform": "as_is",
        "source": {"type": "pmtiles", "url": f"{base}/{key}", "minzoom": a.minzoom,
                   "maxzoom": a.maxzoom, "tile_format": "mvt"},
        "attribution": a.attribution, "license": a.license,
        "coverage": {"bbox": bbox}, "offline": {"allowed": True},
        "update": {"version": version, "interval_days": a.interval_days},
        "style_hints": {"role": a.role, "layers": json.loads(a.style_hints)},
        "recipe": f"https://github.com/zvx-echo6/cache-packs/blob/main/recipes/{a.recipe}",
    }
    if extras:
        definition["extra_files"] = extras
    with tempfile.TemporaryDirectory() as td:
        dpath = os.path.join(td, "def.json")
        json.dump(definition, open(dpath, "w"), indent=2)
        put(dpath, f"{folder}/{a.id}.json", "application/json", "public, max-age=300")

        # merge into catalogue.json (missing on first run)
        cpath = os.path.join(td, "catalogue.json")
        r = subprocess.run(["aws", "s3", "cp", f"s3://{os.environ['R2_BUCKET']}/catalogue.json", cpath,
                            "--endpoint-url", os.environ["R2_ENDPOINT"], "--region", "auto", "--only-show-errors"])
        cat = json.load(open(cpath)) if r.returncode == 0 else {"format": 1, "layers": []}
        cat["layers"] = [l for l in cat["layers"] if l["id"] != a.id] + [definition]
        cat["layers"].sort(key=lambda l: l["id"])
        cat["updated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        json.dump(cat, open(cpath, "w"), indent=2)
        put(cpath, "catalogue.json", "application/json", "public, max-age=300")
    print(f"published {a.id} version {version}; size {os.path.getsize(a.pmtiles)} bytes")

if __name__ == "__main__":
    sys.exit(main())
