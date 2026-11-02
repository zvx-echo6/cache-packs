#!/usr/bin/env python3
"""Stage, list and check off-road region packs for the data host (layout and catalogue: recipes/offroad-regions.md).

  publish_offroad.py stage SOURCE REGION DEST
      SOURCE is a release download URL (https://github.com/OWNER/REPO/releases/download/TAG) or a local folder that holds
      the same REGION-* assets. Downloads REGION-manifest.json and every asset it names, joins split parts, checks every
      part's and file's sha256 against the manifest, and writes DEST/REGION/: offroad.sqlite, ATTRIBUTION.json and
      release-manifest.json.
  publish_offroad.py catalogue --build BUILD --base-url URL --names regions.tsv [--existing current.json] DEST REGION...
      Writes DEST/REGION/manifest.json (the data-host manifest) for each REGION and DEST/current.json: the existing
      catalogue's regions with these REGIONs added or replaced (matched by id), sorted by id.
  publish_offroad.py check current.json
      Every file URL in the catalogue answers 200 with the listed size, and 206 to a one-byte range request.
"""
import argparse, datetime, hashlib, json, os, shutil, sqlite3, sys, urllib.request

FILES = ("offroad.sqlite", "ATTRIBUTION.json")  # the pack's own files; manifest.json is written here
META_KEYS = ("format_version", "mode_profile_hash", "build_unix_time", "source_tiles", "rows_parking", "rows_trailheads",
             "rows_mvum", "cov_land_access", "cov_mvum", "class_friction_sha256")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while b := fh.read(8 << 20):
            h.update(b)
    return h.hexdigest()


def fetch(source, name, dst):
    if source.startswith("https://"):
        req = urllib.request.Request(f"{source}/{name}", headers={"User-Agent": "cache-packs-offroad"})
        with urllib.request.urlopen(req, timeout=120) as r, open(dst, "wb") as out:
            shutil.copyfileobj(r, out, 8 << 20)
    else:
        shutil.copyfile(os.path.join(source, name), dst)


def stage(a):
    d = os.path.join(a.dest, a.region)
    os.makedirs(d, exist_ok=True)
    man_path = os.path.join(d, "release-manifest.json")
    fetch(a.source, f"{a.region}-manifest.json", man_path)
    man = json.load(open(man_path))
    if man.get("region") != a.region:
        raise SystemExit(f"{a.region}: manifest is for {man.get('region')}")
    for e in man["files"]:
        if e["name"] not in FILES:
            continue
        out_path = os.path.join(d, e["name"])
        whole = hashlib.sha256()
        with open(out_path, "wb") as out:
            for p in e.get("parts") or [{"asset": e["asset"], "sha256": e["sha256"]}]:
                tmp = out_path + ".part"
                fetch(a.source, p["asset"], tmp)
                got = sha256_file(tmp)
                if got != p["sha256"]:
                    raise SystemExit(f"{p['asset']}: sha256 {got} is not the manifest's {p['sha256']}")
                with open(tmp, "rb") as fh:
                    while b := fh.read(8 << 20):
                        whole.update(b)
                        out.write(b)
                os.remove(tmp)
        if whole.hexdigest() != e["sha256"] or os.path.getsize(out_path) != e["bytes"]:
            raise SystemExit(f"{a.region}/{e['name']}: size or sha256 does not match the manifest")
        print(f"ok {a.region}/{e['name']} {e['bytes']} bytes, {len(e.get('parts') or [0])} part(s), sha256 {e['sha256']}")
    missing = [f for f in FILES if not os.path.exists(os.path.join(d, f))]
    if missing:
        raise SystemExit(f"{a.region}: manifest has no {missing}")


