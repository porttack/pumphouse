#!/usr/bin/env python3
"""
Daily GPH logging script.

Calculates slow-fill and fast-fill GPH from the past 3 weeks and logs to events.csv.
Also aggregates dosatron_gallons, bypass_gallons, and gallons_used for the previous
calendar day from snapshots.csv.

Run this once per day via cron, e.g.:
    0 2 * * * cd /home/pi/src/pumphouse && /home/pi/src/pumphouse/venv/bin/python3 log_daily_gph.py
"""

import sys
import os
import csv
from datetime import datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor.gph_calculator import calculate_gph_from_snapshots
from monitor.logger import log_event
from monitor.relay import get_all_relay_status
from monitor.config import DEFAULT_SNAPSHOTS_FILE, DEFAULT_EVENTS_FILE


def _aggregate_daily_gallons(snapshots_path, target_date):
    """
    Sum dosatron_gallons, bypass_gallons, and gallons_used for a given date
    from snapshots.csv. Returns dict with totals (None if no rows found).
    """
    date_prefix = target_date.strftime('%Y-%m-%d')
    totals = {'dosatron_gallons': 0.0, 'bypass_gallons': 0.0, 'gallons_used': 0.0}
    row_count = 0

    try:
        with open(snapshots_path, newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not row.get('timestamp', '').startswith(date_prefix):
                    continue
                row_count += 1
                for col in ('dosatron_gallons', 'bypass_gallons', 'gallons_used'):
                    val = row.get(col, '').strip()
                    if val:
                        try:
                            totals[col] += float(val)
                        except ValueError:
                            pass
    except FileNotFoundError:
        return None

    if row_count == 0:
        return None

    return {k: round(v, 1) for k, v in totals.items()}


def main():
    """Calculate and log GPH metrics and daily gallon totals"""
    # Calculate GPH from past 3 weeks
    metrics = calculate_gph_from_snapshots(DEFAULT_SNAPSHOTS_FILE, lookback_days=21)

    if metrics['last_updated'] is None:
        print("No GPH data available to log")
        return 1

    slow_gph_str = f"{metrics['slow_fill_gph']:.1f}" if metrics['slow_fill_gph'] else "N/A"
    fast_gph_str = f"{metrics['fast_fill_gph']:.1f}" if metrics['fast_fill_gph'] else "N/A"

    notes = (
        f"Slow-fill: {slow_gph_str} GPH ({metrics['slow_fill_samples']} samples), "
        f"Fast-fill: {fast_gph_str} GPH ({metrics['fast_fill_samples']} samples)"
    )

    # Aggregate yesterday's gallon totals
    yesterday = (datetime.now() - timedelta(days=1)).date()
    daily = _aggregate_daily_gallons(DEFAULT_SNAPSHOTS_FILE, yesterday)
    if daily:
        notes += (
            f" | {yesterday}: "
            f"dosatron {daily['dosatron_gallons']:.1f} gal, "
            f"bypass {daily['bypass_gallons']:.1f} gal, "
            f"used {daily['gallons_used']:.1f} gal"
        )

    relay_status = get_all_relay_status()

    log_event(
        filepath=DEFAULT_EVENTS_FILE,
        event_type='gph_daily',
        pressure_state=None,
        float_state=None,
        tank_gallons=None,
        tank_depth=None,
        tank_percentage=None,
        estimated_gallons=None,
        relay_status=relay_status,
        notes=notes
    )

    print(f"✓ Logged GPH metrics: {notes}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
