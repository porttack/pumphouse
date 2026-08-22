"""
Chlorine ppm estimate — persistent, continuously updated across both bypass
and dosatron modes.

Model: well-mixed, constant-volume tank. Each water source blends the current
estimate toward its own assumed inflow concentration, proportional to gallons
delivered relative to tank volume:

    ppm_new = c_in + (ppm_old - c_in) * exp(-gallons / tank_gallons)

Dosatron-treated water is assumed to enter at CHLORINE_STEADY_STATE_PPM (so
ppm recovers back toward steady-state once bypass ends and the dosatron
resumes); untreated bypass water is assumed to enter at 0 ppm (so ppm
declines while bypassing). Same mixing math both directions — confirmed with
owner 2026-07-29. See config.py for the full owner-pushback caveat: this is
UNVALIDATED (raw water likely carries its own chlorine demand beyond simple
dilution) — treat as a rough guide, not a precise reading.

State persists in CHLORINE_PPM_STATE_FILE so the estimate keeps evolving
across service restarts and mode switches, rather than resetting each time
bypass toggles.
"""
import json
import math
import os
from datetime import datetime

from monitor.config import (
    CHLORINE_STEADY_STATE_PPM,
    CHLORINE_DOSE_PERCENT,
    CHLORINE_DOSE_TARGET_PPM,
    CHLORINE_DOSE_TARGET_PPM_ALT,
    CHLORINE_ANCHOR_PPM,
    CHLORINE_ANCHOR_TS,
    DATA_DIR,
)
from monitor.bypass_calibration import get_calibrated_bypass_gpm

CHLORINE_PPM_STATE_FILE = DATA_DIR / 'chlorine_ppm_state.json'


def read_chlorine_ppm_state() -> 'tuple[float, float]':
    """Return (ppm, unix_ts) of the last persisted estimate. Defaults to
    steady-state ppm / now if no state has been written yet."""
    try:
        with open(CHLORINE_PPM_STATE_FILE) as f:
            state = json.load(f)
        return float(state['ppm']), datetime.fromisoformat(state['ts']).timestamp()
    except Exception:
        return CHLORINE_STEADY_STATE_PPM, datetime.now().timestamp()


def blend_ppm(ppm: float, gallons: float, tank_gallons: float, c_in: float) -> float:
    """Blend `ppm` toward `c_in` as `gallons` of water (at concentration c_in)
    mix into a well-mixed tank holding `tank_gallons`."""
    if gallons <= 0 or tank_gallons <= 0:
        return ppm
    return c_in + (ppm - c_in) * math.exp(-gallons / tank_gallons)


def update_chlorine_ppm_estimate(dosatron_gallons: float, bypass_flowing_seconds: float,
                                  tank_gallons: 'float | None') -> None:
    """Advance and persist the ppm estimate by one snapshot's worth of
    dosatron + bypass delivery. Called once per ~15-min snapshot from
    monitor/poll.py, using that window's ground-truth dosatron gallons
    (click-based) and real FFT-detected bypass flow seconds (converted here
    via get_calibrated_bypass_gpm(), see monitor.bypass_calibration).

    bypass_flowing_seconds must be actual detected flow time (monitor.dosatron
    .sum_flow_seconds), NOT how long the bypass relay was open — the valve can
    sit open for a long stretch with no float-calling demand while no water
    actually moves, which overstated delivered gallons by ~15x when this used
    relay-on time instead (caught 2026-07-29 comparing against real tank-level
    change during a day with zero house usage)."""
    if not tank_gallons:
        return
    ppm, _ = read_chlorine_ppm_state()
    ppm = blend_ppm(ppm, dosatron_gallons, tank_gallons, CHLORINE_STEADY_STATE_PPM)
    bypass_gallons = bypass_flowing_seconds / 60.0 * get_calibrated_bypass_gpm() if bypass_flowing_seconds else 0.0
    ppm = blend_ppm(ppm, bypass_gallons, tank_gallons, 0.0)
    _write_state(ppm)


