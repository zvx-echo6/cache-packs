# basemap-planet

Worldwide Protomaps v4 base map, hosted in R2 because whole-region offline downloads are not possible from OpenFreeMap (50 MB per download). One PMTiles file, z0 to z15, layout `protomaps-v4`.

## Source

- Publisher: Protomaps. Daily planet builds, listed at https://maps.protomaps.com/builds (the list comes from `https://build-metadata.protomaps.dev/builds.json`; files at `https://build.protomaps.com/<date>.pmtiles`).
- Verified on: https://docs.protomaps.com/basemaps/downloads, which asks users to copy the tileset to their own cloud storage, gives the size (about 120 GB, z0 to z15) and says BLAKE3 hashes are published for daily builds.
- Licence: ODbL Produced Work; attribution "(c) OpenStreetMap contributors" is required.

## Steps (workflow `basemap-planet`, monthly and by hand)

1. `plan`: pick the newest build.
2. `copy`: `curl | hash | rclone rcat` into `basemap/protomaps-planet-<date>.pmtiles` (multipart, 128 MB parts, no local copy). The stream is hashed on the way (MD5 and BLAKE3) and compared with the published values. An existing object of the right size is not copied again, so the step can be re-run.
3. `verify`: stream the object back from R2 through BLAKE3 and compare with the published hash; a mismatch deletes the object.
4. `finalize`: write `<object>.manifest.json` (source, verified page, licence, attribution, size, checksums, date), `basemap/basemap.json` (layer definition) and the `basemap` entry in `catalogue.json`; keep the newest 2 planet files.

Tools: rclone v1.75.2 (hash pinned in `scripts/setup-rclone.sh`), blake3 1.0.11. Script: `scripts/mirror_basemap.py`.
