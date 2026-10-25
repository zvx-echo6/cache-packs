#!/usr/bin/env python3
"""Remove a layer's folder from R2 and its entry from catalogue.json. Usage: remove_layer.py <layer-id>"""
import json, os, re, subprocess, sys, tempfile

lid = sys.argv[1]
if not re.fullmatch(r"[a-z0-9-]{3,64}", lid):
    raise SystemExit("bad layer id")
ep, bucket = os.environ["R2_ENDPOINT"], os.environ["R2_BUCKET"]
base = ["--endpoint-url", ep, "--region", "auto", "--only-show-errors"]
subprocess.run(["aws", "s3", "rm", f"s3://{bucket}/{lid}/", "--recursive", *base], check=True)
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "catalogue.json")
    subprocess.run(["aws", "s3", "cp", f"s3://{bucket}/catalogue.json", p, *base], check=True)
    cat = json.load(open(p))
    before = len(cat["layers"])
    cat["layers"] = [l for l in cat["layers"] if l["id"] != lid]
    json.dump(cat, open(p, "w"), indent=2)
    subprocess.run(["aws", "s3", "cp", p, f"s3://{bucket}/catalogue.json", "--content-type", "application/json",
                    "--cache-control", "public, max-age=300", *base], check=True)
print(f"removed {lid}; catalogue entries {before} -> {len(cat['layers'])}")
