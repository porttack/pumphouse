"""
Owner-facing water status page + live flow API — Flask blueprint.

Mounted at:
  /water2           — owner control page (graph, live flow card, bypass controls)
  /api/flow_status  — JSON polled by that page every ~10s

Flow, GPM and duty cycle are derived from dosatron *clicks* (ground truth for
water moving through the injector), not the pressure signal — which can stick
HIGH for hours. The tank number comes from the monitor's running estimate
(dos_estimate.json): last successful sensor reading + clicks since.

Control-button links point at /control/<token> and the graph at /api/epaper.jpg,
both of which are served by monitor.web.
"""

import json
from datetime import datetime, timedelta

from flask import Blueprint, Response, request, jsonify

from monitor.config import (
    EVENTS_FILE,
    DOS_ESTIMATE_FILE,
    TANK_HEIGHT_INCHES, TANK_CAPACITY_GALLONS, TANK_FULL_GALLONS,
    SECRET_BYPASS_ON_TOKEN, SECRET_BYPASS_OFF_TOKEN,
    SECRET_OVERRIDE_ON_TOKEN, SECRET_OVERRIDE_OFF_TOKEN,
    SECRET_PURGE_TOKEN, SECRET_PURGE_NEXT_TOKEN, SECRET_PURGE_CANCEL_NEXT_TOKEN,
    SECRET_TEST_FILTER_TOKEN, PURGE_PENDING_FILE,
    BYPASS_GPM,
)
from monitor.gpio_helpers import read_float_sensor, FLOAT_STATE_CALLING
from monitor.relay import get_all_relay_status
from monitor.chlorine import read_chlorine_ppm_state, blend_ppm

water2_bp = Blueprint('water2', __name__)


def _depth_inches(gallons):
    """Convert gallons to tank depth in inches (linear; 1522 gal = 60")."""
    if gallons is None:
        return None
    return round(gallons * TANK_HEIGHT_INCHES / TANK_CAPACITY_GALLONS, 1)


def _puff_stats(click_ts, gpk, gap_threshold=120.0):
    """Segment clicks into 'puffs' (bursts split by gaps > gap_threshold seconds).

    Well-recovery mode: the pump delivers water in short bursts every 15-30 min.
    Returns per-puff cadence + the flow rate *during* the bursts (not diluted by
    the idle gaps). None if there are no clicks.
    """
    if not click_ts:
        return None
    ts = sorted(click_ts)
    puffs = []                       # (start_ts, end_ts, click_count)
    start = prev = ts[0]
    count = 1
    for t in ts[1:]:
        if t - prev > gap_threshold:
            puffs.append((start, prev, count))
            start = t
            count = 1
        else:
            count += 1
        prev = t
    puffs.append((start, prev, count))

    durations = [e - s for (s, e, _c) in puffs]                 # seconds (0 if 1 click)
    gaps      = [puffs[i + 1][0] - puffs[i][1] for i in range(len(puffs) - 1)]

    # GPM while actually flowing: total gallons / total on-time, over puffs long
    # enough to time (≥2s span). Short 1-click blips can't give a rate.
    tot_gal = tot_min = 0.0
    for (s, e, c) in puffs:
        if e - s >= 2:
            tot_gal += c * gpk
            tot_min += (e - s) / 60.0
    gpm = round(tot_gal / tot_min, 2) if tot_min > 0 else None

    last = puffs[-1]
    return {
        'n_puffs':      len(puffs),
        'on_avg_s':     round(sum(durations) / len(durations)),
        'gap_avg_s':    round(sum(gaps) / len(gaps)) if gaps else None,
        'gpm':          gpm,
        'last_gallons': round(last[2] * gpk, 1),
    }


