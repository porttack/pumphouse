#!/bin/bash
# Update reservations and check for new bookings
# This script is called by cron twice daily

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

VENV_PYTHON="$SCRIPT_DIR/venv/bin/python3"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "[$TIMESTAMP] Starting reservation update..."

# Step 1: Scrape latest reservations
echo "[$TIMESTAMP] Downloading reservations from TrackHS..."
if $VENV_PYTHON scrape_reservations.py --output reservations.csv; then
    echo "[$TIMESTAMP] ✓ Downloaded reservations successfully"
else
    echo "[$TIMESTAMP] ✗ Failed to download reservations"
    exit 1
fi

# Step 2: Check for new reservations and notify
echo "[$TIMESTAMP] Checking for new reservations..."
if $VENV_PYTHON check_new_reservations.py; then
    echo "[$TIMESTAMP] ✓ Checked for new reservations"
else
    echo "[$TIMESTAMP] ✗ Failed to check new reservations"
    exit 1
fi

echo "[$TIMESTAMP] Reservation update complete"
echo ""
