#!/usr/bin/env bash
# Recipe: mvum (USFS Motor Vehicle Use Map roads and trails). See recipes/mvum.md.
source "$(dirname "$0")/lib.sh"; source "$(dirname "$0")/gdb_layer.sh"
W="$RUNNER_TEMP/mvum"; mkdir -p "$W"
RGDB="$(usfs_fetch Trans_MVUM_Road "$W/roads")";   RL="$(gdb_layer "$RGDB")"; echo "roads layer: $RL"
TGDB="$(usfs_fetch Trans_MVUM_Trail "$W/trails")"; TL="$(gdb_layer "$TGDB")"; echo "trails layer: $TL"
# mvum_kind marks which dataset a feature came from (kept in the GeoJSON-seq for the router)
to_seq "$RGDB" "$RL" "$W/roads_raw.geojsonl" mvum_roads
to_seq "$TGDB" "$TL" "$W/trails_raw.geojsonl" mvum_trails
jq -c '.properties.mvum_kind = "road"' "$W/roads_raw.geojsonl" > "$W/mvum_roads.geojsonl"
jq -c '.properties.mvum_kind = "trail"' "$W/trails_raw.geojsonl" > "$W/mvum_trails.geojsonl"
wc -l "$W"/*.geojsonl
tippecanoe -q -o "$W/mvum.pmtiles" -Z8 -z14 --drop-densest-as-needed --read-parallel --force \
  "${CLIP[@]}" -x OBJECTID -x GLOBALID -x SECURITYID -x SHAPE_LEN -x SHAPE.LEN -x Shape_Length \
  -L mvum_roads:"$W/mvum_roads.geojsonl" -L mvum_trails:"$W/mvum_trails.geojsonl"
cat "$W/mvum_roads.geojsonl" "$W/mvum_trails.geojsonl" | gzip -9 > "$W/mvum.geojsonl.gz"
ls -l "$W/mvum.pmtiles" "$W/mvum.geojsonl.gz"
python3 "$(dirname "$0")/publish_layer.py" --id "mvum$LAYER_SUFFIX" \
  --title "Motor vehicle use map (MVUM)" \
  --description "Forest Service roads and trails open to motor vehicles, by vehicle class and season." \
  --pmtiles "$W/mvum.pmtiles" --minzoom 8 --maxzoom 14 \
  --attribution "USDA Forest Service, Motor Vehicle Use Map (Enterprise Data Warehouse)" \
  --license "Public domain (US Government data); provided without warranty by the USDA Forest Service" \
  --interval-days 30 --role mvum --recipe mvum.md --extra "$W/mvum.geojsonl.gz:application/gzip" \
  --style-hints '[{"name":"mvum_roads","attributes":["NAME","SYMBOL","MVUM_SYMBOL_NAME","SEASONAL","PASSENGERVEHICLE","HIGHCLEARANCEVEHICLE","ATV","MOTORCYCLE","FOURWD_GT50INCHES","PASSENGERVEHICLE_DATESOPEN","ATV_DATESOPEN","MOTORCYCLE_DATESOPEN"]},{"name":"mvum_trails","attributes":["NAME","SYMBOL","MVUM_SYMBOL_NAME","SEASONAL","ATV","MOTORCYCLE","FOURWD_GT50INCHES","TRAILCLASS","ATV_DATESOPEN","MOTORCYCLE_DATESOPEN"]}]'
