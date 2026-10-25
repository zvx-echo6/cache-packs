#!/usr/bin/env bash
# Uploads a built regions/ tree to R2 (polygons first, the index files last). Credentials come from the environment; nothing here prints them.
set -euo pipefail
SRC="$1"   # directory that contains regions/
: "${R2_BUCKET:?}" "${R2_ENDPOINT:?}"
export AWS_DEFAULT_REGION=auto AWS_REQUEST_CHECKSUM_CALCULATION=when_required AWS_RESPONSE_CHECKSUM_VALIDATION=when_required
aws configure set default.s3.max_concurrent_requests 48
aws configure set default.s3.max_queue_size 20000
EP=(--endpoint-url "$R2_ENDPOINT" --only-show-errors)
aws s3 sync "$SRC/regions/polygons" "s3://$R2_BUCKET/regions/polygons" "${EP[@]}" --delete \
  --content-type application/geo+json --cache-control "public, max-age=86400"
for f in "$SRC"/regions/*.json; do
  b="$(basename "$f")"
  [ "$b" = manifest.json ] && continue
  aws s3 cp "$f" "s3://$R2_BUCKET/regions/$b" "${EP[@]}" --content-type application/json --cache-control "public, max-age=3600"
done
# remove shard files of countries that no longer exist
aws s3 ls "s3://$R2_BUCKET/regions/" --endpoint-url "$R2_ENDPOINT" | awk '{print $4}' | grep -E '\.json$' | while read -r b; do
  [ -f "$SRC/regions/$b" ] || aws s3 rm "s3://$R2_BUCKET/regions/$b" "${EP[@]}"
done
aws s3 cp "$SRC/regions/manifest.json" "s3://$R2_BUCKET/regions/manifest.json" "${EP[@]}" --content-type application/json --cache-control "public, max-age=3600"
echo "uploaded"
