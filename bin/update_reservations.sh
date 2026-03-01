#!/bin/bash
# Update reservations and check for new bookings
# This script is called by cron three times daily

BIN_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$( cd "$BIN_DIR/.." && pwd )"
DATA_DIR="$HOME/.local/share/pumphouse"

VENV_PYTHON="$PROJECT_DIR/venv/bin/python3"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# Ensure data directory exists
mkdir -p "$DATA_DIR"

echo "[$TIMESTAMP] Starting reservation update..."

# Step 1: Scrape latest reservations
echo "[$TIMESTAMP] Downloading reservations from TrackHS..."
if $VENV_PYTHON "$BIN_DIR/scrape_reservations.py" --output "$DATA_DIR/reservations.csv"; then
    echo "[$TIMESTAMP] ✓ Downloaded reservations successfully"
else
    echo "[$TIMESTAMP] ✗ Failed to download reservations"
    exit 1
fi

# Step 2: Check for new reservations and notify
echo "[$TIMESTAMP] Checking for new reservations..."
if $VENV_PYTHON "$BIN_DIR/check_new_reservations.py"; then
    echo "[$TIMESTAMP] ✓ Checked for new reservations"
else
    echo "[$TIMESTAMP] ✗ Failed to check new reservations"
    exit 1
fi

echo "[$TIMESTAMP] Reservation update complete"
echo ""
