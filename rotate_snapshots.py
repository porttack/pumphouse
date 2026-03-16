#!/usr/bin/env python3
"""
Monthly rotation for snapshots.csv and events.csv.

Keeps the last 60 days of data in each live file.
Older rows are archived by month to:
    ~/.local/share/pumphouse/snapshots-YYYY-MM.csv.gz
    ~/.local/share/pumphouse/events-YYYY-MM.csv.gz

Safe to run multiple times (idempotent — only archives rows not yet archived).
"""
import csv
import gzip
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

DATA_DIR  = Path.home() / '.local' / 'share' / 'pumphouse'
KEEP_DAYS = 60


def rotate(live_file: Path, prefix: str):
    if not live_file.exists():
        print(f"  Skipping {live_file.name}: not found")
        return

    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)

    with open(live_file, newline='') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        all_rows = list(reader)

    if not fieldnames or not all_rows:
        print(f"  {live_file.name}: empty, nothing to rotate")
        return

    keep_rows = []
    by_month  = defaultdict(list)

    for row in all_rows:
        try:
            ts = datetime.fromisoformat(row['timestamp'])
        except (KeyError, ValueError):
            keep_rows.append(row)
            continue

        if ts >= cutoff:
            keep_rows.append(row)
        else:
            by_month[ts.strftime('%Y-%m')].append(row)

    archived_total = sum(len(v) for v in by_month.values())
    if archived_total == 0:
        print(f"  {live_file.name}: no rows older than {KEEP_DAYS} days")
        return

    for month_key, rows in sorted(by_month.items()):
        archive_path = DATA_DIR / f'{prefix}-{month_key}.csv.gz'
        write_header = not archive_path.exists()
        with gzip.open(archive_path, 'at', newline='') as gz:
            writer = csv.DictWriter(gz, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            writer.writerows(rows)
        print(f"    {len(rows):5d} rows → {archive_path.name}")

    with open(live_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(keep_rows)

    print(f"  {live_file.name}: {archived_total} archived, {len(keep_rows)} remain")


def main():
    print(f"Rotating data files (keeping last {KEEP_DAYS} days)...")
    rotate(DATA_DIR / 'snapshots.csv', 'snapshots')
    rotate(DATA_DIR / 'events.csv',    'events')
    return 0


if __name__ == '__main__':
    sys.exit(main())
