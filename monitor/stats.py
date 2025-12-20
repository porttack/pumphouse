"""
Shared statistics and analytics functions
"""
import csv
import os
from datetime import datetime

def find_last_refill(snapshots_file, threshold_gallons=50, stagnation_hours=6):
    """
    Find the last time tank recovered from stagnation (6+ hours flat/declining followed by 50+ gallon gain).
    Returns (timestamp, days_ago) or (None, None) if not found.

    This fixes the duplicate alert issue by returning the timestamp of the LOW POINT (end of stagnation),
    which remains constant throughout the entire recovery event.

    Args:
        snapshots_file: Path to snapshots.csv file
        threshold_gallons: Minimum gallon increase to count as refill (default: 50)
        stagnation_hours: Hours of flat/declining before recovery (default: 6)

    Returns:
        Tuple of (datetime, float) representing timestamp of low point and days_ago, or (None, None)
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

        # Look for pattern: stagnation period followed by significant gain
        # Iterate backwards to find most recent recovery event
        for i in range(len(parsed_rows) - 1, 0, -1):
            current_snapshot = parsed_rows[i]

            # Look back to find a stagnation period
            stagnation_cutoff = current_snapshot['ts'].timestamp() - (stagnation_hours * 3600)

            # Find snapshot from stagnation_hours ago
            stagnation_start = next(
                (s for s in reversed(parsed_rows[:i]) if s['ts'].timestamp() <= stagnation_cutoff),
                None
            )

            if not stagnation_start:
                continue

            # Find the low point (minimum gallons) during the stagnation period
            stagnation_snapshots = [
                s for s in parsed_rows
                if stagnation_start['ts'] <= s['ts'] <= current_snapshot['ts']
            ]

            if len(stagnation_snapshots) < 2:
                continue

            low_point = min(stagnation_snapshots, key=lambda x: x['gallons'])

            # Check if tank gained threshold+ gallons from low point to current
            gain = current_snapshot['gallons'] - low_point['gallons']

            if gain >= threshold_gallons:
                # Return the LOW POINT timestamp (this remains constant for the entire recovery)
                days_ago = (now - low_point['ts']).total_seconds() / 86400
                return low_point['ts'], days_ago

        return None, None

    except Exception as e:
        # Silently ignore errors in refill calculation
        return None, None

def find_high_flow_event(snapshots_file, gph_threshold=60, window_hours=6,
                         averaging_snapshots=2, snapshot_interval_minutes=15):
    """
    Find the last time tank showed high flow rate (>threshold GPH).

    Args:
        snapshots_file: Path to snapshots.csv
        gph_threshold: Minimum GPH to trigger (default: 60)
        window_hours: How far back to look (default: 6)
        averaging_snapshots: Average over N snapshots (default: 2)
        snapshot_interval_minutes: Interval between snapshots (default: 15)

    Returns:
        (timestamp, gph) or (None, None)
    """
    if not os.path.exists(snapshots_file):
        return None, None

    try:
        with open(snapshots_file, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if len(rows) < averaging_snapshots + 1:
            return None, None

        now = datetime.now()
        window_cutoff = now.timestamp() - (window_hours * 3600)

        # Parse rows with timestamps and deltas
        parsed_rows = []
        for row in rows:
            try:
                ts = datetime.fromisoformat(row['timestamp'])
                # Only look at snapshots within the window
                if ts.timestamp() < window_cutoff:
                    continue

                delta = float(row['tank_gallons_delta']) if row.get('tank_gallons_delta') else 0
                parsed_rows.append({
                    'ts': ts,
                    'delta': delta
                })
            except (ValueError, KeyError):
                continue

        if len(parsed_rows) < averaging_snapshots:
            return None, None

        # Ensure rows are sorted by timestamp
        parsed_rows.sort(key=lambda x: x['ts'])

        # Calculate GPH using sliding window of N snapshots
        for i in range(averaging_snapshots - 1, len(parsed_rows)):
            # Get last N snapshots
            window = parsed_rows[i - averaging_snapshots + 1:i + 1]

            # Calculate average GPH over this window
            total_gain = sum(s['delta'] for s in window)
            total_minutes = averaging_snapshots * snapshot_interval_minutes
            avg_gph = (total_gain / total_minutes) * 60 if total_minutes > 0 else 0

            if avg_gph >= gph_threshold:
                # Return timestamp of first snapshot in this high-flow window
                return window[0]['ts'], avg_gph

        return None, None

    except Exception as e:
        # Silently ignore errors
        return None, None

def find_backflush_event(snapshots_file, threshold_gallons=50, window_snapshots=2,
                         time_start="00:00", time_end="04:30", snapshot_interval_minutes=15):
    """
    Find the last time a backflush occurred (large water usage in short time during specific hours).

    Args:
        snapshots_file: Path to snapshots.csv
        threshold_gallons: Minimum gallons lost to trigger (default: 50)
        window_snapshots: Look back N snapshots (default: 2)
        time_start: Start of backflush window HH:MM (default: "00:00")
        time_end: End of backflush window HH:MM (default: "04:30")
        snapshot_interval_minutes: Interval between snapshots (default: 15)

    Returns:
        (timestamp, gallons_used) or (None, None)
    """
    if not os.path.exists(snapshots_file):
        return None, None

    try:
        with open(snapshots_file, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if len(rows) < window_snapshots + 1:
            return None, None

        # Parse time window
        start_hour, start_min = map(int, time_start.split(':'))
        end_hour, end_min = map(int, time_end.split(':'))

        # Parse rows with timestamps and deltas
        parsed_rows = []
        for row in rows:
            try:
                ts = datetime.fromisoformat(row['timestamp'])
                delta = float(row['tank_gallons_delta']) if row.get('tank_gallons_delta') else 0
                parsed_rows.append({
                    'ts': ts,
                    'delta': delta
                })
            except (ValueError, KeyError):
                continue

        if len(parsed_rows) < window_snapshots:
            return None, None

        # Ensure rows are sorted by timestamp
        parsed_rows.sort(key=lambda x: x['ts'])

        # Look for significant decline during backflush time window
        # Iterate backwards to find most recent event
        for i in range(len(parsed_rows) - 1, window_snapshots - 1, -1):
            current_snapshot = parsed_rows[i]

            # Check if timestamp is within backflush time window
            ts_hour = current_snapshot['ts'].hour
            ts_min = current_snapshot['ts'].minute
            ts_time_mins = ts_hour * 60 + ts_min
            start_time_mins = start_hour * 60 + start_min
            end_time_mins = end_hour * 60 + end_min

            if not (start_time_mins <= ts_time_mins <= end_time_mins):
                continue

            # Calculate total decline over window_snapshots
            window = parsed_rows[i - window_snapshots + 1:i + 1]
            total_decline = sum(s['delta'] for s in window)

            # Backflush is a DECLINE (negative delta)
            if total_decline <= -threshold_gallons:
                gallons_used = abs(total_decline)
                # Return timestamp of first snapshot in backflush window
                return window[0]['ts'], gallons_used

        return None, None

    except Exception as e:
        # Silently ignore errors
        return None, None
