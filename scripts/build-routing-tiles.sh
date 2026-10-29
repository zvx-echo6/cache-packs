#!/bin/sh
# Build the North America Valhalla routing tiles and explode them for publishing.
# Usage: build-routing-tiles.sh <build> <src dir> <work dir> <tilecut src>
#   <src dir>     north-america-latest.osm.pbf, timezones.sqlite, valhalla.json of the previous build
#   <work dir>    empty folder on a disk with about 140 GB free; ends up holding <build>/ (tiles and index.tsv)
#   <tilecut src> the cache repository's packages/valhalla/tools/tilecut
# Everything runs in containers. Then scripts/publish-routing-tiles.sh <build> <work dir> <pbf.md5> uploads it.
set -e
B=$1; S=$2; W=$3; T=$4
I=ghcr.io/valhalla/valhalla-scripted:3.7.0
HERE=$(cd "$(dirname "$0")" && pwd)
# OpenStreetMapSpeeds/schema default_speeds.json, pinned to the commit of 2022-02-14
SPEEDS_URL=https://raw.githubusercontent.com/OpenStreetMapSpeeds/schema/c9c6872d5ec656f5d290944b44eb1a103ed539fa/default_speeds.json
SPEEDS_SHA=9953f78c2407b8d2e3701fce6cc93c6a91b207a365a5d2f5564a6d1c231e5ba7
# Country boundaries the extract cuts (members outside North America); fetched whole from the OSM API. 148838 = US.
BOUNDARIES="148838"
mkdir -p "$W/admin"
cp "$HERE/merge_speeds.py" "$HERE/osm_to_pbf.py" "$HERE/../recipes/routing-speed-corrections.json" "$W/admin/"

# config: the previous build's, paths moved to /work, our admin db and speed table
docker run --rm --entrypoint jq -v "$S":/src:ro $I \
  '.mjolnir.tile_dir="/work/valhalla_tiles" | .mjolnir.tile_extract="/work/valhalla_tiles.tar" | .mjolnir.traffic_extract="/work/traffic.tar"
   | .mjolnir.admin="/work/admin/admins.sqlite" | .mjolnir.timezone="/src/timezones.sqlite" | .mjolnir.transit_dir="/work/transit_tiles"
   | .mjolnir.concurrency=12 | .mjolnir.default_speeds_config="/work/default_speeds.json"' /src/valhalla.json > "$W/valhalla.json"

# admin db: the extract plus the full boundary relations, so the US polygon is complete
docker run --rm -v "$W/admin":/a python:3.12-slim pip install -q --root-user-action ignore --target /a/pylib osmium
EXTRA=""
for R in $BOUNDARIES; do
  curl -sSf -A "cache-packs routing build" -o "$W/admin/r$R.osm" "https://api.openstreetmap.org/api/0.6/relation/$R/full"
  docker run --rm -e PYTHONPATH=/a/pylib -v "$W/admin":/a --entrypoint python3 $I /a/osm_to_pbf.py /a/r$R.osm /a/r$R.osm.pbf
  EXTRA="$EXTRA /work/admin/r$R.osm.pbf"
done
docker run --rm --memory 8g -v "$S":/src:ro -v "$W":/work --entrypoint valhalla_build_admins $I \
  -c /work/valhalla.json /src/north-america-latest.osm.pbf $EXTRA > "$W/admin/build.log" 2>&1
if grep -q "United States (148838) is degenerate" "$W/admin/build.log"; then echo "US boundary still incomplete" >&2; exit 1; fi

# speed table: the published one with our corrections
curl -sSf -o "$W/admin/default_speeds.published.json" "$SPEEDS_URL"
echo "$SPEEDS_SHA  $W/admin/default_speeds.published.json" | sha256sum -c -
docker run --rm -v "$W/admin":/a python:3.12-slim python -I /a/merge_speeds.py \
  /a/default_speeds.published.json /a/routing-speed-corrections.json /a/default_speeds.json
cp "$W/admin/default_speeds.json" "$W/default_speeds.json"
sha256sum "$W/default_speeds.json"

R="docker run --rm --memory 13g --memory-swap 13g --cpus 14 -v $S:/src:ro -v $W:/work"
# parse and build the graph (12 threads), then enhance (6 threads: 12 ran out of memory at 12 GB) through cleanup
$R --entrypoint valhalla_build_tiles $I -c /work/valhalla.json -s initialize -e build -j 12 /src/north-america-latest.osm.pbf
$R --entrypoint valhalla_build_tiles $I -c /work/valhalla.json -s enhance -e cleanup -j 6 /src/north-america-latest.osm.pbf
$R --entrypoint valhalla_build_extract $I -c /work/valhalla.json -v

# one file per tile plus index.tsv
docker run --rm -v "$T":/src -w /src rust:1.90-slim cargo build --release
docker run --rm -v "$W":/work -v "$T":/tc rust:1.90-slim /tc/target/release/tilecut --explode /work/valhalla_tiles.tar /work/$B
rm -rf "$W/valhalla_tiles" "$W/admin/pylib" "$W/admin"/r*.osm
