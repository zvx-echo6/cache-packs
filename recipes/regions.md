# regions

The worldwide region catalogue: a browsable, searchable list of geographic regions at many overlapping scales (countries, states and provinces, counties and districts, cities and towns, metro areas, and informal regions such as "Southern Idaho" or "Pacific Northwest"). Cache shows it as a tree, and downloads a region's layers for its polygon. Overlap is fine; the app de-duplicates overlapping downloads.

Workflow: `.github/workflows/regions.yml` (by hand or monthly). Code: `regions/build_regions.py`, `regions/publish_regions.sh`. Hand-edited data: `regions/informal.yaml`, `regions/aliases.yaml`.

## Sources

| Source | Used for | Licence |
|---|---|---|
| Overture Maps Foundation, divisions theme (`division`, `division_area`), read as GeoParquet from `s3://overturemaps-us-west-2/release/<release>/theme=divisions/` with DuckDB. Schema: https://docs.overturemaps.org/schema/reference/divisions/ | the worldwide backbone: countries, dependencies, regions, counties, localities, hierarchy, multilingual names, polygons (land-clipped `division_area`, `is_land`) | ODbL 1.0 for the theme. Built from OpenStreetMap (ODbL), geoBoundaries (CC BY 4.0), Esri Community Maps contributors (CC BY 4.0), LINZ (CC BY 4.0). Attribution: "(c) OpenStreetMap contributors, Overture Maps Foundation". https://docs.overturemaps.org/attribution/ |
| US Census Bureau Cartographic Boundary Files, CBSA, 1:500k (`cb_2024_us_cbsa_500k.zip`). Page: https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.html | US metropolitan and micropolitan areas, which Overture has no equivalent of | Public domain |
| US Census Bureau population estimates (2020-2024): `metro/totals/cbsa-est2024-alldata.csv`, `counties/totals/co-est2024-alldata.csv` under https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/ | metro populations; US county and state populations where Overture has none | Public domain |

The exact datasets and licences present in a release are counted into `manifest.json` (`datasets_present`).

## What is in the catalogue

- Overture subtypes `country`, `dependency`, `region`, `county` and `locality` that have a land polygon.
- Localities are kept when they are a city or town (Overture `class`), or have a population of 5,000 or more, or are the capital of any division (this covers county seats and state capitals). Villages and hamlets below that are left out. Localities without a polygon are left out (Overture has no area for them).
- Not included: `localadmin`, `borough`, `macrohood`, `neighborhood`, `microhood`. `macroregion` and `macrocounty` do not occur in the current release.
- US metros and micros (Census CBSAs), with their member counties and parent states. US localities that fall inside a CBSA get the metro as an extra parent.
- Informal regions from `regions/informal.yaml`.

## Ids

- Country or dependency: lowercase ISO 3166-1 alpha-2 (`us`, `gb`, `pr`).
- Region: lowercase ISO 3166-2 code (`us-id`, `ca-bc`, `gb-sct`, `ar-q`).
- County or locality: `<region code or country>-<name slug>` (`us-id-twin-falls-county`, `us-id-twin-falls`, `de-by-munich`). The English name is used when Overture has one, otherwise the local name. Name clashes are resolved by rank (country, region, county, locality, then population); the loser gets a suffix from its Wikidata id.
- US metro: `us-metro-<name slug>` or `us-micro-<name slug>`.
- Informal: the id written in the YAML.
- Every record also carries `gers_id`, Overture's registered identifier for the underlying division. Ids are derived from names and ISO codes, not from GERS ids, so they stay the same across releases unless a name or code changes. Overture warns that renames, splits and merges produce new GERS ids; use `gers_id` plus `wikidata` to re-match.

## Informal regions

`regions/informal.yaml` holds one entry per region: `id`, `name`, `kind` (`multi_state`, `multi_country`, `sub_state`, `valley`, `metro_alias`), `aliases`, `description`, `source` and `members`: a list of catalogue ids (states, provinces, counties, districts). The polygon is the union of the members' full-resolution polygons. Parents are worked out automatically: the single state or country the members sit in, plus any informal region that contains it entirely (smallest first); counties get the informal sub-state regions that contain them, states get the multi-state ones. The build stops with an error if a member id does not exist. Where no official grouping exists the `source` line says so.

## Output (R2 prefix `regions/`)

- `index.json`: the top level (countries and dependencies with counts, bbox, shard names), counts by kind, `polygon_base`. A bbox that crosses the antimeridian has west greater than east (RFC 7946).
- `<iso>.json`: one shard per country code (`multi.json` for cross-border informal regions): all records plus the browse tree (country, then multi-state regions and states, then sub-state regions, counties and metros, then cities).
- `<iso>.search.json`: search entries for that shard (name, alternate names, kind, context, population).
- `search-global.json`: countries, regions, metros, informal regions and cities of 100,000 or more, for a first search before any shard is loaded.
- `polygons/<id>.geojson`: one simplified polygon (a GeoJSON Feature, coordinates rounded to 5 decimals). Fetch on demand from `polygon_base`.
- `manifest.json`: sources, licences, release, dates, counts, file sizes and checksums.

Record fields: `id`, `name`, `local_name`, `kind`, `class`, `country`, `region_code`, `parents` (more than one is allowed), `bbox` (west, south, east, north), `area_km2` (spherical area of the full-resolution polygon), `population` with `population_source`, `admin_level`, `wikidata`, `gers_id`, `polygon`, `names` (all languages, for countries, dependencies and regions), `alt` (search names). Informal records add `members`, `description`, `source`; metros add `counties` and `census_geoid`.

Population comes from Overture where it has one and from the Census (US counties, states, metros) otherwise; informal regions sum their members and omit the figure when a member has none (`population_partial` then gives the known part).

## Running

`mode` input: `explore` (prints Overture schema and counts, no output), `build` (builds on the runner, keeps the json files and an id listing as an artifact), `publish` (build and upload to R2 with the existing secrets). `countries` limits a test run to some ISO codes. A publish run replaces everything under `regions/` (polygons are synced with delete).
