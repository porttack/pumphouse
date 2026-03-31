#!/usr/bin/env python3
"""
build_daily.py — Generate (or incrementally update) daily.csv from snapshots.

Usage:
    python build_daily.py            # append-only: adds any missing days
    python build_daily.py --rebuild  # wipe daily.csv and regenerate from scratch

Reads:
    ~/.local/share/pumphouse/snapshots-YYYY-MM.csv.gz   (archives)
    ~/.local/share/pumphouse/snapshots.csv               (current)

Writes:
    ~/.local/share/pumphouse/daily.csv
"""

import argparse
import csv
import gzip
import os
import sys
from collections import defaultdict
from pathlib import Path

DATA_DIR   = Path.home() / '.local' / 'share' / 'pumphouse'
DAILY_CSV  = DATA_DIR / 'daily.csv'
SNAPSHOTS  = DATA_DIR / 'snapshots.csv'

# A 15-min window qualifies as a "high-pressure event" window if
# pressure was high for more than this fraction of the window.
HIGH_THRESHOLD_PCT = 50.0

FIELDNAMES = [
    # Coverage
    'date', 'n_snapshots', 'hours_covered',
    # Tank levels
    'gallons_start', 'gallons_end', 'gallons_net_change',
    'gallons_min', 'gallons_max',
    # Occupancy
    'occupied_windows', 'occupied_pct',
    # Float & override
    'float_calling_windows', 'float_full_windows',
    'override_windows', 'override_minutes',
    'total_purges',
    # Bypass
    'bypass_windows', 'bypass_minutes',
    # Pressure HIGH overall (non-bypass)
    'pressure_high_pct_overall', 'pressure_high_minutes',
    # High-pressure events (consecutive runs >50%, non-bypass)
    'high_events', 'high_events_total_minutes',
    'high_events_avg_minutes', 'high_events_pump_gph',
    # Low-pressure (normal, non-bypass, non-high-event) windows
    'low_events_windows', 'low_events_pump_gph', 'low_events_consume_gph',
    # Weather
    'outdoor_temp_min', 'outdoor_temp_max', 'outdoor_temp_avg',
    'indoor_temp_min',  'indoor_temp_max',  'indoor_temp_avg',
    'humidity_avg', 'baro_avg', 'wind_gust_max',
]


def flt(row, key, default=0.0):
    try:
        return float(row.get(key, default) or default)
    except (ValueError, TypeError):
        return default


def avg(vals):
    v = [x for x in vals if x is not None]
    return round(sum(v) / len(v), 1) if v else None


