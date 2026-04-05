#!/usr/bin/env python3
"""
backfill_rolling_gph.py — Populate tank_rolling_gph for all existing snapshot rows.

Replays every snapshot row in chronological order, carrying rolling-buffer state
across file boundaries so the 75-min window is continuous through archive seams.

Usage:
    python backfill_rolling_gph.py          # dry-run: prints what would change
    python backfill_rolling_gph.py --write  # rewrite files in place
"""
import argparse
import csv
import gzip
import os
from collections import deque
from datetime import datetime
from pathlib import Path

DATA_DIR        = Path.home() / '.local' / 'share' / 'pumphouse'
SNAPSHOTS       = DATA_DIR / 'snapshots.csv'
WINDOW_SECONDS  = 7200   # 2 hours — matches poll.py
MIN_SPAN_HOURS  = 0.2    # ~12 min minimum span before emitting a rate


def _open(path):
    return gzip.open(path, 'rt') if str(path).endswith('.gz') else open(path, 'r')


def _write(path, fieldnames, rows):
    tmp = str(path) + '.tmp'
    if str(path).endswith('.gz'):
        with gzip.open(tmp, 'wt', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    else:
        with open(tmp, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    os.replace(tmp, path)


def main():
    parser = argparse.ArgumentParser(description='Backfill tank_rolling_gph in snapshot CSVs')
    parser.add_argument('--write', action='store_true', help='Rewrite files (default: dry-run)')
    args = parser.parse_args()

    sources = sorted(DATA_DIR.glob('snapshots-*.csv.gz')) + [SNAPSHOTS]
    sources = [s for s in sources if s.exists()]

    buf = deque()   # (ts_float, gallons) — carried across files
    total_filled = 0

    for src in sources:
        with _open(src) as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)

        filled = 0
        for row in rows:
            try:
                ts  = datetime.fromisoformat(row['timestamp']).timestamp()
                gal = float(row['tank_gallons'])
            except (KeyError, ValueError):
                row['tank_rolling_gph'] = ''
                continue

            buf.append((ts, gal))
            cutoff = ts - WINDOW_SECONDS
            while len(buf) > 1 and buf[0][0] < cutoff:
                buf.popleft()

            if len(buf) >= 2:
                ts0, g0 = buf[0]
                ts1, g1 = buf[-1]
                dt_h = (ts1 - ts0) / 3600
                gph  = f'{(g1 - g0) / dt_h:.1f}' if dt_h >= MIN_SPAN_HOURS else ''
            else:
                gph = ''

            if row.get('tank_rolling_gph', '') != gph:
                filled += 1
            row['tank_rolling_gph'] = gph

        if 'tank_rolling_gph' not in fieldnames:
            fieldnames.append('tank_rolling_gph')

        total_filled += filled
        action = 'Would update' if not args.write else 'Updated'
        print(f'{action} {src.name}: {len(rows)} rows, {filled} gph values changed')

        if args.write:
            _write(src, fieldnames, rows)

    print(f'\nTotal rows with changed gph: {total_filled}')
    if not args.write:
        print('Re-run with --write to apply.')


if __name__ == '__main__':
    main()
