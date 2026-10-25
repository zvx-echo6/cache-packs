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

No releases exist yet.
