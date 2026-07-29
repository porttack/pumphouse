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

from monitor.config import CHLORINE_STEADY_STATE_PPM, BYPASS_GPM, DATA_DIR

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


def update_chlorine_ppm_estimate(dosatron_gallons: float, bypass_seconds: float,
                                  tank_gallons: 'float | None') -> None:
    """Advance and persist the ppm estimate by one snapshot's worth of
    dosatron + bypass delivery. Called once per ~15-min snapshot from
    monitor/poll.py, using that window's ground-truth dosatron gallons
    (click-based) and raw bypass-on seconds (converted here via BYPASS_GPM,
    the owner's rough bypass-rate estimate)."""
    if not tank_gallons:
        return
    ppm, _ = read_chlorine_ppm_state()
    ppm = blend_ppm(ppm, dosatron_gallons, tank_gallons, CHLORINE_STEADY_STATE_PPM)
    bypass_gallons = bypass_seconds / 3600.0 * BYPASS_GPM if bypass_seconds else 0.0
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
