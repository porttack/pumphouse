#!/usr/bin/env python3
"""
Fetch Ecobee current temperature and cache it to a CSV file.

This script is designed to be run periodically (via cron) to cache the
Ecobee temperature since fetching it takes 30+ seconds. The cached value
can then be quickly displayed in dashboards and emails.

Usage:
    ./fetch_ecobee_temp.py [--debug] [--thermostat "Living Room Ecobee"]
"""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from monitor.ecobee import EcobeeController, HOUSE_NAME

# Cache file location
CACHE_FILE = Path(__file__).parent / 'ecobee_temp_cache.csv'


def fetch_and_cache_temperature(thermostat_name=None, house_name=None, debug=False):
    """
    Fetch current temperature from Ecobee and cache it to a CSV file.

    Args:
        thermostat_name: Name of thermostat to fetch (None = all thermostats)
        debug: Enable debug output

    Returns:
        list: Temperature data that was cached
    """
    try:
        if debug:
            print(f"Fetching Ecobee temperature data...")

        with EcobeeController(headless=True, debug=debug) as ecobee:
            if thermostat_name:
                tstat = ecobee.get_thermostat(thermostat_name)
                if not tstat:
                    print(f"ERROR: Thermostat '{thermostat_name}' not found", file=sys.stderr)
                    return None
                thermostats = [tstat]
            else:
                # Prefer a specific house if provided; default to configured HOUSE_NAME
                target_house = house_name if house_name else HOUSE_NAME
                thermostats = ecobee.get_all_thermostats(house_name=target_house)

            if not thermostats:
                print("ERROR: No thermostats found", file=sys.stderr)
                return None

            # Get current timestamp
            timestamp = datetime.now().isoformat()

            # Write to CSV file (overwrite each time)
            with open(CACHE_FILE, 'w', newline='') as f:
                fieldnames = ['timestamp', 'thermostat_name', 'temperature', 'heat_setpoint',
                             'cool_setpoint', 'system_mode', 'hold_text', 'vacation_mode']
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()

                for tstat in thermostats:
                    writer.writerow({
                        'timestamp': timestamp,
                        'thermostat_name': tstat['name'],
                        'temperature': tstat['temperature'],
                        'heat_setpoint': tstat.get('heat_setpoint', ''),
                        'cool_setpoint': tstat.get('cool_setpoint', ''),
                        'system_mode': tstat.get('system_mode', ''),
                        'hold_text': tstat.get('hold_text', ''),
                        'vacation_mode': 'True' if tstat.get('vacation_mode', False) else 'False'
                    })

                    if debug:
                        print(f"  {tstat['name']}: {tstat['temperature']}°F")

            if debug:
                print(f"✓ Temperature data cached to {CACHE_FILE}")

            return thermostats

    except Exception as e:
        print(f"ERROR: Failed to fetch temperature: {e}", file=sys.stderr)
        if debug:
            import traceback
            traceback.print_exc()
        return None


def read_cached_temperature(max_age_hours=24):
    """
    Read cached temperature data from CSV file.

    Args:
        max_age_hours: Maximum age of cache in hours (None = no limit)

    Returns:
        list: List of dicts with thermostat data, or None if not available/too old
    """
    try:
        if not CACHE_FILE.exists():
            return None

        with open(CACHE_FILE, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if not rows:
            return None

        # Check age using first row's timestamp
        if max_age_hours is not None:
            cache_time = datetime.fromisoformat(rows[0]['timestamp'])
            age_hours = (datetime.now() - cache_time).total_seconds() / 3600

            if age_hours > max_age_hours:
                return None

        # Convert temperature to float
        for row in rows:
            row['temperature'] = float(row['temperature'])

        return rows

    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description='Fetch and cache Ecobee temperature')
    parser.add_argument('--debug', action='store_true', help='Enable debug output')
    parser.add_argument('--thermostat', help='Specific thermostat name (default: all)')
    parser.add_argument('--house', help='House name to scrape (default: Blackberry Hill)')

    args = parser.parse_args()

    result = fetch_and_cache_temperature(
        thermostat_name=args.thermostat,
        house_name=args.house if args.house else HOUSE_NAME,
        debug=args.debug
    )

    if result:
        return 0
    else:
        return 1


if __name__ == '__main__':
    sys.exit(main())
