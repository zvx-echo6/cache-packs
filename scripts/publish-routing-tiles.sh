#!/bin/sh
# Publish an exploded Valhalla build (tilecut --explode) to R2: tiles first, then the index, then current.json last, so a
# phone only sees a build once every tile of it is there. Usage: publish-routing-tiles.sh <build> <work dir> <pbf.md5>
# Needs <work dir>/r2.env (RCLONE_CONFIG_R2_* for Cloudflare R2 and BUCKET=...), readable only by its owner.
set -e
B=$1; W=$2; MD5FILE=$3
D=$W/$B
cd "$D"
find . -name "*.gph" -print0 | xargs -0 -n 64 -P 8 gzip -k -6 -n -f
awk -F'\t' '{ cmd = "stat -c%s " $1 ".gph.gz"; cmd | getline g; close(cmd); print $1 "\t" $2 "\t" $3 "\t" g }' index.tsv > index.tsv.new
mv index.tsv.new index.tsv
gzip -n -9 -c index.tsv > index.tsv.gz
SHA=$(sha256sum index.tsv.gz | cut -c1-64)
TILES=$(wc -l < index.tsv)
# %.0f, not %d: the totals pass 2^31
BYTES=$(awk -F'\t' '{s+=$2} END {printf "%.0f", s}' index.tsv)
GZB=$(awk -F'\t' '{s+=$4} END {printf "%.0f", s}' index.tsv)
MD5=$(cut -d' ' -f1 "$MD5FILE")
cat > "$W/current.json" <<J
{"format": 1, "build": "$B", "valhalla": "3.7.0", "source": "Geofabrik north-america-latest.osm.pbf (md5 $MD5), OpenStreetMap contributors, ODbL 1.0", "built_with": "valhalla_build_tiles 3.7.0, build_admins and build_time_zones on, use_default_speeds_config off", "tiles": $TILES, "bytes": $BYTES, "gz_bytes": $GZB, "index": "$B/index.tsv.gz", "index_sha256": "$SHA"}
J
R="docker run --rm --env-file $W/r2.env -v $W:/data rclone/rclone:1.68.2"
. "$W/r2.env"
$R copy "/data/$B" "r2:$BUCKET/routing/$B" --include "*.gph.gz" --transfers 32 --checkers 32 --header-upload "Cache-Control: public, max-age=31536000, immutable"
$R copy "/data/$B" "r2:$BUCKET/routing/$B" --include "index.tsv.gz" --header-upload "Cache-Control: public, max-age=31536000, immutable"
$R copyto /data/current.json "r2:$BUCKET/routing/current.json" --header-upload "Cache-Control: public, max-age=300"
echo UPLOADED
