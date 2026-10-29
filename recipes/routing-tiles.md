# routing-tiles

Road routing for every region at once: one Valhalla build, published one file per tile. Cache works out the tiles a
region needs from the region's polygon (the tiling is fixed), downloads them, and joins regions on the phone by putting their
tiles side by side; tiles of one build always fit together. Online, the phone fetches the tiles a route needs from the same
place. Replaces the per-state `valhalla-routing` releases for the app.

## Source

- Valhalla 3.7.0 tiles (`valhalla_tiles.tar`) built from Geofabrik's `north-america-latest.osm.pbf` on the navi builder
  (`scripts/build-routing-tiles.sh`), with our own admin database (below), the time zone database of
  `/build/valhalla/custom_files` and the speed table below. Licence: ODbL 1.0, (c) OpenStreetMap contributors.
- `tilecut --explode` (cache repository, `packages/valhalla/tools/tilecut`) splits the tar into tiles and writes the index.

## Admin database

`valhalla_build_admins` skips a boundary relation that is missing a member, and the North America extract cuts the
United States relation (148838; members outside the extract). Without it US roads had no country: no US access rules
and no US state speed table. The build script now downloads the whole relation from the OSM API
(`/api/0.6/relation/148838/full`, about 32 MB), converts it to PBF (`scripts/osm_to_pbf.py`, pyosmium in a container)
and passes it to `valhalla_build_admins` next to the extract. It stops if the log still says the US is degenerate. Add
another country's relation id to `BOUNDARIES` in the script if one goes missing the same way. With the US present,
Valhalla's US access rules apply too (pedestrian, bridleway and cycleway allow foot and bicycle, path allows moped).

## Speeds

Valhalla sets each road's speed from its `maxspeed` tag, or from a default per road class that the enhance stage lowers
on untagged roads in dense areas. A speed table (`mjolnir.default_speeds_config`) replaces that speed per country/state,
road class and rural / suburban / urban density (road density 0-5, 6-10, over 10), on tagged roads too; a `null` entry
keeps Valhalla's own speed. We use the published OpenStreetMapSpeeds table (`default_speeds.json` of
OpenStreetMapSpeeds/schema, commit c9c6872, sha256 9953f78c...e5ba7), pinned and checked by the build script, with our
corrections (`recipes/routing-speed-corrections.json`, applied by `scripts/merge_speeds.py`). The table for
`na-2026-10-29-speeds2` has sha256 dabc7feb9a83ee1eea5a5a6cac550434970e928821d45ff1ed18ddb1510e1cc1.

The corrections are for Utah and Idaho only, where the 40-trip Apple Maps benchmark of 2026-10-28 shows the published
values off (Apple speeds from its step durations, map-matched onto the tiles):

- rural motorway Idaho 111 -> 116 (Apple 117); rural trunk Idaho 102 -> 83 (Apple 83), Utah 94 -> Valhalla's own (posted
  limits; 94 made in-town stretches like US 191 at Moab far too fast); rural secondary Utah 62 -> 73, Idaho 63 -> 80
  (Apple 73 and 80, SR 313 and ID-75 style highways).
- suburban: motorway Utah 105 -> 112, Idaho 102 -> 105; primary Utah 55 -> 63, Idaho 48 -> 65; trunk Idaho 50 -> 75;
  secondary Idaho 49 -> 56; residential 20 -> 30 (Apple 29).
- urban: motorway Utah 103 -> 112; primary Idaho 47 -> 52 (Apple 52); residential 19/20 -> 30 (Apple 29-31).
- service roads 8-11 km/h -> 25 rural, 21 suburban, 20 urban (Apple 21-31); primary turn channels suburban and urban
  40-46 -> 60 (Apple 59-63).

Valhalla adds turn and signal time on top of edge speeds (a fixed 2 s per signal), so where Apple's step speed and the
trip totals disagree the trip totals won. Costing `top_speed` is no substitute for lower motorway speeds: above it
Valhalla also adds a cost penalty, and at 116 routes leave the interstate.

Result on the 40 trips (Cache duration within 10% of Apple's): 33 of 40 (na-2026-10-18: 25); urban 7/10, suburban
8/10, highway 10/10, rural 8/10. Mean absolute error 9.7% -> 8.3%. The trips still off are mostly route or snapping
differences (2, 4, 13 and 18 differ in distance or start point).

## Output (R2, under `routing/`)

- `<build>/<level>/<aaa>/<bbb>/<ccc>.gph.gz`: each tile, gzip -n. Valhalla tiling: level 0 = 4 degrees, 1 = 1 degree,
  2 = 0.25 degrees; id = row * columns + col. A tile that is not in the index has no roads.
- `<build>/index.tsv.gz`: one line per tile, `<path>\t<bytes>\t<sha256 of the raw tile>\t<gz bytes>`, sorted.
- `current.json`: the build the app uses (`build`, Valhalla version, source, tile count, raw and gz bytes, index path and
  the index file's sha256). Uploaded last, so a phone only sees a build once all of it is there.
- First build: `na-2026-10-18`, 30,720 tiles, 24.2 GB raw, 8.7 GB gzipped, no speed table.
- `na-2026-10-29-speeds2`: same extract, fixed admin database, published speed table with our corrections.

## Steps (on the builder, no host installs: the tools run in containers)

1. `scripts/build-routing-tiles.sh <build> /build/valhalla/custom_files <work dir> <tilecut src>`: parse and graph build
   (12 threads, about 2.2 hours), enhance through cleanup with the speed table (6 threads; 12 ran out of memory at
   12 GB), tar, build tilecut (`rust:1.90-slim`) and `tilecut --explode` (about 3 minutes).
2. Check the speeds before publishing: serve the tar and compare routes with the previous build (`/route`, auto).
3. `scripts/publish-routing-tiles.sh <build>`: gzip every tile, add the gz sizes to the index, write `current.json`, upload
   with `rclone/rclone:1.68.2` (tiles and index with a one-year immutable cache, `current.json` with five minutes). The R2
   keys go in an env file readable only by root for the upload and are deleted after.
4. Delete the working tiles once uploaded. Keep a build in R2 until every device has updated past it.
