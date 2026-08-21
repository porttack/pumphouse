#!/usr/bin/env python3
"""
One-time correction of snapshots.csv rows whose bypass_gallons/gallons_in/
gallons_used were computed with the pre-2026-08-20 formula: bypass relay-open
wall time / SECONDS_PER_GALLON (monitor/poll.py). That formula overstated
gallons whenever the bypass valve sat open without water actually moving
(well-recovery puffs, calling gaps) — the same issue already fixed for the
chlorine ppm estimate on 2026-08-01, now also fixed in poll.py for gallons_in.

This script recomputes bypass_gallons for existing rows using the corrected
method (real FFT-detected flow seconds x get_calibrated_bypass_gpm()) and
rewrites gallons_in/gallons_used to match, in place, for a given date range.
dosatron_gallons is untouched (it was never part of this bug).

Usage:
    venv/bin/python3 bin/backfill_bypass_gallons_fix.py 2026-08-19 2026-08-20
"""
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor.config import DEFAULT_SNAPSHOTS_FILE
from monitor.dosatron import sum_flow_seconds
from monitor.bypass_calibration import get_calibrated_bypass_gpm

_TZ = pytz.timezone('America/Los_Angeles')


def _parse_ts(s: str) -> float:
    return _TZ.localize(datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S')).timestamp()


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    dates = set(sys.argv[1:])

    with open(DEFAULT_SNAPSHOTS_FILE) as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    gpm = get_calibrated_bypass_gpm()
    print(f'Using calibrated bypass GPM: {gpm:.3f}')

    n_changed = 0
    for row in rows:
        if row['timestamp'][:10] not in dates:
            continue
        try:
            dur = float(row['duration_seconds'] or 0)
            dosg = float(row['dosatron_gallons'] or 0)
            delta = float(row['tank_gallons_delta']) if row['tank_gallons_delta'] not in ('', None) else None
        except ValueError:
            continue
        if dur <= 0:
            continue

        end_ts = _parse_ts(row['timestamp'])
        start_ts = end_ts - dur
        flow_s = sum_flow_seconds(start_ts, end_ts)
        new_bypass_gal = round(flow_s / 60.0 * gpm, 2)
        new_gallons_in = round(dosg + new_bypass_gal, 2)
        new_gallons_used = (
            max(0.0, round(new_gallons_in - delta, 1))
            if delta is not None
            else new_gallons_in
        )

        old_bypass_gal = row['bypass_gallons']
        old_gallons_in = row['gallons_in']
        row['bypass_gallons'] = str(new_bypass_gal)
        row['gallons_in']     = str(new_gallons_in)
        row['gallons_used']   = str(new_gallons_used)
        n_changed += 1
        if n_changed <= 5 or n_changed % 20 == 0:
            print(f"  {row['timestamp']}: bypass {old_bypass_gal} -> {new_bypass_gal}, "
                  f"gallons_in {old_gallons_in} -> {new_gallons_in}")

    if n_changed == 0:
        print('No matching rows found — nothing changed.')
        return

    tmp = str(DEFAULT_SNAPSHOTS_FILE) + '.tmp'
    with open(tmp, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    import os
    os.replace(tmp, DEFAULT_SNAPSHOTS_FILE)
    print(f'Corrected {n_changed} rows across {sorted(dates)} in {DEFAULT_SNAPSHOTS_FILE}')


if __name__ == '__main__':
    main()
