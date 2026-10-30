#!/usr/bin/env bash
# Publishes off-road region packs to the data host (recipes/offroad-regions.md).
#
#   publish-offroad-regions.sh BUILD WORK REGION=SOURCE ...
#
# SOURCE is "release" (the cache-packs pre-release named BUILD) or a local folder with the same REGION-* assets
# (scripts/offroad/release_assets.py). Runs on a Docker host; python and rclone run in pinned containers.
# Env: R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT, R2_BUCKET, R2_PUBLIC_URL (the bucket's public address).
# Writes only offroad/BUILD/REGION/* and, last, offroad/current.json.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
BUILD="${1:?build}"; WORK="${2:?work dir}"; shift 2
[[ "$BUILD" =~ ^offroad-[0-9a-z.-]+$ ]] || { echo "bad build $BUILD" >&2; exit 2; }
[ $# -gt 0 ] || { echo "no regions" >&2; exit 2; }
: "${R2_ACCESS_KEY_ID:?}" "${R2_SECRET_ACCESS_KEY:?}" "${R2_ENDPOINT:?}" "${R2_BUCKET:?}" "${R2_PUBLIC_URL:?}"
IMG_PY=python:3.12-slim
IMG_RCLONE=rclone/rclone:1.68.2
RELEASE_URL="https://github.com/zvx-echo6/cache-packs/releases/download/$BUILD"
BASE_URL="${R2_PUBLIC_URL%/}/offroad/"
mkdir -p "$WORK"; WORK="$(cd "$WORK" && pwd)"; mkdir -p "$WORK/stage"

py() { docker run --rm -v "$WORK:/w" -v "$ROOT:/r:ro" "${PYMOUNT[@]}" -w /w "$IMG_PY" python3 -I /r/scripts/offroad/publish_offroad.py "$@"; }
rc() {
  docker run --rm -v "$WORK:/w:ro" -e RCLONE_CONFIG_R2_TYPE=s3 -e RCLONE_CONFIG_R2_PROVIDER=Cloudflare \
    -e RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" -e RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
    -e RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT" -e RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true "$IMG_RCLONE" "$@"
}

regions=()
for spec in "$@"; do
  r="${spec%%=*}"; src="${spec#*=}"
  [[ "$r" =~ ^[a-z0-9-]{1,32}$ ]] || { echo "bad region $r" >&2; exit 2; }
  PYMOUNT=()
  if [ "$src" = release ]; then src="$RELEASE_URL"
  else PYMOUNT=(-v "$(cd "$src" && pwd):/src:ro"); src=/src; fi
  py stage "$src" "$r" /w/stage
  regions+=("$r")
done
PYMOUNT=()

# The live catalogue (if any), so regions already published are kept.
code=$(curl -sS -o "$WORK/stage/current.live.json" -w '%{http_code}' "${BASE_URL}current.json")
case "$code" in 200) ;; 404) : > "$WORK/stage/current.live.json" ;; *) echo "current.json: HTTP $code" >&2; exit 1 ;; esac
py catalogue --build "$BUILD" --base-url "$BASE_URL" --names /r/scripts/offroad/regions.tsv \
  --existing /w/stage/current.live.json /w/stage "${regions[@]}"

for r in "${regions[@]}"; do
  for f in offroad.sqlite ATTRIBUTION.json manifest.json; do
    ct=application/json; [ "$f" = offroad.sqlite ] && ct=application/vnd.sqlite3
    rc copyto --s3-chunk-size 64M --header-upload "Cache-Control: public, max-age=31536000, immutable" \
      --header-upload "Content-Type: $ct" "/w/stage/$r/$f" "r2:$R2_BUCKET/offroad/$BUILD/$r/$f"
  done
  echo "uploaded $r"
done
rc copyto --header-upload "Cache-Control: public, max-age=300" --header-upload "Content-Type: application/json" \
  /w/stage/current.json "r2:$R2_BUCKET/offroad/current.json"
echo "uploaded current.json"
py check /w/stage/current.json
