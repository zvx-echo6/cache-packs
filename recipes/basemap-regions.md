# basemap-regions

Pre-cut base map files: one PMTiles file per catalogue region, holding the planet's tiles inside the region's outline, z0 to z15. A phone can then download one file per region instead of asking for tiles one range at a time.

Workflow: `.github/workflows/basemap-regions.yml` (by hand only). Code: `scripts/basemap_regions.py` (selection, jobs, upload, manifests) and `scripts/pmtiles_cut.py` (cover, directory reads, writer, verify). Tests: `tests/test_pmtiles_cut.py` (offline; run with `python3 -m pytest tests/`, and in the workflow's plan job).

## Source

- The planet mirrored by `basemap-planet` (`recipes/basemap-planet.md`): `<R2_PUBLIC_URL>/basemap/protomaps-planet-<date>.pmtiles`, read by HTTP ranges. The planet is never downloaded whole.
- Outlines: the region lists (`recipes/regions.md`), `<R2_PUBLIC_URL>/regions/<shard>.json` for the records and `polygon_base + <id>.geojson` for each outline.
- Licence: as the planet: ODbL Produced Work, "(c) OpenStreetMap contributors". The metadata, attribution included, is copied unchanged.

## What a file holds

- Tiles: the outline's cover at each zoom, by the Cache app's own rule (`cover.rs`): a tile is in when its rectangle touches the outline; rings are read even-odd, so a region in parts is their union and holes are left out; only tiles inside the outline's box (`download.rs` `tile_range`) are kept. The same outline gives the same tiles as the app's download. Antimeridian regions keep the side the app keeps (`mapOutline`).
- Bytes: every tile is copied from the planet byte for byte. Contents the planet stores once (ocean, empty land) are stored once here too.
- Layout (PMTiles v3): header, root directory inside the first 16 KiB, metadata, leaf directories, tile data in tile-id order (clustered). The header gives the outline's bounds, z0 to z15 and the box centre.

## Running

Inputs: `planet_version` (the date of a planet already in R2), `regions` (`ids:a,b,c`, `state:us-id,us-ut`, `country:us` or `na` for us, ca, mx, gl), `dry_run` (default on), `maxzoom` (`15`, or `catalogue` for the zoom the region list sizes each region at).

- `plan` runs the tests, reads the shards, and makes one job per state or province (`us-id`, `ca-bc`); regions in no single state (the country, metro areas across a state line, multi-state regions) go to `<country>-other`. North America is about 17,900 regions in 105 jobs. At most 8 jobs run at once.
- Each job, region by region: cut, verify, upload, delete the local file. Verify re-reads the written file on its own: header and layout, counts, that its tiles are exactly the cover's, that the metadata is the planet's, and 64 random tiles byte-equal to the planet (looked up there one by one). Any failure fails the job.
- A dry run cuts and verifies but gets no R2 keys and uploads nothing; its manifests are kept as workflow artifacts.

## On the bucket

Only these keys are written, and nothing is deleted:

- `basemap-regions/<planet version>/<region id>.pmtiles` (Content-Type `application/vnd.pmtiles`, object metadata `sha256`)
- `basemap-regions/<planet version>/manifests/<job>.json`: per region `id`, `key`, `bytes`, `sha256`, `tiles`, `seconds`, `minzoom`, `maxzoom`, `bounds`; `complete` is true when the job finished. Written every 10 regions and at the end.

Resume: a region that an earlier manifest lists, whose object has that manifest's size and SHA-256, is skipped. Re-running a failed job carries on where it stopped.

Secrets: the ones `basemap-planet` uses (`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT`, `R2_BUCKET`) and the variable `R2_PUBLIC_URL`. rclone v1.75.2 (`scripts/setup-rclone.sh`).

## Publishing (later, with the owner's go; not done by this workflow)

The region lists and the catalogue do not mention these files yet. A publish step would:

1. Read every manifest under `basemap-regions/<version>/manifests/` and keep only jobs with `complete: true`.
2. For each entry, check the object (HEAD: size equals `bytes`).
3. Add to that region's record in its shard `regions/<shard>.json` a field such as `"basemap_file": {"url": "<R2_PUBLIC_URL>/<key>", "bytes": ..., "sha256": "...", "tiles": ..., "minzoom": 0, "maxzoom": 15, "planet": "<version>"}`. The app would download the URL, check the SHA-256, and fall back to tile-by-tile downloads for a region without one.
4. Upload only the changed shards (like the region-sizes publish), with the same content types and cache headers as `regions/publish_regions.sh`.
5. Retention of older `basemap-regions/<version>/` prefixes is a separate decision, made when a new planet is cut.

## Limits

- Size: about 105 to 155 GB for North America at z15 (measured for Idaho and Utah, estimated for the rest). Every place is stored several times over, once per region that contains it.
- The country-sized regions (`us`, `ca`, `mx`) at z15 are tens of GB each and have not been tried. Each job checks free disk before writing and fails if the file would not fit; `maxzoom: catalogue` cuts them at the zoom the region list uses for them.
- The cover was checked against a port of the app's own tile-by-tile test, and against the measured Utah cut (330,539 tiles, the same count). It has not been run against the Rust code itself.
