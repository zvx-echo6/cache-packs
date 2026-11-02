# offroad-regions: the published off-road packs

Region packs built by `recipes/offroad-region.md` (one file per region) are published to the data host under
`https://data.cached.info/offroad/`, in the same shape as `routing/`: immutable build folders, and one small
`current.json` that is written last.

## Layout

```
offroad/current.json                               catalogue, max-age 300; written after every file it lists
offroad/<build>/<region>/offroad.sqlite            the pack (cache_offroad SqlitePack, format 2), immutable
offroad/<build>/<region>/ATTRIBUTION.json          every source, licence and required statement, immutable
offroad/<build>/<region>/manifest.json             sizes, sha256s, pack meta and the elevation it expects, immutable
```

`<build>` is the build tag (`offroad-2026-10`). A region is added or rebuilt by publishing its folder and replacing its
entry in `current.json` (matched by `id`); other entries are kept. Elevation is not hosted here: the router reads
Mapterhorn's z12 tiles over the region's bbox (`elevation` in `current.json` and in each manifest), fetched from
Mapterhorn by the app. A region that is split for size (for example `california-north`) is simply two entries.

## `current.json`

```json
{"format": 1, "component": "offroad", "updated": "2026-10-30T03:00:00Z", "base_url": "https://data.cached.info/offroad/",
 "elevation": {"source": "Mapterhorn planet.pmtiles", "url": "https://download.mapterhorn.com/planet.pmtiles",
               "tilejson": "https://tiles.mapterhorn.com/tilejson.json", "minzoom": 12, "maxzoom": 12, "hosted_here": false,
               "attribution": "© Mapterhorn"},
 "regions": [
  {"id": "utah", "name": "Utah", "country": "US", "bbox": [-114.8, 36.5, -108.3, 42.5],
   "build": "offroad-2026-10", "built": "2026-10-30T01:38:00Z", "pack_format": 2, "land_access": "us",
   "mode_profile_hash": "<hex>", "bytes": 978800000,
   "files": {"offroad.sqlite":   {"path": "offroad-2026-10/utah/offroad.sqlite", "url": "https://data.cached.info/offroad/offroad-2026-10/utah/offroad.sqlite", "bytes": 978681856, "sha256": "<hex>"},
             "ATTRIBUTION.json": {"path": "...", "url": "...", "bytes": 70264, "sha256": "<hex>"},
             "manifest.json":    {"path": "...", "url": "...", "bytes": 0, "sha256": "<hex>"}},
   "elevation": {"tiles": 5400, "bytes": 520000000, "sha256": "<hex of the Mapterhorn extract>", "planet_md5": "<hex>"}}]}
```

- `bbox` is `[west, south, east, north]`: the region's boundary plus about 50 km, the area the pack covers.
- `path` is relative to `base_url`; `url` is the full address. `bytes` and `sha256` are of the file as served.
- `land_access`: `us` means PAD-US, USFS (MVUM) and BLM were used (they apply inside the US part of the bbox); `none` means
  no land-access data was used anywhere in the pack (outside the US, the `land_rules_unknown` case).
- `elevation`: the Mapterhorn z12 tiles over `bbox`. `sha256` is of `pmtiles extract --bbox=<bbox> --minzoom=12
  --maxzoom=12` of the planet with `planet_md5`, so a fetched copy can be checked.
- `bytes` (region) is the sum of the files listed; it does not include elevation.

## Publishing

`scripts/publish-offroad-regions.sh BUILD WORK SOURCE:REGION...` on a Docker host (navi-build CT 138), with the bucket's S3
keys in the environment (`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT`, `R2_BUCKET`, `R2_PUBLIC_URL`):

1. `publish_offroad.py stage`: download each region's assets from the pre-release (or a local folder), join parts, and
   check every part and file against the release manifest's sha256.
2. `publish_offroad.py catalogue`: write each region's `manifest.json` and the new `current.json` (the live one plus
   these regions).
3. rclone: the region folders (immutable), then `current.json` last.
4. `publish_offroad.py check`: every URL in `current.json` answers 200 with the listed size and 206 to a range request.

Region names are in `scripts/offroad/regions.tsv`. Nothing else in the bucket is touched.