def _write_state(ppm: float) -> None:
    try:
        tmp = str(CHLORINE_PPM_STATE_FILE) + '.tmp'
        with open(tmp, 'w') as f:
            json.dump({'ppm': round(ppm, 3), 'ts': datetime.now().isoformat()}, f)
        os.replace(tmp, CHLORINE_PPM_STATE_FILE)
    except Exception:
        pass


# ── shock-dose calculator ─────────────────────────────────────────────────────
# Available chlorine (as Cl2) per litre of product, from the trade percent:
# 12% trade = 12 g per 100 mL = 120 g/L. See config.py for the trade-vs-weight
# caveat and why erring high is intentional.
_G_CL2_PER_L = CHLORINE_DOSE_PERCENT * 10.0
_L_PER_GAL   = 3.785411784
_FLOZ_PER_L  = 33.814022702


def dose_floz(current_ppm: 'float | None', tank_gallons: 'float | None',
              target_ppm: float = CHLORINE_DOSE_TARGET_PPM) -> 'float | None':
    """Fluid ounces of CHLORINE_DOSE_PERCENT hypochlorite needed to raise a
    well-mixed tank of `tank_gallons` from `current_ppm` to `target_ppm`.

    Returns 0.0 if the tank is already at/above target, None if either input is
    unavailable. Assumes the whole dose stays in the tank (no immediate demand
    satisfied on contact) — the same optimistic assumption the ppm estimate
    itself makes, so a real pour may land slightly under target.
    """
    if current_ppm is None or not tank_gallons or tank_gallons <= 0:
        return None
    delta = target_ppm - current_ppm
    if delta <= 0:
        return 0.0
    grams = delta * tank_gallons * _L_PER_GAL / 1000.0   # mg/L × L → mg → g
    return grams / _G_CL2_PER_L * _FLOZ_PER_L


def dose_summary(current_ppm: 'float | None', tank_gallons: 'float | None') -> dict:
    """Both dose targets at once, for the web/email/daily-table callers."""
    return {
        'target_high': CHLORINE_DOSE_TARGET_PPM,
        'target_low':  CHLORINE_DOSE_TARGET_PPM_ALT,
        'floz_high':   dose_floz(current_ppm, tank_gallons, CHLORINE_DOSE_TARGET_PPM),
        'floz_low':    dose_floz(current_ppm, tank_gallons, CHLORINE_DOSE_TARGET_PPM_ALT),
        'percent':     CHLORINE_DOSE_PERCENT,
    }


# ── historical replay (for the per-day column in daily.csv) ───────────────────
_ppm_series_cache: 'dict | None' = None