def summarize_day(date, rows):
    """Return a dict of daily stats for one calendar day's worth of rows."""
    n = len(rows)
    if n == 0:
        return None

    gallons = [flt(r, 'tank_gallons') for r in rows]

    bypass_rows = [r for r in rows if r.get('relay_bypass', '').upper() == 'ON']
    non_bypass  = [r for r in rows if r.get('relay_bypass', '').upper() != 'ON']

    # Split non-bypass rows into high-event runs vs low windows
    runs, current_run, low_rows = [], [], []
    for r in non_bypass:
        if flt(r, 'pressure_high_percent') > HIGH_THRESHOLD_PCT:
            current_run.append(r)
        else:
            if current_run:
                runs.append(current_run)
                current_run = []
            low_rows.append(r)
    if current_run:
        runs.append(current_run)
    high_rows = [r for run in runs for r in run]

    def _stats(row_list):
        dur  = sum(flt(r, 'duration_seconds') for r in row_list)
        pump = sum(flt(r, 'estimated_gallons_pumped') for r in row_list)
        high = sum(flt(r, 'pressure_high_seconds') for r in row_list)
        gph  = pump / (dur / 3600) if dur > 0 else None
        return dur, pump, high, gph

    bypass_dur, _,          _,       _         = _stats(bypass_rows)
    nb_dur,     _,          nb_high, _         = _stats(non_bypass)
    ext_dur,    _,          _,       ext_gph   = _stats(high_rows)
    low_dur,    _,          _,       low_gph   = _stats(low_rows)

    low_consumed = sum(
        -flt(r, 'tank_gallons_delta') for r in low_rows
        if flt(r, 'tank_gallons_delta') < 0
    )
    low_consume_gph = low_consumed / (low_dur / 3600) if low_dur > 0 else None
    run_mins = [sum(flt(r, 'duration_seconds') for r in run) / 60 for run in runs]

    def weather_vals(key):
        return [flt(r, key) for r in rows if (r.get(key) or '').strip()]

    out_temps = weather_vals('outdoor_temp_f')
    in_temps  = weather_vals('indoor_temp_f')
    gusts     = weather_vals('wind_gust_mph')
    humidity  = weather_vals('outdoor_humidity')
    baro      = weather_vals('baro_abs_inhg')

    occ_n    = sum(1 for r in rows if r.get('occupied', '').upper() == 'YES')
    dur_all  = sum(flt(r, 'duration_seconds') for r in rows)
    ov_wins  = sum(1 for r in rows if r.get('relay_supply_override', '').upper() == 'ON')

    return {
        'date':                      date,
        'n_snapshots':               n,
        'hours_covered':             round(dur_all / 3600, 1),
        'gallons_start':             int(gallons[0]),
        'gallons_end':               int(gallons[-1]),
        'gallons_net_change':        int(gallons[-1] - gallons[0]),
        'gallons_min':               int(min(gallons)),
        'gallons_max':               int(max(gallons)),
        'occupied_windows':          occ_n,
        'occupied_pct':              round(occ_n / n * 100, 0),
        'float_calling_windows':     sum(1 for r in rows if r.get('float_ever_calling', '').upper() == 'YES'),
        'float_full_windows':        sum(1 for r in rows if r.get('float_always_full',  '').upper() == 'YES'),
        'override_windows':          ov_wins,
        'override_minutes':          round(ov_wins * 15, 0),
        'total_purges':              sum(int(flt(r, 'purge_count')) for r in rows),
        'bypass_windows':            len(bypass_rows),
        'bypass_minutes':            round(bypass_dur / 60, 0),
        'pressure_high_pct_overall': round(nb_high / nb_dur * 100, 1) if nb_dur else None,
        'pressure_high_minutes':     round(nb_high / 60, 0),
        'high_events':               len(runs),
        'high_events_total_minutes': round(ext_dur / 60, 0),
        'high_events_avg_minutes':   round(sum(run_mins) / len(run_mins), 0) if run_mins else None,
        'high_events_pump_gph':      round(ext_gph, 1) if ext_gph else None,
        'low_events_windows':        len(low_rows),
        'low_events_pump_gph':       round(low_gph, 1) if low_gph else None,
        'low_events_consume_gph':    round(low_consume_gph, 1) if low_consume_gph else None,
        'outdoor_temp_min':          round(min(out_temps), 1) if out_temps else None,
        'outdoor_temp_max':          round(max(out_temps), 1) if out_temps else None,
        'outdoor_temp_avg':          avg(out_temps),
        'indoor_temp_min':           round(min(in_temps), 1) if in_temps else None,
        'indoor_temp_max':           round(max(in_temps), 1) if in_temps else None,
        'indoor_temp_avg':           avg(in_temps),
        'humidity_avg':              avg(humidity),
        'baro_avg':                  avg(baro),
        'wind_gust_max':             round(max(gusts), 1) if gusts else None,
    }


def iter_all_rows():
    """Yield every snapshot row in chronological order from archives + live file."""
    sources = sorted(DATA_DIR.glob('snapshots-*.csv.gz'))
    sources.append(SNAPSHOTS)
    seen_header = False
    for src in sources:
        if not src.exists():
            continue
        opener = gzip.open(src, 'rt') if str(src).endswith('.gz') else open(src, 'r')
        with opener as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield row


def load_existing_dates():
    """Return set of dates already in daily.csv."""
    if not DAILY_CSV.exists():
        return set()
    with open(DAILY_CSV) as f:
        return {row['date'] for row in csv.DictReader(f)}


def main():
    parser = argparse.ArgumentParser(description='Build daily.csv from snapshots')
    parser.add_argument('--rebuild', action='store_true',
                        help='Wipe daily.csv and regenerate from scratch')
    args = parser.parse_args()

    if args.rebuild and DAILY_CSV.exists():
        DAILY_CSV.unlink()
        print('Removed existing daily.csv — rebuilding from scratch.')

    existing_dates = load_existing_dates()

    # Group all rows by date
    by_day = defaultdict(list)
    for row in iter_all_rows():
        d = row['timestamp'][:10]
        by_day[d].append(row)

    today = __import__('datetime').date.today().isoformat()

    # Only process days not already in daily.csv; never write today (incomplete)
    new_dates = sorted(d for d in by_day if d not in existing_dates and d < today)

    if not new_dates:
        print('daily.csv is already up to date.')
        return

    write_header = not DAILY_CSV.exists()
    written = 0
    with open(DAILY_CSV, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        for date in new_dates:
            summary = summarize_day(date, by_day[date])
            if summary:
                writer.writerow(summary)
                written += 1

    print(f'Wrote {written} new days to {DAILY_CSV}')


if __name__ == '__main__':
    main()
