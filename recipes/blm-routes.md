# blm-routes

BLM public roads and trails from the National Ground Transportation Linear Features (GTLF) public datasets. One PMTiles file, layer `blm_routes`, z6 to z14. Each feature keeps a `subset` attribute naming the BLM dataset it came from.

## Source

- Publisher: Bureau of Land Management, BLM Geospatial Business Platform hub (https://gbp-blm-egis.hub.arcgis.com, search "BLM Natl GTLF").
- Verified on: the hub's dataset records (for example `f94999eb674d4085be0c86729fe4a151_3`, "BLM Natl GTLF Public Motorized Roads") and the owner's FeatureServer listing at `https://services1.arcgis.com/KbxwQRRfWyEYLgp4/ArcGIS/rest/services`.
- Access: the documented ArcGIS REST query API (`.../FeatureServer/<layer>/query`), paged with `resultOffset` and `resultRecordCount` (1000, below the 2000 `maxRecordCount`), filtered by the bbox envelope. The hub's file downloads were not used.
- Services (`subset` value: service, layer id): `motorized_road`: Public_Motorized_Roads, 3; `limited_motorized_road`: Limited_Public_Motorized_Roads, 0; `motorized_trail`: Public_Motorized_Trails, 4; `limited_motorized_trail`: Limited_Public_Motorized_Trails, 1; `nonmechanized_trail`: Public_Nonmechanized_Trails, 5; `nonmotorized_trail`: Public_Nonmotorized_Trails, 6; `managed_trail`: Public_Managed_Trails, 2; `not_assessed_trail`: Public_Not_Assessed_Trails, 7 (all `BLM_Natl_GTLF_<name>`).

## Licence and required statements

The BLM provides these data "as is", with no warranty. Terms from the dataset record: the BLM should be cited as the data source in any product derived from the data; anyone modifying the data should describe the modifications; do not misrepresent the data or imply that changes were approved or endorsed by the BLM.

- Citation: Bureau of Land Management, BLM National GTLF public datasets.
- Modifications made here: features were selected by the region bbox, only the attributes listed in `scripts/fetch_blm_gtlf.py` were kept (empty values removed), a `subset` attribute was added, the eight datasets were merged into one layer, geometry was clipped to the bbox and simplified by tippecanoe for tiling.
- This layer is not approved or endorsed by the BLM.

These statements are in the layer definition's `attribution` and `license` fields.

## Steps

1. `scripts/fetch_blm_gtlf.py` pages every service into GeoJSON-seq (EPSG:4326).
2. Tile with tippecanoe:

```
tippecanoe -o blm-routes.pmtiles -l blm_routes -Z6 -z14 --drop-densest-as-needed --read-parallel --force --clip-bounding-box=<bbox> blm_routes.geojsonl
```

3. Upload with `scripts/publish_layer.py` (interval 30 days).

## Parameters

`region` (default `us`, the national build: no area filter, layer id without a suffix) and `bbox`. Any other region slug is a test run: `bbox` (`west,south,east,north`, default `-118.0,41.5,-110.3,49.5`, Idaho with a 50 km buffer) filters and clips, and the layer id gets `-<region>`. Run it from Actions: `gh workflow run WF` for the national build, or `gh workflow run WF -f region=<slug> -f bbox=<w,s,e,n>` for a test. It also runs monthly or quarterly on a schedule.

## Output

`<id>/<id>.pmtiles` and `<id>/<id>.json` (`<id>` is the recipe name for the national build) (layer definition, format 1) in the R2 bucket, and an entry in `catalogue.json`. Content types: `application/vnd.pmtiles` and `application/json`; Cache-Control `public, max-age=86400` for data and `public, max-age=300` for JSON. Upload uses the AWS CLI with the R2 endpoint and region `auto`. Tools: GDAL from the Ubuntu 24.04 runner image, tippecanoe 2.79.0 (built from the tag).

Scripts: `scripts/build-blm-routes.sh`, `scripts/publish_layer.py`, `scripts/lib.sh`.
