# usfs-trails-roads

National Forest System trails and roads from the USFS Enterprise Data Warehouse (EDW). One PMTiles file, layers `trails` and `roads`, z6 to z14.

## Source

- Publisher: USDA Forest Service, FSGeodata Clearinghouse.
- Verified on: https://data.fs.usda.gov/geodata/edw/datasets.php (searched for "National Forest System Trails" and "National Forest System Roads"). The page lists the file geodatabases used:
  - `https://data.fs.usda.gov/geodata/edw/edw_resources/fc/Trans_Trail_NFS_Publish.gdb.zip` (NFS Trails)
  - `https://data.fs.usda.gov/geodata/edw/edw_resources/fc/Trans_RoadCore_FS.gdb.zip` (Road Core)
  - metadata: `.../edw_resources/meta/<name>.xml` (publication date 2026-10-18 at the time of writing).
- Licence: US Government data. The USFS metadata carries a no-warranty disclaimer; the data are not legal documents.

## Steps

1. Download and unzip both geodatabases.
2. `ogr2ogr` to GeoJSON-seq per dataset: EPSG:4326, bbox filter, keep the fields listed in the script (trail name, number, type, class, surface, per-use managed flags; road name, id, system, maintenance level, surface, status).
3. Tile with tippecanoe:

```
tippecanoe -o usfs.pmtiles -Z6 -z14 --drop-densest-as-needed --read-parallel --force --clip-bounding-box=<bbox> -L trails:trails.geojsonl -L roads:roads.geojsonl
```

4. Upload with `scripts/publish_layer.py` (interval 30 days).

## Parameters

`region` (default `us`, the national build: no area filter, layer id without a suffix) and `bbox`. Any other region slug is a test run: `bbox` (`west,south,east,north`, default `-118.0,41.5,-110.3,49.5`, Idaho with a 50 km buffer) filters and clips, and the layer id gets `-<region>`. Run it from Actions: `gh workflow run WF` for the national build, or `gh workflow run WF -f region=<slug> -f bbox=<w,s,e,n>` for a test. It also runs monthly or quarterly on a schedule.

## Output

`<id>/<id>.pmtiles` and `<id>/<id>.json` (`<id>` is the recipe name for the national build) (layer definition, format 1) in the R2 bucket, and an entry in `catalogue.json`. Content types: `application/vnd.pmtiles` and `application/json`; Cache-Control `public, max-age=86400` for data and `public, max-age=300` for JSON. Upload uses the AWS CLI with the R2 endpoint and region `auto`. Tools: GDAL from the Ubuntu 24.04 runner image, tippecanoe 2.79.0 (built from the tag).

Scripts: `scripts/build-usfs-trails-roads.sh`, `scripts/publish_layer.py`, `scripts/lib.sh`.
