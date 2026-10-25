# valhalla-routing

Region pack (not a tile layer): Valhalla routing tiles built from an OpenStreetMap extract, published as GitHub Release assets.

## Source

- Extract: Geofabrik, `https://download.geofabrik.de/north-america/us/idaho-latest.osm.pbf` (verified on https://download.geofabrik.de/north-america/us/idaho.html). The build resolves "latest" to the dated file, checks it against Geofabrik's `.md5`, and records the dated URL in the manifest.
- Licence: ODbL 1.0. Contains data (c) OpenStreetMap contributors.
- Builder: Valhalla's official container `ghcr.io/valhalla/valhalla-scripted:3.7.0`, Valhalla 3.7.0 (the version the app embeds; tags listed on https://github.com/valhalla/valhalla/pkgs/container/valhalla-scripted). Docker usage follows https://github.com/valhalla/valhalla/blob/master/docker/README.md.

## Steps

1. Download the dated extract and verify the md5.
2. Run the container with `serve_tiles=False force_rebuild=True build_tar=True build_admins=True build_time_zones=True build_elevation=False`; it writes `valhalla_tiles.tar`.
3. Split the tar into parts of at most 1900 MiB (`split -b 1900M`), always at least one part: `valhalla-<region>-<date>.tar.part00`, `.part01`, and so on. Reassemble with `cat` in order, then check the sha256.
4. Write `valhalla-<region>-manifest.json` (Valhalla version, source URL, tar size and sha256, each part with size, sha256 and URL).
5. Create (or update) the release `<region>-<date>` with the parts and the manifest.

## Parameters

`region` (slug, default `idaho`) and `geofabrik_path` (default `north-america/us/idaho`). Run: `gh workflow run valhalla-routing -f region=<slug> -f geofabrik_path=<path>`. Also scheduled quarterly. Script: `scripts/build-valhalla-routing.sh`.
