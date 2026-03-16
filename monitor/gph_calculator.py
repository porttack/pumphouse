"""
GPH (Gallons Per Hour) calculator for well pump performance tracking.

Calculates wall-clock GPH (real-time fill rate) for both slow-fill and fast-fill modes
by analyzing snapshots over the past 2-4 weeks.

Wall-clock GPH answers: "How fast does the tank fill in real calendar time?"
This accounts for pump duty cycle, well recovery, and household usage.
"""

import csv
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple

from monitor.config import DEFAULT_SNAPSHOTS_FILE


def calculate_gph_from_snapshots(
    filepath: str = DEFAULT_SNAPSHOTS_FILE,
    lookback_days: int = 21,
    min_delta_gallons: int = 18,
    window_hours: int = 12
) -> Dict[str, Optional[float]]:
    """
    Calculate wall-clock GPH for slow-fill and fast-fill modes.

    Args:
        filepath: Path to snapshots.csv
        lookback_days: How many days back to analyze (default: 21 = ~3 weeks)
        min_delta_gallons: Minimum tank delta to consider valid (default: 18 = 3x sensor error)
        window_hours: Time window size for analysis (default: 12 hours)

    Returns:
        Dict with:
            - slow_fill_gph: Wall-clock GPH for slow-fill (relay OFF), or None
            - fast_fill_gph: Wall-clock GPH for fast-fill (relay ON), or None
            - slow_fill_samples: Number of valid slow-fill windows analyzed
            - fast_fill_samples: Number of valid fast-fill windows analyzed
            - last_updated: Timestamp of calculation
    """
    if not os.path.exists(filepath):
        return {
            'slow_fill_gph': None,
            'fast_fill_gph': None,
            'slow_fill_samples': 0,
            'fast_fill_samples': 0,
            'last_updated': None
        }

    try:
        # Read all snapshots
        with open(filepath, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if len(rows) == 0:
            return {
                'slow_fill_gph': None,
                'fast_fill_gph': None,
                'slow_fill_samples': 0,
                'fast_fill_samples': 0,
                'last_updated': None
            }

        # Filter to lookback window
        cutoff_time = datetime.now() - timedelta(days=lookback_days)
        recent_rows = []

        for row in rows:
            try:
                ts = datetime.strptime(row['timestamp'], '%Y-%m-%d %H:%M:%S.%f')
            except ValueError:
                try:
                    ts = datetime.strptime(row['timestamp'], '%Y-%m-%d %H:%M:%S')
                except:
                    continue

            if ts >= cutoff_time:
                recent_rows.append({
                    'timestamp': ts,
                    'tank_gallons': float(row['tank_gallons']),
                    'relay': row.get('relay_supply_override', 'OFF')
                })

        if len(recent_rows) < 10:
            return {
                'slow_fill_gph': None,
                'fast_fill_gph': None,
                'slow_fill_samples': 0,
                'fast_fill_samples': 0,
                'last_updated': None
            }

        # Analyze windows
        slow_fill_gph_values = []
        fast_fill_gph_values = []

        i = 0
        while i < len(recent_rows):
            start_time = recent_rows[i]['timestamp']
            end_time = start_time + timedelta(hours=window_hours)

            # Determine if window is slow-fill or fast-fill (majority vote)
            relay_votes = {'ON': 0, 'OFF': 0}
            start_gallons = recent_rows[i]['tank_gallons']
            end_gallons = start_gallons
            window_count = 0

            j = i
            while j < len(recent_rows) and recent_rows[j]['timestamp'] <= end_time:
                relay_votes[recent_rows[j]['relay']] = relay_votes.get(recent_rows[j]['relay'], 0) + 1
                end_gallons = recent_rows[j]['tank_gallons']
                window_count += 1
                j += 1

            # Need at least a few snapshots to be valid
            if window_count < 3:
                i = j if j > i else i + 1
                continue

            delta = end_gallons - start_gallons

            # Only consider positive deltas >= threshold
            if delta >= min_delta_gallons:
                wall_clock_hours = (recent_rows[j-1]['timestamp'] - recent_rows[i]['timestamp']).total_seconds() / 3600

                # Ensure we actually have a meaningful time window
                if wall_clock_hours >= window_hours * 0.8:  # At least 80% of target window
                    gph = delta / wall_clock_hours

                    # Categorize by majority relay status
                    if relay_votes['OFF'] > relay_votes['ON']:
                        slow_fill_gph_values.append(gph)
                    else:
                        fast_fill_gph_values.append(gph)

            i = j if j > i else i + 1

        # Calculate medians (more robust than average)
        slow_fill_gph = None
        if slow_fill_gph_values:
            slow_fill_gph_values.sort()
            slow_fill_gph = slow_fill_gph_values[len(slow_fill_gph_values) // 2]

        fast_fill_gph = None
        if fast_fill_gph_values:
            fast_fill_gph_values.sort()
            fast_fill_gph = fast_fill_gph_values[len(fast_fill_gph_values) // 2]

        return {
            'slow_fill_gph': round(slow_fill_gph, 1) if slow_fill_gph else None,
            'fast_fill_gph': round(fast_fill_gph, 1) if fast_fill_gph else None,
            'slow_fill_samples': len(slow_fill_gph_values),
            'fast_fill_samples': len(fast_fill_gph_values),
            'last_updated': datetime.now()
        }

    except Exception as e:
        print(f"Error calculating GPH: {e}")
        return {
            'slow_fill_gph': None,
            'fast_fill_gph': None,
            'slow_fill_samples': 0,
            'fast_fill_samples': 0,
            'last_updated': None
        }


def get_cached_gph(
    cache_file: str = 'gph_cache.csv',
    max_age_hours: int = 24,
    snapshots_file: str = DEFAULT_SNAPSHOTS_FILE
) -> Dict[str, Optional[float]]:
    """
    Get GPH metrics from cache, recalculating if stale or missing.

    Args:
        cache_file: Path to GPH cache file
        max_age_hours: Maximum age of cache before recalculating (default: 24 hours)
        snapshots_file: Path to snapshots.csv

    Returns:
        Dict with GPH metrics (same format as calculate_gph_from_snapshots)
    """
    # Try to read from cache
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                reader = csv.DictReader(f)
                rows = list(reader)

                if rows:
                    last_row = rows[-1]
                    cache_time = datetime.strptime(last_row['timestamp'], '%Y-%m-%d %H:%M:%S.%f')
                    age_hours = (datetime.now() - cache_time).total_seconds() / 3600

                    # If cache is fresh enough, use it
                    if age_hours < max_age_hours:
                        return {
                            'slow_fill_gph': float(last_row['slow_fill_gph']) if last_row['slow_fill_gph'] else None,
                            'fast_fill_gph': float(last_row['fast_fill_gph']) if last_row['fast_fill_gph'] else None,
                            'slow_fill_samples': int(last_row['slow_fill_samples']),
                            'fast_fill_samples': int(last_row['fast_fill_samples']),
                            'last_updated': cache_time
                        }
        except Exception as e:
            print(f"Error reading GPH cache: {e}")

    # Cache miss or stale - recalculate
    metrics = calculate_gph_from_snapshots(snapshots_file)

    # Write to cache
    try:
        # Initialize cache file if it doesn't exist
        if not os.path.exists(cache_file):
            with open(cache_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'slow_fill_gph', 'fast_fill_gph',
                    'slow_fill_samples', 'fast_fill_samples'
                ])

        # Append new calculation
        with open(cache_file, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                metrics['last_updated'].strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] if metrics['last_updated'] else '',
                metrics['slow_fill_gph'] if metrics['slow_fill_gph'] is not None else '',
                metrics['fast_fill_gph'] if metrics['fast_fill_gph'] is not None else '',
                metrics['slow_fill_samples'],
                metrics['fast_fill_samples']
            ])
    except Exception as e:
        print(f"Error writing GPH cache: {e}")

    return metrics


