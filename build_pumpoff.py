#!/usr/bin/env python3
"""
build_pumpoff.py — Generate pumpoff.csv: all pump-off periods lasting > 24 hours.

Reads all snapshots (archives + live) and writes one row per outage period to:
    ~/.local/share/pumphouse/pumpoff.csv

Columns:
    End               — outage end timestamp (M/D/YY H:MM), suitable for import/analysis
    Duration          — outage length in fractional days
    Bypass Percent    — % of the outage where relay_bypass was ON
    Occupied Percent  — % of the outage where the property was occupied
    Tank Gallons      — tank level at the end of the outage
    Tank Change       — net change in gallons during the outage (+ or -)
    After Percent     — pressure_high % in the 24 hrs following the outage
    After Bypass Pct  — bypass % in those same 24 hrs
    After GPH         — estimated GPH pumped in those 24 hrs
    After Tank Change — net tank change in those 24 hrs
    After Gallons     — tank level 24 hrs after the outage ended
    Checked           — whether a pump check is recorded near this outage
    Comments          — auto-generated summary of what happened

Update PUMPCHECK_DATES in ~/.config/pumphouse/secrets.conf whenever a pump check is made.
Format: PUMPCHECK_DATES=YYYY-MM-DD,YYYY-MM-DD,...
"""

import csv
import gzip
import sys
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR     = Path.home() / '.local' / 'share' / 'pumphouse'
PUMPOFF_CSV  = DATA_DIR / 'pumpoff.csv'
SNAPSHOTS    = DATA_DIR / 'snapshots.csv'
SECRETS_FILE = Path.home() / '.config' / 'pumphouse' / 'secrets.conf'

# Minimum outage duration to include in the report.
MIN_OUTAGE_HOURS = 24


