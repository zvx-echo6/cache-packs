# routing-tiles

Road routing for every region at once: one Valhalla build, published one file per tile. Cache works out the tiles a
region needs from the region's polygon (the tiling is fixed), downloads them, and joins regions on the phone by putting their
tiles side by side; tiles of one build always fit together. Online, the phone fetches the tiles a route needs from the same
place. Replaces the per-state `valhalla-routing` releases for the app.

## Source

- Valhalla 3.7.0 tiles (`valhalla_tiles.tar`) built from Geofabrik's `north-america-latest.osm.pbf` on the navi builder,
  with the admin and time zone databases of `/build/valhalla/custom_files` and the speed table below
  (`scripts/build-routing-tiles.sh`). Licence: ODbL 1.0, (c) OpenStreetMap contributors.
- `tilecut --explode` (cache repository, `packages/valhalla/tools/tilecut`) splits the tar into tiles and writes the index.

## Speeds

Valhalla sets each road's speed from its `maxspeed` tag, or from a default per road class (`lua/graph.lua`: 105, 90, 75,
60, 50, 40, 35, 25 km/h from motorway to service) that the enhance stage lowers on untagged roads in dense areas. A speed
table (`mjolnir.default_speeds_config`, the OpenStreetMapSpeeds format) replaces that speed per road class and per
rural / suburban / urban density (road density 0-5, 6-10, over 10), on tagged roads too; a `null` entry keeps Valhalla's
own value. Our table is `recipes/routing-default-speeds.json`:

| Density | Road class | km/h | Before (Cache) | Apple |
|---|---|---|---|---|
| rural | motorway | 113 | 126 (mostly tagged 80 mph) | 116 |
| rural | primary | 84 | 88 tagged, 76 untagged | 81 tagged, 87 untagged |
| rural | secondary | 78 | 59 (untagged, default 60) | 78 |
| urban | trunk | 45 | 54 | 45 |
| urban | primary | 45 | 56 | 45 |
| urban | secondary | 41 | 48 | 42 |
| urban | tertiary | 30 | 39 | 29 |

Everything else is `null`. "Apple" is Apple Maps' step durations on the 40-trip Utah and Idaho benchmark of 2026-10-28
(steps map-matched onto the tiles, one road class covering at least 70% of the step). Notes:

- The admin database built from the North America extract has no United States polygon (cause not yet checked),
  so US roads match no country entry and get the table's entry without `iso3166-1`. That fits the 2026-10-18 test
  build with the OpenStreetMapSpeeds table: its world defaults (16 km/h on residential roads), not its US state
  entries, showed up, so that build went without a table. Canada, Mexico, Bermuda, Greenland and
  Saint-Pierre-et-Miquelon have all-`null` entries, so only the US gets the table.
- Rural motorway is 113, not Apple's 116: the table cannot scale a posted limit, and 113 (70 mph) leaves 65 and 70 mph
  interstates near where they are. Costing `top_speed` is not a substitute: above it Valhalla also adds a cost penalty,
  and at 116 routes leave the interstate.
- The table ignores surface. Unpaved trunk, primary and secondary roads are 0.0%, 0.1% and 0.5% of those ways in the
  extract (tertiary 4.7%, so tertiary stays `null` outside cities).
- Signal and turn delay are costing time, not tile time: Valhalla 3.7.0 adds a fixed 2 s at a signal and OSRM-style turn
  durations, so the urban numbers above are speeds that include intersection delay.

## Output (R2, under `routing/`)

- `<build>/<level>/<aaa>/<bbb>/<ccc>.gph.gz`: each tile, gzip -n. Valhalla tiling: level 0 = 4 degrees, 1 = 1 degree,
  2 = 0.25 degrees; id = row * columns + col. A tile that is not in the index has no roads.
- `<build>/index.tsv.gz`: one line per tile, `<path>\t<bytes>\t<sha256 of the raw tile>\t<gz bytes>`, sorted.
- `current.json`: the build the app uses (`build`, Valhalla version, source, tile count, raw and gz bytes, index path and
  the index file's sha256). Uploaded last, so a phone only sees a build once all of it is there.
- First build: `na-2026-10-18`, 30,720 tiles, 24.2 GB raw, 8.7 GB gzipped, no speed table.
- `na-2026-10-29-speeds`: same extract and admin data, with the speed table; 30,720 tiles, 24.2 GB raw, 8.7 GB gzipped.
  On the 40 benchmark trips the duration error against Apple (mean absolute) went from 9.7% to 8.8%: urban median -11%
  to +1%, highway -6% to +4%, rural mean 9.3% to 6.4% (SR 313 +26% to +6%, ID-75 +14% to +2%), suburban median +1% to +6%.

## Steps (on the builder, no host installs: the tools run in containers)

1. `scripts/build-routing-tiles.sh <build> /build/valhalla/custom_files <work dir> <tilecut src>`: parse and graph build
   (12 threads, about 2.5 hours), enhance through cleanup with the speed table (6 threads; 12 ran out of memory at
   12 GB), tar, build tilecut (`rust:1.90-slim`) and `tilecut --explode` (about 3 minutes).
2. Check the speeds before publishing: serve the tar and compare routes with the previous build (`/route`, auto).
3. `scripts/publish-routing-tiles.sh <build>`: gzip every tile, add the gz sizes to the index, write `current.json`, upload
   with `rclone/rclone:1.68.2` (tiles and index with a one-year immutable cache, `current.json` with five minutes). The R2
   keys go in an env file readable only by root for the upload and are deleted after.
4. Delete the working tiles once uploaded. Keep a build in R2 until every device has updated past it.
