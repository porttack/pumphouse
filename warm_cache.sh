#!/bin/bash
# Warm Cloudflare edge cache for onblackberryhill.com public pages.
# Run every ~8 minutes so pages stay within the 10-minute max-age and
# stale-if-error can serve them during tunnel outages.
#
# Must hit the public domain (not localhost) so Cloudflare actually caches.

set -euo pipefail
BASE="https://onblackberryhill.com"
CURL="curl -sf --max-time 30 -A cache-warmer/1.0 -o /dev/null"

# Water status page + its JPEG
$CURL "$BASE/water"
$CURL "$BASE/api/epaper.jpg?public=yes"

# Best timelapse page (follow redirect so both the redirect and the
# destination HTML page get into Cloudflare's cache)
TIMELAPSE_URL=$($CURL -w "%{url_effective}" -L "$BASE/timelapse" 2>/dev/null || true)
DATE=$(echo "$TIMELAPSE_URL" | grep -oP '\d{4}-\d{2}-\d{2}' || true)
if [ -n "$DATE" ]; then
    $CURL "$BASE/timelapse/$DATE/snapshot"
fi

# Also warm the last 7 days of snapshots so older pages survive too
for jpg in $(ls -1 /home/pi/timelapses/snapshots/*.jpg 2>/dev/null | tail -7); do
    D=$(basename "$jpg" .jpg)
    $CURL "$BASE/timelapse/$D/snapshot" || true
done
