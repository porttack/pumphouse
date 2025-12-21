"""
Shared statistics and analytics functions
"""
import csv
import os
from datetime import datetime

def _find_recovery_in_data(snapshots, threshold_gallons, stagnation_hours, max_stagnation_gain, lookback_hours=24):
    """
    Core algorithm to find recovery event in snapshot data.
    Separated for easier testing with synthetic data.

    Args:
        snapshots: List of dicts with 'ts' (datetime) and 'gallons' (float)
        threshold_gallons: Min gallons to count as recovery
        stagnation_hours: Length of stagnation period to verify
        max_stagnation_gain: Max gain during stagnation to be considered "stagnant"
        lookback_hours: How far back to search for stagnation windows (default: 24)

    Returns:
        datetime of stagnation start, or None

    Examples:
        >>> from datetime import datetime, timedelta
        >>>
        >>> # TEST 1: Slow continuous fill (should NOT trigger - this is the bug case)
        >>> base_time = datetime(2025, 12, 20, 12, 0)
        >>> slow_fill = [
        ...     {'ts': base_time - timedelta(hours=8), 'gallons': 1000},
        ...     {'ts': base_time - timedelta(hours=6), 'gallons': 1010},  # +10 gal over 2 hrs
        ...     {'ts': base_time - timedelta(hours=4), 'gallons': 1020},  # +10 gal over 2 hrs
        ...     {'ts': base_time - timedelta(hours=2), 'gallons': 1030},  # +10 gal over 2 hrs
        ...     {'ts': base_time, 'gallons': 1060},  # +30 gal over 2 hrs, total +60
        ... ]
        >>> result = _find_recovery_in_data(slow_fill, threshold_gallons=50,
        ...                                  stagnation_hours=6, max_stagnation_gain=15)
        >>> result is None  # Should be None - continuous fill, not stagnant
        True
        >>>
        >>> # TEST 2: True recovery (6 hrs stagnant, then 50+ gal gain - SHOULD trigger)
        >>> recovery = [
        ...     {'ts': base_time - timedelta(hours=10), 'gallons': 1000},
        ...     {'ts': base_time - timedelta(hours=8), 'gallons': 1005},   # Stagnant start
        ...     {'ts': base_time - timedelta(hours=6), 'gallons': 1008},   # +3 gal (stagnant)
        ...     {'ts': base_time - timedelta(hours=4), 'gallons': 1010},   # +2 gal (stagnant)
        ...     {'ts': base_time - timedelta(hours=2), 'gallons': 1012},   # +2 gal (end stagnation)
        ...     {'ts': base_time - timedelta(hours=1), 'gallons': 1040},   # +28 gal (recovery starts)
        ...     {'ts': base_time, 'gallons': 1070},  # +30 gal (total recovery: 58 gal)
        ... ]
        >>> result = _find_recovery_in_data(recovery, threshold_gallons=50,
        ...                                  stagnation_hours=6, max_stagnation_gain=15)
        >>> result == base_time - timedelta(hours=8)  # Returns stagnation start timestamp
        True
    """
    if len(snapshots) < 2:
        return None

    # Work with most recent data
    now = snapshots[-1]['ts']
    lookback_cutoff = now.timestamp() - (lookback_hours * 3600)

    # Filter to recent snapshots only
    recent_snapshots = [s for s in snapshots if s['ts'].timestamp() >= lookback_cutoff]

    if len(recent_snapshots) < 2:
        return None

    # Search for: stagnation window followed by significant gain
    # Check each possible stagnation window (going backwards = most recent first)
    for end_idx in range(len(recent_snapshots) - 1, 0, -1):
        period_end = recent_snapshots[end_idx]
        period_end_time = period_end['ts'].timestamp()
        period_end_gallons = period_end['gallons']

        # Define stagnation window: N hours before this snapshot
        stagnation_start_time = period_end_time - (stagnation_hours * 3600)

        # Find snapshot at start of stagnation window
        period_start = None
        for snapshot in reversed(recent_snapshots[:end_idx]):
            if snapshot['ts'].timestamp() <= stagnation_start_time:
                period_start = snapshot
                break

        if period_start is None:
            continue

        period_start_gallons = period_start['gallons']

        # CHECK #1: Was tank truly stagnant? (didn't gain more than threshold)
        gain_during_stagnation = period_end_gallons - period_start_gallons
        if gain_during_stagnation > max_stagnation_gain:
            continue  # Tank was filling during this period - not stagnant

        # CHECK #2: Did tank recover significantly after stagnation?
        current_gallons = recent_snapshots[-1]['gallons']
        recovery_gain = current_gallons - period_end_gallons

        if recovery_gain >= threshold_gallons:
            # FOUND IT! Return start of stagnation as unique identifier
            return period_start['ts']

    return None


def find_last_refill(snapshots_file, threshold_gallons=50, stagnation_hours=6, max_stagnation_gain=15):
    """
    Find most recent well recovery: stagnation followed by significant refill.

    WHAT IT DETECTS:
    - Tank was stagnant for 6+ hours (gained ≤15 gallons)
    - Then gained 50+ gallons after that stagnation
    - Returns: timestamp of stagnation start (stays constant for entire recovery)

    WHAT IT FILTERS OUT:
    - Continuous slow fill (your current situation)
    - Gradual increases without a true low/stagnant period

    Args:
        snapshots_file: Path to snapshots.csv file
        threshold_gallons: Min gallon increase to count as recovery (default: 50)
        stagnation_hours: Hours of stagnation to verify (default: 6)
        max_stagnation_gain: Max gallons gained during stagnation (default: 15)

    Returns:
        Tuple of (datetime, float) representing stagnation start timestamp and days_ago,
        or (None, None) if no recovery found
    """
    if not os.path.exists(snapshots_file):
        return None, None

    try:
        # Read and parse snapshot data
        with open(snapshots_file, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        snapshots = []
        for row in rows:
            try:
                snapshots.append({
                    'ts': datetime.fromisoformat(row['timestamp']),
                    'gallons': float(row['tank_gallons'])
                })
            except (ValueError, KeyError):
                continue

        snapshots.sort(key=lambda x: x['ts'])

        # Run the core algorithm
        recovery_ts = _find_recovery_in_data(
            snapshots,
            threshold_gallons,
            stagnation_hours,
            max_stagnation_gain
        )

        if recovery_ts:
            days_ago = (datetime.now() - recovery_ts).total_seconds() / 86400
            return recovery_ts, days_ago

        return None, None

    except Exception as e:
        # Silently ignore errors
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
