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
- BLM GTLF (BLM roads and trails): provided as is; the BLM is cited as the source, modifications are described in the recipe, and the data are not endorsed by the BLM.
- Elevation: Mapterhorn terrain tiles (Terrarium-encoded lossless WebP), "© Mapterhorn", from many open national sources listed at https://mapterhorn.com/attribution (the off-road pack carries the list from Mapterhorn's attribution.json).

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
| [offroad-region](recipes/offroad-region.md) | Off-road data file (`offroad.sqlite`, pack format 2) plus a Mapterhorn z12 elevation extract, per region | Idaho test build on navi-build, not published |

Not yet built: places. The off-road data file has a test build only (Idaho, below).

## Off-road Idaho test build (2026-10-29)

Built on navi-build CT 138 with `BBOX=-118.0,41.5,-110.3,49.5` (Idaho plus 50 km), `AGENCY_LINES=1`, OSM from Geofabrik `north-america-latest.osm.pbf` (2026-10-18). Not published.

| File | Size | Contents |
|---|---|---|
| `offroad.sqlite` | 1,023 MB | 11,748 z12 tiles; class 151 MB, friction 138 MB, trails 30 MB, barriers and wilderness 0.6 MB; 1,530,226 trailheads, 26,168 parking, 31,787 MVUM features (121 MB of f64 geometry) |
| `elevation.pmtiles` | 1,113 MB | 11,748 Mapterhorn z12 tiles (lossless WebP), extracted in 26 s |

Step times (4-6 CPUs): fetches 0.5-6 min each and in parallel (the OSM cut from the North America file is the longest), prep 1 min each, rasterise 4.5 min, pack 3 min.

Checks against navi's inputs (B0, read-only): class -> friction is a pure lookup over 3.9 billion pixels; PAD-US barriers agree on 98.9% and wilderness on 99.9% of the cells that are set in either; OSM-only trails agree on 93.8% of such cells in central Idaho with `ALL_TOUCHED` (72.6% without); trailheads on the Idaho extract are 773,336 against navi's 740,430 (+4.4%), and 97.8% of navi's points lie within 1 m of a new one.