def ppm_series_by_day(snapshots_csv: 'str | None' = None) -> dict:
    """Replay the ppm model from CHLORINE_ANCHOR_PPM/_TS forward over every
    snapshot, returning {'YYYY-MM-DD': end_of_day_ppm}.

    The live model only persists one current value, so historical daily rows
    need this replay. Uses the same blend math and the same inputs as the live
    path: per-snapshot click-based dosatron gallons, and real FFT-detected
    bypass flow seconds × the calibrated bypass GPM. Computed once per process
    and cached — build_daily.py --rebuild would otherwise re-walk it per day.
    """
    global _ppm_series_cache
    if _ppm_series_cache is not None:
        return _ppm_series_cache

    import csv as _csv
    series: dict = {}
    try:
        from monitor.dosatron import FLOW_CYCLES_JSONL
        path = snapshots_csv or str(DATA_DIR / 'snapshots.csv')
        anchor_unix = datetime.fromisoformat(CHLORINE_ANCHOR_TS).timestamp()

        # Load flow episodes once, sorted, so lookups are a linear sweep.
        episodes = []
        try:
            with open(FLOW_CYCLES_JSONL) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        episodes.append((datetime.fromisoformat(rec['start_ts']).timestamp(),
                                         datetime.fromisoformat(rec['end_ts']).timestamp()))
                    except Exception:
                        pass
        except Exception:
            pass
        episodes.sort()

        # Manual pours, as (unix, raw_ppm_bump) — re-applied at their own place in
        # the timeline so the replayed series matches the live state.
        additions = []
        for rec in read_additions():
            try:
                a_unix = datetime.fromisoformat(rec['ts']).timestamp()
                a_bump = rec.get('ppm_bump_raw')
                if a_bump is None:
                    a_bump = ppm_from_floz(rec.get('floz'), rec.get('tank_gallons'),
                                           rec.get('percent'))
                if a_bump:
                    additions.append((a_unix, float(a_bump)))
            except Exception:
                pass
        additions.sort()
        a_i = 0

        gpm = get_calibrated_bypass_gpm()
        ppm = CHLORINE_ANCHOR_PPM
        prev_unix = anchor_unix
        idx = 0

        with open(path) as f:
            for row in _csv.DictReader(f):
                ts_raw = row.get('timestamp', '')
                if not ts_raw:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_raw)
                except Exception:
                    continue
                unix = ts.timestamp()
                if unix < anchor_unix:
                    continue
                try:
                    tank = float(row.get('tank_gallons') or 0)
                except Exception:
                    tank = 0.0
                if tank > 0:
                    try:
                        dos = float(row.get('dosatron_gallons') or 0)
                    except Exception:
                        dos = 0.0
                    # bypass flow seconds overlapping (prev_unix, unix]
                    byp_s = 0.0
                    while idx < len(episodes) and episodes[idx][1] < prev_unix:
                        idx += 1
                    for s, e in episodes[idx:]:
                        if s > unix:
                            break
                        byp_s += max(0.0, min(e, unix) - max(s, prev_unix))
                    # Pours land before this window's mixing, so the water that
                    # moved during the window dilutes them like anything else.
                    while a_i < len(additions) and additions[a_i][0] <= unix:
                        if additions[a_i][0] >= anchor_unix:  # pre-anchor pours are already baked into the anchor
                            ppm += additions[a_i][1]
                        a_i += 1
                    ppm = blend_ppm(ppm, dos, tank, CHLORINE_STEADY_STATE_PPM)
                    ppm = blend_ppm(ppm, byp_s / 60.0 * gpm, tank, 0.0)
                    series[ts.strftime('%Y-%m-%d')] = round(ppm, 2)
                prev_unix = unix
    except Exception:
        pass

    _ppm_series_cache = series
    return series


# ── manual chlorine additions ─────────────────────────────────────────────────
# When a person physically pours hypochlorite into the main tank, the model has no
# way to see it — clicks and FFT flow only describe water movement. Recording the
# pour here steps the persisted estimate up and leaves an auditable trail, so the
# running math survives a manual intervention instead of being silently wrong from
# that point on.
CHLORINE_ADDITIONS_FILE = DATA_DIR / 'chlorine_additions.jsonl'


def ppm_from_floz(floz: float, tank_gallons: float,
                  percent: 'float | None' = None) -> 'float | None':
    """ppm rise from pouring `floz` fl oz of `percent`-strength hypochlorite into
    a well-mixed tank of `tank_gallons`. Inverse of dose_floz()."""
    if not floz or not tank_gallons or tank_gallons <= 0:
        return None
    gpl = (percent if percent is not None else CHLORINE_DOSE_PERCENT) * 10.0
    if gpl <= 0:
        return None
    grams = floz / _FLOZ_PER_L * gpl
    return grams * 1000.0 / (tank_gallons * _L_PER_GAL)


