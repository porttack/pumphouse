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

# Step 3: Scrape work orders
echo "[$TIMESTAMP] Downloading work orders from TrackHS..."
if $VENV_PYTHON "$BIN_DIR/scrape_work_orders.py" --output "$DATA_DIR/work_orders.csv"; then
    echo "[$TIMESTAMP] ✓ Downloaded work orders successfully"

    # Step 4: Check for new work orders and notify
    echo "[$TIMESTAMP] Checking for new work orders..."
    if $VENV_PYTHON "$BIN_DIR/check_new_work_orders.py"; then
        echo "[$TIMESTAMP] ✓ Checked for new work orders"
    else
        echo "[$TIMESTAMP] ✗ Failed to check new work orders (non-fatal)"
    fi
else
    echo "[$TIMESTAMP] ✗ Failed to download work orders (non-fatal)"
fi

# Step 5: Scrape owner statements
echo "[$TIMESTAMP] Downloading owner statements from TrackHS..."
if $VENV_PYTHON "$BIN_DIR/scrape_statements.py" --output "$DATA_DIR/statements.csv"; then
    echo "[$TIMESTAMP] ✓ Downloaded statements successfully"
else
    echo "[$TIMESTAMP] ✗ Failed to download statements (non-fatal)"
fi

echo "[$TIMESTAMP] Reservation update complete"
echo ""
