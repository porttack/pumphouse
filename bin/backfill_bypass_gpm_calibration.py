#!/usr/bin/env python3
"""
One-time seed of monitor/bypass_calibration.py's persisted state from
historical snapshots.csv rows, so BYPASS_GPM calibration doesn't have to
start from zero the day the feature shipped.

Replays every snapshot row in chronological order through the exact same
monitor.bypass_calibration.record_snapshot() the live poller calls, so the
seeded state is produced by the real logic, not a parallel reimplementation.

Usage:
    venv/bin/python3 bin/backfill_bypass_gpm_calibration.py
"""
import csv
import sys
from datetime import datetime
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor.config import DEFAULT_SNAPSHOTS_FILE
import monitor.bypass_calibration as calib

_TZ = pytz.timezone('America/Los_Angeles')


def _parse_ts(s: str) -> float:
    return _TZ.localize(datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S')).timestamp()


def main() -> None:
    if calib.CALIBRATION_STATE_FILE.exists():
        print(f'{calib.CALIBRATION_STATE_FILE} already exists — refusing to overwrite. '
              'Delete it first if you want to re-run the backfill.')
        return

    with open(DEFAULT_SNAPSHOTS_FILE) as f:
        rows = list(csv.DictReader(f))

    n = 0
    for row in rows:
        try:
            dur = float(row['duration_seconds'] or 0)
            delta = float(row['tank_gallons_delta']) if row['tank_gallons_delta'] not in ('', None) else None
            phs = float(row['pressure_high_seconds'] or 0)
            dosg = float(row['dosatron_gallons'] or 0)
        except ValueError:
            continue
        if dur <= 0:
            continue
        end_ts = _parse_ts(row['timestamp'])
        start_ts = end_ts - dur
        calib.record_snapshot(
            occupied_status=row['occupied'],
            bypass_on=(row['relay_bypass'] == 'ON'),
            dosatron_gallons=dosg,
            pressure_high_seconds=phs,
            tank_gallons_delta=delta,
            window_start_ts=start_ts,
            window_end_ts=end_ts,
        )
        n += 1

    status = calib.get_calibration_status()
    print(f'Replayed {n} snapshot rows.')
    print(f'Seeded calibration: {status}')


if __name__ == '__main__':
    main()
