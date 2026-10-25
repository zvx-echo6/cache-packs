#!/usr/bin/env python3
"""List a release-hosted pack in catalogue.json (key "packs"). Usage: publish_pack.py <manifest.json> <release-tag>
The pack files stay on GitHub Releases (single source of truth); the catalogue only points at the manifest."""
import datetime, json, os, subprocess, sys, tempfile

man = json.load(open(sys.argv[1])); tag = sys.argv[2]
repo = os.environ["GITHUB_REPOSITORY"]
ep, bucket = os.environ["R2_ENDPOINT"], os.environ["R2_BUCKET"]
base = ["--endpoint-url", ep, "--region", "auto", "--only-show-errors"]
entry = {"id": man["id"], "title": f"Routing tiles ({man['id'].split('valhalla-routing-')[-1]})", "kind": "valhalla-routing-tiles",
         "version": man["version"], "valhalla_version": man["valhalla_version"], "tag": tag,
         "manifest": f"https://github.com/{repo}/releases/download/{tag}/{os.path.basename(sys.argv[1])}",
         "size": man["tar"]["size"], "parts": len(man["parts"]), "license": man["source"]["license"],
         "attribution": man["source"]["attribution"], "source_url": man["source"]["url"],
         "recipe": f"https://github.com/{repo}/blob/main/recipes/valhalla-routing.md"}
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "catalogue.json")
    r = subprocess.run(["aws", "s3", "cp", f"s3://{bucket}/catalogue.json", p, *base])
    cat = json.load(open(p)) if r.returncode == 0 else {"format": 1, "layers": []}
    cat["packs"] = sorted([x for x in cat.get("packs", []) if x["id"] != entry["id"]] + [entry], key=lambda x: x["id"])
    cat["updated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    json.dump(cat, open(p, "w"), indent=2)
    subprocess.run(["aws", "s3", "cp", p, f"s3://{bucket}/catalogue.json", "--content-type", "application/json",
                    "--cache-control", "public, max-age=300", *base], check=True)
print("listed", entry["id"])
