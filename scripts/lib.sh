# Shared helpers, sourced by the build scripts. Validates the region parameters.
set -euo pipefail
REGION="${REGION:-idaho}"
BBOX="${BBOX:--118.0,41.5,-110.3,49.5}"
[[ "$REGION" =~ ^[a-z0-9-]{1,32}$ ]] || { echo "bad region slug" >&2; exit 2; }
[[ "$BBOX" =~ ^-?[0-9]+(\.[0-9]+)?,-?[0-9]+(\.[0-9]+)?,-?[0-9]+(\.[0-9]+)?,-?[0-9]+(\.[0-9]+)?$ ]] || { echo "bad bbox" >&2; exit 2; }
IFS=, read -r BB_W BB_S BB_E BB_N <<<"$BBOX"
export REGION BBOX BB_W BB_S BB_E BB_N
VERSION="$(date -u +%Y-%m-%d)"
export VERSION
# download URL DEST: retrying download that fails on HTTP errors
download() { curl -fL --retry 5 --retry-delay 10 --retry-all-errors -sS -o "$2" "$1"; }
