#!/usr/bin/env python3
"""
Record that someone poured chlorine into the main tank, and step the running ppm
estimate up to match.

The model can only see water movement (dosatron clicks, FFT-detected bypass flow),
so a manual pour is invisible to it. Without recording it, every ppm figure after
the pour stays wrong. See docs/chlorine-estimate.md.

Usage:
    venv/bin/python3 bin/add_chlorine.py 12
    venv/bin/python3 bin/add_chlorine.py 1.5 --cups --note "Jose, after filter swap"
    venv/bin/python3 bin/add_chlorine.py 12 --percent 10
    venv/bin/python3 bin/add_chlorine.py 12 --ago 6h       # poured 6 hours ago
    venv/bin/python3 bin/add_chlorine.py --list
    venv/bin/python3 bin/add_chlorine.py 12 --dry-run
"""
import argparse
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor.chlorine import (CHLORINE_DOSE_PERCENT, dose_summary, ppm_from_floz,
                              read_additions, read_chlorine_ppm_state, record_addition)


def parse_ago(text):
    """'90m' / '6h' / '2d' -> timedelta."""
    m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*([mhd])', text.strip().lower())
    if not m:
        raise argparse.ArgumentTypeError("use e.g. 90m, 6h, 2d")
    n, unit = float(m.group(1)), m.group(2)
    return timedelta(**{{'m': 'minutes', 'h': 'hours', 'd': 'days'}[unit]: n})


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('amount', nargs='?', type=float,
                    help='amount poured, in fluid ounces (or cups with --cups)')
    ap.add_argument('--cups', action='store_true', help='amount is in cups, not fl oz')
    ap.add_argument('--percent', type=float, default=None,
                    help=f'hypochlorite strength (default {CHLORINE_DOSE_PERCENT:.0f}%%)')
    ap.add_argument('--ago', type=parse_ago, default=None,
                    help='poured this long ago (90m / 6h / 2d); bump is decayed accordingly')
    ap.add_argument('--tank', type=float, default=None,
                    help='tank gallons at the time (default: current reading)')
    ap.add_argument('--note', default='', help='who poured it / why')
    ap.add_argument('--dry-run', action='store_true', help='show the effect, record nothing')
    ap.add_argument('--list', action='store_true', help='list recorded additions and exit')
    args = ap.parse_args()

    if args.list:
        rows = read_additions()
        if not rows:
            print('No chlorine additions recorded.')
            return 0
        print(f"{'when':20} {'fl oz':>6} {'%':>4} {'+ppm':>6} {'ppm after':>10}  note")
        for r in rows:
            print(f"{r.get('ts','')[:19]:20} {r.get('floz',0):6.1f} "
                  f"{r.get('percent',0):4.0f} {r.get('ppm_bump',0):6.1f} "
                  f"{r.get('ppm_after',0):10.1f}  {r.get('note','')}")
        return 0

    if args.amount is None:
        ppm, _ = read_chlorine_ppm_state()
        d = dose_summary(ppm, None)
        print(f'Current estimate: ~{ppm:.1f} ppm')
        print(f'Nothing recorded. Pass an amount in fl oz (see --help), '
              f'or check /water2 for the suggested pour.')
        return 1

    floz = args.amount * 8.0 if args.cups else args.amount
    ts = datetime.now() - args.ago if args.ago else None

    if args.dry_run:
        tank = args.tank
        if tank is None:
            try:
                from monitor.tank import get_tank_data
                from monitor.config import TANK_URL
                _td = get_tank_data(TANK_URL) or {}
                tank = _td.get('gallons') if _td.get('status') == 'success' else None
            except Exception:
                tank = None
        if not tank:
            print('Tank volume unavailable; pass --tank to dry-run.', file=sys.stderr)
            return 1
        ppm, _ = read_chlorine_ppm_state()
        bump = ppm_from_floz(floz, tank, args.percent)
        print(f'DRY RUN — would record {floz:.1f} fl oz of '
              f'{(args.percent or CHLORINE_DOSE_PERCENT):.0f}% into {tank:.0f} gal')
        print(f'  {ppm:.1f} ppm  ->  {ppm + bump:.1f} ppm  (+{bump:.1f})')
        return 0

    try:
        r = record_addition(floz, percent=args.percent, ts=ts,
                            tank_gallons=args.tank, note=args.note, source='cli')
    except ValueError as e:
        print(f'Error: {e}', file=sys.stderr)
        return 1

    print(f"Recorded {r['floz']:.1f} fl oz of {r['percent']:.0f}% "
          f"into {r['tank_gallons']:.0f} gal at {r['ts'][:19]}")
    if r['decay_gallons']:
        print(f"  retroactive: decayed by {r['decay_gallons']:.0f} gal of throughput "
              f"since ({r['ppm_bump_raw']:.1f} -> {r['ppm_bump']:.1f} ppm)")
    print(f"  estimate: {r['ppm_before']:.1f} ppm  ->  {r['ppm_after']:.1f} ppm")
    print('\nNote: daily.csv picks this up on its next build (or run build_daily.py --rebuild).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
