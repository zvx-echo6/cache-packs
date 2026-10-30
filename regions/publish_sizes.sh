#!/usr/bin/env bash
# Uploads only the sized region shards (regions/<shard>.json) to R2. Nothing else under regions/ is touched (no polygons, index.json,
# manifest.json or search files) and nothing is deleted. Credentials come from the environment; nothing here prints them.
# Usage: publish_sizes.sh DIR SHARD...   (DIR contains regions/<shard>.json)
set -euo pipefail
SRC="$1"; shift
: "${R2_BUCKET:?}" "${R2_ENDPOINT:?}"
[ "$#" -gt 0 ] || { echo "no shards given"; exit 1; }
export AWS_DEFAULT_REGION=auto AWS_REQUEST_CHECKSUM_CALCULATION=when_required AWS_RESPONSE_CHECKSUM_VALIDATION=when_required
EP=(--endpoint-url "$R2_ENDPOINT" --only-show-errors)
# check every file first, so nothing is uploaded unless all of them are good
for s in "$@"; do
  [[ "$s" =~ ^[a-z0-9-]+$ ]] || { echo "bad shard name"; exit 1; }
  python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if any("sizes" in r for r in d["regions"]) else 1)' "$SRC/regions/$s.json" \
    || { echo "$s.json is missing, is not a shard, or has no sizes"; exit 1; }
done
for s in "$@"; do
  aws s3 cp "$SRC/regions/$s.json" "s3://$R2_BUCKET/regions/$s.json" "${EP[@]}" --content-type application/json --cache-control "public, max-age=3600"
done
echo "uploaded sized shards: $*"
