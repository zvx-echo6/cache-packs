# Shared helpers, sourced by the build scripts. Validates the region parameters.
# REGION=us (the default) is the national build: no area filter, layer id without a suffix.
# Any other REGION is a test/regional run: BBOX (default Idaho + 50 km) filters and clips, layer id gets "-<region>".
set -euo pipefail
REGION="${REGION:-us}"
[[ "$REGION" =~ ^[a-z0-9-]{1,32}$ ]] || { echo "bad region slug" >&2; exit 2; }
if [ "$REGION" = us ]; then
  NATIONAL=1; LAYER_SUFFIX=""; BBOX="${BBOX:--180,-14.6,180,71.5}"   # BBOX here is only the coverage written to the definition
else
  NATIONAL=0; LAYER_SUFFIX="-$REGION"; BBOX="${BBOX:--118.0,41.5,-110.3,49.5}"
fi
[[ "$BBOX" =~ ^-?[0-9]+(\.[0-9]+)?,-?[0-9]+(\.[0-9]+)?,-?[0-9]+(\.[0-9]+)?,-?[0-9]+(\.[0-9]+)?$ ]] || { echo "bad bbox" >&2; exit 2; }
IFS=, read -r BB_W BB_S BB_E BB_N <<<"$BBOX"
# SPAT: ogr2ogr area filter args; CLIP: tippecanoe clip args (both empty for national builds)
SPAT=(); CLIP=()
if [ "$NATIONAL" = 0 ]; then
  SPAT=(-spat "$BB_W" "$BB_S" "$BB_E" "$BB_N" -spat_srs EPSG:4326)
  CLIP=(--clip-bounding-box="$BBOX")
fi
export REGION NATIONAL LAYER_SUFFIX BBOX BB_W BB_S BB_E BB_N
VERSION="$(date -u +%Y-%m-%d)"
export VERSION
# download URL DEST: retrying download that fails on HTTP errors
download() { curl -fL --retry 5 --retry-delay 10 --retry-all-errors -sS -o "$2" "$1"; }
