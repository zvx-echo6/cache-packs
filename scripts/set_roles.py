#!/usr/bin/env python3
"""Add style_hints.role to the published layer definitions and catalogue.json without rebuilding anything."""
import json, os, subprocess, tempfile

ROLES = {"public-lands": "public_lands", "usfs-trails-roads": "trails", "mvum": "mvum", "blm-routes": "blm_routes", "basemap": "basemap"}
SUB = {"usfs-trails-roads": {"trails": "trails", "roads": "roads"}}
ep, bucket = os.environ["R2_ENDPOINT"], os.environ["R2_BUCKET"]
base = ["--endpoint-url", ep, "--region", "auto", "--only-show-errors"]

def get(key, path):
    return subprocess.run(["aws", "s3", "cp", f"s3://{bucket}/{key}", path, *base]).returncode == 0

def put(path, key):
    subprocess.run(["aws", "s3", "cp", path, f"s3://{bucket}/{key}", "--content-type", "application/json",
                    "--cache-control", "public, max-age=300", *base], check=True)

def patch(d):
    sh = d.setdefault("style_hints", {})
    sh["role"] = ROLES[d["id"]]
    for l in sh.get("layers", []):
        r = SUB.get(d["id"], {}).get(l["name"])
        if r: l["role"] = r
    return d

with tempfile.TemporaryDirectory() as td:
    cp = os.path.join(td, "c.json")
    assert get("catalogue.json", cp)
    cat = json.load(open(cp))
    cat["layers"] = [patch(l) if l["id"] in ROLES else l for l in cat["layers"]]
    for lid in ROLES:
        p = os.path.join(td, lid + ".json")
        key = f"{lid}/{lid}.json"
        if get(key, p):
            put_d = patch(json.load(open(p))); json.dump(put_d, open(p, "w"), indent=2); put(p, key); print("patched", key)
    json.dump(cat, open(cp, "w"), indent=2); put(cp, "catalogue.json")
print("roles set")
