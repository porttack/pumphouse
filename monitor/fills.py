"""
Recent-fills table for /water2 (/api/fills).

A "fill" is one contiguous inflow episode: dosatron click bursts and/or bypass
flow-cycle records (FFT-detected), merged when the gap between them is under
FILL_MERGE_GAP_S. Per fill we report type (dosatron / bypass / mixed), gallons
in, flowing time, clicks, tank before/after, ppm before/after, purge, and car
count. Gallons are estimates: clicks x GALLONS_PER_CLICK, bypass flow seconds x
calibrated bypass GPM.
"""

import csv
import io
import time
from datetime import datetime

FILL_MERGE_GAP_S = 600      # inflow episodes closer than this are one fill
CLICK_BURST_GAP_S = 120     # clicks closer than this belong to one burst
PURGE_MARGIN_S = 60         # a PURGE event this close to a fill counts as in it
SNAP_MAX_AGE_S = 1800       # snapshot must be this near a fill to supply cars/tank


def _snapshot_rows(start_unix: float, end_unix: float) -> 'list[dict]':
    from monitor.config import SNAPSHOTS_FILE
    from monitor.web_water2 import _tail_covering, _csv_ts
    with open(SNAPSHOTS_FILE, 'r') as f:
        header = f.readline().strip()
    data = _tail_covering(SNAPSHOTS_FILE, start_unix, _csv_ts, min_chunk=262144).decode(errors='replace')
    rows = []
    for r in csv.DictReader(io.StringIO(header + '\n' + data)):
        try:
            r['_ts'] = datetime.fromisoformat(r['timestamp'].strip()).timestamp()
        except Exception:
            continue
        if start_unix <= r['_ts'] <= end_unix:
            rows.append(r)
    rows.sort(key=lambda r: r['_ts'])
    return rows


def _purge_times(start_unix: float, end_unix: float) -> 'list[float]':
    from monitor.web_water2 import _read_events_tail_raw
    out = []
    for line in _read_events_tail_raw(524288).splitlines():
        parts = line.split(',', 2)
        if len(parts) < 2 or parts[1].strip() != 'PURGE':
            continue
        try:
            t = datetime.fromisoformat(parts[0].strip()).timestamp()
        except Exception:
            continue
        if start_unix <= t <= end_unix:
            out.append(t)
    return out


def _episodes(clicks: 'list[float]', bypass: 'list[dict]', windows) -> 'list[dict]':
    """Click bursts + bypass flow records as (start, end, kind, clicks).

    A dosatron burst is stretched to the pressure-HIGH window(s) it falls in, so
    its duration runs from pressure going high to pressure going low (not just
    first click to last click). Bypass episodes keep their FFT flow bounds."""
    eps = []
    if clicks:
        s = prev = clicks[0]
        n = 1
        for t in clicks[1:]:
            if t - prev > CLICK_BURST_GAP_S:
                eps.append({'start': s, 'end': prev, 'kind': 'dosatron', 'clicks': n})
                s, n = t, 0
            prev = t
            n += 1
        eps.append({'start': s, 'end': prev, 'kind': 'dosatron', 'clicks': n})
        for e in eps:
            wins = [w for w in windows if w[0] <= e['end'] and w[1] >= e['start']]
            e['wins'] = wins
            if wins:
                e['start'] = min(e['start'], min(w[0] for w in wins))
                e['end'] = max(e['end'], max(w[1] for w in wins))
    for r in bypass:
        eps.append({'start': r['start'], 'end': r['end'], 'kind': 'bypass', 'clicks': 0})
    eps.sort(key=lambda e: e['start'])
    return eps


def _audio_index(start_unix: float) -> 'tuple[list, list]':
    """([(high_unix, filename)], [(start_unix, filename)]) for pump-cycle and
    bypass-flow recordings that start at/after start_unix (and still exist)."""
    import json
    from monitor.web_water2 import _tail_covering, _jsonl_ts
    from monitor.dosatron import CYCLES_JSONL, FLOW_CYCLES_JSONL, CYCLES_DIR, FLOW_CYCLES_DIR
    from monitor.web_dosatron import _resolve_audio

    def load(path, key, directory):
        out = []
        try:
            for line in _tail_covering(path, start_unix, _jsonl_ts(key), min_chunk=262144).split(b'\n'):
                try:
                    rec = json.loads(line)
                    name = rec.get('audio_file')
                    t = datetime.fromisoformat(rec[key]).timestamp()
                    if name and t >= start_unix - 60 and _resolve_audio(directory, name)[0]:
                        out.append((t, name))
                except Exception:
                    pass
        except Exception:
            pass
        return sorted(out)

    return (load(CYCLES_JSONL, 'high_ts', CYCLES_DIR),
            load(FLOW_CYCLES_JSONL, 'start_ts', FLOW_CYCLES_DIR))


def _nearest(rows, ts, before: bool):
    best = None
    for r in rows:
        if before and r['_ts'] <= ts:
            best = r
        elif not before and r['_ts'] >= ts:
            return r
    return best if before else None