def load_pumpcheck_dates():
    """Read PUMPCHECK_DATES from secrets.conf (comma-separated ISO dates)."""
    if not SECRETS_FILE.exists():
        return []
    with open(SECRETS_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith('PUMPCHECK_DATES='):
                value = line.split('=', 1)[1].strip()
                return [d.strip() for d in value.split(',') if d.strip()]
    return []

FIELDNAMES = [
    'End',
    'Duration',
    'Bypass Percent',
    'Occupied Percent',
    'Tank Gallons',
    'Tank Change',
    'After Percent',
    'After Bypass Pct',
    'After GPH',
    'After Tank Change',
    'After Gallons',
    'Checked',
    'Comments',
]


def iter_all_rows():
    sources = sorted(DATA_DIR.glob('snapshots-*.csv.gz'))
    sources.append(SNAPSHOTS)
    for src in sources:
        if not src.exists():
            continue
        opener = gzip.open(src, 'rt') if str(src).endswith('.gz') else open(src)
        with opener as f:
            for row in csv.DictReader(f):
                yield row


def parse_rows(raw):
    parsed = []
    for row in raw:
        try:
            ts  = datetime.fromisoformat(row['timestamp'])
            ph  = float(row['pressure_high_seconds'])
            dur = float(row['duration_seconds'])
            gal = float(row['tank_gallons']) if row.get('tank_gallons') else None
            parsed.append({
                'ts':      ts,
                'ph':      ph,
                'dur':     dur,
                'gallons': gal,
                'bypass':  row.get('relay_bypass',  '').strip().upper(),
                'occupied': row.get('occupied',     '').strip().upper(),
                'pumped':  float(row.get('estimated_gallons_pumped') or 0),
            })
        except (KeyError, ValueError):
            pass
    return parsed


def find_outages(parsed):
    """Return list of (start, end, start_idx, end_idx) for zero-pressure runs > MIN_OUTAGE_HOURS."""
    outages = []
    current_start = current_end = current_si = current_ei = None

    for i, p in enumerate(parsed):
        if p['ph'] == 0:
            if current_start is None:
                current_start = p['ts']
                current_si    = i
            current_end = p['ts'] + timedelta(seconds=p['dur'])
            current_ei  = i
        else:
            if current_start is not None:
                if (current_end - current_start) >= timedelta(hours=MIN_OUTAGE_HOURS):
                    outages.append((current_start, current_end, current_si, current_ei))
                current_start = current_end = current_si = current_ei = None

    if current_start is not None:
        if (current_end - current_start) >= timedelta(hours=MIN_OUTAGE_HOURS):
            outages.append((current_start, current_end, current_si, current_ei))

    return outages


def contact_near(start, end, pumpcheck_dates):
    """Return True if any pumpcheck date falls within the outage or 2 days after."""
    window_end = end + timedelta(days=2)
    for ds in pumpcheck_dates:
        try:
            d = datetime.strptime(ds, '%Y-%m-%d').date()
            if start.date() <= d <= window_end.date():
                return True
        except ValueError:
            pass
    return False


def make_comment(bypass_pct, occ_pct, tank_change, dur_days,
                 after_pct, after_gph, after_tank_change, is_ongoing, contacted,
                 ongoing_days=None):
    parts = []

    # Duration character
    if dur_days >= 5:
        parts.append(f"Extended {dur_days:.1f}-day outage.")
    elif dur_days >= 3:
        parts.append(f"Multi-day outage ({dur_days:.1f} days).")
    else:
        parts.append(f"Short outage ({dur_days:.1f} days).")

    # Bypass
    if bypass_pct >= 80:
        parts.append(f"Bypass carried nearly all demand ({bypass_pct:.0f}%).")
    elif bypass_pct >= 30:
        parts.append(f"Bypass partially active ({bypass_pct:.0f}% of period).")

    # Occupancy + tank drain
    if occ_pct >= 90 and tank_change is not None and tank_change < -200:
        parts.append(f"Property occupied; tank drew down {abs(tank_change):.0f} gal.")
    elif occ_pct >= 90:
        parts.append("Property occupied throughout.")
    elif occ_pct == 0:
        parts.append("Property unoccupied — minimal demand.")
    elif occ_pct > 0:
        parts.append(f"Partially occupied ({occ_pct:.0f}% of windows).")

    if tank_change is not None and tank_change == 0 and occ_pct == 0:
        parts.append("Tank held steady (no usage).")

    # Recovery
    if is_ongoing:
        ongoing_str = f"{ongoing_days:.1f} days" if ongoing_days is not None else "unknown duration"
        parts.append(f"ONGOING ({ongoing_str} so far) — no recovery data yet.")
    elif after_pct is not None:
        if after_pct >= 80:
            parts.append(f"Strong recovery: {after_pct:.0f}% pressure, {after_gph:.0f} GPH after.")
        elif after_pct >= 30:
            parts.append(f"Moderate recovery: {after_pct:.0f}% pressure, {after_gph:.0f} GPH after.")
        else:
            parts.append(f"Weak recovery: only {after_pct:.0f}% pressure in first 24 hrs.")

    if contacted:
        parts.append("Neighbor contacted.")

    return ' '.join(parts)


def fmt(v, decimals=1, sign=False):
    if v is None:
        return ''
    s = f'{abs(v):.{decimals}f}'
    if sign:
        return ('+' if v >= 0 else '-') + s
    return s



def main():
    pumpcheck_dates = load_pumpcheck_dates()
    parsed  = parse_rows(iter_all_rows())
    outages = find_outages(parsed)

    now = datetime.now()
    rows_out = []

    for start, end, si, ei in outages:
        period  = parsed[si:ei + 1]
        dur_sec = (end - start).total_seconds()
        dur_days = dur_sec / 86400

        total_dur    = sum(p['dur'] for p in period)
        bypass_pct   = sum(p['dur'] for p in period if p['bypass']  == 'ON') / total_dur * 100 if total_dur else 0
        occ_pct      = sum(p['dur'] for p in period if p['occupied']== 'YES') / total_dur * 100 if total_dur else 0

        gallons_vals = [p['gallons'] for p in period if p['gallons'] is not None]
        gal_start    = gallons_vals[0]  if gallons_vals else None
        gal_end      = gallons_vals[-1] if gallons_vals else None
        tank_change  = (gal_end - gal_start) if (gal_start is not None and gal_end is not None) else None

        # 24 hrs after
        is_ongoing   = end > now - timedelta(minutes=15)  # outage hasn't clearly ended yet
        post_rows    = [p for p in parsed if end <= p['ts'] < end + timedelta(hours=24)]

        if post_rows and not is_ongoing:
            post_dur          = sum(p['dur'] for p in post_rows)
            after_pct         = sum(p['ph']  for p in post_rows) / post_dur * 100 if post_dur else 0
            after_bypass_pct  = sum(p['dur'] for p in post_rows if p['bypass'] == 'ON') / post_dur * 100 if post_dur else 0
            after_pumped      = sum(p['pumped'] for p in post_rows)
            after_gph         = after_pumped / (post_dur / 3600) if post_dur else 0
            after_gal_start   = next((p['gallons'] for p in post_rows if p['gallons'] is not None), gal_end)
            after_gal_end     = next((p['gallons'] for p in reversed(post_rows) if p['gallons'] is not None), None)
            after_tank_change = (after_gal_end - after_gal_start) if (after_gal_start and after_gal_end) else None
            after_gallons     = after_gal_end
        else:
            after_pct = after_bypass_pct = after_gph = after_tank_change = after_gallons = None

        contacted    = contact_near(start, end, pumpcheck_dates)
        ongoing_days = (now - start).total_seconds() / 86400 if is_ongoing else None
        comment      = make_comment(
            bypass_pct, occ_pct, tank_change, dur_days,
            after_pct, after_gph, after_tank_change, is_ongoing, contacted, ongoing_days,
        )

        rows_out.append({
            'End':               end.strftime('%-m/%-d/%y %-H:%M'),
            'Duration':          fmt(dur_days, 2),
            'Bypass Percent':    fmt(bypass_pct, 1),
            'Occupied Percent':  fmt(occ_pct, 1),
            'Tank Gallons':      str(int(round(gal_end))) if gal_end is not None else '',
            'Tank Change':       fmt(tank_change, 0, sign=True) if tank_change is not None else '',
            'After Percent':     fmt(after_pct, 1),
            'After Bypass Pct':  fmt(after_bypass_pct, 1),
            'After GPH':         fmt(after_gph, 2),
            'After Tank Change': fmt(after_tank_change, 0, sign=True) if after_tank_change is not None else '',
            'After Gallons':     str(int(round(after_gallons))) if after_gallons is not None else '',
            'Checked':           'Yes' if contacted else 'No',
            'Comments':          comment,
        })

    with open(PUMPOFF_CSV, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows_out)

    print(f'Wrote {len(rows_out)} outage periods to {PUMPOFF_CSV}')


if __name__ == '__main__':
    sys.exit(main())
