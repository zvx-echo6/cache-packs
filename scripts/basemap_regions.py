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
import argparse, concurrent.futures as cf, datetime, hashlib, json, math, os, re, subprocess, sys, threading, time, urllib.request

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


# ---------------------------------------------------------------- time estimates and job split

# The model the plan sizes jobs by. Calibrated on the first dry run (2026-11-14: about 0.25 s per range request from a
# runner, about 20 range requests a small region) and the measurement's 20 to 45 MB/s; all on the slow side on purpose.
FIXED_S = 2.5            # per region, cut and verify: outline fetch, its directory pages, merged tile and verify reads
FIXED_UPLOAD_S = 3.0     # per region when uploading: rclone start, PUT, the size/sha256 check
READ_BPS = 40e6          # planet range reads, per job
UPLOAD_BPS = 40e6        # uploads to R2, per job
ZOOM_STEP = 2.7          # bytes to one zoom deeper (z0..z15 over z0..z14), for regions the list sizes below z15
SPLIT_S = 4 * 3600       # a job estimated above this is split into parts
PARALLEL = 4             # regions cut at once in a job


def density(picked):
    """Bytes per km² of the base map at z15 over the box, from the sized regions of the selection (fallback for the few the
    region list has no size for)."""
    b = a = 0.0
    for _, r in picked:
        s = (((r.get("sizes") or {}).get("map") or {}).get("basemap"))
        if s and s[1] == 15 and (r.get("area_km2") or 0) >= 1000:
            b += s[0]
            a += r["area_km2"]
    return b / a if a else 2500.0


def est_bytes(rec, maxz, dens):
    s = (((rec.get("sizes") or {}).get("map") or {}).get("basemap"))
    if s and isinstance(s[0], (int, float)) and isinstance(s[1], int):
        return float(s[0]) * ZOOM_STEP ** (maxz - s[1])
    return float(rec.get("area_km2") or 0) * dens * ZOOM_STEP ** (maxz - 15)


def est_seconds(sizes, dry, parallel=PARALLEL):
    if not sizes:
        return 0.0
    fixed = FIXED_S + (0 if dry else FIXED_UPLOAD_S)
    per_byte = 1 / READ_BPS + (0 if dry else 1 / UPLOAD_BPS)
    return max(len(sizes) * fixed / parallel + sum(sizes) * per_byte, fixed + max(sizes) * per_byte)


def jobs_for(picked, maxzoom, dry):
    """[(group, part, parts, [records], est seconds, est bytes)], a group split into as many parts as it needs to stay under
    SPLIT_S (largest regions first, each to the least loaded part). The same input gives the same split in plan and run."""
    dens = density(picked)
    groups = {}
    for g, r in picked:
        groups.setdefault(g, []).append((r, est_bytes(r, maxzoom_for(r, maxzoom), dens)))
    out = []
    for g in sorted(groups):
        items = groups[g]
        total = est_seconds([b for _, b in items], dry)
        parts = max(1, min(len(items), math.ceil(total / SPLIT_S)))
        while True:
            bins = [[] for _ in range(parts)]
            load = [0.0] * parts
            for r, b in sorted(items, key=lambda x: (-x[1], x[0]["id"])):
                k = min(range(parts), key=lambda i: (load[i], i))
                bins[k].append((r, b))
                load[k] += b + FIXED_S * READ_BPS / PARALLEL      # fixed time as bytes, so counts balance too
            ests = [est_seconds([b for _, b in x], dry) for x in bins]
            if max(ests) <= SPLIT_S or parts >= len(items):
                break
            parts += 1
        for i, x in enumerate(bins):
            out.append((g, i + 1, parts, sorted((r for r, _ in x), key=lambda r: r["id"]), ests[i], sum(b for _, b in x)))
    return out


def job_name(g, part, parts):
    return g if parts == 1 else f"{g}-part{part}-of{parts}"


# ---------------------------------------------------------------- commands

