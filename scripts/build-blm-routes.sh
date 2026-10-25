#!/usr/bin/env bash
# Recipe: blm-routes (BLM National GTLF public datasets). See recipes/blm-routes.md.
source "$(dirname "$0")/lib.sh"
W="$RUNNER_TEMP/blm"; mkdir -p "$W"
python3 "$(dirname "$0")/fetch_blm_gtlf.py" "$W/blm_routes.geojsonl"
wc -l "$W/blm_routes.geojsonl"
tippecanoe -q -o "$W/blm-routes.pmtiles" -l blm_routes -Z6 -z14 --drop-densest-as-needed --read-parallel --force \
  "${CLIP[@]}" "$W/blm_routes.geojsonl"
ls -l "$W/blm-routes.pmtiles"
python3 "$(dirname "$0")/publish_layer.py" --id "blm-routes$LAYER_SUFFIX" \
  --title "BLM roads and trails" \
  --description "BLM public roads and trails (GTLF public datasets) with route name, surface and designation." \
  --pmtiles "$W/blm-routes.pmtiles" --minzoom 6 --maxzoom 14 \
  --attribution "Source: Bureau of Land Management (BLM National GTLF public datasets). Data were clipped to the region and re-tiled by Cache; attributes were kept but not otherwise changed. Not approved or endorsed by the BLM." \
  --license "BLM data provided as is, with no warranty. The BLM must be cited as the data source; modifications are described here and in the recipe; this layer does not imply BLM endorsement." \
  --interval-days 30 --recipe blm-routes.md \
  --style-hints '[{"name":"blm_routes","attributes":["subset","FLTP_CODE","ROUTE_PRMRY_NM","OBSRVE_SRFCE_TYPE","PLAN_OHV_ROUTE_DSGNTN","OHV_ROUTE_DSGNTN_LIM","ROUTE_SPCL_DSGNTN_TYPE","PLAN_ACCESS_RSTRCT","PLAN_SEASON_RSTRCT_CODE"]}]'
