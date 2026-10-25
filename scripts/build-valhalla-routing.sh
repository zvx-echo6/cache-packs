#!/usr/bin/env bash
# Recipe: valhalla-routing (Geofabrik extract -> Valhalla tile tar -> GitHub Release). See recipes/valhalla-routing.md.
# Needs GH_TOKEN (the workflow's GITHUB_TOKEN) and GITHUB_REPOSITORY in the environment.
set -euo pipefail
REGION="${REGION:-idaho}"
GEOFABRIK_PATH="${GEOFABRIK_PATH:-north-america/us/idaho}"      # path on download.geofabrik.de, without -latest.osm.pbf
VALHALLA_IMAGE="ghcr.io/valhalla/valhalla-scripted:3.7.0"
VALHALLA_VERSION="3.7.0"
PART_SIZE="1900M"                                                # under 2 GiB
[[ "$REGION" =~ ^[a-z0-9-]{1,32}$ ]] || { echo "bad region slug" >&2; exit 2; }
[[ "$GEOFABRIK_PATH" =~ ^[a-z0-9/-]{1,80}$ ]] || { echo "bad geofabrik path" >&2; exit 2; }
VERSION="$(date -u +%Y-%m-%d)"
TAG="$REGION-$VERSION"
W="$RUNNER_TEMP/valhalla"; mkdir -p "$W/custom_files" "$W/out"

# resolve the dated extract behind "latest" so the build records exactly which file it used
LATEST="https://download.geofabrik.de/$GEOFABRIK_PATH-latest.osm.pbf"
DATED="$(curl -fsS -o /dev/null -w '%{redirect_url}' "$LATEST")"
[[ "$DATED" == https://download.geofabrik.de/*.osm.pbf ]] || { echo "unexpected redirect" >&2; exit 1; }
echo "extract: $DATED"
curl -fL --retry 5 --retry-all-errors -sS -o "$W/custom_files/$REGION.osm.pbf" "$DATED"
curl -fsS "$DATED.md5" | awk '{print $1}' > "$W/pbf.md5"
echo "$(cat "$W/pbf.md5")  $W/custom_files/$REGION.osm.pbf" | md5sum -c -
ls -l "$W/custom_files"

docker pull -q "$VALHALLA_IMAGE"
docker run --rm -v "$W/custom_files:/custom_files" \
  -e serve_tiles=False -e force_rebuild=True -e build_tar=True -e build_admins=True -e build_time_zones=True \
  -e build_elevation=False -e tileset_name=valhalla_tiles "$VALHALLA_IMAGE"
sudo chown -R "$(id -u):$(id -g)" "$W/custom_files"
ls -l "$W/custom_files"
TAR="$W/custom_files/valhalla_tiles.tar"
[ -s "$TAR" ] || { echo "tile tar missing" >&2; exit 1; }

# split into parts under 2 GiB (always at least one part; reassemble with cat)
NAME="valhalla-$REGION-$VERSION.tar"
split -b "$PART_SIZE" -d -a 2 "$TAR" "$W/out/$NAME.part"
TAR_SHA="$(sha256sum "$TAR" | awk '{print $1}')"
TAR_SIZE="$(stat -c %s "$TAR")"
( cd "$W/out" && jq -n --arg id "valhalla-routing-$REGION" --arg version "$VERSION" --arg valhalla "$VALHALLA_VERSION" \
    --arg src "$DATED" --arg name "$NAME" --arg sha "$TAR_SHA" --argjson size "$TAR_SIZE" --arg tag "$TAG" \
    --arg repo "$GITHUB_REPOSITORY" \
    '{format:1, id:$id, version:$version, valhalla_version:$valhalla, source:{url:$src, license:"ODbL 1.0", attribution:"Contains data (c) OpenStreetMap contributors"},
      tar:{name:$name, size:$size, sha256:$sha},
      parts:[ $ARGS.positional[] | split("|") | {name:.[0], size:(.[1]|tonumber), sha256:.[2], url:("https://github.com/"+$repo+"/releases/download/"+$tag+"/"+.[0])} ],
      reassemble:"cat <parts in order> > the tar name, then check sha256"}' \
    --args $(for f in "$NAME".part*; do echo "$f|$(stat -c %s "$f")|$(sha256sum "$f" | awk '{print $1}')"; done) \
    > "valhalla-$REGION-manifest.json" )
cat "$W/out/valhalla-$REGION-manifest.json"
ls -l "$W/out"

if gh release view "$TAG" >/dev/null 2>&1; then
  gh release upload "$TAG" "$W/out"/* --clobber
else
  gh release create "$TAG" "$W/out"/* --title "$REGION routing tiles $VERSION" \
    --notes "Valhalla $VALHALLA_VERSION routing tiles for $REGION, built from the Geofabrik extract $(basename "$DATED"). Contains data (c) OpenStreetMap contributors (ODbL). See recipes/valhalla-routing.md."
fi
