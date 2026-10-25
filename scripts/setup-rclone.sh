#!/usr/bin/env bash
# Installs a pinned rclone (hash pinned in this file) and blake3 (pinned) on a runner.
set -euo pipefail
V="v1.75.2"
SHA="349ac8fba6ff65d6247043f1750cdcb518ec5d500ef91463a10d37c0ccdf3702"
mkdir -p "$RUNNER_TEMP/bin" "$RUNNER_TEMP/rclone-dl"
curl -fsSL --retry 5 -o "$RUNNER_TEMP/rclone-dl/rclone.zip" "https://downloads.rclone.org/$V/rclone-$V-linux-amd64.zip"
echo "$SHA  $RUNNER_TEMP/rclone-dl/rclone.zip" | sha256sum -c -
unzip -q -j "$RUNNER_TEMP/rclone-dl/rclone.zip" "rclone-$V-linux-amd64/rclone" -d "$RUNNER_TEMP/bin"
echo "$RUNNER_TEMP/bin" >> "$GITHUB_PATH"
"$RUNNER_TEMP/bin/rclone" version | head -1
pip install --quiet blake3==1.0.11
