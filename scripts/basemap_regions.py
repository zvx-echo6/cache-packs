#!/usr/bin/env python3
"""Pre-cut base map files, one per catalogue region: the planet's tiles inside the region's outline (scripts/pmtiles_cut.py).

  basemap_regions.py plan --selector SEL                  -> the job matrix (one job per state/province, or per country
                                                            for regions that sit in no single state)
  basemap_regions.py run --selector SEL --group G --planet-version V [--dry-run]
                                                         -> for each region of group G: cut, verify, upload to
                                                            basemap-regions/<V>/<id>.pmtiles, delete the local file,
                                                            and keep basemap-regions/<V>/manifests/<G>.json current

Selector (the same in plan and run):
  ids:us-id-ada-county,us-ut-moab   these regions
  state:us-id,us-ut                 every region of these states/provinces (ISO 3166-2, the records' region_code)
  country:us,ca                     every region in these countries' shards
  na                                country:us,ca,mx,gl

Only these keys are ever written on the bucket: basemap-regions/<V>/<region id>.pmtiles and
basemap-regions/<V>/manifests/<group>.json. Nothing is deleted or overwritten anywhere else; the region lists and the
catalogue are not touched (adding the files to them is a separate publish, see recipes/basemap-regions.md).

Resume: a region is skipped when an earlier manifest under the same prefix lists it and the object on the bucket has that
manifest's size and SHA-256 (stored as the object's sha256 metadata at upload).

Env: R2_PUBLIC_URL (regions and planet are read from it over HTTPS). For uploads also R2_BUCKET, R2_ENDPOINT,
AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (rclone is configured from them; nothing prints them).
"""
import argparse, datetime, hashlib, json, os, re, subprocess, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pmtiles_cut as pc  # noqa: E402

PREFIX = "basemap-regions"
NA = ("us", "ca", "mx", "gl")
UA = {"User-Agent": "cache-packs-basemap-regions"}
MAX_JOBS = 256                       # GitHub's matrix limit
KEY_RE = re.compile(r"^basemap-regions/\d{8}/(?:[a-z0-9][a-z0-9_-]{0,127}\.pmtiles|manifests/[a-z0-9][a-z0-9_-]{0,127}\.json)$")
CACHE = "public, max-age=86400"


def get(url, tries=6):
    err = None
    for t in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return r.read()
        except Exception as e:
            err = e
            time.sleep(2 * (t + 1))
    raise pc.CutError(f"fetching {url} failed: {err}")


# ---------------------------------------------------------------- selection

def parse_selector(sel):
    sel = sel.strip().lower()
    if sel == "na":
        return "country", list(NA)
    m = re.fullmatch(r"(ids|state|country):([a-z0-9_,-]+)", sel)
    if not m:
        raise pc.CutError(f"bad selector {sel!r}: use ids:a,b  state:us-id  country:us  or  na")
    vals = [v for v in m.group(2).split(",") if v]
    if not vals:
        raise pc.CutError("the selector names nothing")
    return m.group(1), vals


def load_shards(base, names):
    """{shard: parsed json} and a SHA-256 over the raw bytes (so the jobs can check they see what the plan saw)."""
    out, hs = {}, hashlib.sha256()
    for n in sorted(set(names)):
        if not re.fullmatch(r"[a-z]{2}|multi", n):
            raise pc.CutError(f"bad shard name {n!r}")
        raw = get(base + n + ".json")
        hs.update(n.encode() + b"\0" + hashlib.sha256(raw).digest())
        out[n] = json.loads(raw)
    return out, hs.hexdigest()


def group_of(rec, shard):
    rc = rec.get("region_code")
    g = rc.lower() if rc else f"{shard}-other"
    return re.sub(r"[^a-z0-9-]", "-", g)


def select(base, selector):
    """[(group, record)] for the selector, sorted by group then id, and the shards' SHA-256."""
    kind, vals = parse_selector(selector)
    if kind == "country":
        names = vals
    elif kind == "state":
        names = [v.split("-")[0] for v in vals]
    else:
        names = [v.split("-")[0] for v in vals]
    shards, sha = load_shards(base, names)
    picked, seen = [], set()
    for shard, doc in shards.items():
        for r in doc.get("regions", []):
            if not r.get("polygon") or r["id"] in seen:
                continue
            ok = (kind == "country") or (kind == "state" and (r.get("region_code") or "").lower() in vals) or (kind == "ids" and r["id"] in vals)
            if ok:
                seen.add(r["id"])
                picked.append((group_of(r, shard), r))
    if kind == "ids":
        missing = sorted(set(vals) - seen)
        if missing:
            raise pc.CutError(f"not in the region lists (or without an outline): {missing}")
    if not picked:
        raise pc.CutError("the selector matches no region")
    for _, r in picked:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", r["id"]):
            raise pc.CutError(f"region id {r['id']!r} cannot be used as an object name")
    picked.sort(key=lambda p: (p[0], p[1]["id"]))
    return picked, sha


