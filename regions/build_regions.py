#!/usr/bin/env python3
"""Builds the region catalogue.

Backbone: Overture Maps divisions theme (division + division_area, GeoParquet on the official S3 bucket, read with DuckDB).
Added from the US Census Bureau: metropolitan and micropolitan statistical areas (CBSAs) and, where Overture has no
population, county populations. Informal regions come from regions/informal.yaml (unions of catalogue regions).

Output (under --out):
  regions/index.json            top level: countries/dependencies, multi-country regions, shard list
  regions/<iso>.json            one shard per country code: records + nested tree
  regions/<iso>.search.json     search entries of that shard
  regions/search-global.json    countries, regions, informal regions, metros and big cities
  regions/polygons/<id>.geojson one simplified polygon per region
  regions/manifest.json         sources, licences, dates, counts, file sizes
"""
import argparse, collections, csv, datetime, hashlib, json, math, os, re, subprocess, sys, unicodedata, zipfile
import duckdb, numpy as np, shapefile, shapely, yaml
from shapely import wkb
from shapely.geometry import mapping, shape, Point
from shapely.ops import unary_union
from shapely.strtree import STRtree

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "cache-packs-regions/1.0 (https://github.com/zvx-echo6/cache-packs)"
CENSUS_YEAR = 2024
CB_BASE = f"https://www2.census.gov/geo/tiger/GENZ{CENSUS_YEAR}/shp"
POP_BASE = f"https://www2.census.gov/programs-surveys/popest/datasets/2020-{CENSUS_YEAR}"
SUBTYPE_RANK = {"country": 0, "dependency": 1, "region": 2, "county": 3, "locality": 4}
UPPER = ("country", "dependency", "region")          # these keep the full multilingual names map
INFORMAL_KINDS = {"multi_state", "multi_country", "sub_state", "valley", "metro_alias"}
MULTI_KINDS = {"multi_state", "multi_country"}
R_KM = 6371.0088


def log(*a):
    print(*a, flush=True)


