#!/usr/bin/env python3
"""
Reconstruct historical cancellations from events.csv into canceled_reservations.csv.

canceled_reservations.csv didn't exist before check_new_reservations.py gained
record_canceled_reservation(); every CANCELED_RESERVATION logged before that only
lives as a free-text note in events.csv. This parses those notes back out and
records them the same way, including 'Days Before Checkin'. Reservation Id, Unit,
Booked Date and Currency aren't present in the old notes text, so those columns
are left blank/defaulted for backfilled rows.

Usage:
    python3 backfill_canceled_reservations.py [--dry-run]
"""

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # project root
from monitor.config import EVENTS_FILE, CANCELED_RESERVATIONS_FILE

sys.path.insert(0, str(Path(__file__).parent))  # bin/, for check_new_reservations
from check_new_reservations import record_canceled_reservation

NOTE_RE = re.compile(
    r'^(?P<guest>.+?) \| (?P<checkin>\d{4}-\d{2}-\d{2}) to (?P<checkout>\d{4}-\d{2}-\d{2}) '
    r'\((?P<nights>\d+)n\) \| (?P<type>.+?) \| \$(?P<income>[\d.]+)$'
)


def load_existing_backfilled_keys(path):
    """(Guest, Check-In, Canceled Date) tuples already recorded, so reruns don't duplicate."""
    keys = set()
    if not path.exists():
        return keys
    with open(path, 'r') as f:
        for row in csv.DictReader(f):
            keys.add((row.get('Guest'), row.get('Check-In'), row.get('Canceled Date')))
    return keys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help="Print what would be recorded, don't write")
    args = parser.parse_args()

    if not EVENTS_FILE.exists():
        print(f"No events file at {EVENTS_FILE}")
        return 1

    existing = load_existing_backfilled_keys(CANCELED_RESERVATIONS_FILE)

    recorded = 0
    skipped = 0
    unparsed = 0

    with open(EVENTS_FILE, 'r') as f:
        for row in csv.DictReader(f):
            if row.get('event_type') != 'CANCELED_RESERVATION':
                continue

            try:
                canceled_dt = datetime.strptime(row['timestamp'], '%Y-%m-%d %H:%M:%S')
            except (KeyError, ValueError):
                print(f"  Could not parse timestamp: {row.get('timestamp')!r}")
                unparsed += 1
                continue

            m = NOTE_RE.match((row.get('notes') or '').strip())
            if not m:
                print(f"  Could not parse note: {row.get('notes')!r}")
                unparsed += 1
                continue

            canceled_ts = canceled_dt.strftime('%Y-%m-%d %H:%M:%S')
            guest = m.group('guest')
            checkin = m.group('checkin')

            if (guest, checkin, canceled_ts) in existing:
                skipped += 1
                continue

            reservation = {
                'Reservation Id': '',
                'Status': 'Cancelled',
                'Type': m.group('type'),
                'Unit': '',
                'Guest': guest,
                'Booked Date': '',
                'Check-In': checkin,
                'Checkout': m.group('checkout'),
                'Nights': m.group('nights'),
                'Income': m.group('income'),
                'Currency': 'USD',
            }

            print(f"  {canceled_ts}  {guest} | {checkin} to {m.group('checkout')} | ${m.group('income')}")

            if not args.dry_run:
                record_canceled_reservation(reservation, canceled_dt=canceled_dt)
                existing.add((guest, checkin, canceled_ts))
            recorded += 1

    print()
    verb = 'Would record' if args.dry_run else 'Recorded'
    print(f"{verb}: {recorded}, already present: {skipped}, unparsed: {unparsed}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
