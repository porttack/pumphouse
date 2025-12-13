"""
Shared statistics and analytics functions
"""
import csv
import os
from datetime import datetime

def find_last_refill(snapshots_file, threshold_gallons=50, window_hours=24):
    """
    Find the last time tank increased by threshold+ gallons in window_hours.
    Returns (timestamp, days_ago) or (None, None) if not found.

    This implements the same logic as web.py get_snapshots_stats() for refill detection.

    Args:
        snapshots_file: Path to snapshots.csv file
        threshold_gallons: Minimum gallon increase to count as refill (default: 50)
        window_hours: Time window to measure increase (default: 24)

    Returns:
        Tuple of (datetime, float) representing timestamp and days_ago, or (None, None)
    """
    if not os.path.exists(snapshots_file):
        return None, None

    try:
        with open(snapshots_file, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if len(rows) <= 1:
            return None, None

        now = datetime.now()

        # Parse rows with timestamps and gallons
        parsed_rows = []
        for row in rows:
            try:
                parsed_rows.append({
                    'ts': datetime.fromisoformat(row['timestamp']),
                    'gallons': float(row['tank_gallons'])
                })
            except (ValueError, KeyError):
                continue

        # Ensure rows are sorted by timestamp
        parsed_rows.sort(key=lambda x: x['ts'])

        # Iterate backwards from the most recent snapshot
        for i in range(len(parsed_rows) - 1, 0, -1):
            current_snapshot = parsed_rows[i]
            cutoff_ts = current_snapshot['ts'].timestamp() - (window_hours * 3600)

            # Find the closest snapshot from ~window_hours ago
            past_snapshot = next(
                (s for s in reversed(parsed_rows[:i]) if s['ts'].timestamp() <= cutoff_ts),
                None
            )

            if past_snapshot and (current_snapshot['gallons'] - past_snapshot['gallons']) >= threshold_gallons:
                days_ago = (now - current_snapshot['ts']).total_seconds() / 86400
                return current_snapshot['ts'], days_ago

        return None, None

    except Exception as e:
        # Silently ignore errors in refill calculation
        return None, None
