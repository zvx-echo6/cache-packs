# Cache region packs

This repository holds region data packs for Cache, an offline preparedness app. A pack contains only the data the app cannot fetch from a public source on its own:

- road routing tiles for Valhalla,
- an off-road data file,
- places.

The packs are built from public open data and published as GitHub Release assets. There is one release per region and date, and every file is under 2 GiB (larger data is split into parts).

## Data sources and licences

- OpenStreetMap: ODbL. Contains data © OpenStreetMap contributors. See https://www.openstreetmap.org/copyright
- Overture Maps: used for places, under the licences Overture publishes for each theme. See https://docs.overturemaps.org/attribution/
- PAD-US (USGS land ownership): public domain.
- USFS MVUM (Forest Service motor vehicle use maps): public domain.
- ESA WorldCover: CC BY 4.0, where land cover is included in a pack. Contains ESA WorldCover data, (c) ESA WorldCover project, CC BY 4.0.
- Elevation: AWS Terrain Tiles (Terrarium encoding), from AWS Open Data. See https://registry.opendata.aws/terrain-tiles/ for the attribution it requires.

## Status

First batch built for Idaho (bbox -118.0,41.5,-110.3,49.5, 50 km buffer) on 2026-10-25. Each recipe has a workflow (run by hand or on a schedule) and a document in `recipes/`.

| Recipe | Kind | Output |
|---|---|---|
| [public-lands](recipes/public-lands.md) | PMTiles layer (USGS PAD-US 4.1) | built, 23 MB |
| [usfs-trails-roads](recipes/usfs-trails-roads.md) | PMTiles layer (USFS EDW) | built, 113 MB |
| [mvum](recipes/mvum.md) | PMTiles layer plus GeoJSON-seq (USFS MVUM) | built, 76 MB plus 33 MB |
| [blm-routes](recipes/blm-routes.md) | PMTiles layer (BLM GTLF public datasets) | built, 15 MB |
| [valhalla-routing](recipes/valhalla-routing.md) | Release assets (Valhalla 3.7.0 tiles) | release `idaho-2026-10-25`, 222 MB |

Layers are published to the tile host with a layer definition each and a `catalogue.json` listing them. Not yet built: other regions, places, the off-road data file, land cover.
