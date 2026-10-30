#!/usr/bin/env python3
"""Release assets for one off-road region build: every file of OUT_DIR named REGION-<file>, files over 2 GiB (the GitHub
Release asset limit) cut into parts, and REGION-manifest.json with the sizes and sha256s of every file and part.

usage: release_assets.py REGION OUT_DIR STAGE_DIR [--part-bytes N] [--limit N] [--skip NAME ...] [--extra KEY=JSON_FILE ...]
A split file is rebuilt by concatenating its parts in manifest order; the whole file's sha256 is in the manifest.
"""
import argparse, hashlib, json, os

LIMIT = 2 * 1024**3


def sha256_file(path, chunk=8 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while b := fh.read(chunk):
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("region")
    ap.add_argument("out_dir")
    ap.add_argument("stage_dir")
    ap.add_argument("--part-bytes", type=int, default=1900 * 1024**2)
    ap.add_argument("--limit", type=int, default=LIMIT, help="split files bigger than this (default 2 GiB)")
    ap.add_argument("--skip", action="append", default=[])
    ap.add_argument("--extra", action="append", default=[], help="KEY=JSON_FILE merged into the manifest")
    a = ap.parse_args()
    os.makedirs(a.stage_dir, exist_ok=True)
    files = []
    for name in sorted(os.listdir(a.out_dir)):
        src = os.path.join(a.out_dir, name)
        if name in a.skip or not os.path.isfile(src):
            continue
        size = os.path.getsize(src)
        entry = {"name": name, "bytes": size, "sha256": sha256_file(src)}
        if size <= a.limit:
            asset = f"{a.region}-{name}"
            link_or_copy(src, os.path.join(a.stage_dir, asset))
            entry["asset"] = asset
        else:
            entry["parts"] = []
            with open(src, "rb") as fh:
                n = 0
                while True:
                    asset = f"{a.region}-{name}.part{n:03d}"
                    dst = os.path.join(a.stage_dir, asset)
                    h, wrote = hashlib.sha256(), 0
                    with open(dst, "wb") as out:
                        while wrote < a.part_bytes:
                            b = fh.read(min(8 << 20, a.part_bytes - wrote))
                            if not b:
                                break
                            out.write(b); h.update(b); wrote += len(b)
                    if wrote == 0:
                        os.remove(dst)
                        break
                    entry["parts"].append({"asset": asset, "bytes": wrote, "sha256": h.hexdigest()})
                    n += 1
        files.append(entry)
    manifest = {"format": 1, "region": a.region, "files": files}
    for kv in a.extra:
        k, _, p = kv.partition("=")
        with open(p) as fh:
            manifest[k] = json.load(fh)
    with open(os.path.join(a.stage_dir, f"{a.region}-manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
        fh.write("\n")


def link_or_copy(src, dst):
    try:
        os.link(src, dst)
        return
    except OSError:
        pass
    with open(src, "rb") as i, open(dst, "wb") as o:
        while b := i.read(8 << 20):
            o.write(b)


if __name__ == "__main__":
    main()
