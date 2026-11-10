#!/usr/bin/env python3
"""
Seed chlorine_ppm_history.jsonl (used by /water2's fills table) for the period
before live logging began.

Works BACKWARD from the first live history point by inverting each snapshot's
blend step, so the result is consistent with the live persisted estimate. (A
forward replay from CHLORINE_ANCHOR_PPM drifts away from it -- config constants
changed since the anchor -- and was off by ~1.2 ppm on 2026-10-19.) Stops at any
manual pour, whose effect can't be inverted here. Backfilled rows carry
"src": "backfill" and are replaced on re-run; live rows are never touched.

Usage:
    venv/bin/python3 bin/backfill_ppm_history.py [--days 3]
"""
import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor import chlorine as cl
from monitor.config import CHLORINE_STEADY_STATE_PPM


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=float, default=3)
    args = ap.parse_args()

    path = cl.CHLORINE_PPM_HISTORY_FILE
    live = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get('src') != 'backfill':
                live.append(rec)
    if not live:
        sys.exit('no live history point yet to anchor on; wait for the next snapshot')

    anchor_ts = datetime.fromisoformat(live[0]['ts']).timestamp()
    ppm = float(live[0]['ppm'])
    cutoff = max(time.time() - args.days * 86400, anchor_ts - args.days * 86400)

    pour_ts = []
    for rec in cl.read_additions():
        try:
            pour_ts.append(datetime.fromisoformat(rec['ts']).timestamp())
        except Exception:
            pass

    # Steps at/before the anchor, newest first. The step whose snapshot is nearest
    # the anchor produced the anchor value itself.
    steps = [st for st in cl.ppm_steps_replay() if st[0] <= anchor_ts + 60]
    steps.sort(reverse=True)
    pts = []
    for unix, dos, byp_eff, tank in steps:
        if unix < cutoff or any(unix - 900 <= t <= unix + 900 for t in pour_ts) or tank <= 0:
            break
        # point(unix) == ppm; step back one snapshot
        pts.append((unix, ppm))
        p1 = ppm / math.exp(-byp_eff / tank) if byp_eff > 0 else ppm
        ppm = CHLORINE_STEADY_STATE_PPM + (p1 - CHLORINE_STEADY_STATE_PPM) / math.exp(-dos / tank) if dos > 0 else p1
    pts.reverse()
    # the newest reconstructed point duplicates the live anchor; keep live's.
    pts = [pt for pt in pts if pt[0] < anchor_ts - 30]

    with open(path, 'w') as f:
        for t, p in pts:
            f.write(json.dumps({'ts': datetime.fromtimestamp(t).isoformat(),
                                'ppm': round(p, 3), 'src': 'backfill'}) + '\n')
        for rec in live:
            f.write(json.dumps(rec) + '\n')
    print(f'backfilled {len(pts)} points back to '
          f'{datetime.fromtimestamp(pts[0][0]) if pts else "n/a"}; kept {len(live)} live')


if __name__ == '__main__':
    main()
