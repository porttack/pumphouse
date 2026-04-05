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

DATA_DIR          = Path.home() / '.local' / 'share' / 'pumphouse'
DAILY_CSV         = DATA_DIR / 'daily.csv'
SNAPSHOTS         = DATA_DIR / 'snapshots.csv'
RESERVATIONS_FILE = DATA_DIR / 'reservations.csv'

# A 15-min window qualifies as a "high-pressure event" window if
# pressure was high for more than this fraction of the window.
HIGH_THRESHOLD_PCT = 50.0

# Maximum plausible gallons pumped in one 15-min window (~80 GPH × 0.25 hr).
# Values above this are sensor glitches and are clamped to 0.
MAX_GAL_PER_WINDOW = 20.0

# Backflush detection — mirrors config.py values.
BACKFLUSH_THRESHOLD   = 50    # gallons lost to qualify as a backflush
BACKFLUSH_WINDOW_ROWS = 3     # consecutive snapshot rows to sum
BACKFLUSH_TIME_START  = (0,   0)   # (hour, minute) — start of overnight window
BACKFLUSH_TIME_END    = (4,  30)   # (hour, minute) — end of overnight window

# Income — mirrors config.py MANAGEMENT_FEE_PERCENT.
MANAGEMENT_FEE_PCT = 36

# Reservation types that generate guest income (exclude owner stays / maintenance).
PAYING_TYPES = {'Regular - Renter', 'Airbnb', 'Vrbo', 'HomeToGo',
                'MyBookingPal', 'Bookingcom', 'Owner Guest'}