def format_gph_for_display(metrics: Dict[str, Optional[float]]) -> str:
    """
    Format GPH metrics for human-readable display.

    Args:
        metrics: Dict from calculate_gph_from_snapshots or get_cached_gph

    Returns:
        Formatted string like "Slow: 5 GPH (12 samples), Fast: 11 GPH (8 samples)"
    """
    parts = []

    if metrics.get('slow_fill_gph') is not None:
        samples = metrics.get('slow_fill_samples', 0)
        parts.append(f"Slow: {metrics['slow_fill_gph']:.0f} GPH ({samples} samples)")
    else:
        parts.append("Slow: N/A")

    if metrics.get('fast_fill_gph') is not None:
        samples = metrics.get('fast_fill_samples', 0)
        parts.append(f"Fast: {metrics['fast_fill_gph']:.0f} GPH ({samples} samples)")
    else:
        parts.append("Fast: N/A")

    return ", ".join(parts)


if __name__ == '__main__':
    # Test calculation
    import sys

    snapshots_file = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SNAPSHOTS_FILE

    print(f"Calculating GPH from {snapshots_file}...")
    print()

    metrics = calculate_gph_from_snapshots(snapshots_file)

    print("Results:")
    print(f"  Slow-fill GPH: {metrics['slow_fill_gph']} ({metrics['slow_fill_samples']} samples)")
    print(f"  Fast-fill GPH: {metrics['fast_fill_gph']} ({metrics['fast_fill_samples']} samples)")
    print(f"  Last updated: {metrics['last_updated']}")
    print()
    print(f"Formatted: {format_gph_for_display(metrics)}")
