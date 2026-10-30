# offroad-region

Region pack (not a tile layer): the off-road router's data for one region, built from original public sources. Two files:

- `offroad.sqlite`: the `cache_offroad` region pack, format 2 (the format `SqlitePack` reads today). Layers `friction`, `class`, `trails`, `barriers`, `wilderness` on the z12 web-mercator pixel grid in 256-px tiles (zstd), plus the `parking`, `trailheads` and `mvum` tables with R*Tree indexes.
- `elevation.pmtiles`: a Mapterhorn area extract at z12 (512-px lossless Terrarium WebP, bytes unchanged). The router reads elevation from this file; it is never copied into the pack.

`ATTRIBUTION.json` lists every source with its licence and required statement.

Nothing here uses navi's files. navi's data was read once (B0, read-only) to recover the rules that were never written down; those rules are now the files in `rules/offroad/`.

## Sources

| Input | Source | Licence | Access |
|---|---|---|---|
| `class` | ESA WorldCover 2021 v200, 10 m, 3 x 3 degree tiles. Tile list from the bucket's grid file `v200/2021/esa_worldcover_grid.geojson`; tiles from `https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_<tile>_Map.tif` | CC BY 4.0. "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium" | AWS Open Data bucket, HTTPS |
| `friction` | Computed from `class` with `rules/offroad/class_friction.tsv` | as class | none |
| `trails` | OpenStreetMap highways from a Geofabrik extract (`OSM_PBF`, cut to the bbox with `osmium extract`); with `AGENCY_LINES=1` (default) also USFS road core and NFS trails, and BLM GTLF roads and trails | ODbL 1.0 (OSM); US Government data (USFS); BLM terms below | Geofabrik download; USFS EDW file downloads; BLM documented ArcGIS REST query API (`scripts/fetch_blm_gtlf.py`) |
| `barriers`, `wilderness` | USGS PAD-US 4.1, Manager Name feature service (`scripts/fetch_padus_service.py`, see `public-lands.md`): `Pub_Access = 'XA'` and `Des_Tp = 'WA'` | Public domain | documented REST query API |
| `mvum` | USFS EDW `Trans_MVUM_Road.gdb.zip`, `Trans_MVUM_Trail.gdb.zip`; null trail shapes filled from `Trans_Trail_NFS_Publish.gdb.zip` | US Government data, no warranty | file downloads (`https://data.fs.usda.gov/geodata/edw/edw_resources/fc/`) |
| `parking`, `trailheads` | OpenStreetMap, the same extract | ODbL 1.0 | as trails |
| elevation | Mapterhorn `planet.pmtiles` (z0-12), area extract with the PMTiles CLI as Mapterhorn's Data Access page describes (`pmtiles extract --bbox=... --minzoom=12 --maxzoom=12 https://download.mapterhorn.com/planet.pmtiles`) | per source; "© Mapterhorn", full list generated from `https://download.mapterhorn.com/attribution.json` | documented download server |

BLM terms (from the dataset record): cite the BLM as the source; describe modifications; do not imply BLM approval. Modifications here: lines selected by the region bbox, classed by `rules/offroad/agency_line_classes.tsv` and rasterised into the trails layer. Not approved or endorsed by the BLM.

## Rules (`rules/offroad/`)