def _ppm_at(points, ts, before: bool):
    cand = [p for p in points if (p[0] <= ts if before else p[0] >= ts)]
    if not cand:
        return None
    return (cand[-1] if before else cand[0])[1]


def recent_fills(hours: float = 24.0) -> 'list[dict]':
    """Fills in the last `hours`, newest first."""
    from monitor.web_water2 import _click_times, _bypass_flow_records
    from monitor.dosatron import GALLONS_PER_CLICK
    from monitor.bypass_calibration import get_calibrated_bypass_gpm
    from monitor.chlorine import read_ppm_history

    now = time.time()
    start = now - hours * 3600
    gpm = get_calibrated_bypass_gpm()

    from monitor.dosatron import _fill_pressure_windows
    eps = _episodes(_click_times(start, now), _bypass_flow_records(start, now),
                    _fill_pressure_windows(start - 3600, now))
    # Dosatron clicks outside any fill window are idle noise (pump not filling), not a fill.
    eps = [e for e in eps if e['kind'] != 'dosatron' or e.get('wins')]
    fills = []
    for e in eps:
        if fills and e['start'] - fills[-1]['end'] < FILL_MERGE_GAP_S:
            f = fills[-1]
            f['end'] = max(f['end'], e['end'])
            f['eps'].append(e)
        else:
            fills.append({'start': e['start'], 'end': e['end'], 'eps': [e]})

    snaps = _snapshot_rows(start - 3600, now)
    purges = _purge_times(start - 600, now)
    ppm_pts = read_ppm_history(start - 3600, now)

    cycle_audio, flow_audio = _audio_index(start - 3600)

    out = []
    for f in fills:
        audio = []
        for e in f['eps']:
            if e['kind'] == 'dosatron':
                for ws, _we in e.get('wins', []):
                    audio += [('/api/dosatron/cycles/' + n) for t, n in cycle_audio if abs(t - ws) <= 5]
            else:
                audio += [('/api/dosatron/flow_cycles/' + n) for t, n in flow_audio if abs(t - e['start']) <= 5]
        audio = list(dict.fromkeys(audio))
        clicks = sum(e['clicks'] for e in f['eps'])
        dos_wins = {w for e in f['eps'] if e['kind'] == 'dosatron' for w in e.get('wins', [])}
        dos_s = sum(e - s for s, e in dos_wins)
        byp_s = sum(e['end'] - e['start'] for e in f['eps'] if e['kind'] == 'bypass')
        dos_gal = clicks * GALLONS_PER_CLICK
        byp_gal = byp_s / 60.0 * gpm
        kinds = {e['kind'] for e in f['eps']}
        kind = 'mixed' if len(kinds) > 1 else kinds.pop()

        before = _nearest(snaps, f['start'], True)
        after = _nearest(snaps, f['end'], False)

        def _tank(r):
            try:
                return float(r['tank_gallons']) if r and r.get('tank_gallons') else None
            except Exception:
                return None
        tank_before = _tank(before) if before and f['start'] - before['_ts'] <= SNAP_MAX_AGE_S else None
        tank_after = _tank(after) if after and after['_ts'] - f['end'] <= 2 * SNAP_MAX_AGE_S else None
        tank_guess = None
        if tank_after is None and tank_before is not None:
            tank_guess = round(tank_before + dos_gal + byp_gal)

        # Cars: nearest non-blank vehicle_count to the fill (camera is daytime-only).
        cars = None
        best = None
        for r in snaps:
            v = (r.get('vehicle_count') or '').strip()
            if not v:
                continue
            d = 0 if f['start'] <= r['_ts'] <= f['end'] else min(abs(r['_ts'] - f['start']), abs(r['_ts'] - f['end']))
            if d <= SNAP_MAX_AGE_S and (best is None or d < best):
                best, cars = d, int(float(v))

        ppm_b = _ppm_at(ppm_pts, f['start'], True)
        ppm_a = _ppm_at(ppm_pts, f['end'], False)

        out.append({
            'start_ts': datetime.fromtimestamp(f['start']).isoformat(timespec='seconds'),
            'end_ts': datetime.fromtimestamp(f['end']).isoformat(timespec='seconds'),
            'kind': kind,
            'gallons_in': round(dos_gal + byp_gal, 1),
            'dosatron_gallons': round(dos_gal, 1),
            'bypass_gallons': round(byp_gal, 1),
            'flow_seconds': round(dos_s + byp_s),
            'gpm': round((dos_gal + byp_gal) / ((dos_s + byp_s) / 60.0), 2) if (dos_s + byp_s) >= 10 else None,
            'clicks': clicks,
            'tank_before': None if tank_before is None else round(tank_before),
            'tank_after': None if tank_after is None else round(tank_after),
            'tank_guess': tank_guess,
            'ppm_before': None if ppm_b is None else round(ppm_b, 1),
            'ppm_after': None if ppm_a is None else round(ppm_a, 1),
            'purge': any(f['start'] - PURGE_MARGIN_S <= p <= f['end'] + PURGE_MARGIN_S for p in purges),
            'cars': cars,
            'audio': audio,
        })
    out.reverse()
    return out
