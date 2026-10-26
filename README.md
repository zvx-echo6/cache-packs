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

Layers are national builds (the whole US, one file per layer); routing packs are per region. Built 2026-10-25. The tile host also carries `catalogue.json`, which lists the layers and the routing packs.

| Recipe | Kind | Output |
|---|---|---|
| [public-lands](recipes/public-lands.md) | PMTiles layer (USGS PAD-US 4.1), z4-z12 | built, 392 MB |
| [usfs-trails-roads](recipes/usfs-trails-roads.md) | PMTiles layer (USFS EDW), z6-z14 | built, 500 MB |
| [mvum](recipes/mvum.md) | PMTiles layer plus GeoJSON-seq (USFS MVUM), z8-z14 | built |
| [blm-routes](recipes/blm-routes.md) | PMTiles layer (BLM GTLF public datasets), z6-z14 | built, 98 MB |
| [valhalla-routing](recipes/valhalla-routing.md) | Release assets, one release per region (Valhalla 3.7.0) | Idaho, Montana, Utah, Oregon, Washington, Nevada, Wyoming |
| [regions](recipes/regions.md) | Region catalogue JSON plus one polygon per region (Overture divisions, US Census metro areas) | worldwide, see recipe |
| [basemap-planet](recipes/basemap-planet.md) | Protomaps v4 planet base map, z0-z15, copied to the tile host | see the catalogue |

Not yet built: places, the off-road data file.
