#!/usr/bin/env bash
# Recipe: offroad-region (one off-road data file per region, plus its elevation extract). See recipes/offroad-region.md.
#
#   build-offroad-region.sh REGION WORK [STEP ...]
#
# Runs on a Docker host (navi-build CT 138); every tool runs in a pinned container, nothing is installed on the host.
# Env: BBOX=w,s,e,n (required); OSM_PBF (a Geofabrik .osm.pbf that covers BBOX, read only); CACHE_SRC (a checkout of
# the Cache repo, for offroad_pack); CPUS (default 8); AGENCY_LINES (1 = burn USFS and BLM lines into the trails
# layer, the default; 0 = OSM only).
# Steps, in order (default: all): fetch-worldcover fetch-osm fetch-padus fetch-usfs fetch-blm fetch-elevation
#   prep-osm prep-mvum prep-agency rasterise pack attribution
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
REGION="${1:?region slug}"; WORK="${2:?work dir}"; shift 2
[[ "$REGION" =~ ^[a-z0-9-]{1,32}$ ]] || { echo "bad region slug" >&2; exit 2; }
[[ "${BBOX:?BBOX=w,s,e,n}" =~ ^-?[0-9]+(\.[0-9]+)?,-?[0-9]+(\.[0-9]+)?,-?[0-9]+(\.[0-9]+)?,-?[0-9]+(\.[0-9]+)?$ ]] || { echo "bad bbox" >&2; exit 2; }
IFS=, read -r BB_W BB_S BB_E BB_N <<<"$BBOX"
STEPS=("$@")
[ ${#STEPS[@]} -gt 0 ] || STEPS=(fetch-worldcover fetch-osm fetch-padus fetch-usfs fetch-blm fetch-elevation prep-osm prep-mvum prep-agency rasterise pack attribution)
CPUS="${CPUS:-8}"; AGENCY_LINES="${AGENCY_LINES:-1}"

# Pinned tool images
IMG_GDAL=ghcr.io/osgeo/gdal:ubuntu-small-3.9.3
IMG_PY=python:3.12-slim
IMG_PMTILES=protomaps/go-pmtiles:v1.28.0
IMG_RUST=rust:1.90-slim
IMG_OSMIUM=cache-osmium:1            # built from scripts/offroad/osmium.Dockerfile (debian:12-slim + osmium-tool)

# Sources
WC_BASE=https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021
EDW=https://data.fs.usda.gov/geodata/edw/edw_resources
MAPTERHORN=https://download.mapterhorn.com

mkdir -p "$WORK"/{in,work,out}
WORK="$(cd "$WORK" && pwd)"
log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$WORK/steps.log" >&2; }
# run IMAGE [docker args --] CMD...: the work dir is /w, this repo is /r (read only)
run() { local img="$1"; shift; docker run --rm --cpus "$CPUS" -v "$WORK:/w" -v "$ROOT:/r:ro" -w /w "$img" "$@"; }
download() { curl -fL --retry 5 --retry-delay 10 --retry-all-errors -sS -o "$2" "$1"; }

fetch_worldcover() {
  local d="$WORK/in/worldcover"; mkdir -p "$d"
  download "$WC_BASE/esa_worldcover_grid.geojson" "$d/grid.geojson"
  run "$IMG_PY" python3 -I /r/scripts/offroad/worldcover_tiles.py /w/in/worldcover/grid.geojson "$BBOX" > "$d/tiles.txt"
  local t n=0
  while read -r t; do
    local f="ESA_WorldCover_10m_2021_v200_${t}_Map.tif"
    if [ ! -s "$d/$f" ]; then download "$WC_BASE/map/$f" "$d/$f.part"; mv "$d/$f.part" "$d/$f"; fi
    n=$((n + 1))
  done < "$d/tiles.txt"
  log "worldcover: $n tiles, $(du -sh "$d" | cut -f1)"
}

fetch_osm() {
  [ -s "${OSM_PBF:?OSM_PBF}" ] || { echo "OSM_PBF not found" >&2; exit 2; }
  mkdir -p "$WORK/in/osm"
  docker image inspect "$IMG_OSMIUM" >/dev/null 2>&1 || docker build -q -t "$IMG_OSMIUM" -f "$HERE/offroad/osmium.Dockerfile" "$HERE/offroad"
  local src; src="$(cd "$(dirname "$OSM_PBF")" && pwd)"
  docker run --rm --cpus "$CPUS" -v "$WORK:/w" -v "$src:/src:ro" "$IMG_OSMIUM" \
    osmium extract --overwrite -s complete_ways -b "$BBOX" "/src/$(basename "$OSM_PBF")" -o /w/in/osm/region.osm.pbf
  { echo "source $(basename "$OSM_PBF") $(stat -c '%s %y' "$OSM_PBF")"
    [ -s "$OSM_PBF.md5" ] && echo "md5 $(cut -d' ' -f1 "$OSM_PBF.md5")"
    docker run --rm -v "$WORK:/w" "$IMG_OSMIUM" osmium fileinfo -g header.option.osmosis_replication_timestamp /w/in/osm/region.osm.pbf | sed 's/^/replication_timestamp /'
  } > "$WORK/in/osm/source.txt" || true
  log "osm: $(du -h "$WORK/in/osm/region.osm.pbf" | cut -f1)"
}

fetch_padus() {
  run "$IMG_PY" env NATIONAL=0 BBOX="$BBOX" python3 -I /r/scripts/fetch_padus_service.py /w/in/padus.geojsonl
  log "padus: $(wc -l < "$WORK/in/padus.geojsonl") features"
}

fetch_usfs() {
  local d="$WORK/in/usfs"; mkdir -p "$d"
  local n
  for n in Trans_MVUM_Road Trans_MVUM_Trail Trans_Trail_NFS_Publish Trans_RoadCore_FS; do
    download "$EDW/fc/$n.gdb.zip" "$d/$n.gdb.zip"
    download "$EDW/meta/$n.xml" "$d/$n.xml" || true
  done
  log "usfs: $(du -sh "$d" | cut -f1)"
}

fetch_blm() {
  run "$IMG_PY" env NATIONAL=0 BBOX="$BBOX" python3 -I /r/scripts/fetch_blm_gtlf.py /w/in/blm.geojsonl
  log "blm: $(wc -l < "$WORK/in/blm.geojsonl") features"
}

fetch_elevation() {
  mkdir -p "$WORK/in/elevation"
  download "$MAPTERHORN/attribution.json" "$WORK/in/elevation/attribution.json"
  download "$MAPTERHORN/download_urls.json" "$WORK/in/elevation/download_urls.json"
  # Area extract of planet.pmtiles (z0-12) at z12 only: the router reads z12 alone.
  run "$IMG_PMTILES" extract --bbox="$BBOX" --minzoom=12 --maxzoom=12 "$MAPTERHORN/planet.pmtiles" /w/out/elevation.pmtiles
  log "elevation: $(du -h "$WORK/out/elevation.pmtiles" | cut -f1)"
}

prep_osm() {
  docker image inspect "$IMG_OSMIUM" >/dev/null 2>&1 || docker build -q -t "$IMG_OSMIUM" -f "$HERE/offroad/osmium.Dockerfile" "$HERE/offroad"
  local o=(--rm --cpus "$CPUS" -v "$WORK:/w" -v "$ROOT:/r:ro" -w /w "$IMG_OSMIUM")
  docker run "${o[@]}" osmium tags-filter --overwrite in/osm/region.osm.pbf w/highway -o work/highways.osm.pbf
  docker run "${o[@]}" osmium export --overwrite -c /r/scripts/offroad/osmium-lines.json -f geojsonseq --geometry-types=linestring \
    -x print_record_separator=false work/highways.osm.pbf -o work/highways.geojsonl
  docker run "${o[@]}" osmium tags-filter --overwrite in/osm/region.osm.pbf nwr/amenity=parking -o work/parking.osm.pbf
  docker run "${o[@]}" osmium export --overwrite -c /r/scripts/offroad/osmium-parking.json -f geojsonseq -a type,id \
    -x print_record_separator=false work/parking.osm.pbf -o work/parking.geojsonl
  run "$IMG_GDAL" python3 -I /r/scripts/offroad/prep_osm.py --rules /r/rules/offroad --bbox="$BBOX" \
    --highways work/highways.geojsonl --parking work/parking.geojsonl --out work
  log "prep-osm: $(cat "$WORK/work/prep_osm.counts")"
}

prep_mvum() {
  run "$IMG_GDAL" python3 -I /r/scripts/offroad/prep_mvum.py --usfs in/usfs --bbox="$BBOX" --out work/mvum.geojsonl
  log "prep-mvum: $(cat "$WORK/work/mvum.counts")"
}

prep_agency() {
  run "$IMG_GDAL" python3 -I /r/scripts/offroad/prep_agency.py --rules /r/rules/offroad --usfs in/usfs --blm in/blm.geojsonl \
    --bbox="$BBOX" --out work/agency_lines.geojsonl
  log "prep-agency: $(cat "$WORK/work/agency_lines.counts")"
}

# grid W S E N ANCHOR_X ANCHOR_Y -> -te args snapped outward to the 0.0003 degree grid through the anchor, plus 2 px
grid_te() {
  python3 -I -c 'import math,sys
w,s,e,n,ax,ay=map(float,sys.argv[1:]);p=0.0003
f=lambda v,a,up:a+p*(math.ceil(round((v-a)/p,6)) if up else math.floor(round((v-a)/p,6)))
print("%.4f %.4f %.4f %.4f"%(f(w,ax,0)-2*p,f(s,ay,0)-2*p,f(e,ax,1)+2*p,f(n,ay,1)+2*p))' "$@"
}

rasterise() {
  local r="$WORK/work/r"; mkdir -p "$r"
  local co=(-ot Byte -a_nodata none -init 0 -co COMPRESS=DEFLATE -co TILED=YES -co BLOCKXSIZE=512 -co BLOCKYSIZE=512 -co BIGTIFF=IF_SAFER)
  # trails: 0.0003 degree grid anchored at (-180, 90) like navi's trails.tif, ALL_TOUCHED (B0: 93.8% cell agreement with
  # navi's raster against 72.6% for the default mode); burn order 25, 15, 5 so the lower value wins
  local te_t; te_t=$(grid_te "$BB_W" "$BB_S" "$BB_E" "$BB_N" -180 90)
  rm -f "$r/trails.tif"
  local lines=(work/osm_lines.geojsonl); [ "$AGENCY_LINES" = 1 ] && lines+=(work/agency_lines.geojsonl)
  local first=1 v l
  for v in 25 15 5; do
    for l in "${lines[@]}"; do
      if [ $first = 1 ]; then
        # shellcheck disable=SC2086
        run "$IMG_GDAL" gdal_rasterize -q -at -burn "$v" -where "burn = $v" -te $te_t -tr 0.0003 0.0003 "${co[@]}" "/w/$l" /w/work/r/trails.tif
        first=0
      else
        run "$IMG_GDAL" gdal_rasterize -q -at -burn "$v" -where "burn = $v" "/w/$l" /w/work/r/trails.tif
      fi
    done
  done
  # barriers and wilderness: the same pixel size, anchored at (-125, 50) like navi's PAD-US rasters
  local te_p; te_p=$(grid_te "$BB_W" "$BB_S" "$BB_E" "$BB_N" -125 50)
  rm -f "$r/barriers.tif" "$r/wilderness.tif"
  # shellcheck disable=SC2086
  run "$IMG_GDAL" gdal_rasterize -q -burn 255 -where "Pub_Access = 'XA'" -te $te_p -tr 0.0003 0.0003 "${co[@]}" /w/in/padus.geojsonl /w/work/r/barriers.tif
  # shellcheck disable=SC2086
  run "$IMG_GDAL" gdal_rasterize -q -burn 255 -where "Des_Tp = 'WA'" -te $te_p -tr 0.0003 0.0003 "${co[@]}" /w/in/padus.geojsonl /w/work/r/wilderness.tif
  log "rasterise: $(du -ch "$r"/*.tif | tail -1 | cut -f1)"
}

pack() {
  : "${CACHE_SRC:?CACHE_SRC}"
  mkdir -p "$WORK/cargo" "$WORK/target"
  docker run --rm --cpus "$CPUS" -v "$CACHE_SRC/packages/cache_search:/src:ro" -v "$WORK/cargo:/usr/local/cargo/registry" \
    -v "$WORK/target:/t" -e CARGO_TARGET_DIR=/t -w /src/native "$IMG_RUST" cargo build -q --locked --release -j "$CPUS" -p offroad_pack
  local s="$BB_S,$BB_N,$BB_W,$BB_E"
  docker run --rm --cpus "$CPUS" -v "$WORK:/w" -v "$WORK/target:/t:ro" -v "$ROOT:/r:ro" -w /w "$IMG_RUST" \
    /t/release/offroad_pack build-sources --out /w/out/offroad.sqlite --bbox "$s" --threads "$CPUS" \
      --coverage-pmtiles /w/out/elevation.pmtiles --class-dir /w/in/worldcover --class-friction /r/rules/offroad/class_friction.tsv \
      --trails /w/work/r/trails.tif --barriers /w/work/r/barriers.tif --wilderness /w/work/r/wilderness.tif \
      --parking /w/work/parking.tsv --entry-points /w/work/entry_points.tsv --mvum /w/work/mvum.geojsonl
  log "pack: $(du -h "$WORK/out/offroad.sqlite" | cut -f1)"
}

attribution() {
  run "$IMG_PY" python3 -I /r/scripts/offroad/attribution.py --mapterhorn in/elevation/attribution.json \
    --urls in/elevation/download_urls.json --inputs in --out out/ATTRIBUTION.json
  log "attribution: out/ATTRIBUTION.json"
}

for s in "${STEPS[@]}"; do
  t0=$(date +%s)
  case "$s" in
    fetch-worldcover) fetch_worldcover ;; fetch-osm) fetch_osm ;; fetch-padus) fetch_padus ;; fetch-usfs) fetch_usfs ;;
    fetch-blm) fetch_blm ;; fetch-elevation) fetch_elevation ;; prep-osm) prep_osm ;; prep-mvum) prep_mvum ;;
    prep-agency) prep_agency ;; rasterise) rasterise ;; pack) pack ;; attribution) attribution ;;
    *) echo "unknown step $s" >&2; exit 2 ;;
  esac
  log "step $s done in $(( $(date +%s) - t0 ))s"
done
