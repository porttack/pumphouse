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

    Detects: Stagnation (6hrs gaining ≤15 gal) followed by recovery (50+ gal gain in next 6hrs).
    Uses FIXED time windows to prevent false positives from continuous slow fill.

    Args:
        snapshots: List of dicts with 'ts' (datetime) and 'gallons' (float)
        threshold_gallons: Min gallons to count as recovery
        stagnation_hours: Length of stagnation period to verify (also used for recovery window)
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
        # Look for recovery within a fixed window (stagnation_hours) after stagnation ends
        # This prevents false positives from continuous slow fill
        recovery_end_time = period_end_time + (stagnation_hours * 3600)

        # Find the snapshot closest to recovery_end_time
        recovery_snapshot = None
        for snapshot in recent_snapshots[end_idx:]:
            if snapshot['ts'].timestamp() >= recovery_end_time:
                recovery_snapshot = snapshot
                break

        # If we don't have data far enough ahead, use the last snapshot we have
        if recovery_snapshot is None:
            recovery_snapshot = recent_snapshots[-1]

        recovery_gain = recovery_snapshot['gallons'] - period_end_gallons

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


def find_full_flow_periods(snapshots_file, pressure_threshold=90.0, lookback_hours=24):
    """
    Find periods where pressure_high_percent >= threshold, indicating full-flow.

    Groups consecutive high-pressure snapshots into periods and calculates:
    - Duration of each period
    - Total gallons pumped (from estimated_gallons_pumped)
    - Estimated GPH based on tank level changes

    Args:
        snapshots_file: Path to snapshots.csv
        pressure_threshold: Min pressure_high_percent to count as full-flow (default: 90.0)
        lookback_hours: How far back to search (default: 24)

    Returns:
        List of dicts, each containing:
        {
            'start_ts': datetime,
            'end_ts': datetime,
            'duration_minutes': float,
            'snapshot_count': int,
            'total_gallons_pumped': float,
            'tank_start_gallons': float,
            'tank_end_gallons': float,
            'tank_gain': float,
            'estimated_gph': float
        }
    """
    if not os.path.exists(snapshots_file):
        return []

    try:
        with open(snapshots_file, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if len(rows) < 1:
            return []

        now = datetime.now()
        cutoff = now.timestamp() - (lookback_hours * 3600)

        # Parse snapshots with pressure data
        snapshots = []
        for row in rows:
            try:
                ts = datetime.fromisoformat(row['timestamp'])
                if ts.timestamp() < cutoff:
                    continue

                pressure_pct = float(row['pressure_high_percent'])
                tank_gallons = float(row['tank_gallons'])

                # Parse estimated_gallons_pumped (might have + sign)
                est_gal_str = row.get('estimated_gallons_pumped', '0')
                est_gallons = float(est_gal_str.replace('+', '')) if est_gal_str else 0.0

                snapshots.append({
                    'ts': ts,
                    'pressure_pct': pressure_pct,
                    'tank_gallons': tank_gallons,
                    'est_gallons': est_gallons
                })
            except (ValueError, KeyError):
                continue

        if len(snapshots) < 1:
            return []

        # Sort by timestamp
        snapshots.sort(key=lambda x: x['ts'])

        # Group consecutive high-pressure periods
        periods = []
        current_period = None

        for snap in snapshots:
            if snap['pressure_pct'] >= pressure_threshold:
                if current_period is None:
                    # Start new period
                    current_period = {
                        'start_ts': snap['ts'],
                        'end_ts': snap['ts'],
                        'snapshot_count': 1,
                        'total_gallons_pumped': snap['est_gallons'],
                        'tank_start_gallons': snap['tank_gallons'],
                        'tank_end_gallons': snap['tank_gallons']
                    }
                else:
                    # Extend current period
                    current_period['end_ts'] = snap['ts']
                    current_period['snapshot_count'] += 1
                    current_period['total_gallons_pumped'] += snap['est_gallons']
                    current_period['tank_end_gallons'] = snap['tank_gallons']
            else:
                if current_period is not None:
                    # End of period - finalize and save
                    _finalize_period(current_period)
                    periods.append(current_period)
                    current_period = None

        # Handle last period if still active
        if current_period is not None:
            _finalize_period(current_period)
            periods.append(current_period)

        return periods

    except Exception as e:
        return []


def _finalize_period(period):
    """Calculate derived fields for a full-flow period"""
    duration_seconds = (period['end_ts'] - period['start_ts']).total_seconds()
    period['duration_minutes'] = duration_seconds / 60

    # Tank gain during period
    period['tank_gain'] = period['tank_end_gallons'] - period['tank_start_gallons']

    # Estimate GPH based on tank readings
    if duration_seconds > 0:
        duration_hours = duration_seconds / 3600
        period['estimated_gph'] = period['tank_gain'] / duration_hours
    else:
        period['estimated_gph'] = 0.0
