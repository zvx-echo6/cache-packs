#!/usr/bin/env bash
# One region of the offroad-regions workflow on a GitHub-hosted runner: free disk, fetch and merge the Geofabrik extracts
# that cover BBOX, run the offroad-region recipe, then stage out/ (without the elevation extract) as release assets.
# Env: REGION, BBOX, EXTRACTS (Geofabrik paths under north-america/, space-separated), LAND_ACCESS, RELEASE, GH_TOKEN,
# OFFROAD_PACK. Writes the work dir's path to $RUNNER_TEMP/workdir for the smoke step.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$(dirname "$HERE")")"
: "${REGION:?}" "${BBOX:?}" "${EXTRACTS:?}" "${RELEASE:?}" "${OFFROAD_PACK:?}" "${RUNNER_TEMP:?}"
GEOFABRIK=https://download.geofabrik.de/north-america
IMG_OSMIUM=cache-osmium:1
t_start=$(date +%s)

# Disk: drop preinstalled toolchains this job never uses, then work on the larger of / and /mnt.
sudo rm -rf /usr/share/dotnet /usr/local/lib/android /opt/ghc /usr/local/.ghcup /opt/hostedtoolcache/CodeQL \
  /usr/local/share/boost /usr/share/swift /usr/local/share/powershell /usr/local/share/chromium
docker image prune -af >/dev/null 2>&1 || true
avail() { df --output=avail -B1 "$1" 2>/dev/null | tail -1 || echo 0; }
BASE=/
if [ -d /mnt ] && [ "$(avail /mnt)" -gt "$(avail /)" ]; then BASE=/mnt; fi
sudo mkdir -p "$BASE/offroad" && sudo chown "$(id -u):$(id -g)" "$BASE/offroad"
W="$BASE/offroad/$REGION"; O="$BASE/offroad/osm"; S="$BASE/offroad/stage"
mkdir -p "$W" "$O" "$S"
echo "$W" > "$RUNNER_TEMP/workdir"
df -h / /mnt 2>/dev/null || df -h /
chmod +x "$OFFROAD_PACK"

# OSM: the Geofabrik extracts that cover BBOX, md5-checked, merged into one file (identical objects are kept once).
docker build -q -t "$IMG_OSMIUM" -f "$HERE/osmium.Dockerfile" "$HERE" >/dev/null
t0=$(date +%s)
: > "$O/merged.osm.pbf.sources"
files=()
for e in $EXTRACTS; do
  [[ "$e" =~ ^[a-z-]+(/[a-z-]+)?$ ]] || { echo "bad extract $e" >&2; exit 2; }
  f="$(basename "$e")-latest.osm.pbf"
  curl -fL --retry 5 --retry-delay 10 --retry-all-errors -sS -o "$O/$f" "$GEOFABRIK/$e-latest.osm.pbf"
  curl -fL --retry 5 --retry-delay 10 --retry-all-errors -sS -o "$O/$f.md5" "$GEOFABRIK/$e-latest.osm.pbf.md5"
  (cd "$O" && md5sum -c --quiet "$f.md5")
  ts=$(docker run --rm -v "$O:/o:ro" "$IMG_OSMIUM" osmium fileinfo -g header.option.osmosis_replication_timestamp "/o/$f" || true)
  echo "extract:north-america/$e md5=$(cut -d' ' -f1 "$O/$f.md5") bytes=$(stat -c %s "$O/$f") replication_timestamp=$ts" >> "$O/merged.osm.pbf.sources"
  files+=("/o/$f")
done
docker run --rm -v "$O:/o" "$IMG_OSMIUM" osmium merge --overwrite "${files[@]}" -o /o/merged.osm.pbf
for f in "${files[@]}"; do rm -f "$O/$(basename "$f")" "$O/$(basename "$f").md5"; done
echo "osm extracts merged in $(( $(date +%s) - t0 ))s: $(du -h "$O/merged.osm.pbf" | cut -f1)"
cat "$O/merged.osm.pbf.sources"

# The recipe (default steps for LAND_ACCESS).
OSM_PBF="$O/merged.osm.pbf" CPUS=4 LAND_ACCESS="${LAND_ACCESS:-us}" "$ROOT/scripts/build-offroad-region.sh" "$REGION" "$W"
df -h "$BASE"
sudo rm -rf "$O" "$W/in/osm" "$W/in/worldcover" "$W/in/usfs" "$W/work" "$W/cargo" "$W/target"
sudo chown -R "$(id -u):$(id -g)" "$W"

# What elevation the pack expects: Mapterhorn's z12 tiles over BBOX (not republished here).
python3 -I "$HERE/elevation_json.py" "$W/in/elevation/download_urls.json" "$W/out/elevation.pmtiles" "$BBOX" > "$W/elevation.json"

# Release assets: REGION-offroad.sqlite (or parts), REGION-ATTRIBUTION.json, REGION-manifest.json, REGION-steps.log.
python3 -I "$HERE/release_assets.py" "$REGION" "$W/out" "$S" --skip elevation.pmtiles --extra "elevation=$W/elevation.json"
cp "$W/steps.log" "$S/$REGION-steps.log"
ls -l "$S"
gh release upload "$RELEASE" -R "$GITHUB_REPOSITORY" --clobber "$S"/*
rm -rf "$S"

{
  echo "### $REGION"
  echo "bbox $BBOX, land access ${LAND_ACCESS:-us}, total $(( ($(date +%s) - t_start) / 60 )) min"
  echo '```'
  ls -l "$W/out"
  grep -E 'step .* done in|: ' "$W/steps.log" || true
  echo '```'
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
