#!/usr/bin/env bash
# Installs the tools the layer builds need on a GitHub Actions ubuntu-24.04 runner.
# gdal-bin comes from the runner image's Ubuntu 24.04 archive (GDAL 3.8.x); tippecanoe is built from a pinned tag.
set -euo pipefail
TIPPECANOE_TAG="2.79.0"
sudo apt-get update -qq
sudo apt-get install -y -qq gdal-bin jq build-essential libsqlite3-dev zlib1g-dev unzip
git clone --quiet --depth 1 --branch "$TIPPECANOE_TAG" https://github.com/felt/tippecanoe.git "$RUNNER_TEMP/tippecanoe"
make -C "$RUNNER_TEMP/tippecanoe" -j"$(nproc)" >/dev/null
sudo make -C "$RUNNER_TEMP/tippecanoe" install >/dev/null
ogr2ogr --version
tippecanoe --version 2>&1 | head -1
aws --version
