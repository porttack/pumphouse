#!/usr/bin/env python3
"""
Daily GPH logging script.

Calculates slow-fill and fast-fill GPH from the past 3 weeks and logs to events.csv.
This creates a historical record of well performance over time.

Run this once per day via cron, e.g.:
    0 2 * * * cd /home/pi/src/pumphouse && /home/pi/src/pumphouse/venv/bin/python3 log_daily_gph.py
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor.gph_calculator import calculate_gph_from_snapshots
from monitor.logger import log_event
from monitor.relay import get_all_relay_status

def main():
    """Calculate and log GPH metrics"""
    # Calculate GPH from past 3 weeks
    metrics = calculate_gph_from_snapshots('snapshots.csv', lookback_days=21)

    if metrics['last_updated'] is None:
        print("No GPH data available to log")
        return 1

    # Format notes with GPH info
    slow_gph_str = f"{metrics['slow_fill_gph']:.1f}" if metrics['slow_fill_gph'] else "N/A"
    fast_gph_str = f"{metrics['fast_fill_gph']:.1f}" if metrics['fast_fill_gph'] else "N/A"

    notes = (
        f"Slow-fill: {slow_gph_str} GPH ({metrics['slow_fill_samples']} samples), "
        f"Fast-fill: {fast_gph_str} GPH ({metrics['fast_fill_samples']} samples)"
    )

    # Get current relay status
    relay_status = get_all_relay_status()

    # Log to events.csv
    log_event(
        filepath='events.csv',
        event_type='gph_daily',
        pressure_state=None,  # Not applicable
        float_state=None,  # Not applicable
        tank_gallons=None,  # Not applicable
        tank_depth=None,  # Not applicable
        tank_percentage=None,  # Not applicable
        estimated_gallons=None,  # Not applicable
        relay_status=relay_status,
        notes=notes
    )

    print(f"✓ Logged GPH metrics: {notes}")
    return 0

if __name__ == '__main__':
    sys.exit(main())
