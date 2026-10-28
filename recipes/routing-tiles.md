# routing-tiles

Road routing for every region at once: one Valhalla build, published one file per tile. Cache works out the tiles a
region needs from the region's polygon (the tiling is fixed), downloads them, and joins regions on the phone by putting their
tiles side by side; tiles of one build always fit together. Online, the phone fetches the tiles a route needs from the same
place. Replaces the per-state `valhalla-routing` releases for the app.

## Source

- Valhalla 3.7.0 tiles (`valhalla_tiles.tar`) built from Geofabrik's `north-america-latest.osm.pbf` on the navi builder
  (`/build/valhalla/custom_files`), with `build_admins` and `build_time_zones` on and `use_default_speeds_config` off; see
  navi's Valhalla runbook for the build. Licence: ODbL 1.0, (c) OpenStreetMap contributors.
- `tilecut --explode` (cache repository, `packages/valhalla/tools/tilecut`) splits the tar into tiles and writes the index.

## Output (R2, under `routing/`)

- `<build>/<level>/<aaa>/<bbb>/<ccc>.gph.gz`: each tile, gzip -n. Valhalla tiling: level 0 = 4 degrees, 1 = 1 degree,
  2 = 0.25 degrees; id = row * columns + col. A tile that is not in the index has no roads.
- `<build>/index.tsv.gz`: one line per tile, `<path>\t<bytes>\t<sha256 of the raw tile>\t<gz bytes>`, sorted.
- `current.json`: the build the app uses (`build`, Valhalla version, source, tile count, raw and gz bytes, index path and
  the index file's sha256). Uploaded last, so a phone only sees a build once all of it is there.
- First build: `na-2026-10-18`, 30,720 tiles, 24.2 GB raw, 8.7 GB gzipped.

## Steps (on the builder, no host installs: the tools run in containers)

1. Build tilecut: `docker run --rm -v <src>:/src -w /src rust:1.85-slim cargo build --release`.
2. `tilecut --explode valhalla_tiles.tar <out>/<build>` (about 3 minutes for North America).
3. `scripts/publish-routing-tiles.sh <build>`: gzip every tile, add the gz sizes to the index, write `current.json`, upload
   with `rclone/rclone:1.68.2` (tiles and index with a one-year immutable cache, `current.json` with five minutes). The R2
   keys go in an env file readable only by root for the upload and are deleted after.
4. Delete the working tiles once uploaded. Keep a build in R2 until every device has updated past it.