def cmd_plan(a):
    picked, sha = select(a.regions_base, a.selector)
    jobs = jobs_for(picked, a.maxzoom, a.dry_run)
    if len(jobs) > MAX_JOBS:
        raise pc.CutError(f"{len(jobs)} jobs is above GitHub's matrix limit of {MAX_JOBS}; select less")
    matrix = {"include": [{"group": g, "part": p, "parts": n, "regions": len(rs), "est_minutes": round(s / 60)}
                          for g, p, n, rs, s, _ in jobs]}
    wall = sum(s for *_, s, _ in jobs)
    print(f"{len(picked)} regions in {len(jobs)} jobs ({'dry run' if a.dry_run else 'with upload'}, maxzoom {a.maxzoom}); "
          f"estimated {sum(b for *_, b in jobs) / 1e9:.1f} GB, {wall / 3600:.1f} job-hours, "
          f"about {wall / 3600 / 8:.1f} h at 8 jobs at once (the longest job sets a floor of {max(s for *_, s, _ in jobs) / 3600:.1f} h)")
    print(f"shards sha256 {sha}")
    for g, p, n, rs, s, b in sorted(jobs, key=lambda j: -j[4]):
        print(f"  {job_name(g, p, n):24s} {len(rs):5d} regions  {b / 1e9:7.2f} GB  est {s / 60:6.0f} min")
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
    mine = [j for j in jobs_for(picked, a.maxzoom, a.dry_run) if j[0] == a.group]
    if not mine:
        raise pc.CutError(f"no region of the selector is in group {a.group}")
    if mine[0][2] != a.parts or not 1 <= a.part <= a.parts:
        raise pc.CutError(f"group {a.group} has {mine[0][2]} parts now, not {a.parts}; run the workflow again")
    _, _, _, regions, est_s, est_b = mine[a.part - 1]
    name = job_name(a.group, a.part, a.parts)
    print(f"job {name}: {len(regions)} regions, estimated {est_b / 1e9:.2f} GB and {est_s / 60:.0f} min "
          f"({a.parallel} at once)", flush=True)
    index = json.loads(get(a.regions_base + "index.json"))
    poly_base = index.get("polygon_base") or (a.regions_base + "polygons/")
    planet = f"{a.public_url.rstrip('/')}/basemap/protomaps-planet-{a.planet_version}.pmtiles"
    arc = pc.Archive(pc.open_source(planet))
    print(f"planet {planet}: {arc.header['addressed_tiles']} tiles, z{arc.header['min_zoom']}-{arc.header['max_zoom']}", flush=True)
    r2 = None if a.dry_run else R2(a.planet_version)
    earlier = r2.manifests() if r2 else {}
    os.makedirs(a.work, exist_ok=True)
    disk = pc.DiskBudget(a.work)
    man_path = os.path.join(a.work, f"manifest-{name}.json")
    man_key = f"{PREFIX}/{a.planet_version}/manifests/{name}.json"
    manifest = {"format": 1, "prefix": f"{PREFIX}/{a.planet_version}/", "planet": planet, "planet_version": a.planet_version,
                "group": a.group, "part": a.part, "parts": a.parts, "selector": a.selector, "shards_sha256": sha,
                "dry_run": bool(a.dry_run), "maxzoom_mode": a.maxzoom, "planned_regions": len(regions),
                "estimate_minutes": round(est_s / 60), "started": now(), "regions": []}
    lock = threading.Lock()
    stop = threading.Event()

    def save(final=False):
        with lock:
            manifest["regions"].sort(key=lambda e: e["id"])
            manifest["updated"] = now()
            manifest["complete"] = final
            with open(man_path, "w") as f:
                json.dump(manifest, f, indent=1)
        if r2:
            r2.put(man_path, man_key, "application/json", "no-cache")

    def one(rec):
        if stop.is_set():
            return None
        rid = rec["id"]
        key = f"{PREFIX}/{a.planet_version}/{rid}.pmtiles"
        prev = earlier.get(rid)
        if r2 and prev and prev.get("key") == key:
            st = r2.stat(key)
            if st and st.get("Size") == prev.get("bytes") and (st.get("Metadata") or {}).get("sha256") == prev["sha256"]:
                print(f"{rid}: already on the bucket with sha256 {prev['sha256'][:12]}; skipped", flush=True)
                return dict(prev, skipped=True)
        t0 = time.time()
        geo = json.loads(get(poly_base + rid + ".geojson"))
        rings = pc.outline_rings(geo, rec.get("bbox"))
        maxz = maxzoom_for(rec, a.maxzoom)
        out = os.path.join(a.work, rid + ".pmtiles")
        facts = pc.cut_region(arc, rings, out, 0, maxz, samples=a.samples, disk=disk)
        try:
            if r2:
                r2.put(out, key, "application/vnd.pmtiles", CACHE, sha256=facts["sha256"])
                st = r2.stat(key)
                if not st or st.get("Size") != facts["bytes"] or (st.get("Metadata") or {}).get("sha256") != facts["sha256"]:
                    raise pc.CutError(f"{rid}: the uploaded object does not have the file's size and sha256")
        finally:
            if os.path.exists(out):
                os.remove(out)
            disk.release(facts["bytes"])
        return {"id": rid, "key": key, "bytes": facts["bytes"], "sha256": facts["sha256"], "tiles": facts["tiles"],
                "seconds": round(time.time() - t0, 2), "stages": facts["seconds"], "requests": facts["requests"],
                "minzoom": 0, "maxzoom": maxz, "bounds": facts["bounds"], "cover_tiles": facts["cover_tiles"],
                "sampled": facts["verified"]["sampled"], "uploaded": bool(r2)}

    done, failure = 0, None
    t_job = time.time()
    with cf.ThreadPoolExecutor(max(1, a.parallel)) as ex:
        futs = [ex.submit(one, r) for r in regions]
        for f in cf.as_completed(futs):
            try:
                e = f.result()
            except BaseException as err:     # stop taking new regions; the ones running finish and are recorded
                if failure is None:
                    failure = err
                    stop.set()
                    for x in futs:
                        x.cancel()
                    print(f"error: {err}", flush=True)
                continue
            if e is None:
                continue
            with lock:
                manifest["regions"].append(e)
            if not e.get("skipped"):
                done += 1
                print(json.dumps(e), flush=True)
                if done % 10 == 0:
                    save()
    if failure is not None:
        save()
        print(f"stopped after {done} regions; manifest so far in {man_path}", flush=True)
        raise failure
    save(final=True)
    total = sum(e["bytes"] for e in manifest["regions"])
    took = time.time() - t_job
    print(f"job {name}: {len(manifest['regions'])} regions ({done} cut), {total} bytes in {took / 60:.1f} min "
          f"(estimated {est_s / 60:.0f}); read {arc.src.fetched} bytes in {arc.src.requests} requests, "
          f"{arc.page_reads} directory pages", flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(f"### {name}\n\n{len(regions)} regions planned, estimated {est_s / 60:.0f} min, took {took / 60:.1f} min\n\n"
                    "| region | bytes | tiles | seconds | requests (dir/tiles/verify) | sha256 |\n|---|---|---|---|---|---|\n")
            for e in manifest["regions"]:
                rq = e.get("requests") or {}
                f.write(f"| {e['id']} | {e['bytes']} | {e['tiles']} | {e['seconds']}{' (skipped)' if e.get('skipped') else ''} | "
                        f"{rq.get('directory', '')}/{rq.get('tiles', '')}/{rq.get('verify', '')} | {e['sha256'][:16]} |\n")


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    pub = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--regions-base", default=(pub + "/regions/") if pub else None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--selector", required=True)
    p.add_argument("--maxzoom", default="15")
    p.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--selector", required=True)
    r.add_argument("--group", required=True)
    r.add_argument("--part", type=int, default=1)
    r.add_argument("--parts", type=int, default=1)
    r.add_argument("--parallel", type=int, default=PARALLEL, help="regions cut at once (disk permitting)")
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
    if a.maxzoom != "catalogue" and not (a.maxzoom.isdigit() and 0 <= int(a.maxzoom) <= 15):
        sys.exit("error: --maxzoom is 0 to 15 or 'catalogue'")
    if a.cmd == "run":
        if not a.public_url:
            sys.exit("error: set R2_PUBLIC_URL or --public-url")
    {"plan": cmd_plan, "run": cmd_run}[a.cmd](a)


if __name__ == "__main__":
    try:
        main()
    except pc.CutError as e:
        sys.exit(f"error: {e}")
