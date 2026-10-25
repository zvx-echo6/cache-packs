# public-lands

USGS PAD-US 4.1 (Protected Areas Database of the United States) as vector tiles. Layer `public_lands`, z4 to z12.

## Source

- Publisher: USGS Gap Analysis Project. PAD-US 4.1, DOI 10.5066/P96WBCHS. Licence: public domain (the FGDC metadata says "These data are in the public domain").
- Used: the USGS "PAD-US Manager Name" ArcGIS feature service (`https://services.arcgis.com/v01gqwM5QqNysAAi/arcgis/rest/services/Manager_Name_PADUS/FeatureServer/0`, layer `PADUS4_1ManagerName`, 655,335 polygons nationwide including Alaska, Hawaii and the territories). It is built from the PAD-US Combined (Proclamation, Marine, Fee, Designation, Easement) feature class and is linked from the USGS PAD-US Web Services page (https://www.usgs.gov/programs/gap-analysis-project/science/pad-us-data-web-services); the same page set (https://www.usgs.gov/programs/gap-analysis-project/science/pad-us-data-download) points to the ScienceBase item for the file geodatabase. Queried through the documented REST query API, 500 features per page, paced under the service's limit of 60 large-geometry requests a minute.
- Why not the geodatabase: ScienceBase item `652d4fc5d34e44db0e2ee45e` (`PADUS4_1Geodatabase.zip`, 1.5 GB) now puts a captcha in front of its download, so it cannot be fetched by a workflow. The script still supports it with `PADUS_SOURCE=gdb` if the download opens again.

## Steps

1. `scripts/fetch_padus_service.py` pages the whole service into GeoJSON-seq (EPSG:4326, coordinate precision 6 decimals), keeping `Unit_Nm, Mang_Name, Mang_Type, Des_Tp, Own_Type, Own_Name, Pub_Access, GAP_Sts, GIS_Acres, State_Nm, Category`. Test runs add a bbox filter.
3. Tile with tippecanoe:

```
tippecanoe -o public-lands.pmtiles -l public_lands -Z4 -z12 --drop-smallest-as-needed --simplification=2 --read-parallel --force pad.geojsonl   # test runs add --clip-bounding-box=<bbox>
```

4. Upload with `scripts/publish_layer.py` (interval 90 days).

## Parameters

`region` (default `us`, the national build: no area filter, layer id without a suffix) and `bbox`. Any other region slug is a test run: `bbox` (`west,south,east,north`, default `-118.0,41.5,-110.3,49.5`, Idaho with a 50 km buffer) filters and clips, and the layer id gets `-<region>`. Run it from Actions: `gh workflow run WF` for the national build, or `gh workflow run WF -f region=<slug> -f bbox=<w,s,e,n>` for a test. It also runs monthly or quarterly on a schedule.

## Output

`<id>/<id>.pmtiles` and `<id>/<id>.json` (`<id>` is the recipe name for the national build) (layer definition, format 1) in the R2 bucket, and an entry in `catalogue.json`. Content types: `application/vnd.pmtiles` and `application/json`; Cache-Control `public, max-age=86400` for data and `public, max-age=300` for JSON. Upload uses the AWS CLI with the R2 endpoint and region `auto`. Tools: GDAL from the Ubuntu 24.04 runner image, tippecanoe 2.79.0 (built from the tag).

Scripts: `scripts/build-public-lands.sh`, `scripts/publish_layer.py`, `scripts/lib.sh`.