def _read_events_tail_raw(chunk: int = 262144) -> str:
    """Read last `chunk` bytes of events.csv as a decoded string."""
    try:
        with open(EVENTS_FILE, 'rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - chunk))
            return f.read().decode('utf-8', errors='replace')
    except Exception:
        return ''


def _click_times(start_unix: float, end_unix: float) -> 'list[float]':
    """Return sorted unix timestamps of dosatron clicks in [start,end] via tail read.

    Clicks are the ground truth for "water is moving through the dosatron" — far
    more reliable than the pressure signal, which can stick HIGH for hours.
    """
    from monitor.dosatron import JSONL_FILE
    out = []
    try:
        with open(JSONL_FILE, 'rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 524288))   # ~last few thousand clicks
            data = f.read()
        for line in data.split(b'\n'):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ts = datetime.fromisoformat(rec['ts']).timestamp()
                if start_unix <= ts <= end_unix:
                    out.append(ts)
            except Exception:
                pass
    except Exception:
        pass
    out.sort()
    return out


def _click_duty_pct(click_ts: 'list[float]', win_start: float, win_end: float,
                    bucket: float = 30.0) -> 'float | None':
    """Duty cycle = fraction of `bucket`-second slots in the window with ≥1 click.

    This is "% of time we're actually receiving water": slots with clicks are
    pumping/flowing, empty slots are idle (between pump cycles, or valve closed)."""
    span = win_end - win_start
    if span <= 0:
        return None
    n_buckets = max(1, int((span + bucket - 1) // bucket))
    filled = set()
    for t in click_ts:
        if win_start <= t <= win_end:
            filled.add(int((t - win_start) // bucket))
    return round(len(filled) / n_buckets * 100, 1)


def _bypass_flow_records(start_unix: float, end_unix: float,
                          chunk: int = 262144) -> 'list[dict]':
    """Return bypass flow-cycle records (start, end, duration_s) overlapping
    [start_unix, end_unix], read from flow_cycles.jsonl via tail read (mirrors
    _click_times). Each record is one continuous FFT-detected flow episode —
    the bypass-mode analog of a dosatron click burst."""
    from monitor.dosatron import FLOW_CYCLES_JSONL as _FCJ
    out = []
    try:
        with open(_FCJ, 'rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - chunk))
            data = f.read()
        for line in data.split(b'\n'):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                s = datetime.fromisoformat(rec['start_ts']).timestamp()
                e = datetime.fromisoformat(rec['end_ts']).timestamp()
                if e < start_unix or s > end_unix:
                    continue
                out.append({'start': s, 'end': e,
                            'duration_s': rec.get('duration_s', e - s)})
            except Exception:
                pass
    except Exception:
        pass
    out.sort(key=lambda r: r['start'])
    return out


def _last_tank_reading() -> 'tuple[float | None, float]':
    """Return (gallons, unix_ts) of the most recent real TANK_LEVEL sensor event."""
    raw = _read_events_tail_raw(262144)
    gal, ts = None, 0.0
    for line in raw.splitlines():
        parts = line.split(',')
        if len(parts) < 5 or parts[1].strip() != 'TANK_LEVEL':
            continue
        try:
            gal = float(parts[4])
            ts  = datetime.fromisoformat(parts[0].strip()).timestamp()
        except Exception:
            pass
    return gal, ts


def _fill_window(float_calling: bool, now: float) -> 'tuple[float | None, float, str | None, float | None]':
    """Return (win_start, win_end, label, start_gallons) for scoping, from float events.

    Calling  → this fill: last FLOAT_CALLING … now.
    Full     → last completed fill: last FLOAT_CALLING … following FLOAT_FULL.
    start_gallons is the tank sensor reading recorded on that FLOAT_CALLING event
    (the level when the fill window began), or None.
    """
    raw = _read_events_tail_raw(262144)
    calling_ts = calling_gal = None
    full_ts    = None
    for line in raw.splitlines():
        parts = line.split(',')
        if len(parts) < 2:
            continue
        et = parts[1].strip()
        if et not in ('FLOAT_CALLING', 'FLOAT_FULL'):
            continue
        try:
            ts = datetime.fromisoformat(parts[0].strip()).timestamp()
        except Exception:
            continue
        if et == 'FLOAT_CALLING':
            calling_ts, full_ts = ts, None
            try:
                calling_gal = float(parts[4])
            except Exception:
                calling_gal = None
        else:
            full_ts = ts

    if calling_ts is None:
        return None, now, None, None
    if float_calling:
        return calling_ts, now, f'since calling ({round((now - calling_ts) / 60)}m)', calling_gal
    win_end = full_ts if full_ts else now
    return calling_ts, win_end, f'last fill ({round((win_end - calling_ts) / 60)}m)', calling_gal


@water2_bp.route('/api/flow_status')
def api_flow_status():
    """Real-time flow status: is water flowing, GPM, duty cycle, live tank level.

    Flow/GPM/duty are derived from dosatron *clicks* (ground truth for water
    moving), not the pressure signal. The tank number is the real sensor reading
    when the tank is full; during a fill it's that reading plus clicks since,
    capped — so it can never show a physically impossible value.
    """
    import time as _t
    from monitor.dosatron import (
        FLOW_STATE_FILE as _FLOW_STATE_FILE,
        GALLONS_PER_CLICK as _GPK,
    )

    now           = _t.time()
    relay_status  = get_all_relay_status()
    bypass_on     = relay_status.get('bypass') == 'ON'
    override_on   = relay_status.get('supply_override') == 'ON'
    float_calling = read_float_sensor() == FLOAT_STATE_CALLING

    result: dict = {
        'mode':                 'bypass' if bypass_on else 'dosatron',
        'bypass_on':            bypass_on,
        'override_on':          override_on,
        'purge_pending':        PURGE_PENDING_FILE.exists(),
        'float_calling':        float_calling,
        'is_flowing':           False,
        'gpm':                  None,
        'last_gpm':             None,
        'last_flow_gallons':    None,
        'last_click_age_s':     None,
        'duty_cycle_pct':       None,
        'duty_window_label':    None,
        'duty_window_gallons':  None,
        'house_used_gallons':   None,
        'puff_count':           None,
        'puff_on_avg_s':        None,
        'puff_gap_avg_s':       None,
        'puff_gpm':             None,
        'eta_full_ts':          None,
        'eta_full_gph':         None,
        'tank_gallons':         None,
        'tank_inches':          None,
        'tank_reading_age_min': None,
        'tank_is_estimate':     False,
        'tank_base_gallons':    None,
        'tank_base_inches':     None,
        'tank_base_age_min':    None,
        'chlorine_ppm_est':     None,
        'chlorine_est_age_min': None,
        'bypass_gallons_delivered': None,
    }

    # Current tank level — same live source the graph uses. dos_estimate.json is
    # written by the monitor every ~10s as (last successful sensor fetch + clicks
    # since), so when idle it equals the graph's live reading, and during a fill it's
    # the real-time estimate. Falls back to the last TANK_LEVEL event only if the
    # monitor's estimate is stale/unavailable (e.g. monitor down).
    est_gal = None
    base_gal = base_ts = None
    try:
        _e = json.loads(DOS_ESTIMATE_FILE.read_text())
        if (datetime.now() - datetime.fromisoformat(_e['timestamp'])).total_seconds() < 300:
            est_gal  = float(_e['gallons'])
            base_gal = _e.get('base_gallons')
            base_ts  = _e.get('base_time')
    except Exception:
        pass

    if est_gal is not None:
        result['tank_gallons']     = round(est_gal)
        result['tank_inches']      = _depth_inches(est_gal)
        result['tank_is_estimate'] = float_calling   # includes live clicks during a fill
        if base_gal is not None:
            result['tank_base_gallons'] = round(base_gal)
            result['tank_base_inches']  = _depth_inches(base_gal)
            if base_ts:
                # Age of the last real sensor reading (shown filling *and* stopped).
                result['tank_base_age_min'] = round((now - float(base_ts)) / 60)
    else:
        real_gal, real_ts = _last_tank_reading()
        if real_gal is not None:
            result['tank_gallons']         = round(real_gal)
            result['tank_inches']          = _depth_inches(real_gal)
            result['tank_reading_age_min'] = round((now - real_ts) / 60)

    # Chlorine ppm estimate — always shown, regardless of mode. Baseline is the
    # persisted estimate (monitor.chlorine), updated once per ~15-min snapshot
    # by monitor/poll.py from ground-truth dosatron/bypass gallons; it declines
    # toward 0ppm while bypassing and recovers toward CHLORINE_STEADY_STATE_PPM
    # once the dosatron resumes (same mixing math both directions). While bypass
    # is actively on, project the baseline forward with elapsed real-time bypass
    # delivery since the bypass session started (BYPASS_PPM_SESSION_FILE) so the
    # number keeps moving live between snapshot updates, not just every 15 min.
    # UNVALIDATED model — see config.py note; treat as a rough guide only.
    baseline_ppm, baseline_ts = read_chlorine_ppm_state()
    display_ppm = baseline_ppm
    tank_gal = result.get('tank_gallons')
    if bypass_on and tank_gal:
        try:
            from monitor.dosatron import BYPASS_PPM_SESSION_FILE as _BYPASS_PPM_SESSION_FILE
            with open(_BYPASS_PPM_SESSION_FILE) as f:
                session_start_ts = datetime.fromisoformat(json.load(f)['start_ts']).timestamp()
            live_start = max(session_start_ts, baseline_ts)
            gallons_delivered = max(0.0, now - live_start) / 3600.0 * BYPASS_GPM
            result['bypass_gallons_delivered'] = round(gallons_delivered, 1)
            display_ppm = blend_ppm(baseline_ppm, gallons_delivered, tank_gal, 0.0)
        except Exception:
            pass
    result['chlorine_ppm_est'] = round(display_ppm)
    result['chlorine_est_age_min'] = round((now - baseline_ts) / 60)

    win_start, win_end, label, win_start_gal = _fill_window(float_calling, now)
    result['duty_window_label'] = label

    if not bypass_on:
        # ── dosatron mode: everything from clicks ────────────────────────────
        # Fetch at least the last 6 h so puff-cadence + delivered-rate stats have
        # several well-recovery cycles to average, plus the whole fill window.
        puff_window = 6 * 3600
        span_start  = min(win_start if win_start else now, now - puff_window)
        clicks = _click_times(span_start, now)

        if clicks:
            result['last_click_age_s'] = round(now - clicks[-1])

        # Flowing = valve open AND a click within the last 20 s (real movement).
        is_flowing = (float_calling and result['last_click_age_s'] is not None
                      and result['last_click_age_s'] <= 20)
        result['is_flowing'] = is_flowing

        # GPM from clicks in the last 60 s (clicks/min × gal/click).
        if is_flowing:
            c60 = sum(1 for t in clicks if t >= now - 60)
            result['gpm'] = round(c60 * _GPK, 2)

        if win_start:
            result['duty_cycle_pct'] = _click_duty_pct(clicks, win_start, win_end)

            # Average GPM over the fill window's *active* (clicking) time — shown as
            # the "last fill" rate when we're not currently flowing. Active time is
            # counted from 30s buckets that contain a click (matches the duty calc).
            win_clicks = [t for t in clicks if win_start <= t <= win_end]
            # Total gallons received over the window (since calling / last fill).
            received = round(len(win_clicks) * _GPK, 1)
            result['duty_window_gallons'] = received
            if win_clicks:
                active_buckets = len({int((t - win_start) // 30) for t in win_clicks})
                active_min = active_buckets * 30 / 60
                if active_min > 0:
                    result['last_gpm'] = round(len(win_clicks) * _GPK / active_min, 2)

            # House usage over the window by water balance: in − tank level change.
            # Uses sensor readings for the level change (start = FLOAT_CALLING event,
            # end = last sensor fetch / base of the estimate) so dosatron clicks
            # aren't double-counted on both sides.
            tank_now_sensor = base_gal if base_gal is not None else result.get('tank_gallons')
            if win_start_gal is not None and tank_now_sensor is not None:
                used = received - (tank_now_sensor - win_start_gal)
                result['house_used_gallons'] = round(max(0.0, used), 1)

        # ── Puff (well-recovery) cadence over the last 6 h ───────────────────
        recent = [t for t in clicks if t >= now - puff_window]
        pstats = _puff_stats(recent, _GPK)
        if pstats:
            result['last_flow_gallons'] = pstats['last_gallons']
            if pstats['n_puffs'] >= 2:
                result['puff_count']     = pstats['n_puffs']
                result['puff_on_avg_s']  = pstats['on_avg_s']
                result['puff_gap_avg_s'] = pstats['gap_avg_s']
                result['puff_gpm']       = pstats['gpm']

        # ── ETA to full (1450 gal) from the recent delivered rate ────────────
        cur = result.get('tank_gallons')
        if cur is not None and cur < TANK_FULL_GALLONS and recent:
            span_h = (now - recent[0]) / 3600.0
            if span_h >= 0.5:
                gph = (len(recent) * _GPK) / span_h
                if gph > 0:
                    eta_h = (TANK_FULL_GALLONS - cur) / gph
                    if 0 < eta_h < 168:
                        result['eta_full_ts']  = (datetime.now() + timedelta(hours=eta_h)).isoformat()
                        result['eta_full_gph'] = round(gph, 1)

    else:
        # ── bypass mode: flow from FFT detection (audio), no dosatron clicks ──
        # GPM isn't acoustically measurable (no volumetric metering) — BYPASS_GPM
        # is an assumed constant (owner's rough field estimate), not a computed
        # rate the way dosatron GPM is. Everything gallons-based below is that
        # constant applied to real, audio-detected flow *duration* — the timing
        # is measured, the rate is assumed.
        try:
            with open(_FLOW_STATE_FILE) as f:
                result['is_flowing'] = json.load(f).get('state') == 'flowing'
        except Exception:
            pass
        if result['is_flowing']:
            result['gpm'] = BYPASS_GPM

        puff_window = 6 * 3600
        span_start  = min(win_start if win_start else now, now - puff_window)
        records = _bypass_flow_records(span_start, now)

        if records:
            last = records[-1]
            result['last_click_age_s']  = round(now - last['end'])
            result['last_flow_gallons'] = round(last['duration_s'] / 3600.0 * BYPASS_GPM, 1)

        if win_start:
            win_records  = [r for r in records if r['end'] >= win_start]
            flowing_secs = sum(min(r['end'], win_end) - max(r['start'], win_start)
                               for r in win_records)
            win_len = win_end - win_start
            if win_len > 0:
                result['duty_cycle_pct'] = round(min(flowing_secs, win_len) / win_len * 100, 1)

            received = round(flowing_secs / 3600.0 * BYPASS_GPM, 1)
            result['duty_window_gallons'] = received

            # House usage over the window by water balance (same as dosatron mode).
            tank_now_sensor = base_gal if base_gal is not None else result.get('tank_gallons')
            if win_start_gal is not None and tank_now_sensor is not None:
                used = received - (tank_now_sensor - win_start_gal)
                result['house_used_gallons'] = round(max(0.0, used), 1)

        # ── Flow-cycle cadence over the last 6 h (well recovery limits bypass
        # delivery the same way it limits dosatron delivery — same well) ──────
        recent = [r for r in records if r['start'] >= now - puff_window]
        if len(recent) >= 2:
            durations = [r['duration_s'] for r in recent]
            gaps = [recent[i + 1]['start'] - recent[i]['end'] for i in range(len(recent) - 1)]
            result['puff_count']     = len(recent)
            result['puff_on_avg_s']  = round(sum(durations) / len(durations))
            result['puff_gap_avg_s'] = round(sum(gaps) / len(gaps)) if gaps else None
            result['puff_gpm']       = BYPASS_GPM

        # ── ETA to full (1450 gal) from the recent delivered rate ────────────
        cur = result.get('tank_gallons')
        if cur is not None and cur < TANK_FULL_GALLONS and recent:
            span_h = (now - recent[0]['start']) / 3600.0
            if span_h >= 0.5:
                recent_flowing_secs = sum(r['duration_s'] for r in recent)
                gph = (recent_flowing_secs / 3600.0 * BYPASS_GPM) / span_h
                if gph > 0:
                    eta_h = (TANK_FULL_GALLONS - cur) / gph
                    if 0 < eta_h < 168:
                        result['eta_full_ts']  = (datetime.now() + timedelta(hours=eta_h)).isoformat()
                        result['eta_full_gph'] = round(gph, 1)

    resp = jsonify(result)
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@water2_bp.route('/water2')
def water2_status():
    """
    Owner-facing water status page with bypass controls.
    Same graph as /water; bypass buttons always shown (URL is obscure enough).
    Designed to work via Cloudflare — uses token-based /control/<token> links.
    Cache-Control is no-store so relay state is always fresh.
    """
    via_cloudflare = bool(request.headers.get('CF-Ray'))
    dashboard_url  = f'https://{request.host}/?hours=120&totals=income'

    hours_arg = request.args.get('hours', type=int)
    days_arg  = request.args.get('days',  type=int)
    if hours_arg is None and days_arg is not None:
        hours_arg = days_arg * 24
    if hours_arg is None:
        hours_arg = 6   # default to a 6-hour view; 3-day toggle below

    import time as _time
    _ts = int(_time.time())
    img_param   = f'tenant=no&hours={hours_arg}&est=1&minrange=4&_t={_ts}'
    img_base    = f'/api/epaper.jpg?tenant=no&hours={hours_arg}&est=1&minrange=4'
    img_tag     = f'<img id="tank-img" src="/api/epaper.jpg?{img_param}" alt="Water tank level graph">'

    # Range toggle: 6 hours (default) vs 3 days
    is_long_view = hours_arg >= 72
    def _range_link(text, qs, active):
        style = ('color:#cdd8f0;font-weight:600;' if active else 'color:#7aa4d8;')
        return f'<a href="/water2?{qs}" style="text-decoration:none;{style}">{text}</a>'
    range_html = (
        '<div style="font-size:0.82rem;color:#6a82b0;display:flex;gap:14px;'
        'justify-content:center;margin-top:-8px;">'
        + _range_link('6 hours', 'hours=6', not is_long_view)
        + '<span>&bull;</span>'
        + _range_link('3 days', 'days=3', is_long_view)
        + '</div>'
    )
    if via_cloudflare:
        img_inner   = img_tag
        extra_links = ''
    else:
        img_inner   = f'<a href="{dashboard_url}">{img_tag}</a>'
        extra_links = (
            '\n    <span>&bull;</span>'
            '\n    <a href="/">Dashboard</a>'
        )
    img_html = f'<div class="img-wrap">{img_inner}<div class="spinner" id="tank-spinner"></div></div>'

    back = '/water2'

    controls_html = ''
    ctrl_cfg = None
    if SECRET_BYPASS_ON_TOKEN:
        relay_status = get_all_relay_status()
        bypass_on    = relay_status.get('bypass') == 'ON'
        override_on  = relay_status.get('supply_override') == 'ON'

        btn_active  = 'background:#f44336;border-color:#f44336;color:#fff;'
        btn_warning = 'background:#ff9800;border-color:#ff9800;color:#fff;'
        btn_base    = 'display:inline-block;padding:8px 16px;border:1px solid #7aa4d8;border-radius:6px;color:#7aa4d8;text-decoration:none;font-size:0.9rem;'

        if bypass_on:
            bypass_btn = f'<a id="btn-bypass" href="/control/{SECRET_BYPASS_OFF_TOKEN}?back={back}" style="{btn_base}{btn_active}">&#9679; Bypass Filters/Dosatron: ON</a>'
        else:
            bypass_btn = f'<a id="btn-bypass" href="/control/{SECRET_BYPASS_ON_TOKEN}?back={back}" style="{btn_base}">&#9675; Bypass Filters/Dosatron: OFF</a>'

        if override_on:
            override_btn = f'<a id="btn-override" href="/control/{SECRET_OVERRIDE_OFF_TOKEN}?back={back}" style="{btn_base}{btn_warning}">&#9679; Force Fill Tank Valve Open: ON</a>'
        else:
            override_btn = f'<a id="btn-override" href="/control/{SECRET_OVERRIDE_ON_TOKEN}?back={back}" style="{btn_base}">&#9675; Force Fill Tank Valve Open: OFF</a>'

        purge_btn       = f'<a href="/control/{SECRET_PURGE_TOKEN}?back={back}" style="{btn_base}" onclick="return confirm(\'Trigger a purge cycle?\')">&#9881; Purge now</a>'
        # Purge with pressure: schedule for the next pump cycle (+15s); toggles to
        # a cancel button while one is pending — same as the dashboard.
        if PURGE_PENDING_FILE.exists():
            purge_next_btn = f'<a id="btn-purgenext" href="/control/{SECRET_PURGE_CANCEL_NEXT_TOKEN}?back={back}" style="{btn_base}{btn_warning}">&#9881; Purge pending &mdash; cancel?</a>'
        else:
            purge_next_btn = f'<a id="btn-purgenext" href="/control/{SECRET_PURGE_NEXT_TOKEN}?back={back}" style="{btn_base}" onclick="return confirm(\'Schedule purge for next pump cycle (+15s)?\')">&#9881; Purge w/ pressure</a>'
        test_filter_btn = f'<a href="/control/{SECRET_TEST_FILTER_TOKEN}?back={back}" style="{btn_base}" title="Runs water through the filters + dosatron on demand (Override ON so the pump pushes water, Bypass OFF so it flows through the filter). For inspecting the filter/dosatron — turn Override off when done." onclick="return confirm(\'Start test filter mode? This turns Override ON and Bypass OFF.\')">&#128308; Test Filter</a>'

        float_state = read_float_sensor()
        if float_state == FLOAT_STATE_CALLING:
            tank_valve_html = '<div id="tank-valve" style="color:#ff9800;font-size:0.9rem;text-align:center;">&#9654; Fill Tank Valve: OPEN (tank calling for water)</div>'
        else:
            tank_valve_html = '<div id="tank-valve" style="color:#ff9800;font-size:0.9rem;text-align:center;">&#9679; Fill Tank Valve: CLOSED (tank full)</div>'

        controls_html = f'{tank_valve_html}<div style="display:flex;gap:10px;flex-wrap:wrap;justify-content:center;margin-top:8px;">{bypass_btn}{override_btn}{purge_btn}{purge_next_btn}{test_filter_btn}</div>'

        # Config for the client to re-sync button state on each poll (so a change
        # made from another device shows here within the poll interval).
        ctrl_cfg = {
            'base':       btn_base,
            'active':     btn_base + btn_active,
            'warning':    btn_base + btn_warning,
            'bypassOn':   f'/control/{SECRET_BYPASS_ON_TOKEN}?back={back}',
            'bypassOff':  f'/control/{SECRET_BYPASS_OFF_TOKEN}?back={back}',
            'ovrOn':      f'/control/{SECRET_OVERRIDE_ON_TOKEN}?back={back}',
            'ovrOff':     f'/control/{SECRET_OVERRIDE_OFF_TOKEN}?back={back}',
            'purgeNext':  f'/control/{SECRET_PURGE_NEXT_TOKEN}?back={back}',
            'purgeCancel': f'/control/{SECRET_PURGE_CANCEL_NEXT_TOKEN}?back={back}',
        }

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Water Status &mdash; onblackberryhill.com</title>
  <link rel="icon" type="image/svg+xml" href='data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M16 2C16 2 6 14 6 21a10 10 0 0 0 20 0C26 14 16 2 16 2z" fill="%231e90ff"/><path d="M11 20c0-3 2-5 4-7" stroke="white" stroke-width="1.5" fill="none" stroke-linecap="round" opacity="0.5"/></svg>'>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #1a2440;
      color: #cdd8f0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      display: flex;
      flex-direction: column;
      align-items: center;
      min-height: 100vh;
      padding: 24px 16px;
      gap: 20px;
    }}
    h1 {{
      font-size: 1.2rem;
      font-weight: 600;
      letter-spacing: 0.04em;
      color: #8fb3e8;
      text-transform: uppercase;
    }}
    .card {{
      background: #243060;
      border-radius: 12px;
      padding: 12px;
      max-width: 640px;
      width: 100%;
      box-shadow: 0 4px 24px rgba(0,0,0,0.4);
    }}
    /* Reserve the graph's box before the JPEG loads so text below doesn't jump.
       The e-paper JPEG renders at a fixed 1000x488. */
    .img-wrap {{
      position: relative;
      width: 100%;
      aspect-ratio: 1000 / 488;
      border-radius: 6px;
      overflow: hidden;
    }}
    .card img {{
      width: 100%;
      height: 100%;
      object-fit: contain;
      border-radius: 6px;
      display: block;
    }}
    .img-wrap a {{ display: block; width: 100%; height: 100%; }}
    .spinner {{
      position: absolute;
      top: 50%;
      left: 50%;
      width: 40px;
      height: 40px;
      margin: -20px 0 0 -20px;
      border: 4px solid rgba(143,179,232,0.25);
      border-top-color: #8fb3e8;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      pointer-events: none;
    }}
    .spinner.hidden {{ display: none; }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    .flow-card {{
      background: #1e2d54;
      border-radius: 12px;
      padding: 14px 18px;
      max-width: 640px;
      width: 100%;
      box-shadow: 0 2px 12px rgba(0,0,0,0.3);
      font-size: 0.9rem;
    }}
    .flow-row {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 6px;
    }}
    .flow-row:last-child {{ margin-bottom: 0; }}
    .flow-dot {{
      width: 10px; height: 10px;
      border-radius: 50%;
      flex-shrink: 0;
      background: #444;
    }}
    .flow-dot.on  {{ background: #1e90ff; box-shadow: 0 0 6px #1e90ff; }}
    .flow-dot.off {{ background: #4a5568; }}
    .flow-label {{ color: #8fb3e8; min-width: 120px; }}
    .flow-value {{ color: #cdd8f0; font-weight: 500; }}
    .flow-ts {{ font-size: 0.75rem; color: #4a6080; margin-top: 8px; }}
    .links {{
      font-size: 0.82rem;
      color: #6a82b0;
      text-align: center;
      display: flex;
      gap: 16px;
      flex-wrap: wrap;
      justify-content: center;
    }}
    .links a {{ color: #7aa4d8; text-decoration: none; }}
    .links a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <h1>Water Tank &mdash; onblackberryhill.com</h1>
  <div class="card">
    {img_html}
  </div>
  {range_html}
  <div class="flow-card" id="flow-card">
    <div class="flow-row">
      <div class="flow-dot" id="flow-dot"></div>
      <span class="flow-label">Flow:</span>
      <span class="flow-value" id="flow-status-text">&mdash;</span>
    </div>
    <div class="flow-row" id="flow-gpm-row" style="display:none">
      <div style="width:10px"></div>
      <span class="flow-label" id="flow-gpm-label">Est. GPM (60s):</span>
      <span class="flow-value" id="flow-gpm-text">&mdash;</span>
    </div>
    <div class="flow-row" id="flow-puff-row" style="display:none">
      <div style="width:10px"></div>
      <span class="flow-label">Puffs:</span>
      <span class="flow-value" id="flow-puff-text">&mdash;</span>
    </div>
    <div class="flow-row" id="flow-duty-row" style="display:none">
      <div style="width:10px"></div>
      <span class="flow-label" id="flow-duty-label">Duty:</span>
      <span class="flow-value" id="flow-duty-text">&mdash;</span>
    </div>
    <div class="flow-row" id="flow-used-row" style="display:none">
      <div style="width:10px"></div>
      <span class="flow-label">House used:</span>
      <span class="flow-value" id="flow-used-text">&mdash;</span>
    </div>
    <div class="flow-row" id="flow-ppm-row" style="display:none">
      <div style="width:10px"></div>
      <span class="flow-label" title="Rough, unvalidated model — treat as an order-of-magnitude guide, not a precise reading">Est. chlorine (rough):</span>
      <span class="flow-value" id="flow-ppm-text">&mdash;</span>
    </div>
    <div class="flow-row" id="flow-eta-row" style="display:none">
      <div style="width:10px"></div>
      <span class="flow-label">Full (1450):</span>
      <span class="flow-value" id="flow-eta-text">&mdash;</span>
    </div>
    <div class="flow-row" id="flow-tank-row" style="display:none">
      <div style="width:10px"></div>
      <span class="flow-label" id="flow-tank-label">Tank level:</span>
      <span class="flow-value" id="flow-tank-text">&mdash;</span>
    </div>
    <div class="flow-row" id="flow-sensor-row" style="display:none">
      <div style="width:10px"></div>
      <span class="flow-label">Last sensor:</span>
      <span class="flow-value" id="flow-sensor-text">&mdash;</span>
    </div>
    <div class="flow-ts" id="flow-updated"></div>
  </div>
  {controls_html}
  <div class="links">
    <a href="/timelapse">Timelapse</a>
    <span>&bull;</span>
    <a href="/water">Public view</a>
    <span>&bull;</span>
    <a href="https://onblackberryhill.com/weather">Weather</a>{extra_links}
  </div>
  <script>
    const IMG_BASE = {json.dumps(img_base)};
    const CTRL = {json.dumps(ctrl_cfg)};
    let _lastFlowing = false;
    let _imgTimer = null;

    // Re-sync the control buttons from live state each poll, so a change made
    // from another device (or the pump itself) shows here within the interval.
    function applyControlState(d) {{
      if (!CTRL) return;
      const setToggle = (id, on, onUrl, offUrl, onLabel, offLabel, onStyle) => {{
        const el = document.getElementById(id);
        if (!el) return;
        el.setAttribute('href', on ? onUrl : offUrl);
        el.setAttribute('style', on ? onStyle : CTRL.base);
        el.innerHTML = on ? onLabel : offLabel;
      }};
      setToggle('btn-bypass', d.bypass_on, CTRL.bypassOff, CTRL.bypassOn,
        '&#9679; Bypass Filters/Dosatron: ON', '&#9675; Bypass Filters/Dosatron: OFF', CTRL.active);
      setToggle('btn-override', d.override_on, CTRL.ovrOff, CTRL.ovrOn,
        '&#9679; Force Fill Tank Valve Open: ON', '&#9675; Force Fill Tank Valve Open: OFF', CTRL.warning);

      const pn = document.getElementById('btn-purgenext');
      if (pn) {{
        if (d.purge_pending) {{
          pn.setAttribute('href', CTRL.purgeCancel);
          pn.setAttribute('style', CTRL.warning);
          pn.onclick = null;
          pn.innerHTML = '&#9881; Purge pending &mdash; cancel?';
        }} else {{
          pn.setAttribute('href', CTRL.purgeNext);
          pn.setAttribute('style', CTRL.base);
          pn.onclick = (e) => {{ if (!confirm('Schedule purge for next pump cycle (+15s)?')) e.preventDefault(); }};
          pn.innerHTML = '&#9881; Purge w/ pressure';
        }}
      }}

      const tv = document.getElementById('tank-valve');
      if (tv) {{
        tv.innerHTML = d.float_calling
          ? '&#9654; Fill Tank Valve: OPEN (tank calling for water)'
          : '&#9679; Fill Tank Valve: CLOSED (tank full)';
      }}
    }}

    function showSpinner(on) {{
      const sp = document.getElementById('tank-spinner');
      if (sp) sp.classList.toggle('hidden', !on);
    }}

    function refreshImage() {{
      const img = document.getElementById('tank-img');
      if (!img) return;
      showSpinner(true);
      img.src = IMG_BASE + '&_t=' + Math.floor(Date.now() / 1000);
    }}

    (function initImg() {{
      const img = document.getElementById('tank-img');
      if (!img) return;
      img.addEventListener('load',  () => showSpinner(false));
      img.addEventListener('error', () => showSpinner(false));
      // If it was cached and already complete, hide immediately.
      if (img.complete && img.naturalWidth > 0) showSpinner(false);
    }})();

    function scheduleImg(flowing) {{
      if (_imgTimer) clearTimeout(_imgTimer);
      const delay = flowing ? 30000 : 120000;
      _imgTimer = setTimeout(() => {{ refreshImage(); scheduleImg(_lastFlowing); }}, delay);
    }}

    function updateFlow() {{
      fetch('/api/flow_status')
        .then(r => r.json())
        .then(d => {{
          applyControlState(d);
          const flowing   = !!d.is_flowing;
          const dot       = document.getElementById('flow-dot');
          const statusEl  = document.getElementById('flow-status-text');
          const gpmRow    = document.getElementById('flow-gpm-row');
          const dutyRow   = document.getElementById('flow-duty-row');
          const dutyLabel = document.getElementById('flow-duty-label');
          const gpmEl     = document.getElementById('flow-gpm-text');
          const dutyEl    = document.getElementById('flow-duty-text');
          const tankRow   = document.getElementById('flow-tank-row');
          const tankEl    = document.getElementById('flow-tank-text');
          const updEl     = document.getElementById('flow-updated');

          dot.className = 'flow-dot ' + (flowing ? 'on' : 'off');

          if (flowing) {{
            const mode = d.mode === 'bypass' ? ' (bypass)' : '';
            statusEl.textContent = 'Flowing' + mode;
          }} else if (!d.float_calling) {{
            statusEl.textContent = 'Not filling (tank full)';
          }} else if (d.last_click_age_s !== null && d.last_click_age_s !== undefined) {{
            const ago = d.last_click_age_s < 120
              ? d.last_click_age_s + 's ago'
              : Math.round(d.last_click_age_s / 60) + 'm ago';
            let s = 'Filling, between puffs (last flow ' + ago;
            if (d.last_flow_gallons !== null && d.last_flow_gallons !== undefined) {{
              s += ', ' + d.last_flow_gallons + ' gal';
            }}
            statusEl.textContent = s + ')';
          }} else {{
            statusEl.textContent = 'Calling for water (no flow detected yet)';
          }}

          const fmtDur = (s) => {{
            if (s === null || s === undefined) return '';
            if (s < 60) return s + 's';
            const m = Math.floor(s / 60), sec = s % 60;
            if (m < 60) return sec ? (m + 'm ' + sec + 's') : (m + 'm');
            const h = Math.floor(m / 60), mm = m % 60;
            return mm ? (h + 'h ' + mm + 'm') : (h + 'h');
          }};

          // GPM: live rate while flowing; else puff-flow rate; else last fill avg.
          // Bypass mode: flow *timing* is measured (audio), but the rate itself
          // is an assumed constant (BYPASS_GPM), not computed — label honestly.
          const isBypass = d.mode === 'bypass';
          const gpmLabel = document.getElementById('flow-gpm-label');
          if (d.gpm !== null && d.gpm !== undefined) {{
            gpmRow.style.display = '';
            gpmLabel.textContent = isBypass ? 'Bypass GPM (assumed):' : 'Est. GPM (60s):';
            gpmEl.textContent = d.gpm.toFixed(2) + ' gal/min';
          }} else if (d.puff_gpm !== null && d.puff_gpm !== undefined) {{
            gpmRow.style.display = '';
            gpmLabel.textContent = isBypass ? 'Bypass GPM (assumed):' : 'Puff GPM:';
            gpmEl.textContent = d.puff_gpm.toFixed(2) + ' gal/min';
          }} else if (d.last_gpm !== null && d.last_gpm !== undefined) {{
            gpmRow.style.display = '';
            gpmLabel.textContent = 'Last fill GPM:';
            gpmEl.textContent = d.last_gpm.toFixed(2) + ' gal/min';
          }} else {{
            gpmRow.style.display = 'none';
          }}

          // Puff cadence (well-recovery cycling): avg on-time and gap between.
          // In bypass mode these are FFT-detected flow cycles, not dosatron clicks.
          const puffRow = document.getElementById('flow-puff-row');
          const puffEl  = document.getElementById('flow-puff-text');
          if (d.puff_count) {{
            puffRow.style.display = '';
            const unit = isBypass ? 'cycles' : 'puffs';
            puffEl.textContent = 'on ~' + fmtDur(d.puff_on_avg_s)
              + ', every ~' + fmtDur(d.puff_gap_avg_s)
              + ' (' + d.puff_count + ' ' + unit + '/6h)';
          }} else {{
            puffRow.style.display = 'none';
          }}

          // ETA to full (1450 gal) from the recent delivered rate
          const etaRow = document.getElementById('flow-eta-row');
          const etaEl  = document.getElementById('flow-eta-text');
          if (d.eta_full_ts) {{
            etaRow.style.display = '';
            const dt = new Date(d.eta_full_ts), n2 = new Date();
            let when = dt.toLocaleTimeString([], {{ hour: 'numeric', minute: '2-digit' }});
            if (dt.toDateString() !== n2.toDateString()) {{
              when = dt.toLocaleDateString([], {{ weekday: 'short' }}) + ' ' + when;
            }}
            const mins = Math.max(0, Math.round((dt - n2) / 60000));
            const rel  = mins >= 60
              ? ('in ' + Math.floor(mins / 60) + 'h ' + (mins % 60) + 'm')
              : ('in ' + mins + 'm');
            etaEl.textContent = '~' + when + ' (' + rel
              + (d.eta_full_gph ? ', ' + d.eta_full_gph + ' gph' : '') + ')';
          }} else {{
            etaRow.style.display = 'none';
          }}

          // Duty cycle (% of time receiving water) + gallons over the window
          if (d.duty_cycle_pct !== null && d.duty_cycle_pct !== undefined) {{
            dutyRow.style.display = '';
            dutyLabel.textContent = 'Receiving water (' + (d.duty_window_label || '?') + '):';
            let v = d.duty_cycle_pct.toFixed(0) + '%';
            if (d.duty_window_gallons !== null && d.duty_window_gallons !== undefined) {{
              v += ' · ' + d.duty_window_gallons + ' gal';
            }}
            dutyEl.textContent = v;
          }} else {{
            dutyRow.style.display = 'none';
          }}

          // House usage over the window (received − tank level change)
          const usedRow = document.getElementById('flow-used-row');
          const usedEl  = document.getElementById('flow-used-text');
          if (d.house_used_gallons !== null && d.house_used_gallons !== undefined) {{
            usedRow.style.display = '';
            let uv = '~' + d.house_used_gallons + ' gal';
            // Flag when usage is outrunning what the well delivered
            if (d.duty_window_gallons !== null && d.duty_window_gallons !== undefined
                && d.house_used_gallons > d.duty_window_gallons + 2) {{
              uv += ' (> ' + d.duty_window_gallons + ' received)';
            }}
            usedEl.textContent = uv;
          }} else {{
            usedRow.style.display = 'none';
          }}

          // Est. chlorine ppm — always shown; declines during bypass, recovers
          // once the dosatron resumes (whole-tank estimate, not per-session).
          const ppmRow = document.getElementById('flow-ppm-row');
          const ppmEl  = document.getElementById('flow-ppm-text');
          if (d.chlorine_ppm_est !== null && d.chlorine_ppm_est !== undefined) {{
            ppmRow.style.display = '';
            let pv = '~' + d.chlorine_ppm_est + ' ppm?';
            if (d.bypass_on && d.bypass_gallons_delivered !== null && d.bypass_gallons_delivered !== undefined) {{
              pv += ' (' + d.bypass_gallons_delivered + ' gal bypass delivered)';
            }} else if (d.chlorine_est_age_min !== null && d.chlorine_est_age_min !== undefined) {{
              pv += ' (updated ' + (d.chlorine_est_age_min < 1 ? 'just now' : d.chlorine_est_age_min + 'm ago') + ')';
            }}
            ppmEl.textContent = pv;
          }} else {{
            ppmRow.style.display = 'none';
          }}

          // Tank level: while filling show the live estimate AND the sensor base it
          // builds on; when idle the estimate equals the sensor, so show one line.
          const tankLabel  = document.getElementById('flow-tank-label');
          const sensorRow  = document.getElementById('flow-sensor-row');
          const sensorEl   = document.getElementById('flow-sensor-text');
          const fmtAge = (m) => (m === null || m === undefined) ? ''
            : (m < 1 ? 'just now' : m + 'm ago');
          const fmtGalIn = (gal, inches) => gal + ' gal'
            + ((inches !== null && inches !== undefined) ? ' / ' + inches + ' in' : '');
          if (d.tank_gallons !== null && d.tank_gallons !== undefined) {{
            tankRow.style.display = '';
            if (d.tank_is_estimate) {{
              tankLabel.textContent = 'Est. level:';
              tankEl.textContent = '~' + fmtGalIn(d.tank_gallons, d.tank_inches) + ' (filling)';
              // second line: the sensor reading the estimate is built on
              if (d.tank_base_gallons !== null && d.tank_base_gallons !== undefined) {{
                sensorRow.style.display = '';
                const a = fmtAge(d.tank_base_age_min);
                sensorEl.textContent = fmtGalIn(d.tank_base_gallons, d.tank_base_inches) + (a ? ' (' + a + ')' : '');
              }} else {{
                sensorRow.style.display = 'none';
              }}
            }} else {{
              tankLabel.textContent = 'Tank level:';
              // when stopped, show when the tank was last actually read
              const age = (d.tank_base_age_min !== null && d.tank_base_age_min !== undefined)
                ? d.tank_base_age_min : d.tank_reading_age_min;
              const a = fmtAge(age);
              tankEl.textContent = fmtGalIn(d.tank_gallons, d.tank_inches) + (a ? ' (read ' + a + ')' : '');
              sensorRow.style.display = 'none';
            }}
          }} else {{
            tankRow.style.display = 'none';
            sensorRow.style.display = 'none';
          }}

          updEl.textContent = 'Updated ' + new Date().toLocaleTimeString();

          if (flowing !== _lastFlowing) {{
            _lastFlowing = flowing;
            scheduleImg(flowing);
          }}
        }})
        .catch(() => {{
          document.getElementById('flow-updated').textContent = 'Flow status unavailable';
        }});
    }}

    updateFlow();
    setInterval(updateFlow, 10000);
    scheduleImg(false);
  </script>
</body>
</html>"""

    resp = Response(html, mimetype='text/html')
    resp.headers['Cache-Control'] = 'no-store'
    return resp
