# public-lands

USGS PAD-US 4.1 (Protected Areas Database of the United States) as vector tiles. Layer `public_lands`, z4 to z12.

## Source

- Publisher: USGS Gap Analysis Project. DOI 10.5066/P96WBCHS.
- Verified on: ScienceBase item `652d4fc5d34e44db0e2ee45e` ("PAD-US 4.1 Full Inventory Database", https://www.sciencebase.gov/catalog/item/652d4fc5d34e44db0e2ee45e), listed through the ScienceBase item API, which shows `PADUS4_1Geodatabase.zip` (about 1.5 GB). The download is the documented ScienceBase file API: `https://www.sciencebase.gov/catalog/file/get/<item>?name=PADUS4_1Geodatabase.zip`.
- Feature class: `PADUS4_1Combined_Proclamation_Marine_Fee_Designation_Easement` (fields per the item's FGDC metadata).
- Licence: public domain (the item's metadata says "These data are in the public domain").
- To use a newer PAD-US, set the `PADUS_ITEM`, `PADUS_FILE` and `PADUS_LAYER` variables in the script.

## Steps

1. Check the file is listed on the ScienceBase item, download it, unzip.
2. `ogr2ogr` to GeoJSON-seq: reproject to EPSG:4326, filter by the bbox, clip to it (`-clipdst`, `-makevalid`), drop Z, keep `Unit_Nm, Mang_Name, Mang_Type, Des_Tp, Own_Type, Own_Name, Pub_Access, GAP_Sts, GIS_Acres, State_Nm, Category`.
3. Tile with tippecanoe:

```
tippecanoe -o public-lands.pmtiles -l public_lands -Z4 -z12 --drop-smallest-as-needed --simplification=2 --read-parallel --force pad.geojsonl
```

4. Upload with `scripts/publish_layer.py` (interval 90 days).

## Parameters

`region` (slug, default `idaho`) and `bbox` (`west,south,east,north`, default `-118.0,41.5,-110.3,49.5`, Idaho with a 50 km buffer). The layer id is `public-lands-<region>`. Run it from Actions: `gh workflow run layer-public-lands -f region=<slug> -f bbox=<w,s,e,n>`. It also runs on a schedule with the defaults.

## Output

`<id>/<id>.pmtiles` and `<id>/<id>.json` (layer definition, format 1) in the R2 bucket, and an entry in `catalogue.json`. Content types: `application/vnd.pmtiles` and `application/json`; Cache-Control `public, max-age=86400` for data and `public, max-age=300` for JSON. Upload uses the AWS CLI with the R2 endpoint and region `auto`. Tools: GDAL from the Ubuntu 24.04 runner image, tippecanoe 2.79.0 (built from the tag).

Scripts: `scripts/build-public-lands.sh`, `scripts/publish_layer.py`, `scripts/lib.sh`.
