#!/usr/bin/env python3
"""Mirror the newest Protomaps v4 planet build into R2, in resumable steps.

  mirror_basemap.py plan      -> picks the newest build, prints/exports its key, size and published hashes
  mirror_basemap.py copy      -> curl | hash | rclone rcat (multipart, no local copy); skipped if the object is already there
  mirror_basemap.py verify    -> streams the object back from R2 through BLAKE3 and compares with the published hash
  mirror_basemap.py finalize  -> manifest, layer definition, catalogue entry, keep the newest 2

Env: R2_BUCKET, R2_ENDPOINT, R2_PUBLIC_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (rclone is configured from them;
nothing is printed). The build is chosen once by `plan` and handed to later steps through BUILD_KEY.
"""
import base64, datetime, hashlib, json, os, subprocess, sys, tempfile, time, urllib.request

META = "https://build-metadata.protomaps.dev/builds.json"      # the list behind https://maps.protomaps.com/builds
SRC = "https://build.protomaps.com/"
PAGE = "https://docs.protomaps.com/basemaps/downloads"
BUCKET = os.environ.get("R2_BUCKET", "")
ENV = dict(os.environ, RCLONE_CONFIG_R2_TYPE="s3", RCLONE_CONFIG_R2_PROVIDER="Cloudflare",
           RCLONE_CONFIG_R2_ACCESS_KEY_ID=os.environ.get("AWS_ACCESS_KEY_ID", ""),
           RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
           RCLONE_CONFIG_R2_ENDPOINT=os.environ.get("R2_ENDPOINT", ""), RCLONE_CONFIG_R2_NO_CHECK_BUCKET="true",
           RCLONE_CONFIG_R2_REGION="auto")
CACHE = "public, max-age=86400"

def rclone(*a, **kw):
    return subprocess.run(["rclone", *a], env=ENV, check=kw.pop("check", True), **kw)

def build():
    builds = json.load(urllib.request.urlopen(urllib.request.Request(META, headers={"User-Agent": "cache-packs"})))
    key = os.environ.get("BUILD_KEY")
    b = next(x for x in builds if x["key"] == key) if key else max(builds, key=lambda x: x["key"])
    assert b["key"].endswith(".pmtiles") and b["key"][:8].isdigit()
    return b

def objkey(b): return f"basemap/protomaps-planet-{b['key'][:8]}.pmtiles"
def hexmd5(b): return base64.b64decode(b["md5sum"]).hex()

def plan():
    b = build()
    print(f"newest build {b['key']} version {b['version']} size {b['size']} uploaded {b['uploaded']}")
    if os.environ.get("GITHUB_OUTPUT"):
        open(os.environ["GITHUB_OUTPUT"], "a").write(f"key={b['key']}\n")

def remote_size(key):
    r = rclone("size", f"r2:{BUCKET}/{key}", "--json", check=False, stdout=subprocess.PIPE, text=True)
    return json.loads(r.stdout)["bytes"] if r.returncode == 0 and r.stdout else -1

def copy():
    import blake3
    b = build(); key = objkey(b)
    if remote_size(key) == b["size"]:
        print("object already in R2 with the right size; skipping copy"); return
    md5, b3 = hashlib.md5(), blake3.blake3(max_threads=blake3.blake3.AUTO)
    curl = subprocess.Popen(["curl", "-fL", "--retry", "5", "--retry-all-errors", "-sS", SRC + b["key"]], stdout=subprocess.PIPE)
    rc = subprocess.Popen(["rclone", "rcat", f"r2:{BUCKET}/{key}", "--s3-chunk-size", "128M", "--s3-upload-concurrency", "8",
                           "--header-upload", "Content-Type: application/vnd.pmtiles", "--header-upload", f"Cache-Control: {CACHE}"],
                          stdin=subprocess.PIPE, env=ENV)
    total, t0, last = 0, time.time(), time.time()
    while True:
        chunk = curl.stdout.read(8 << 20)
        if not chunk: break
        md5.update(chunk); b3.update(chunk); rc.stdin.write(chunk); total += len(chunk)
        if time.time() - last > 300:
            last = time.time(); print(f"{total/1e9:.1f} GB in {last-t0:.0f}s ({total/(last-t0)/1e6:.0f} MB/s)", flush=True)
    rc.stdin.close()
    if curl.wait() != 0 or rc.wait() != 0:
        rclone("deletefile", f"r2:{BUCKET}/{key}", check=False); sys.exit("transfer failed")
    dt = time.time() - t0
    print(f"copied {total} bytes in {dt:.0f}s ({total/dt/1e6:.0f} MB/s)")
    if total != b["size"] or md5.hexdigest() != hexmd5(b) or b3.hexdigest() != b["b3sum"]:
        rclone("deletefile", f"r2:{BUCKET}/{key}", check=False); sys.exit("verification of the download stream failed")
    print("download stream matched the published MD5 and BLAKE3")

