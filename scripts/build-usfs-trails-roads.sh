#!/usr/bin/env bash
# Recipe: usfs-trails-roads (USFS EDW NFS trails and road core). See recipes/usfs-trails-roads.md.
source "$(dirname "$0")/lib.sh"; source "$(dirname "$0")/gdb_layer.sh"
W="$RUNNER_TEMP/usfs"; mkdir -p "$W"
TGDB="$(usfs_fetch Trans_Trail_NFS_Publish "$W/trails")"; TL="$(gdb_layer "$TGDB")"; echo "trails layer: $TL"
RGDB="$(usfs_fetch Trans_RoadCore_FS "$W/roads")";       RL="$(gdb_layer "$RGDB")"; echo "roads layer: $RL"
to_seq "$TGDB" "$TL" "$W/trails.geojsonl" trails -select TRAIL_NAME,TRAIL_NO,TRAIL_TYPE,TRAIL_CLASS,TRAIL_SURFACE,MANAGING_ORG,NATIONAL_TRAIL_DESIGNATION,ATTRIBUTESUBSET,ALLOWED_TERRA_USE,ALLOWED_SNOW_USE,HIKER_PEDESTRIAN_MANAGED,PACK_SADDLE_MANAGED,BICYCLE_MANAGED,MOTORCYCLE_MANAGED,ATV_MANAGED,FOURWD_MANAGED,SNOWMOBILE_MANAGED,GIS_MILES
to_seq "$RGDB" "$RL" "$W/roads.geojsonl" roads -select NAME,ID,SYSTEM,JURISDICTION,ROUTE_STATUS,OPER_MAINT_LEVEL,SURFACE_TYPE,SYMBOL_CODE,OPENFORUSETO,MANAGING_ORG,GIS_MILES
wc -l "$W"/*.geojsonl
tippecanoe -q -o "$W/usfs.pmtiles" -Z6 -z14 --drop-densest-as-needed --read-parallel --force \
  "${CLIP[@]}" \
  -L trails:"$W/trails.geojsonl" -L roads:"$W/roads.geojsonl"
ls -l "$W/usfs.pmtiles"
python3 "$(dirname "$0")/publish_layer.py" --id "usfs-trails-roads$LAYER_SUFFIX" \
  --title "Forest Service trails and roads" \
  --description "National Forest System trails and roads from the USFS Enterprise Data Warehouse." \
  --pmtiles "$W/usfs.pmtiles" --minzoom 6 --maxzoom 14 \
  --attribution "USDA Forest Service, Enterprise Data Warehouse (NFS Trails and Road Core)" \
  --license "Public domain (US Government data); provided without warranty by the USDA Forest Service" \
  --interval-days 30 --role trails --recipe usfs-trails-roads.md \
  --style-hints '[{"name":"trails","role":"trails","attributes":["TRAIL_NAME","TRAIL_TYPE","TRAIL_CLASS","ALLOWED_TERRA_USE","MOTORCYCLE_MANAGED","ATV_MANAGED","BICYCLE_MANAGED","HIKER_PEDESTRIAN_MANAGED"]},{"name":"roads","role":"roads","attributes":["NAME","SYSTEM","OPER_MAINT_LEVEL","SURFACE_TYPE","ROUTE_STATUS","OPENFORUSETO"]}]'
