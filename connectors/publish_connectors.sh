#!/usr/bin/env bash
# Uploads connectors/ to R2 under connectors/: the definitions first, catalogue.json last (so a reader never sees a catalogue that points at a
# file that is not there yet), then removes files that are no longer in the catalogue. Credentials come from the environment; nothing here prints them.
set -euo pipefail
SRC="${1:?folder that holds catalogue.json}"
: "${R2_BUCKET:?}" "${R2_ENDPOINT:?}"
export AWS_DEFAULT_REGION=auto AWS_REQUEST_CHECKSUM_CALCULATION=when_required AWS_RESPONSE_CHECKSUM_VALIDATION=when_required
EP=(--endpoint-url "$R2_ENDPOINT" --only-show-errors)
HDR=(--content-type application/json --cache-control "public, max-age=300")
[ -f "$SRC/catalogue.json" ] || { echo "no catalogue.json in $SRC" >&2; exit 2; }

# 1. every definition (every .json under the folder except the catalogue itself)
cd "$SRC"
while IFS= read -r f; do
  rel="${f#./}"
  aws s3 cp "$rel" "s3://$R2_BUCKET/connectors/$rel" "${EP[@]}" "${HDR[@]}"
done < <(find . -type f -name '*.json' ! -path './catalogue.json' | sort)

# 2. the catalogue, last
aws s3 cp catalogue.json "s3://$R2_BUCKET/connectors/catalogue.json" "${EP[@]}" "${HDR[@]}"

# 3. files that left the catalogue
aws s3 ls "s3://$R2_BUCKET/connectors/" --recursive --endpoint-url "$R2_ENDPOINT" | awk '{print $4}' | while IFS= read -r key; do
  rel="${key#connectors/}"
  if [ -n "$rel" ] && [ ! -f "$rel" ]; then
    aws s3 rm "s3://$R2_BUCKET/$key" "${EP[@]}"
  fi
done
echo "uploaded"