def pack_meta(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    m = dict(c.execute("SELECT key, value FROM meta"))
    c.close()
    return m


def read_names(path):
    out = {}
    with open(path) as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            p = line.split("\t")
            if p[0] == "id":
                continue
            out[p[0]] = {"name": p[1], "country": p[2]}
    return out


def file_entry(base_url, rel, path):
    return {"path": rel, "url": base_url + rel, "bytes": os.path.getsize(path), "sha256": sha256_file(path)}


def catalogue(a):
    base = a.base_url if a.base_url.endswith("/") else a.base_url + "/"
    names = read_names(a.names)
    cat = {"regions": []}
    if a.existing and os.path.exists(a.existing) and os.path.getsize(a.existing):
        cat = json.load(open(a.existing))
    regions = {r["id"]: r for r in cat.get("regions", [])}
    for rid in a.regions:
        if rid not in names:
            raise SystemExit(f"{rid}: not in {a.names}")
        d = os.path.join(a.dest, rid)
        rel_man = json.load(open(os.path.join(d, "release-manifest.json")))
        meta = pack_meta(os.path.join(d, "offroad.sqlite"))
        bbox = [float(meta[k]) for k in ("bbox_west", "bbox_south", "bbox_east", "bbox_north")]
        built = datetime.datetime.fromtimestamp(int(meta["build_unix_time"]), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        files = {f: file_entry(base, f"{a.build}/{rid}/{f}", os.path.join(d, f)) for f in FILES}
        for f in FILES:
            want = next(e["sha256"] for e in rel_man["files"] if e["name"] == f)
            if files[f]["sha256"] != want:
                raise SystemExit(f"{rid}/{f}: sha256 changed since staging")
        land = meta.get("cov_land_access", "us")
        man = {"format": 1, "component": "offroad", "region": rid, "build": a.build, "built": built,
               "pack_format": int(meta["format_version"]), "bbox": bbox, "land_access": land,
               "files": [{"name": f, "bytes": files[f]["bytes"], "sha256": files[f]["sha256"]} for f in FILES],
               "elevation": rel_man.get("elevation"), "meta": {k: meta[k] for k in META_KEYS if k in meta}}
        with open(os.path.join(d, "manifest.json"), "w") as fh:
            json.dump(man, fh, indent=1)
            fh.write("\n")
        files["manifest.json"] = file_entry(base, f"{a.build}/{rid}/manifest.json", os.path.join(d, "manifest.json"))
        el = rel_man.get("elevation") or {}
        regions[rid] = {
            "id": rid, **names[rid], "bbox": bbox, "build": a.build, "built": built, "pack_format": man["pack_format"],
            "land_access": land, "mode_profile_hash": meta.get("mode_profile_hash"),
            "bytes": sum(v["bytes"] for v in files.values()), "files": files,
            "elevation": {"tiles": el.get("tiles"), "bytes": el.get("extract_bytes"), "sha256": el.get("extract_sha256"),
                          "planet_md5": (el.get("planet") or {}).get("md5sum")},
        }
    doc = {
        "format": 1, "component": "offroad", "updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "base_url": base,
        "elevation": {"source": "Mapterhorn planet.pmtiles", "url": "https://download.mapterhorn.com/planet.pmtiles",
                      "tilejson": "https://tiles.mapterhorn.com/tilejson.json", "minzoom": 12, "maxzoom": 12,
                      "hosted_here": False, "attribution": "© Mapterhorn"},
        "regions": sorted(regions.values(), key=lambda r: r["id"]),
    }
    with open(os.path.join(a.dest, "current.json"), "w") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(f"current.json: {len(doc['regions'])} regions")


def check(a):
    doc = json.load(open(a.catalogue))
    bad = 0
    for r in doc["regions"]:
        for name, f in r["files"].items():
            req = urllib.request.Request(f["url"], method="HEAD", headers={"User-Agent": "cache-packs-offroad"})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    st, size = resp.status, int(resp.headers.get("Content-Length", -1))
                rq = urllib.request.Request(f["url"], headers={"Range": "bytes=0-0", "User-Agent": "cache-packs-offroad"})
                with urllib.request.urlopen(rq, timeout=60) as resp:
                    st2, cr = resp.status, resp.headers.get("Content-Range", "")
            except Exception as e:  # report every URL, then fail
                print(f"FAIL {f['url']}: {e}")
                bad += 1
                continue
            ok = st == 200 and size == f["bytes"] and st2 == 206 and cr.endswith(f"/{f['bytes']}")
            print(f"{'ok' if ok else 'FAIL'} {st} {st2} {size} {f['url']}")
            bad += not ok
    sys.exit(1 if bad else 0)


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("stage")
    s.add_argument("source"); s.add_argument("region"); s.add_argument("dest")
    c = sp.add_parser("catalogue")
    c.add_argument("--build", required=True); c.add_argument("--base-url", required=True)
    c.add_argument("--names", required=True); c.add_argument("--existing")
    c.add_argument("dest"); c.add_argument("regions", nargs="+")
    k = sp.add_parser("check")
    k.add_argument("catalogue")
    a = ap.parse_args()
    {"stage": stage, "catalogue": catalogue, "check": check}[a.cmd](a)


if __name__ == "__main__":
    main()
