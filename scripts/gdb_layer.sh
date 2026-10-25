# source me. gdb_layer GDB -> name of the first layer in a file geodatabase
gdb_layer() {
  local out; out="$(ogrinfo -ro -so "$1" 2>&1)" || true
  echo "gdb: $1" >&2; echo "$out" | head -8 >&2
  echo "$out" | sed -n -E 's/^(Layer: |[0-9]+: )([^ ]+) \(.*/\2/p' | head -1
}
# usfs_fetch NAME DIR -> downloads EDW file geodatabase NAME into DIR, prints the .gdb path
usfs_fetch() {
  local name="$1" dir="$2"
  mkdir -p "$dir"
  download "https://data.fs.usda.gov/geodata/edw/edw_resources/fc/$name.gdb.zip" "$dir/$name.zip" >&2
  unzip -q "$dir/$name.zip" -d "$dir" >&2; rm "$dir/$name.zip"
  find "$dir" -maxdepth 3 -name '*.gdb' -type d | head -1
}
# to_seq GDB LAYER OUT NAME [extra ogr2ogr args] -> clipped GeoJSON-seq in WGS84
to_seq() {
  ogr2ogr -f GeoJSONSeq "$3" "$1" "$2" -t_srs EPSG:4326 -dim XY -nlt PROMOTE_TO_MULTI \
    "${SPAT[@]}" -nln "$4" "${@:5}"
}