def read_additions(limit: 'int | None' = None) -> list:
    """Return recorded additions, oldest first. Empty list if none/unreadable."""
    out = []
    try:
        with open(CHLORINE_ADDITIONS_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        return []
    out.sort(key=lambda r: r.get('ts', ''))
    return out[-limit:] if limit else out


def _throughput_gallons_since(start_unix: float, end_unix: float) -> float:
    """Total gallons through the tank between two times — dosatron (click-based)
    plus bypass (FFT flow seconds × calibrated GPM). Used to decay a retroactively
    recorded pour by the water that has moved through since it actually happened."""
    total = 0.0
    try:
        from monitor.dosatron import count_clicks, sum_flow_seconds, GALLONS_PER_CLICK
        total += count_clicks(start_unix, end_unix) * GALLONS_PER_CLICK
        total += sum_flow_seconds(start_unix, end_unix) / 60.0 * get_calibrated_bypass_gpm()
    except Exception:
        pass
    return total


def record_addition(floz: float, percent: 'float | None' = None,
                    ts: 'datetime | None' = None,
                    tank_gallons: 'float | None' = None,
                    note: str = '', source: str = 'manual') -> dict:
    """Record that someone poured `floz` fl oz of hypochlorite into the main tank,
    step the persisted ppm estimate up accordingly, and log a CHLORINE_ADDED event.

    `ts` may be in the past (maintenance reports a pour after the fact); the ppm
    bump is then decayed by the water that has moved through the tank since, using
    the same mixing math as the rest of the model. `tank_gallons` defaults to the
    current live tank reading.

    Returns a dict describing what was applied. Raises ValueError on bad input.
    """
    if floz is None or float(floz) <= 0:
        raise ValueError('floz must be greater than 0')
    floz = float(floz)
    percent = float(percent) if percent is not None else CHLORINE_DOSE_PERCENT
    if percent <= 0 or percent > 100:
        raise ValueError('percent must be between 0 and 100')
    when = ts or datetime.now()
    if when.timestamp() > datetime.now().timestamp() + 60:
        raise ValueError('addition timestamp is in the future')

    if tank_gallons is None:
        try:
            from monitor.tank import get_tank_data
            from monitor.config import TANK_URL
            _td = get_tank_data(TANK_URL) or {}
            if _td.get('status') == 'success':
                tank_gallons = _td.get('gallons')
        except Exception:
            tank_gallons = None
    if not tank_gallons or tank_gallons <= 0:
        raise ValueError('tank volume unavailable — pass tank_gallons explicitly')
    tank_gallons = float(tank_gallons)

    bump_raw = ppm_from_floz(floz, tank_gallons, percent)
    if bump_raw is None:
        raise ValueError('could not compute ppm rise')
    bump = bump_raw

    # Decay the bump by throughput since the pour actually happened (0 gallons for
    # a pour being recorded right now, so the common case is an undamped step).
    now = datetime.now()
    elapsed_gal = 0.0
    if when < now:
        elapsed_gal = _throughput_gallons_since(when.timestamp(), now.timestamp())
        bump *= math.exp(-elapsed_gal / tank_gallons)

    # Re-read immediately before mutating: the snapshot updater owns this same
    # file and a stale in-memory copy is exactly how the relay_state bug happened.
    ppm_before, _ = read_chlorine_ppm_state()
    ppm_after = ppm_before + bump
    _write_state(ppm_after)

    record = {
        'ts':            when.isoformat(),
        'recorded_ts':   now.isoformat(),
        'floz':          round(floz, 2),
        'percent':       percent,
        'tank_gallons':  round(tank_gallons, 1),
        'ppm_bump':      round(bump, 2),
        'ppm_bump_raw':  round(bump_raw, 2),
        'ppm_before':    round(ppm_before, 2),
        'ppm_after':     round(ppm_after, 2),
        'decay_gallons': round(elapsed_gal, 1),
        'source':        source,
        'note':          note or '',
    }
    try:
        with open(CHLORINE_ADDITIONS_FILE, 'a') as f:
            f.write(json.dumps(record) + '\n')
    except Exception:
        pass

    try:
        from monitor.logger import log_event
        from monitor.config import EVENTS_FILE
        log_event(str(EVENTS_FILE), 'CHLORINE_ADDED', None, None, tank_gallons,
                  None, None, None, None,
                  notes=(f"{floz:.0f} fl oz of {percent:.0f}% "
                         f"(+{bump:.1f} ppm: {ppm_before:.1f} -> {ppm_after:.1f})"
                         + (f" — {note}" if note else '')))
    except Exception:
        pass

    return record
