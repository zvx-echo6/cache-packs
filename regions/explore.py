#!/usr/bin/env python3
"""Prints facts about the Overture divisions release (schema, counts) so the build rules can be checked. Read-only."""
import os, sys, duckdb
rel = sys.argv[1]
base = f"s3://overturemaps-us-west-2/release/{rel}/theme=divisions"
con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs; INSTALL spatial; LOAD spatial; SET s3_region='us-west-2'; SET threads=4; SET memory_limit='4GB';")
D = f"read_parquet('{base}/type=division/*', hive_partitioning=1)"
A = f"read_parquet('{base}/type=division_area/*', hive_partitioning=1)"
def q(title, sql, n=60):
    print(f"\n=== {title}", flush=True)
    try:
        r = con.execute(sql)
        cols = [c[0] for c in r.description]
        print(" | ".join(cols))
        for row in r.fetchmany(n):
            print(" | ".join(str(x)[:160] for x in row))
    except Exception as e:
        print("ERROR", e)
q("division schema", f"DESCRIBE SELECT * FROM {D}")
q("division_area schema", f"DESCRIBE SELECT * FROM {A}")
q("divisions by subtype", f"SELECT subtype, count(*) n, count(population) with_pop, count(wikidata) with_wd FROM {D} GROUP BY 1 ORDER BY n DESC")
q("areas by subtype/is_land/is_territorial", f"SELECT subtype, is_land, is_territorial, count(*) n, count(distinct division_id) d FROM {A} GROUP BY ALL ORDER BY 1,2,3")
q("localities by class", f"SELECT class, count(*) n, count(population) with_pop, sum((population>=5000)::int) ge5k FROM {D} WHERE subtype='locality' GROUP BY 1 ORDER BY n DESC")
q("localities with an area (land) by class", f"SELECT d.class, count(*) n FROM {D} d JOIN {A} a ON a.division_id=d.id AND a.is_land WHERE d.subtype='locality' GROUP BY 1 ORDER BY n DESC")
q("capitals", f"SELECT subtype, count(*) FROM {D} WHERE capital_of_divisions IS NOT NULL GROUP BY 1")
q("capital_of_divisions sample", f"SELECT names.primary, subtype, capital_of_divisions FROM {D} WHERE country='US' AND region='US-ID' AND capital_of_divisions IS NOT NULL LIMIT 8")
q("US-ID rows", f"SELECT subtype, class, names.primary, names.common['en'] en, region, admin_level, population, wikidata, parent_division_id, id FROM {D} WHERE country='US' AND region='US-ID' AND subtype IN ('region','county') ORDER BY subtype, 3", 60)
q("US country/dep", f"SELECT subtype, names.primary, region, admin_level, id FROM {D} WHERE country='US' AND subtype IN ('country','dependency','macroregion','region') ORDER BY 1,2", 80)
q("hierarchy sample (Twin Falls)", f"SELECT names.primary, subtype, class, hierarchies, parent_division_id, population FROM {D} WHERE country='US' AND region='US-ID' AND names.primary='Twin Falls'")
q("names sample", f"SELECT names FROM {D} WHERE country='DE' AND subtype='region' LIMIT 2")
for cc in ['GB','DE','AR','CL','CA']:
    q(f"{cc} upper levels", f"SELECT subtype, class, names.primary, names.common['en'] en, region, admin_level, population, wikidata FROM {D} WHERE country='{cc}' AND subtype IN ('country','dependency','macroregion','region','macrocounty') ORDER BY subtype, 3", 80)
q("GB subtype counts", f"SELECT subtype, count(*) FROM {D} WHERE country='GB' GROUP BY 1 ORDER BY 2 DESC")
q("DE subtype counts", f"SELECT subtype, count(*) FROM {D} WHERE country='DE' GROUP BY 1 ORDER BY 2 DESC")
q("BC counties", f"SELECT subtype, names.primary, admin_level, region FROM {D} WHERE country='CA' AND region='CA-BC' AND subtype IN ('county','macrocounty','region') ORDER BY 1,2", 60)
q("Highland-ish GB", f"SELECT subtype, class, names.primary, region, admin_level FROM {D} WHERE country='GB' AND (names.primary ILIKE '%highland%' OR names.primary ILIKE 'Inverness%' OR names.primary ILIKE 'Argyll%') ORDER BY 1,3", 30)
q("Bavaria", f"SELECT subtype, names.primary, region, admin_level, id FROM {D} WHERE country='DE' AND region='DE-BY' AND subtype IN ('region','county','macrocounty','macroregion') ORDER BY 1,2", 120)
q("Patagonia AR/CL regions", f"SELECT country, subtype, names.primary, region, admin_level FROM {D} WHERE country IN ('AR','CL') AND subtype IN ('region','macroregion') ORDER BY 1,3", 80)
q("selection candidates per sample country", f"""SELECT country, subtype, count(*) n,
  sum((subtype='locality' AND (class IN ('megacity','city','town') OR population>=5000 OR capital_of_divisions IS NOT NULL))::int) cand_loc
  FROM {D} WHERE country IN ('US','CA','GB','DE','AR') GROUP BY ALL ORDER BY 1,3 DESC""", 120)