- `class_friction.tsv`: WorldCover class -> friction x10. Recovered from a joint histogram of 3.9 billion pixels (three Idaho tiles against navi's friction tiles of the same grid, `scripts/offroad/b0_class_friction.py`). Every class had exactly one friction value, so friction is a pure lookup and a later format can compute it on the phone instead of shipping it.
- `osm_trail_classes.tsv`: OSM `highway` -> 5 road, 15 track, 25 foot trail. Burn order 25, 15, 5, so the lower value wins where lines overlap.
- `entry_point_classes.tsv`: the `highway` values whose way end nodes become trailhead (entry) points; the class set found in navi's `trail_entry_points` over Idaho.
- `agency_line_classes.tsv`: USFS and BLM attribute codes -> trails value. Proposed, not reviewed: these lines change routes against an OSM-only build (`AGENCY_LINES=0`).
- MVUM backfill (`scripts/offroad/prep_mvum.py`): a trail with no shape takes the NFS segments with the same `TRAIL_NO` and `TRAIL_NAME`, within the forest's `ADMIN_ORG` 4-character prefix learned from shaped trails, falling back to number and name alone.

## Grid

- Rasters (`gdal_rasterize`): 0.0003 degree EPSG:4326 pixels. Trails are anchored at (-180, 90) and burned with `ALL_TOUCHED`; barriers and wilderness are anchored at (-125, 50) with the default burn mode. These are the grids of navi's rasters, so a line is 2-3 z12 cells wide after the pack's nearest resample.
- Pack: every z12 tile the elevation extract holds inside the bbox; each cell takes the nearest source pixel at its true web-mercator centre (`offroad_pack build-sources`).

## Steps

`scripts/build-offroad-region.sh REGION WORK [STEP ...]` with `BBOX=w,s,e,n`, `OSM_PBF`, `CACHE_SRC` (a Cache checkout, for `offroad_pack`). Every tool runs in a pinned container: `ghcr.io/osgeo/gdal:ubuntu-small-3.9.3`, `python:3.12-slim`, `protomaps/go-pmtiles:v1.28.0`, `rust:1.90-slim`, and `cache-osmium:1` (built from `scripts/offroad/osmium.Dockerfile`).

1. `fetch-worldcover`, `fetch-osm`, `fetch-padus`, `fetch-usfs`, `fetch-blm`, `fetch-elevation` (independent; can run in parallel).
2. `prep-osm` (highway lines with burn values, entry points, parking centroids with blocked access dropped), `prep-mvum`, `prep-agency`.
3. `rasterise`: `trails.tif`, `barriers.tif`, `wilderness.tif`.
4. `pack`: builds `offroad_pack` and runs `build-sources`.
5. `attribution`: `ATTRIBUTION.json`.

Outputs in `WORK/out/`. Publishing is a separate step and needs the owner's go.

Options:
- `OFFROAD_PACK`: a prebuilt `offroad_pack` (built in the same rust image) instead of building it from `CACHE_SRC`.
- `LAND_ACCESS=none`, for regions outside the US: PAD-US, USFS and BLM do not cover them, so they are not fetched; `barriers`, `wilderness` and MVUM are empty, agency lines are off, `ATTRIBUTION.json` lists only the sources used, and the pack's meta has `cov_land_access = none`, `cov_mvum = none` and a `land_rules` note (land access unknown; the router's `land_rules_unknown` case).
- `OSM_PBF.sources`: when `OSM_PBF` was merged from several extracts, one line per extract (path, md5, size, replication timestamp), copied into `in/osm/source.txt` and so into `ATTRIBUTION.json`.
- Step `smoke` (not in the default list), with `SMOKE="lat,lon lat,lon"`: one foot route with `offroad_pack route` (the `SqlitePack` and `PmtilesElevation` readers), plus `inspect`; output in `WORK/smoke.txt`.

## GitHub Actions (`.github/workflows/offroad-regions.yml`)

Manual only. One job per region on a standard `ubuntu-24.04` runner (4 CPUs, 16 GB), at most four at a time; `offroad_pack` is built once from a pinned Cache commit. Each job (`scripts/offroad/ci-region.sh`) frees disk, works on the larger of `/` and `/mnt`, downloads the Geofabrik extracts that cover the region's bbox (md5-checked) and merges them with `osmium merge`, runs the recipe, deletes the inputs, and stages the region's files as assets of a pre-release (`offroad-YYYY-MM`):

- `REGION-offroad.sqlite`, or `REGION-offroad.sqlite.partNNN` when it is over 2 GiB (concatenate in order);
- `REGION-manifest.json` (`scripts/offroad/release_assets.py`): bytes and sha256 of every file and part, plus `elevation`: the Mapterhorn `planet.pmtiles` version, bbox, zoom 12, tile count and extract sha256;
- `REGION-ATTRIBUTION.json`, `REGION-steps.log`, `REGION-smoke.txt`.

`elevation.pmtiles` is not an asset: elevation comes from Mapterhorn, and the extract is used on the runner only to pick the pack's cells and for the smoke route. Regions are a 50 km (about 0.5 degree) pad around the state or province. Alberta is built with `LAND_ACCESS=none`.

## Idaho reference build

See the off-road section of the README for the measured sizes and timings.
