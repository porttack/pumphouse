#!/usr/bin/env python3
"""
Monthly rotation for snapshots.csv.

Keeps the last 60 days of data in the live file.
Older rows are archived by month to:
    ~/.local/share/pumphouse/snapshots-YYYY-MM.csv.gz

Safe to run multiple times (idempotent — only archives rows not yet archived).
"""
import csv
import gzip
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

DATA_DIR   = Path.home() / '.local' / 'share' / 'pumphouse'
LIVE_FILE  = DATA_DIR / 'snapshots.csv'
KEEP_DAYS  = 60   # rows newer than this stay in the live file


def main():
    if not LIVE_FILE.exists():
        print(f"Nothing to do: {LIVE_FILE} not found")
        return 0

    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)

    with open(LIVE_FILE, newline='') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        all_rows = list(reader)

    if not fieldnames or not all_rows:
        print("File empty, nothing to rotate")
        return 0

    keep_rows   = []
    by_month    = defaultdict(list)   # 'YYYY-MM' -> [rows]

    for row in all_rows:
        try:
            ts = datetime.fromisoformat(row['timestamp'])
        except (KeyError, ValueError):
            keep_rows.append(row)   # unparseable — keep it
            continue

        if ts >= cutoff:
            keep_rows.append(row)
        else:
            month_key = ts.strftime('%Y-%m')
            by_month[month_key].append(row)

    archived_total = sum(len(v) for v in by_month.values())
    if archived_total == 0:
        print(f"No rows older than {KEEP_DAYS} days — nothing to archive")
        return 0

    # Archive each month's rows into its own .csv.gz (append if exists)
    for month_key, rows in sorted(by_month.items()):
        archive_path = DATA_DIR / f'snapshots-{month_key}.csv.gz'
        write_header = not archive_path.exists()

        with gzip.open(archive_path, 'at', newline='') as gz:
            writer = csv.DictWriter(gz, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            writer.writerows(rows)

        print(f"  Archived {len(rows):5d} rows → {archive_path.name}")

    # Rewrite live file with only the kept rows
    with open(LIVE_FILE, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(keep_rows)

    print(f"Done: {archived_total} rows archived, {len(keep_rows)} rows remain in live file")
    return 0


if __name__ == '__main__':
    sys.exit(main())
