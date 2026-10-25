#!/usr/bin/env bash
# Recipe: public-lands (USGS PAD-US). See recipes/public-lands.md.
source "$(dirname "$0")/lib.sh"
ITEM="${PADUS_ITEM:-652d4fc5d34e44db0e2ee45e}"      # ScienceBase item: PAD-US 4.1 Full Inventory Database
FILE="${PADUS_FILE:-PADUS4_1Geodatabase.zip}"
FC="${PADUS_LAYER:-PADUS4_1Combined_Proclamation_Marine_Fee_Designation_Easement}"
W="$RUNNER_TEMP/public-lands"; mkdir -p "$W/src"

# confirm the file is listed on the official ScienceBase item before downloading it
curl -fsS "https://www.sciencebase.gov/catalog/item/$ITEM?format=json&fields=title,files" \
  | jq -e --arg f "$FILE" --arg i "$ITEM" '.files[] | select(.name==$f) | {item:$i, name, size}'
download "https://www.sciencebase.gov/catalog/file/get/$ITEM?name=$FILE" "$W/padus.zip"
ls -l "$W/padus.zip"
unzip -q "$W/padus.zip" -d "$W/src"; rm "$W/padus.zip"
GDB="$(find "$W/src" -maxdepth 3 -name '*.gdb' -type d | head -1)"
ogrinfo -ro -so -q "$GDB" | head -20

df -h "$RUNNER_TEMP" | tail -1
# national: whole inventory, split at the antimeridian (Aleutians); regional: bbox filter and clip
EXTRA=(-wrapdateline)
[ "$NATIONAL" = 0 ] && EXTRA=(-clipdst "$BB_W" "$BB_S" "$BB_E" "$BB_N")
ogr2ogr -f GeoJSONSeq "$W/pad.geojsonl" "$GDB" "$FC" \
  -t_srs EPSG:4326 "${SPAT[@]}" "${EXTRA[@]}" -makevalid -dim XY -nlt MULTIPOLYGON \
  -select Unit_Nm,Mang_Name,Mang_Type,Des_Tp,Own_Type,Own_Name,Pub_Access,GAP_Sts,GIS_Acres,State_Nm,Category \
  -nln public_lands
wc -l "$W/pad.geojsonl"; ls -l "$W/pad.geojsonl"; rm -rf "$W/src"; df -h "$RUNNER_TEMP" | tail -1

tippecanoe -q -o "$W/public-lands.pmtiles" -l public_lands -Z4 -z12 \
  --drop-smallest-as-needed --simplification=2 --read-parallel --force "$W/pad.geojsonl"
ls -l "$W/public-lands.pmtiles"; df -h "$RUNNER_TEMP" | tail -1

python3 "$(dirname "$0")/publish_layer.py" --id "public-lands$LAYER_SUFFIX" \
  --title "Public lands (PAD-US)" \
  --description "Public land ownership, managers and designations from USGS PAD-US 4.1." \
  --pmtiles "$W/public-lands.pmtiles" --minzoom 4 --maxzoom 12 \
  --attribution "USGS Gap Analysis Project, PAD-US 4.1 (doi:10.5066/P96WBCHS)" \
  --license "Public domain (US Government data)" --interval-days 90 --recipe public-lands.md \
  --style-hints '[{"name":"public_lands","attributes":["Mang_Name","Mang_Type","Own_Type","Des_Tp","GAP_Sts","Pub_Access","Unit_Nm","GIS_Acres"]}]'
