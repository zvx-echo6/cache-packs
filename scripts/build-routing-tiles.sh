#!/bin/sh
# Build the North America Valhalla routing tiles with the speed table and explode them for publishing.
# Usage: build-routing-tiles.sh <build> <src dir> <work dir> <tilecut src>
#   <src dir>     north-america-latest.osm.pbf, admins.sqlite, timezones.sqlite, valhalla.json of the previous build
#   <work dir>    empty folder on a disk with about 140 GB free; ends up holding <build>/ (tiles, gz tiles, index)
#   <tilecut src> the cache repository's packages/valhalla/tools/tilecut
# Everything runs in containers. Then scripts/publish-routing-tiles.sh <build> <work dir> <pbf.md5> uploads it.
set -e
B=$1; S=$2; W=$3; T=$4
I=ghcr.io/valhalla/valhalla-scripted:3.7.0
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$W"
cp "$HERE/../recipes/routing-default-speeds.json" "$W/default_speeds.json"

# same config as the previous build, paths moved to /work, the speed table added
docker run --rm --entrypoint jq -v "$S":/src:ro $I \
  '.mjolnir.tile_dir="/work/valhalla_tiles" | .mjolnir.tile_extract="/work/valhalla_tiles.tar" | .mjolnir.traffic_extract="/work/traffic.tar"
   | .mjolnir.admin="/src/admins.sqlite" | .mjolnir.timezone="/src/timezones.sqlite" | .mjolnir.transit_dir="/work/transit_tiles"
   | .mjolnir.concurrency=12 | .mjolnir.default_speeds_config="/work/default_speeds.json"' /src/valhalla.json > "$W/valhalla.json"

R="docker run --rm --memory 13g --memory-swap 13g --cpus 14 -v $S:/src:ro -v $W:/work"
# parse and build the graph (12 threads), then enhance (6 threads: 12 ran out of memory at 12 GB) through cleanup
$R --entrypoint valhalla_build_tiles $I -c /work/valhalla.json -s initialize -e build -j 12 /src/north-america-latest.osm.pbf
$R --entrypoint valhalla_build_tiles $I -c /work/valhalla.json -s enhance -e cleanup -j 6 /src/north-america-latest.osm.pbf
$R --entrypoint valhalla_build_extract $I -c /work/valhalla.json -v

# one file per tile plus index.tsv
docker run --rm -v "$T":/src -w /src rust:1.90-slim cargo build --release
docker run --rm -v "$W":/work -v "$T":/tc rust:1.90-slim /tc/target/release/tilecut --explode /work/valhalla_tiles.tar /work/$B
rm -rf "$W/valhalla_tiles"
