# mvum

USFS Motor Vehicle Use Map roads and trails. One PMTiles file with layers `mvum_roads` and `mvum_trails`, z8 to z14, keeping every per-vehicle-class status and season-dates field. The same features are also published as a gzip-compressed GeoJSON-seq file, `mvum.geojsonl.gz`, next to the PMTiles (one feature per line; `mvum_kind` is `road` or `trail`), for the off-road router's rules.

## Source

- Publisher: USDA Forest Service, FSGeodata Clearinghouse.
- Verified on: https://data.fs.usda.gov/geodata/edw/datasets.php (keyword "Motor Vehicle Use Map"). Files:
  - `https://data.fs.usda.gov/geodata/edw/edw_resources/fc/Trans_MVUM_Road.gdb.zip`
  - `https://data.fs.usda.gov/geodata/edw/edw_resources/fc/Trans_MVUM_Trail.gdb.zip`
  - metadata: `.../edw_resources/meta/Trans_MVUM_Road.xml` and `Trans_MVUM_Trail.xml`.
- Licence: US Government data, no-warranty disclaimer in the USFS metadata.

## Steps

1. Download and unzip both geodatabases.
2. `ogr2ogr` to GeoJSON-seq (EPSG:4326, bbox filter), then `jq` sets `mvum_kind` to `road` or `trail` on each feature.
3. Tile with tippecanoe:

```
tippecanoe -o mvum.pmtiles -Z8 -z14 --drop-densest-as-needed --read-parallel --force --clip-bounding-box=<bbox> -x OBJECTID -x GLOBALID -x SECURITYID -x SHAPE_LEN -x SHAPE.LEN -x Shape_Length -L mvum_roads:mvum_roads.geojsonl -L mvum_trails:mvum_trails.geojsonl
```

4. `cat mvum_roads.geojsonl mvum_trails.geojsonl | gzip -9 > mvum.geojsonl.gz`, then upload both with `scripts/publish_layer.py` (interval 30 days; the seq file is listed in the definition as `extra_files`).

## Parameters

`region` (default `us`, the national build: no area filter, layer id without a suffix) and `bbox`. Any other region slug is a test run: `bbox` (`west,south,east,north`, default `-118.0,41.5,-110.3,49.5`, Idaho with a 50 km buffer) filters and clips, and the layer id gets `-<region>`. Run it from Actions: `gh workflow run WF` for the national build, or `gh workflow run WF -f region=<slug> -f bbox=<w,s,e,n>` for a test. It also runs monthly or quarterly on a schedule.

## Output

`<id>/<id>.pmtiles` and `<id>/<id>.json` (`<id>` is the recipe name for the national build) (layer definition, format 1) in the R2 bucket, and an entry in `catalogue.json`. Content types: `application/vnd.pmtiles` and `application/json`; Cache-Control `public, max-age=86400` for data and `public, max-age=300` for JSON. Upload uses the AWS CLI with the R2 endpoint and region `auto`. Tools: GDAL from the Ubuntu 24.04 runner image, tippecanoe 2.79.0 (built from the tag).

Scripts: `scripts/build-mvum.sh`, `scripts/publish_layer.py`, `scripts/lib.sh`.