FIELDNAMES = [
    # ── Priority columns (most useful at a glance) ────────────────────────
    'date',
    'occupied_pct',
    'gallons_end',              # tank level at end of day
    'gallons_net_change',
    'tank_rolling_gph_avg',     # avg fill rate during calling windows
    'pressure_high_pct_overall',
    'high_events_pump_gph',
    'low_events_pump_gph',
    'bypass_hours',
    'backflush_gallons',
    'checkout_net_income',
    'net_income_cumulative',
    # ── Tank detail ───────────────────────────────────────────────────────
    'gallons_start', 'gallons_min', 'gallons_max',
    # ── GPH detail ────────────────────────────────────────────────────────
    'tank_rolling_gph_min', 'tank_rolling_gph_max', 'low_events_consume_gph',
    # ── Pressure / event detail ───────────────────────────────────────────
    'pressure_high_minutes',
    'high_events', 'high_events_total_minutes', 'high_events_avg_minutes',
    'low_events_windows',
    # ── Float, override, purge ────────────────────────────────────────────
    'float_calling_windows', 'float_full_windows',
    'override_windows', 'override_minutes',
    'total_purges',
    # ── Bypass detail ─────────────────────────────────────────────────────
    'bypass_windows',
    # ── Occupancy detail ──────────────────────────────────────────────────
    'occupied_windows',
    # ── Coverage ──────────────────────────────────────────────────────────
    'n_snapshots', 'hours_covered',
    # ── Weather ───────────────────────────────────────────────────────────
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


def summarize_day(date, rows, checkout_net_income=0.0):
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
        # Clamp per-window gallons to filter out sensor glitches
        pump = sum(min(flt(r, 'estimated_gallons_pumped'), MAX_GAL_PER_WINDOW) for r in row_list)
        high = sum(flt(r, 'pressure_high_seconds') for r in row_list)
        gph  = pump / (dur / 3600) if dur > 0 else None
        return dur, pump, high, gph

    bypass_dur, _,          _,       _         = _stats(bypass_rows)
    nb_dur,     _,          nb_high, _         = _stats(non_bypass)
    ext_dur,    _,          _,       _         = _stats(high_rows)
    low_dur,    _,          _,       low_gph   = _stats(low_rows)

    # For high-event GPH, only count windows where the tank isn't full —
    # when float_always_full=YES the pump is running against a full tank
    # and not actually delivering water to it.
    high_delivery_rows = [r for r in high_rows if r.get('float_always_full', '').upper() != 'YES']
    _, _, _, ext_gph = _stats(high_delivery_rows) if high_delivery_rows else (0, 0, 0, None)

    low_consumed = sum(
        -flt(r, 'tank_gallons_delta') for r in low_rows
        if flt(r, 'tank_gallons_delta') < 0
    )
    low_consume_gph = low_consumed / (low_dur / 3600) if low_dur > 0 else None
    run_mins = [sum(flt(r, 'duration_seconds') for r in run) / 60 for run in runs]

    # Backflush detection: look for a run of BACKFLUSH_WINDOW_ROWS consecutive
    # overnight (00:00–04:30) rows where the total tank decline ≥ threshold.
    backflush_gallons = None
    try:
        _start_m = BACKFLUSH_TIME_START[0] * 60 + BACKFLUSH_TIME_START[1]
        _end_m   = BACKFLUSH_TIME_END[0]   * 60 + BACKFLUSH_TIME_END[1]
        _night_rows = []
        for r in rows:
            try:
                from datetime import datetime as _dt
                _ts = _dt.fromisoformat(r['timestamp'])
                _t  = _ts.hour * 60 + _ts.minute
                if _start_m <= _t <= _end_m:
                    _night_rows.append(flt(r, 'tank_gallons_delta'))
            except Exception:
                pass
        for _i in range(len(_night_rows) - BACKFLUSH_WINDOW_ROWS + 1):
            _decline = sum(_night_rows[_i:_i + BACKFLUSH_WINDOW_ROWS])
            if _decline <= -BACKFLUSH_THRESHOLD:
                backflush_gallons = round(abs(_decline), 0)
                break
    except Exception:
        pass

    calling_gph = []
    for r in rows:
        if r.get('float_ever_calling', '').upper() == 'YES':
            try:
                calling_gph.append(float(r['tank_rolling_gph']))
            except (KeyError, ValueError, TypeError):
                pass

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
        'bypass_hours':              round(bypass_dur / 3600, 2),
        'backflush_gallons':         backflush_gallons,
        'checkout_net_income':       round(checkout_net_income, 2) if checkout_net_income else None,
        'net_income_cumulative':     None,  # filled in by main() after running total is known
        'pressure_high_pct_overall': round(nb_high / nb_dur * 100, 1) if nb_dur else None,
        'pressure_high_minutes':     round(nb_high / 60, 0),
        'high_events':               len(runs),
        'high_events_total_minutes': round(ext_dur / 60, 0),
        'high_events_avg_minutes':   round(sum(run_mins) / len(run_mins), 0) if run_mins else None,
        'high_events_pump_gph':      round(ext_gph, 1) if ext_gph else None,
        'low_events_windows':        len(low_rows),
        # Require at least 4 windows (1 hr) of low-pressure data for a meaningful rate;
        # fewer windows means a single pump cycle dominates and the average is unreliable.
        'low_events_pump_gph':       round(low_gph, 1) if (low_gph and len(low_rows) >= 4) else None,
        'low_events_consume_gph':    round(low_consume_gph, 1) if low_consume_gph else None,
        'tank_rolling_gph_avg':      round(sum(calling_gph) / len(calling_gph), 1) if calling_gph else None,
        'tank_rolling_gph_min':      round(min(calling_gph), 1) if calling_gph else None,
        'tank_rolling_gph_max':      round(max(calling_gph), 1) if calling_gph else None,
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


def load_reservations_by_checkout():
    """Return {checkout_date_str: net_income} for all paying reservations."""
    by_checkout = defaultdict(float)
    if not RESERVATIONS_FILE.exists():
        return by_checkout
    try:
        with open(RESERVATIONS_FILE, newline='') as f:
            for row in csv.DictReader(f):
                if row.get('Type') not in PAYING_TYPES:
                    continue
                if row.get('Status') not in ('Confirmed', 'Checked Out', 'Checked In'):
                    continue
                checkout = (row.get('Checkout') or '').strip()[:10]
                if not checkout:
                    continue
                try:
                    gross = float(row.get('Income') or 0)
                    by_checkout[checkout] += gross * (1 - MANAGEMENT_FEE_PCT / 100)
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass
    return by_checkout


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
    reservations   = load_reservations_by_checkout()

    # Seed monthly running income from the last existing row only if it shares
    # the same month as the first new date we are about to write.
    running_income = 0.0
    current_month  = None

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
            month = date[:7]
            if month != current_month:
                # Entering a new month — try to seed from the last existing row
                # if it falls in this same month (incremental run mid-month).
                running_income = 0.0
                if current_month is None and DAILY_CSV.exists():
                    try:
                        with open(DAILY_CSV) as _seed_f:
                            _last = None
                            for _last in csv.DictReader(_seed_f):
                                pass
                            if _last and _last['date'][:7] == month:
                                running_income = float(_last.get('net_income_cumulative') or 0)
                    except Exception:
                        pass
                current_month = month
            income = reservations.get(date, 0.0)
            running_income += income
            summary = summarize_day(date, by_day[date], checkout_net_income=income)
            if summary:
                summary['net_income_cumulative'] = round(running_income, 2)
                writer.writerow(summary)
                written += 1

    print(f'Wrote {written} new days to {DAILY_CSV}')


if __name__ == '__main__':
    main()
