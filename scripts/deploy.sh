#!/usr/bin/env bash
# Deploy the integration to a running Home Assistant instance.
# Usage: ./scripts/deploy.sh [ha-host] [ha-config-path]
#
# Defaults: HA_HOST=homeassistant.local  HA_CONFIG=/config
# Uses rsync over SSH; HA must be accessible via SSH.

set -euo pipefail

HA_HOST="${1:-homeassistant.local}"
HA_CONFIG="${2:-/config}"
DEST="${HA_HOST}:${HA_CONFIG}/custom_components/lymow"

echo "Deploying to ${HA_HOST}..."

rsync -avz --delete \
  custom_components/lymow/ \
  "${DEST}/"

ssh "${HA_HOST}" "mkdir -p ${HA_CONFIG}/www"

rsync -avz \
  www/lymow-map-card.js \
  "${HA_HOST}:${HA_CONFIG}/www/lymow-map-card.js"

echo "Done. Restart Home Assistant to pick up changes."