def verify():
    import blake3
    b = build(); key = objkey(b)
    h, total, t0 = blake3.blake3(max_threads=blake3.blake3.AUTO), 0, time.time()
    p = subprocess.Popen(["rclone", "cat", f"r2:{BUCKET}/{key}", "--multi-thread-streams", "4"], stdout=subprocess.PIPE, env=ENV)
    while True:
        chunk = p.stdout.read(8 << 20)
        if not chunk: break
        h.update(chunk); total += len(chunk)
    if p.wait() != 0: sys.exit("read-back failed")
    print(f"read back {total} bytes in {time.time()-t0:.0f}s")
    if total != b["size"] or h.hexdigest() != b["b3sum"]:
        rclone("deletefile", f"r2:{BUCKET}/{key}", check=False); sys.exit("BLAKE3 read-back mismatch; object deleted")
    print("BLAKE3 of the object in R2 matches the published hash:", b["b3sum"][:16] + "...")

def finalize():
    b = build(); key = objkey(b); date = b["key"][:8]
    base = os.environ["R2_PUBLIC_URL"].rstrip("/"); now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = {"format": 1, "id": "basemap", "object": f"{base}/{key}", "key": key, "size": b["size"],
                "checksums": {"blake3": b["b3sum"], "md5": hexmd5(b)}, "verified": "BLAKE3 read back from R2",
                "source_url": SRC + b["key"], "verified_page": PAGE, "builds_list": "https://maps.protomaps.com/builds",
                "license": "ODbL 1.0 Produced Work (data (c) OpenStreetMap contributors)",
                "attribution": "(c) OpenStreetMap contributors", "protomaps_build_version": b["version"],
                "build_uploaded": b["uploaded"], "mirrored": now}
    definition = {"format": 1, "id": "basemap", "title": "Base map (Protomaps, planet)",
                  "description": "Worldwide vector base map, Protomaps v4 layout, from OpenStreetMap.",
                  "kind": "vector", "transform": "as_is", "layout": "protomaps-v4",
                  "source": {"type": "pmtiles", "url": f"{base}/{key}", "minzoom": 0, "maxzoom": 15, "tile_format": "mvt"},
                  "attribution": "(c) OpenStreetMap contributors",
                  "license": "ODbL 1.0 Produced Work (OpenStreetMap data); Protomaps basemap tiles",
                  "coverage": {"bbox": [-180, -85.0511, 180, 85.0511]}, "offline": {"allowed": True, "limit": None}, "style_hints": {"role": "basemap"},
                  "update": {"version": date, "interval_days": 30},
                  "recipe": "https://github.com/zvx-echo6/cache-packs/blob/main/recipes/basemap-planet.md"}
    with tempfile.TemporaryDirectory() as td:
        def put(obj, dest, ctype, cache):
            p = os.path.join(td, "x.json"); json.dump(obj, open(p, "w"), indent=2)
            rclone("copyto", p, f"r2:{BUCKET}/{dest}", "--header-upload", f"Content-Type: {ctype}", "--header-upload", f"Cache-Control: {cache}")
        put(manifest, key + ".manifest.json", "application/json", "public, max-age=300")
        put(definition, "basemap/basemap.json", "application/json", "public, max-age=300")
        # retention: keep the newest 2 planet builds
        names = sorted(x for x in rclone("lsf", f"r2:{BUCKET}/basemap/", stdout=subprocess.PIPE, text=True).stdout.split()
                       if x.startswith("protomaps-planet-") and x.endswith(".pmtiles"))
        for old in names[:-2]:
            rclone("deletefile", f"r2:{BUCKET}/basemap/{old}"); rclone("deletefile", f"r2:{BUCKET}/basemap/{old}.manifest.json", check=False)
            print("removed", old)
        cp = os.path.join(td, "cat.json")
        r = rclone("copyto", f"r2:{BUCKET}/catalogue.json", cp, check=False)
        cat = json.load(open(cp)) if r.returncode == 0 and os.path.exists(cp) else {"format": 1, "layers": []}
        cat["layers"] = sorted([l for l in cat["layers"] if l["id"] != "basemap"] + [definition], key=lambda l: l["id"])
        cat["updated"] = now
        put(cat, "catalogue.json", "application/json", "public, max-age=300")
    print("finalized", key)

if __name__ == "__main__":
    {"plan": plan, "copy": copy, "verify": verify, "finalize": finalize}[sys.argv[1]]()