def maxzoom_for(rec, mode):
    if mode == "catalogue":
        z = (((rec.get("sizes") or {}).get("map") or {}).get("basemap") or [None, None])[1]
        return int(z) if isinstance(z, int) and 0 <= z <= 15 else 15
    return int(mode)


# ---------------------------------------------------------------- R2 (rclone, configured from the environment)

def rclone_env():
    return dict(os.environ, RCLONE_CONFIG_R2_TYPE="s3", RCLONE_CONFIG_R2_PROVIDER="Cloudflare",
                RCLONE_CONFIG_R2_ACCESS_KEY_ID=os.environ.get("AWS_ACCESS_KEY_ID", ""),
                RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
                RCLONE_CONFIG_R2_ENDPOINT=os.environ.get("R2_ENDPOINT", ""), RCLONE_CONFIG_R2_NO_CHECK_BUCKET="true",
                RCLONE_CONFIG_R2_REGION="auto")


class R2:
    """The only bucket access this job has: list and read under basemap-regions/<version>/, and put the two kinds of key
    KEY_RE allows. There is no delete."""

    def __init__(self, version):
        self.bucket = os.environ.get("R2_BUCKET", "")
        if not self.bucket or not os.environ.get("R2_ENDPOINT") or not os.environ.get("AWS_ACCESS_KEY_ID"):
            raise pc.CutError("R2_BUCKET, R2_ENDPOINT and the R2 keys must be set for an upload run")
        self.version = version
        self.env = rclone_env()

    def _run(self, *a, check=True):
        r = subprocess.run(["rclone", *a], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if check and r.returncode != 0:
            raise pc.CutError(f"rclone {a[0]} failed ({r.returncode}): {r.stderr.strip()[-400:]}")
        return r

    def _path(self, key):
        if not KEY_RE.match(key) or not key.startswith(f"{PREFIX}/{self.version}/"):
            raise pc.CutError(f"refusing a key outside {PREFIX}/{self.version}/: {key}")
        return f"r2:{self.bucket}/{key}"

    def put(self, local, key, ctype, cache, sha256=None):
        args = ["copyto", local, self._path(key), "--header-upload", f"Content-Type: {ctype}",
                "--header-upload", f"Cache-Control: {cache}", "--s3-chunk-size", "64M", "--s3-upload-concurrency", "4"]
        if sha256:
            args += ["-M", "--metadata-set", f"sha256={sha256}"]
        self._run(*args)

    def stat(self, key):
        """{'Size': .., 'Metadata': {..}} of an object, or None."""
        r = self._run("lsjson", "-M", self._path(key), check=False)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        items = json.loads(r.stdout)
        return items[0] if items else None

    def manifests(self):
        """{region id: manifest entry} from every manifest under this version (later uploads win)."""
        base = f"{PREFIX}/{self.version}/manifests/"
        r = self._run("lsf", f"r2:{self.bucket}/{base}", "--files-only", check=False)
        found = {}
        if r.returncode != 0:
            return found
        for name in sorted(r.stdout.split()):
            if not name.endswith(".json"):
                continue
            c = self._run("cat", self._path(base + name), check=False)
            try:
                doc = json.loads(c.stdout)
            except Exception:
                print(f"warning: manifest {name} is not JSON; ignored", flush=True)
                continue
            for e in doc.get("regions", []):
                if e.get("id") and e.get("sha256") and e.get("key"):
                    found[e["id"]] = e
        return found


# ---------------------------------------------------------------- commands

def cmd_plan(a):
    picked, sha = select(a.regions_base, a.selector)
    groups = {}
    for g, _ in picked:
        groups[g] = groups.get(g, 0) + 1
    if len(groups) > MAX_JOBS:
        raise pc.CutError(f"{len(groups)} jobs is above GitHub's matrix limit of {MAX_JOBS}; select less")
    matrix = {"include": [{"group": g, "regions": n} for g, n in sorted(groups.items())]}
    print(f"{len(picked)} regions in {len(groups)} jobs; shards sha256 {sha}")
    for g, n in sorted(groups.items()):
        print(f"  {g}: {n}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"matrix={json.dumps(matrix, separators=(',', ':'))}\nshards_sha={sha}\n")


def cmd_run(a):
    if not re.fullmatch(r"\d{8}", a.planet_version):
        raise pc.CutError("planet version must be the build date, YYYYMMDD")
    if not re.fullmatch(r"[a-z0-9-]{1,64}", a.group):
        raise pc.CutError("bad group")
    picked, sha = select(a.regions_base, a.selector)
    if a.shards_sha and sha != a.shards_sha:
        raise pc.CutError("the region lists changed since the plan; run the workflow again")
    regions = [r for g, r in picked if g == a.group]
    if not regions:
        raise pc.CutError(f"no region of the selector is in group {a.group}")
    index = json.loads(get(a.regions_base + "index.json"))
    poly_base = index.get("polygon_base") or (a.regions_base + "polygons/")
    planet = f"{a.public_url.rstrip('/')}/basemap/protomaps-planet-{a.planet_version}.pmtiles"
    arc = pc.Archive(pc.open_source(planet))
    print(f"planet {planet}: {arc.header['addressed_tiles']} tiles, z{arc.header['min_zoom']}-{arc.header['max_zoom']}", flush=True)
    r2 = None if a.dry_run else R2(a.planet_version)
    earlier = r2.manifests() if r2 else {}
    os.makedirs(a.work, exist_ok=True)
    man_path = os.path.join(a.work, f"manifest-{a.group}.json")
    man_key = f"{PREFIX}/{a.planet_version}/manifests/{a.group}.json"
    manifest = {"format": 1, "prefix": f"{PREFIX}/{a.planet_version}/", "planet": planet, "planet_version": a.planet_version,
                "group": a.group, "selector": a.selector, "shards_sha256": sha, "dry_run": bool(a.dry_run),
                "maxzoom_mode": a.maxzoom, "started": now(), "regions": []}

    def save(final=False):
        manifest["updated"] = now()
        manifest["complete"] = final
        with open(man_path, "w") as f:
            json.dump(manifest, f, indent=1)
        if r2:
            r2.put(man_path, man_key, "application/json", "no-cache")

    done = 0
    try:
        for rec in regions:
            rid = rec["id"]
            key = f"{PREFIX}/{a.planet_version}/{rid}.pmtiles"
            prev = earlier.get(rid)
            if r2 and prev and prev.get("key") == key:
                st = r2.stat(key)
                if st and st.get("Size") == prev.get("bytes") and (st.get("Metadata") or {}).get("sha256") == prev["sha256"]:
                    manifest["regions"].append(dict(prev, skipped=True))
                    print(f"{rid}: already on the bucket with sha256 {prev['sha256'][:12]}; skipped", flush=True)
                    continue
            geo = json.loads(get(poly_base + rid + ".geojson"))
            rings = pc.outline_rings(geo, rec.get("bbox"))
            maxz = maxzoom_for(rec, a.maxzoom)
            out = os.path.join(a.work, rid + ".pmtiles")
            t0 = time.time()
            facts = pc.cut_region(arc, rings, out, 0, maxz, samples=a.samples)
            if r2:
                r2.put(out, key, "application/vnd.pmtiles", CACHE, sha256=facts["sha256"])
                st = r2.stat(key)
                if not st or st.get("Size") != facts["bytes"] or (st.get("Metadata") or {}).get("sha256") != facts["sha256"]:
                    raise pc.CutError(f"{rid}: the uploaded object does not have the file's size and sha256")
            os.remove(out)
            entry = {"id": rid, "key": key, "bytes": facts["bytes"], "sha256": facts["sha256"], "tiles": facts["tiles"],
                     "seconds": round(time.time() - t0, 2), "minzoom": 0, "maxzoom": maxz, "bounds": facts["bounds"],
                     "cover_tiles": facts["cover_tiles"], "sampled": facts["verified"]["sampled"], "uploaded": bool(r2)}
            manifest["regions"].append(entry)
            done += 1
            print(json.dumps(entry), flush=True)
            if done % 10 == 0:
                save()
    except Exception:
        try:
            save()
        finally:
            print(f"stopped after {done} regions; manifest so far in {man_path}", flush=True)
        raise
    save(final=True)
    total = sum(e["bytes"] for e in manifest["regions"])
    print(f"group {a.group}: {len(manifest['regions'])} regions ({done} cut), {total} bytes; read {arc.src.fetched} bytes "
          f"in {arc.src.requests} requests", flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(f"### {a.group}\n\n| region | bytes | tiles | seconds | sha256 |\n|---|---|---|---|---|\n")
            for e in manifest["regions"]:
                f.write(f"| {e['id']} | {e['bytes']} | {e['tiles']} | {e['seconds']}{' (skipped)' if e.get('skipped') else ''} | {e['sha256'][:16]} |\n")


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    pub = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--regions-base", default=(pub + "/regions/") if pub else None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--selector", required=True)
    r = sub.add_parser("run")
    r.add_argument("--selector", required=True)
    r.add_argument("--group", required=True)
    r.add_argument("--planet-version", required=True)
    r.add_argument("--public-url", default=pub)
    r.add_argument("--shards-sha", default="")
    r.add_argument("--work", default="work")
    r.add_argument("--samples", type=int, default=64)
    r.add_argument("--maxzoom", default="15", help="15 (or another zoom) for every region, or 'catalogue' for the zoom the region list sizes the base map at")
    r.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.regions_base:
        sys.exit("error: set R2_PUBLIC_URL or --regions-base")
    if not a.regions_base.endswith("/"):
        a.regions_base += "/"
    if a.cmd == "run":
        if not a.public_url:
            sys.exit("error: set R2_PUBLIC_URL or --public-url")
        if a.maxzoom != "catalogue" and not (a.maxzoom.isdigit() and 0 <= int(a.maxzoom) <= 15):
            sys.exit("error: --maxzoom is 0 to 15 or 'catalogue'")
    {"plan": cmd_plan, "run": cmd_run}[a.cmd](a)


if __name__ == "__main__":
    try:
        main()
    except pc.CutError as e:
        sys.exit(f"error: {e}")