def slug(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s


def sphere_area_km2(g):
    """Spherical polygon area (Chamberlain-Duquette); accurate to well under 1% at these sizes."""
    total = 0.0
    for poly in shapely.get_parts(g):
        if poly.geom_type != "Polygon":
            continue
        for i, ring in enumerate([poly.exterior, *poly.interiors]):
            c = np.asarray(ring.coords)
            lon, lat = np.radians(c[:, 0]), np.radians(c[:, 1])
            s = np.sum((np.roll(lon, -1) - lon) * (2 + np.sin(lat) + np.sin(np.roll(lat, -1)))) * 0.5 * R_KM * R_KM
            total += abs(s) if i == 0 else -abs(s)
    return max(total, 0.0)


def round_area(a):
    return round(a, 2) if a < 10 else round(a, 1) if a < 1000 else round(a)


def bbox_of(g):
    return [round(v, 5) for v in g.bounds]


def simplify_geom(g):
    w = g.bounds
    ext = max(w[2] - w[0], w[3] - w[1])
    tol = min(0.01, max(0.00015, ext * 0.0004))
    s = shapely.simplify(g, tol, preserve_topology=True)
    try:
        s = shapely.set_precision(s, 1e-5)
    except Exception:
        s = shapely.make_valid(s)
    if s.is_empty:
        s = g
    return s


def rnd(c):
    if isinstance(c[0], (int, float)):
        return [round(c[0], 5), round(c[1], 5)]
    return [rnd(x) for x in c]


def write_polygon(outdir, rid, name, g):
    s = simplify_geom(g)
    m = mapping(s)
    feat = {"type": "Feature", "properties": {"id": rid, "name": name},
            "geometry": {"type": m["type"], "coordinates": rnd(m["coordinates"])}}
    path = os.path.join(outdir, "polygons", rid + ".geojson")
    with open(path, "w") as f:
        json.dump(feat, f, separators=(",", ":"), ensure_ascii=False)
    return os.path.getsize(path)


def curl(url, dest):
    subprocess.run(["curl", "-fL", "--retry", "5", "--retry-delay", "10", "--retry-all-errors", "-sS", "-A", UA, "-o", dest, url], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--countries", default="", help="test filter: comma separated ISO codes")
    ap.add_argument("--public-url", default="")
    a = ap.parse_args()
    countries = [c.strip().upper() for c in a.countries.split(",") if c.strip()]
    for c in countries:
        assert re.fullmatch(r"[A-Z]{2}", c)
    out = os.path.join(a.out, "regions")
    os.makedirs(os.path.join(out, "polygons"), exist_ok=True)
    os.makedirs(a.work, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)

    informal = yaml.safe_load(open(os.path.join(HERE, "informal.yaml"))) or {}
    informal_regions = informal.get("regions") or []
    alias_doc = yaml.safe_load(open(os.path.join(HERE, "aliases.yaml"))) or {}
    extra_alias = alias_doc.get("aliases") or {}
    need_full = {m for r in informal_regions for m in r.get("members", [])}

    # ---------------------------------------------------------------- Overture divisions
    base = f"s3://overturemaps-us-west-2/release/{a.release}/theme=divisions"
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial; SET s3_region='us-west-2'; SET threads=4; SET memory_limit='5GB';")
    D = f"read_parquet('{base}/type=division/*', hive_partitioning=1)"
    A = f"read_parquet('{base}/type=division_area/*', hive_partitioning=1)"
    cf = f"AND country IN ({','.join(repr(c) for c in countries)})" if countries else ""
    log("selecting divisions")
    con.execute(f"""
      CREATE TABLE sel AS SELECT id, country, subtype, class, region, admin_level, parent_division_id, population, wikidata,
        names.primary AS pname, element_at(names.common,'en')[1] AS en,
        CASE WHEN subtype IN ('country','dependency','region') THEN map_keys(names.common) END AS nk,
        CASE WHEN subtype IN ('country','dependency','region') THEN map_values(names.common) END AS nv,
        list_transform(hierarchies, h -> list_transform(h, x -> x.division_id)) AS hier,
        (capital_of_divisions IS NOT NULL AND len(capital_of_divisions) > 0) AS is_cap,
        ST_X(geometry) AS lon, ST_Y(geometry) AS lat,
        list_distinct(list_transform(sources, x -> coalesce(x.dataset,'?') || '|' || coalesce(x.license,'?'))) AS srcs
      FROM {D}
      WHERE subtype IN ('country','dependency','region','county','locality') {cf}
        AND (subtype <> 'locality' OR class IN ('megacity','city','town') OR population >= 5000
             OR (capital_of_divisions IS NOT NULL AND len(capital_of_divisions) > 0))
    """)
    log("selected", con.execute("SELECT count(*) FROM sel").fetchone()[0])
    con.execute(f"CREATE TABLE has_area AS SELECT DISTINCT division_id FROM {A} WHERE is_land {cf}")
    rows = con.execute("""SELECT s.*, (h.division_id IS NOT NULL) AS has_area FROM sel s LEFT JOIN has_area h ON h.division_id = s.id""").fetchall()
    cols = [d[0] for d in con.description]
    rows = [dict(zip(cols, r)) for r in rows]
    no_area = collections.Counter(r["subtype"] for r in rows if not r["has_area"])
    rows = [r for r in rows if r["has_area"]]
    log("with land polygon:", len(rows), "dropped for no polygon:", dict(no_area))

    # ids ------------------------------------------------------------------
    byg = {r["id"]: r for r in rows}
    for r in rows:
        r["name"] = (r["en"] or r["pname"] or "").strip()
        if " / " in r["name"] and not r["en"]:
            r["name"] = r["name"].split(" / ")[-1].strip()
        r["local"] = r["pname"] if r["pname"] and r["pname"] != r["name"] else None

    def candidate(r):
        cc = r["country"].lower()
        if r["subtype"] in ("country", "dependency"):
            return cc
        prefix = r["region"].lower() if r["region"] else cc
        if r["subtype"] == "region" and r["region"]:
            return prefix
        sl = slug(r["name"]) or slug(r["pname"]) or (r["wikidata"] or "").lower() or r["id"][:8]
        return f"{prefix}-{sl}"

    groups = collections.defaultdict(list)
    for r in rows:
        groups[candidate(r)].append(r)
    for cid, g in groups.items():
        g.sort(key=lambda r: (SUBTYPE_RANK[r["subtype"]], -(r["population"] or 0), int((r["wikidata"] or "Q999999999")[1:] or 0), r["id"]))
        for i, r in enumerate(g):
            if i == 0:
                r["rid"] = cid
                continue
            suf = (r["wikidata"] or "").lower()
            if not suf or any((o.get("wikidata") or "").lower() == suf for o in g if o is not r):
                suf = f"{round(r['lat'], 2):g}_{round(r['lon'], 2):g}".replace("-", "m").replace(".", "p") if r["lat"] is not None else r["id"][:8]
            r["rid"] = f"{cid}-{suf}"
    seen = {}
    for r in rows:
        if r["rid"] in seen:   # last resort: GERS id fragment
            r["rid"] = f"{r['rid']}-{r['id'][:8]}"
        seen[r["rid"]] = r
    assert len(seen) == len(rows)
    rid_of = {r["id"]: r["rid"] for r in rows}

    # geographic parents: nearest selected ancestor in each hierarchy
    for r in rows:
        ps = []
        for h in r["hier"] or []:
            anc = [x for x in h if x != r["id"]]
            for x in reversed(anc):
                if x in rid_of:
                    if rid_of[x] not in ps:
                        ps.append(rid_of[x])
                    break
        r["parents"] = ps
    # a country-level node must have no parent; other nodes without one hang from their country
    ccid = {r["country"]: r["rid"] for r in rows if r["subtype"] == "country"}
    for r in rows:
        if r["subtype"] == "country":
            r["parents"] = []
        elif not r["parents"] and r["country"] in ccid and ccid[r["country"]] != r["rid"]:
            r["parents"] = [ccid[r["country"]]]

    # ---------------------------------------------------------------- US Census population fallbacks
    cen_dir = os.path.join(a.work, "census")
    os.makedirs(cen_dir, exist_ok=True)
    us_present = (not countries) or "US" in countries
    cen_county, cen_state, cbsa_pop = {}, {}, {}
    if us_present:
        curl(f"{POP_BASE}/counties/totals/co-est{CENSUS_YEAR}-alldata.csv", os.path.join(cen_dir, "co.csv"))
        for row in csv.DictReader(open(os.path.join(cen_dir, "co.csv"), encoding="latin-1")):
            key = (row["STNAME"].lower(), row["CTYNAME"].lower())
            v = int(row[f"POPESTIMATE{CENSUS_YEAR}"])
            if row["SUMLEV"] == "040":
                cen_state[row["STNAME"].lower()] = v
            elif row["SUMLEV"] == "050":
                cen_county[key] = v
        curl(f"{POP_BASE}/metro/totals/cbsa-est{CENSUS_YEAR}-alldata.csv", os.path.join(cen_dir, "cbsa.csv"))
        for row in csv.DictReader(open(os.path.join(cen_dir, "cbsa.csv"), encoding="latin-1")):
            if row.get("LSAD", "").endswith("Statistical Area") and not row.get("MDIV"):
                cbsa_pop[row["CBSA"]] = int(row[f"POPESTIMATE{CENSUS_YEAR}"])
        log("census pop: counties", len(cen_county), "states", len(cen_state), "cbsa", len(cbsa_pop))
    st_name = {r["rid"]: (r["name"].lower()) for r in rows if r["country"] == "US" and r["subtype"] == "region"}
    cen_used = cen_miss = 0
    for r in rows:
        r["pop_src"] = "overture" if r["population"] else None
        if r["country"] != "US" or r["population"]:
            continue
        if r["subtype"] == "region" and r["name"].lower() in cen_state:
            r["population"], r["pop_src"] = cen_state[r["name"].lower()], f"census-{CENSUS_YEAR}"
        elif r["subtype"] == "county":
            sn = next((st_name[p] for p in r["parents"] if p in st_name), None)
            v = cen_county.get((sn, r["pname"].lower())) or cen_county.get((sn, r["name"].lower()))
            if v:
                r["population"], r["pop_src"] = v, f"census-{CENSUS_YEAR}"
                cen_used += 1
            else:
                cen_miss += 1
    log("US county population from Census:", cen_used, "unmatched:", cen_miss)

    # ---------------------------------------------------------------- polygons (streamed)
    sizes = {}
    full = {}
    us_county_pts = {}
    rec = {}
    log("streaming polygons")
    cur = con.execute(f"""SELECT division_id, ST_AsWKB(geometry) FROM {A}
                          WHERE is_land {cf} AND division_id IN (SELECT id FROM sel)""")
    done = 0
    while True:
        batch = cur.fetchmany(400)
        if not batch:
            break
        for gid, blob in batch:
            r = byg.get(gid)
            if r is None:
                continue
            g = wkb.loads(bytes(blob))
            if g.is_empty:
                continue
            if not g.is_valid:
                g = shapely.make_valid(g)
            rid = r["rid"]
            if rid in need_full:
                full[rid] = g
            if r["country"] == "US" and r["subtype"] == "county":
                us_county_pts[rid] = g.representative_point()
            r["bbox"] = bbox_of(g)
            r["area"] = sphere_area_km2(g)
            sizes[rid] = write_polygon(out, rid, r["name"], g)
            done += 1
            if done % 10000 == 0:
                log("polygons", done)
    log("polygons written", done)
    rows = [r for r in rows if "bbox" in r]
    gone = {r["rid"] for r in rows}

    records = {}
    for r in rows:
        k = r["subtype"]
        d = {"id": r["rid"], "name": r["name"], "kind": k}
        if r["local"]:
            d["local_name"] = r["local"]
        if r["class"]:
            d["class"] = r["class"]
        d["country"] = r["country"]
        if r["region"]:
            d["region_code"] = r["region"]
        d["parents"] = [p for p in r["parents"] if p in gone] or ([ccid[r["country"]]] if k != "country" and r["country"] in ccid and ccid[r["country"]] in gone and ccid[r["country"]] != r["rid"] else [])
        d["bbox"] = r["bbox"]
        d["area_km2"] = round_area(r["area"])
        if r["population"]:
            d["population"] = r["population"]
            d["population_source"] = r["pop_src"]
        if r["admin_level"] is not None:
            d["admin_level"] = r["admin_level"]
        if r["wikidata"]:
            d["wikidata"] = r["wikidata"]
        d["gers_id"] = r["id"]
        d["polygon"] = f"polygons/{r['rid']}.geojson"
        if r["nk"]:
            d["names"] = dict(sorted(dict(zip(r["nk"], r["nv"])).items()))
        records[r["rid"]] = d
        r["rec"] = d
    ctx = {}   # "Twin Falls County, Idaho" style context for search
    for rid, d in records.items():
        par = d["parents"][0] if d["parents"] else None
        chain = []
        while par and len(chain) < 2:
            chain.append(records[par]["name"] if par in records else par)
            par = records[par]["parents"][0] if par in records and records[par]["parents"] else None
        ctx[rid] = ", ".join(chain)

    # ---------------------------------------------------------------- informal regions
    kids = collections.defaultdict(list)
    for rid, d in records.items():
        for p in d["parents"]:
            kids[p].append(rid)

    def coverage_of(rid, memo={}):
        if rid in memo:
            return memo[rid]
        s = {rid}
        for c in kids.get(rid, []):
            if records[c]["kind"] in ("region", "county", "dependency"):
                s |= coverage_of(c)
        memo[rid] = s
        return s

    inf = {}
    for ir in informal_regions:
        iid = ir["id"]
        assert re.fullmatch(r"[a-z0-9-]+", iid), iid
        assert ir["kind"] in INFORMAL_KINDS, (iid, ir["kind"])
        assert iid not in records, f"informal id collides with a catalogue id: {iid}"
        missing = [m for m in ir["members"] if m not in full]
        if missing:
            sys.exit(f"informal region {iid}: members not in the catalogue or without a polygon: {missing}")
        mem = ir["members"]
        cov = set()
        for m in mem:
            cov |= coverage_of(m)
        inf[iid] = {"def": ir, "cov": cov, "members": mem}
    # a metro_alias etc. needs the same treatment as other informal regions
    for iid, x in inf.items():
        ir = x["def"]
        g = unary_union([full[m] for m in x["members"]])
        if g.geom_type in ("Polygon", "MultiPolygon"):
            polys = [p for p in shapely.get_parts(g)]
            keep = []
            for p in polys:   # drop slivers inside the union (shared borders that did not dissolve)
                holes = [h for h in p.interiors if shapely.Polygon(h).area > 2e-5]
                keep.append(shapely.Polygon(p.exterior, holes))
            g = unary_union(keep)
        mrecs = [records[m] for m in x["members"]]
        ccs = sorted({m["country"] for m in mrecs})
        regs = sorted({m.get("region_code") or m["id"] for m in mrecs if m["kind"] in ("county", "region") and (m["kind"] == "county")})
        pops = [m.get("population") for m in mrecs]
        d = {"id": iid, "name": ir["name"], "kind": ir["kind"], "country": ccs[0] if len(ccs) == 1 else "XX",
             "countries": ccs, "parents": [], "bbox": bbox_of(g), "area_km2": round_area(sphere_area_km2(g)),
             "members": x["members"], "polygon": f"polygons/{iid}.geojson"}
        if all(pops):
            d["population"] = sum(pops)
            d["population_source"] = "sum of members"
        elif any(pops):
            d["population_partial"] = sum(p for p in pops if p)
        d["description"] = ir.get("description", "")
        d["source"] = ir.get("source", "")
        sizes[iid] = write_polygon(out, iid, ir["name"], g)
        x["rec"] = d
        x["ccs"] = ccs
    # parents
    for iid, x in inf.items():
        d, ir = x["rec"], x["def"]
        multi = ir["kind"] in MULTI_KINDS
        geo = []
        if len(x["ccs"]) == 1:
            mem_par = {records[m]["parents"][0] if records[m]["parents"] else None for m in x["members"] if records[m]["kind"] == "county"}
            mem_par.discard(None)
            if not multi and len(mem_par) == 1 and mem_par <= set(records) and records[next(iter(mem_par))]["kind"] == "region":
                geo = [next(iter(mem_par))]
            elif x["ccs"][0] in ccid:
                geo = [ccid[x["ccs"][0]]]
        else:
            geo = [ccid[c] for c in x["ccs"] if c in ccid]
        sup = [j for j, y in inf.items() if j != iid and (y["def"]["kind"] in MULTI_KINDS) == multi
               and x["cov"] < y["cov"]]
        mins = [j for j in sup if not any(inf[k]["cov"] < inf[j]["cov"] for k in sup if k != j)]
        d["parents"] = geo + mins
        d["informal_parents"] = mins
        al = ir.get("aliases") or []
        x["alias"] = al
    # members' parents (counties get minimal non-multi informal regions; regions get minimal multi ones)
    for rid, d in records.items():
        if d["kind"] not in ("county", "region"):
            continue
        want_multi = d["kind"] == "region"
        cand = [j for j, y in inf.items() if (y["def"]["kind"] in MULTI_KINDS) == want_multi and rid in y["cov"]
                and (want_multi or True)]
        if d["kind"] == "region" or d["kind"] == "county":
            mins = [j for j in cand if not any(inf[k]["cov"] < inf[j]["cov"] for k in cand if k != j)]
            if mins:
                d["parents"] = d["parents"] + [m for m in mins if m not in d["parents"]]
                d["informal_parents"] = mins
    for iid, x in inf.items():
        records[iid] = x["rec"]

    # ---------------------------------------------------------------- US metros (Census CBSA)
    metros = {}
    if us_present:
        zp = os.path.join(cen_dir, "cbsa.zip")
        curl(f"{CB_BASE}/cb_{CENSUS_YEAR}_us_cbsa_500k.zip", zp)
        sd = os.path.join(cen_dir, "cbsa")
        zipfile.ZipFile(zp).extractall(sd)
        sf = shapefile.Reader(os.path.join(sd, f"cb_{CENSUS_YEAR}_us_cbsa_500k"))
        flds = [f[0] for f in sf.fields[1:]]
        cbs = []
        for sr in sf.iterShapeRecords():
            row = dict(zip(flds, sr.record))
            g = shape(sr.shape.__geo_interface__)
            if not g.is_valid:
                g = shapely.make_valid(g)
            cbs.append((row, g))
        log("cbsa polygons", len(cbs))
        cty_ids = list(us_county_pts)
        cty_tree = STRtree([us_county_pts[c] for c in cty_ids])
        loc_ids = [r["rid"] for r in rows if r["country"] == "US" and r["subtype"] == "locality" and r["lat"] is not None]
        loc_pts = [Point(byg_r["lon"], byg_r["lat"]) for byg_r in (seen[i] for i in loc_ids)]
        loc_tree = STRtree(loc_pts)
        used = set(records) | set(inf)
        for row, g in cbs:
            micro = row.get("LSAD") == "M2"
            nm = row["NAME"]
            mid = f"us-{'micro' if micro else 'metro'}-{slug(nm)}"
            assert mid not in used, mid
            used.add(mid)
            cties = [cty_ids[i] for i in cty_tree.query(g, predicate="contains")]
            states = []
            for c in cties:
                for p in records[c]["parents"]:
                    if records[p]["kind"] == "region" and p not in states:
                        states.append(p)
            ar = sphere_area_km2(g)
            d = {"id": mid, "name": nm, "kind": "metro", "class": "micropolitan" if micro else "metropolitan",
                 "country": "US", "parents": states or ([ccid["US"]] if "US" in ccid else []), "bbox": bbox_of(g),
                 "area_km2": round_area(ar), "counties": sorted(cties), "census_geoid": row["GEOID"],
                 "polygon": f"polygons/{mid}.geojson"}
            if row["GEOID"] in cbsa_pop:
                d["population"], d["population_source"] = cbsa_pop[row["GEOID"]], f"census-{CENSUS_YEAR}"
            sizes[mid] = write_polygon(out, mid, nm, g)
            records[mid] = d
            metros[mid] = d
            for i in loc_tree.query(g, predicate="contains"):
                lid = loc_ids[i]
                if mid not in records[lid]["parents"]:
                    records[lid]["parents"].append(mid)
            # metro parent display: states; cities: county + metro
        log("metros", len(metros))

    # ---------------------------------------------------------------- aliases, search, tree, shards
    for rid, al in extra_alias.items():
        if rid not in records:
            log("WARNING alias target not in catalogue:", rid)
    srow = {r["rid"]: r for r in rows}

    def alts(rid):
        d = records[rid]
        s = []
        r = srow.get(rid)
        if r:
            for x in (r["pname"], r["en"]):
                if x:
                    s.append(x)
            if r["nv"]:
                s += list(r["nv"])
            if d["kind"] == "region" and r["region"]:
                s.append(r["region"].split("-", 1)[1])
            if d["kind"] == "county":
                s.append(re.sub(r"\s+(County|Parish|Borough|Census Area)$", "", d["name"]))
            if d["kind"] == "country":
                s.append(r["country"])
        elif rid in inf:
            s += inf[rid]["alias"]
        elif d["kind"] == "metro":
            nm = d["name"].rsplit(",", 1)[0]
            s += [x.strip() for x in nm.split("-")] + [nm]
        s += extra_alias.get(rid, [])
        out_, seenl = [], {d["name"].lower()}
        for x in s:
            if x and x.lower() not in seenl:
                seenl.add(x.lower())
                out_.append(x)
        return out_[:40]

    def shard_of(d):
        if d["kind"] == "multi_country":
            return "multi"
        return d["country"].lower()

    shards = collections.defaultdict(list)
    for rid, d in records.items():
        shards[shard_of(d)].append(rid)

    def is_informal(rid):
        return records[rid]["kind"] in INFORMAL_KINDS

    # display parents for the tree
    disp = {}
    for rid, d in records.items():
        ps = d["parents"]
        ip = [p for p in ps if is_informal(p)]
        if is_informal(rid):
            geo = [p for p in ps if not is_informal(p)]
            disp[rid] = ip or geo
        else:
            disp[rid] = ip or ps
    tchild = collections.defaultdict(list)
    for rid, ps in disp.items():
        for p in ps:
            tchild[p].append(rid)

    def sort_key(rid):
        d = records[rid]
        return (-(d.get("population") or 0) if d["kind"] == "locality" else 0, d["name"].casefold())

    def node(rid, depth=0):
        d = records[rid]
        n = {"id": rid, "name": d["name"], "kind": d["kind"]}
        ch = sorted(tchild.get(rid, []), key=sort_key)
        if ch:
            if depth > 12:
                return n
            n["children"] = [node(c, depth + 1) if shard_of(records[c]) == shard_of(d) or True else None for c in ch]
        return n

    def stub(rid):
        d = records[rid]
        return {"id": rid, "name": d["name"], "kind": d["kind"], "shard": shard_of(d)}

    index_countries = []
    shard_info = {}
    for sh, ids in sorted(shards.items()):
        ids = sorted(ids)
        recs = [records[i] for i in ids]
        for i in ids:
            al = alts(i)
            if al:
                records[i]["alt"] = al
        top = [i for i in ids if (records[i]["kind"] in ("country", "dependency") and shard_of(records[i]) == sh)]
        if sh == "multi":
            top = [i for i in ids if records[i]["kind"] == "multi_country"]
        in_shard = set(ids)

        def node_s(rid, depth=0):
            d = records[rid]
            n = {"id": rid, "name": d["name"], "kind": d["kind"]}
            ch = sorted(tchild.get(rid, []), key=sort_key)
            kids_ = []
            for c in ch:
                if c in in_shard:
                    kids_.append(node_s(c, depth + 1))
                else:
                    kids_.append(stub(c))
            if kids_:
                n["children"] = kids_
            return n
        tree = [node_s(i) for i in top]
        # multi-country informal regions appear as stubs under each country
        for t in tree:
            pass
        shard = {"format": 1, "shard": sh, "release": a.release, "count": len(recs), "regions": recs, "tree": tree}
        p = os.path.join(out, f"{sh}.json")
        json.dump(shard, open(p, "w"), separators=(",", ":"), ensure_ascii=False)
        search = [{"id": i, "name": records[i]["name"], "kind": records[i]["kind"], "alt": records[i].get("alt", []),
                   "in": ctx.get(i, ""), "pop": records[i].get("population", 0)} for i in ids]
        sp = os.path.join(out, f"{sh}.search.json")
        json.dump(search, open(sp, "w"), separators=(",", ":"), ensure_ascii=False)
        kc = collections.Counter(r["kind"] for r in recs)
        shard_info[sh] = {"path": f"{sh}.json", "search": f"{sh}.search.json", "count": len(recs),
                          "bytes": os.path.getsize(p), "search_bytes": os.path.getsize(sp), "kinds": dict(kc)}
        for i in top:
            d = records[i]
            index_countries.append({"id": i, "name": d["name"], "kind": d["kind"], "shard": sh, "bbox": d["bbox"],
                                    "area_km2": d["area_km2"], "population": d.get("population"),
                                    "parents": d["parents"], "count": len(recs), "kinds": dict(kc)})
    # search-global
    gl = []
    for rid, d in records.items():
        if d["kind"] in ("country", "dependency", "region", "metro", "multi_state", "multi_country", "sub_state", "valley", "metro_alias") \
                or (d["kind"] == "locality" and (d.get("population") or 0) >= 100000):
            gl.append({"id": rid, "name": d["name"], "kind": d["kind"], "alt": d.get("alt", []), "in": ctx.get(rid, ""),
                       "pop": d.get("population", 0), "shard": shard_of(d)})
    gp = os.path.join(out, "search-global.json")
    json.dump(gl, open(gp, "w"), separators=(",", ":"), ensure_ascii=False)

    # sources actually present in the selected Overture rows
    src = collections.Counter()
    for r in rows:
        for s in r["srcs"] or []:
            src[s] += 1
    overture_sources = [{"dataset": k.split("|")[0], "license": k.split("|")[1], "divisions": v} for k, v in src.most_common()]

    total_poly = sum(sizes.values())
    kinds_all = collections.Counter(d["kind"] for d in records.values())
    index = {
        "format": 1, "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "overture_release": a.release,
        "polygon_base": (a.public_url.rstrip("/") + "/regions/polygons/") if a.public_url else "polygons/",
        "polygon_note": "Fetch a region's polygon on demand from polygon_base + <id>.geojson",
        "ids": "Country ids are the lowercase ISO 3166-1 alpha-2 code, regions the lowercase ISO 3166-2 code, others <region-code>-<slug>; gers_id is Overture's stable identifier for the underlying division.",
        "countries": sorted(index_countries, key=lambda c: c["name"].casefold()),
        "shards": shard_info, "counts_by_kind": dict(kinds_all), "total": len(records),
    }
    json.dump(index, open(os.path.join(out, "index.json"), "w"), separators=(",", ":"), ensure_ascii=False)

    files = {}
    for fn in sorted(os.listdir(out)):
        fp = os.path.join(out, fn)
        if os.path.isfile(fp):
            files[fn] = {"bytes": os.path.getsize(fp), "sha256": hashlib.sha256(open(fp, "rb").read()).hexdigest()}
    manifest = {
        "format": 1, "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "overture_release": a.release, "total_regions": len(records), "polygon_files": len(sizes),
        "polygon_bytes": total_poly, "counts_by_kind": dict(kinds_all),
        "test_filter": countries or None,
        "sources": [
            {"name": "Overture Maps Foundation, divisions theme (division, division_area)", "release": a.release,
             "url": "https://docs.overturemaps.org/schema/reference/divisions/",
             "access": f"GeoParquet on s3://overturemaps-us-west-2/release/{a.release}/theme=divisions/, read with DuckDB",
             "license": "ODbL 1.0 (theme licence); per-dataset licences listed below",
             "attribution": "© OpenStreetMap contributors, Overture Maps Foundation",
             "attribution_page": "https://docs.overturemaps.org/attribution/",
             "datasets_present": overture_sources},
            {"name": "US Census Bureau Cartographic Boundary Files, CBSA 1:500k", "vintage": CENSUS_YEAR,
             "url": f"{CB_BASE}/cb_{CENSUS_YEAR}_us_cbsa_500k.zip", "page": "https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.html",
             "license": "Public domain (US Government work)", "used_for": "metropolitan and micropolitan areas"},
            {"name": "US Census Bureau population estimates", "vintage": CENSUS_YEAR,
             "url": f"{POP_BASE}/", "license": "Public domain (US Government work)",
             "used_for": "metro populations; county and state populations where Overture has none"},
        ],
        "informal_regions": {"file": "regions/informal.yaml", "count": len(inf)},
        "license_note": "The catalogue contains Overture data and is therefore distributed under ODbL 1.0; keep the attribution above.",
        "files": files,
    }
    json.dump(manifest, open(os.path.join(out, "manifest.json"), "w"), indent=1, ensure_ascii=False)

    # id listing for authoring informal.yaml
    with open(os.path.join(a.out, "ids.tsv"), "w") as f:
        for rid, d in sorted(records.items()):
            if d["kind"] != "locality":
                f.write("\t".join([rid, d["kind"], d["name"], ",".join(d["parents"]), str(d.get("population", ""))]) + "\n")
    log("done:", len(records), "regions;", dict(kinds_all), "polygon bytes", total_poly)


if __name__ == "__main__":
    main()
