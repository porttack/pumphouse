"""
Ongoing calibration of BYPASS_GPM from real, zero-usage bypass sessions.

BYPASS_GPM (config.py) started as an owner field guess with no volumetric
metering behind it. This module derives a better estimate from actual tank
readings during "clean" bypass sessions — stretches where nothing could have
been drawing water, so the entire tank-level rise is attributable to bypass
inflow.

A session counts as clean only if, for its whole span:
  - the property was unoccupied (occupied == 'NO')
  - the well pump/dosatron never ran (pressure_high_seconds == 0 and
    dosatron_gallons == 0 in every window)
and, critically, the idle (bypass-off) snapshot immediately before the
session started and the one immediately after it ended were both flat
(tank level within one sensor step of 0 change). That bracket check is what
catches a running dishwasher/laundry load left by just-departed guests or
housekeeping — "unoccupied" alone doesn't rule out a real draw in progress,
but a flat tank right up to and right after the bypass-only stretch does.

Sessions that qualify contribute (tank_gallons_delta, real FFT-detected flow
seconds) to a running total in CALIBRATION_STATE_FILE. Once enough real flow
time has accumulated, get_calibrated_bypass_gpm() returns the empirical
total_gallons / total_flow_minutes instead of the config fallback.

record_snapshot() is the live hook, called once per ~15-min snapshot from
monitor/poll.py with that window's fields. bin/backfill_bypass_gpm_calibration.py
replays historical snapshots.csv rows through the same function to seed the
state from before this module existed.
"""
import json
import os

from monitor.config import BYPASS_GPM, DATA_DIR, BYPASS_GPM_MIN_CALIBRATION_MINUTES

CALIBRATION_STATE_FILE = DATA_DIR / 'bypass_gpm_calibration.json'

FLAT_THRESHOLD_GAL = 6.5      # just over one ~5.8 gal tank-sensor step
BRACKET_MAX_FLOW_SECONDS = 30  # tolerate a brief FFT boundary edge, reject real flow

_DEFAULT_STATE = {
    'total_gallons': 0.0,
    'total_flow_seconds': 0.0,
    'session_count': 0,
    'pending': None,          # {'start_ts': float, 'end_ts': float, 'gallons': float}
    'last_idle_flat': False,  # was the most recent non-session row idle+flat+unoccupied?
}


def _read_state() -> dict:
    try:
        with open(CALIBRATION_STATE_FILE) as f:
            state = json.load(f)
        merged = dict(_DEFAULT_STATE)
        merged.update(state)
        return merged
    except Exception:
        return dict(_DEFAULT_STATE)


def _write_state(state: dict) -> None:
    try:
        tmp = str(CALIBRATION_STATE_FILE) + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(state, f)
        os.replace(tmp, CALIBRATION_STATE_FILE)
    except Exception:
        pass


def get_calibrated_bypass_gpm() -> float:
    """Return the empirical bypass GPM once enough clean-session flow time
    has accumulated, else BYPASS_GPM (the owner's original field guess)."""
    state = _read_state()
    total_flow_min = state['total_flow_seconds'] / 60.0
    if total_flow_min >= BYPASS_GPM_MIN_CALIBRATION_MINUTES and state['total_gallons'] > 0:
        return state['total_gallons'] / total_flow_min
    return BYPASS_GPM


def get_calibration_status() -> dict:
    """Return the raw calibration state, for display (e.g. /water2)."""
    state = _read_state()
    total_flow_min = state['total_flow_seconds'] / 60.0
    calibrated = (total_flow_min >= BYPASS_GPM_MIN_CALIBRATION_MINUTES and state['total_gallons'] > 0)
    return {
        'calibrated': calibrated,
        'gpm': (state['total_gallons'] / total_flow_min) if calibrated else BYPASS_GPM,
        'session_count': state['session_count'],
        'total_flow_minutes': round(total_flow_min, 1),
        'total_gallons': round(state['total_gallons'], 1),
    }


def record_snapshot(*, occupied_status: str, bypass_on: bool, dosatron_gallons: float,
                     pressure_high_seconds: float, tank_gallons_delta: 'float | None',
                     window_start_ts: float, window_end_ts: float) -> None:
    """Advance calibration state by one ~15-min snapshot window.

    Called once per snapshot from monitor/poll.py, and replayed over
    historical snapshots.csv rows (in chronological order) by
    bin/backfill_bypass_gpm_calibration.py — same state machine either way.

    The bracket-flatness check (idle_and_flat below) deliberately does NOT
    use the snapshot's logged gallons_used figure — that's derived using
    SECONDS_PER_GALLON for bypass_gallons, which is known-wrong (see
    config.py) and contaminates exactly the boundary windows this check
    needs to trust. Instead it checks real FFT-detected flow directly.
    """
    from monitor.dosatron import sum_flow_seconds

    if tank_gallons_delta is None:
        # Tank delta is unknown for exactly one window right after a service
        # restart (last_snapshot_tank_gallons resets). Too common to treat as
        # a broken bracket/session — just skip this window rather than
        # spuriously discarding whatever session was in progress.
        return

    state = _read_state()
    occupied_no = (occupied_status == 'NO')

    is_clean_session_row = (
        bypass_on and occupied_no and pressure_high_seconds == 0 and dosatron_gallons == 0
    )
    idle_and_flat = (
        not bypass_on and occupied_no and dosatron_gallons == 0 and
        abs(tank_gallons_delta) <= FLAT_THRESHOLD_GAL and
        sum_flow_seconds(window_start_ts, window_end_ts) <= BRACKET_MAX_FLOW_SECONDS
    )

    pending = state['pending']

    if pending is None:
        if is_clean_session_row and state['last_idle_flat']:
            state['pending'] = {
                'start_ts': window_start_ts,
                'end_ts':   window_end_ts,
                'gallons':  tank_gallons_delta,
            }
        if not is_clean_session_row:
            state['last_idle_flat'] = idle_and_flat
        _write_state(state)
        return

    if is_clean_session_row:
        pending['gallons'] += tank_gallons_delta
        pending['end_ts'] = window_end_ts
        state['pending'] = pending
        _write_state(state)
        return

    # This row broke the clean-session condition — session is over.
    if idle_and_flat:
        flow_s = sum_flow_seconds(pending['start_ts'], pending['end_ts'])
        if flow_s > 0:
            state['total_gallons']       += pending['gallons']
            state['total_flow_seconds']  += flow_s
            state['session_count']       += 1
    # else: trailing bracket wasn't flat/idle — can't confirm zero usage, discard.
    state['pending'] = None
    state['last_idle_flat'] = idle_and_flat
    _write_state(state)
