"""
Public weather page — Flask blueprint.

Mounted at /weather for weather.onblackberryhill.com.
No authentication required.

Shows:
  - Current conditions from the Ambient Weather station (via snapshots.csv)
  - 10-day forecast from Open-Meteo (afternoon best-code to avoid marine-layer pessimism)
  - 12-month temperature + wind gust chart from all available snapshot archives
  - Latest sunset timelapse thumbnail linking to /timelapse, plus a Live camera link
"""

import csv
import gzip
import io
import json
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, Response

from monitor.config import (
    DEFAULT_SNAPSHOTS_FILE,
    AMBIENT_WEATHER_DASHBOARD_URL,
    NATIONAL_WEATHER_URL,
)
from monitor.weather_api import current_weather_desc, _WMO

weather_bp = Blueprint('weather', __name__)

_LAT = '44.6368'
_LON = '-124.0535'

_WMO_EMOJI = {
    0: '☀️', 1: '🌤️', 2: '⛅', 3: '☁️',
    45: '🌫️', 48: '🌫️',
    51: '🌦️', 53: '🌦️', 55: '🌧️',
    56: '🌨️', 57: '🌨️',
    61: '🌦️', 63: '🌧️', 65: '🌧️',
    66: '🌨️', 67: '🌨️',
    71: '🌨️', 73: '❄️', 75: '❄️', 77: '❄️',
    80: '🌦️', 81: '🌧️', 82: '⛈️',
    85: '🌨️', 86: '❄️',
    95: '⛈️', 96: '⛈️', 99: '⛈️',
}


def _current_conditions():
    """Return dict with curr_temp, curr_humid, curr_wind, curr_pressure, curr_ts."""
    result = {k: None for k in ('temp', 'humid', 'wind', 'pressure', 'ts')}
    try:
        with open(DEFAULT_SNAPSHOTS_FILE, 'r') as f:
            rows = list(csv.DictReader(f))
        if rows:
            row = rows[-1]
            for key, col in (('temp', 'outdoor_temp_f'), ('humid', 'outdoor_humidity'),
                              ('wind', 'wind_gust_mph'), ('pressure', 'baro_abs_inhg')):
                v = row.get(col, '')
                if v:
                    result[key] = float(v)
            ts_str = row.get('timestamp', '')
            if ts_str:
                result['ts'] = datetime.fromisoformat(ts_str).strftime('%-I:%M %p')
    except Exception:
        pass
    return result


def _fetch_tides():
    """Return tide curve + high/low events for next 24h from NOAA (Newport, OR)."""
    tz = ZoneInfo('America/Los_Angeles')
    now = datetime.now(tz)
    station = '9435380'
    begin = now.strftime('%Y%m%d')
    window_start = now
    window_end = now + timedelta(hours=24)

    result = {'labels': [], 'levels': [], 'hilo': [], 'now_level': None, 'now_pct': 0}
    curve_dts = []

    try:
        url = (
            f'https://api.tidesandcurrents.noaa.gov/api/prod/datagetter'
            f'?product=predictions&datum=MLLW&station={station}'
            f'&time_zone=lst_ldt&units=english&interval=6&format=json'
            f'&begin_date={begin}&range=48'
        )
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read())
        for p in data.get('predictions', []):
            dt = datetime.fromisoformat(p['t'])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            if window_start <= dt <= window_end:
                curve_dts.append(dt)
                result['labels'].append(dt.strftime('%H:%M'))
                result['levels'].append(round(float(p['v']), 1))
        if result['levels']:
            result['now_level'] = result['levels'][0]
            result['now_pct'] = 0
    except Exception:
        pass

    try:
        url = (
            f'https://api.tidesandcurrents.noaa.gov/api/prod/datagetter'
            f'?product=predictions&datum=MLLW&station={station}'
            f'&time_zone=lst_ldt&units=english&interval=hilo&format=json'
            f'&begin_date={begin}&range=48'
        )
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read())
        n = len(curve_dts)
        # Collect all events, split into past and future
        past, future = [], []
        for p in data.get('predictions', []):
            dt = datetime.fromisoformat(p['t'])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            entry = {
                'type': p['type'],
                'time': dt.strftime('%-I:%M %p'),
                'height': round(float(p['v']), 1),
                'dt': dt,
                'past': dt < now,
            }
            if n > 0:
                idx = min(range(n), key=lambda i: abs((curve_dts[i] - dt).total_seconds()))
                entry['idx'] = idx
                entry['pct'] = round(idx / max(n - 1, 1) * 100, 1)
            else:
                entry['idx'] = 0
                entry['pct'] = 0
            if dt < now:
                past.append(entry)
            else:
                future.append(entry)
        # 1 most recent past + 5 upcoming
        selected = past[-1:] + future[:5]
        for e in selected:
            del e['dt']
        result['hilo'] = selected
    except Exception:
        pass

    return result


def _temp_chart_data(sunrise_hm=None, sunset_hm=None):
    """Return JSON: 18h historical + 6h forecast temps, with sun positions."""
    tz = ZoneInfo('America/Los_Angeles')
    now = datetime.now(tz)
    cutoff = now - timedelta(hours=18)

    labels, hist = [], []
    try:
        with open(DEFAULT_SNAPSHOTS_FILE, 'r') as f:
            for row in csv.DictReader(f):
                try:
                    ts = datetime.fromisoformat(row['timestamp'])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=tz)
                    if ts >= cutoff:
                        t = row.get('outdoor_temp_f', '')
                        if t:
                            labels.append(ts.strftime('%H:%M'))
                            hist.append(round(float(t), 1))
                except Exception:
                    continue
    except Exception:
        pass

    now_idx = len(hist) - 1
    forecast = [None] * len(hist)
    if hist:
        forecast[-1] = hist[-1]

    try:
        url = (
            f'https://api.open-meteo.com/v1/forecast'
            f'?latitude={_LAT}&longitude={_LON}'
            '&hourly=temperature_2m'
            '&temperature_unit=fahrenheit'
            '&timezone=America%2FLos_Angeles'
            '&forecast_hours=8'
        )
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        fc_end = now + timedelta(hours=6)
        hourly = data.get('hourly', {})
        fc_hours = []
        for i, t_str in enumerate(hourly.get('time', [])):
            dt = datetime.fromisoformat(t_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            if now < dt <= fc_end:
                fc_hours.append((dt, hourly['temperature_2m'][i]))
        # Interpolate hourly forecast to 15-min intervals to match historical density
        for j in range(len(fc_hours)):
            t0, v0 = fc_hours[j]
            if j + 1 < len(fc_hours):
                t1, v1 = fc_hours[j + 1]
                for q in range(4):
                    dt_q = t0 + timedelta(minutes=15 * q)
                    val = round(v0 + (v1 - v0) * q / 4, 1)
                    labels.append(dt_q.strftime('%H:%M'))
                    hist.append(None)
                    forecast.append(val)
            else:
                labels.append(t0.strftime('%H:%M'))
                hist.append(None)
                forecast.append(round(v0, 1))
    except Exception:
        pass

    # Sun marker positions (% of the 24h span)
    total = 24 * 3600
    rise_pct = set_pct = None

    def _sun_pct(hm):
        if not hm:
            return None
        h, m = map(int, hm.split(':'))
        for day_offset in [-1, 0]:
            dt = (now + timedelta(days=day_offset)).replace(
                hour=h, minute=m, second=0, microsecond=0)
            elapsed = (dt - cutoff).total_seconds()
            if 0 < elapsed < total:
                return round(elapsed / total * 100, 1)
        return None

    rise_pct = _sun_pct(sunrise_hm)
    set_pct = _sun_pct(sunset_hm)

    return json.dumps({
        'labels': labels, 'hist': hist, 'forecast': forecast,
        'nowIdx': now_idx, 'risePct': rise_pct, 'setPct': set_pct,
    })


def _fetch_forecast():
    """
    Return list of day dicts from Open-Meteo 10-day forecast.

    Uses the best (lowest = clearest) hourly weather code between 10am–5pm
    rather than the daily code ("most severe of the day"), so marine-layer
    mornings don't make the whole day look overcast.
    """
    url = (
        f'https://api.open-meteo.com/v1/forecast'
        f'?latitude={_LAT}&longitude={_LON}'
        '&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,windspeed_10m_max,windgusts_10m_max,sunrise,sunset'
        '&hourly=weather_code'
        '&wind_speed_unit=mph&temperature_unit=fahrenheit'
        '&precipitation_unit=inch'
        '&timezone=America%2FLos_Angeles&forecast_days=10'
    )
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            fdata = json.loads(resp.read())
    except Exception:
        return [], None, None, None, None

    daily  = fdata.get('daily', {})
    hourly = fdata.get('hourly', {})
    fc_times  = daily.get('time', [])
    fc_tmax   = daily.get('temperature_2m_max', [])
    fc_tmin   = daily.get('temperature_2m_min', [])
    fc_precip = daily.get('precipitation_sum', [])
    fc_speeds = daily.get('windspeed_10m_max', [])
    fc_gusts  = daily.get('windgusts_10m_max', [])
    fc_rises  = daily.get('sunrise', [])
    fc_sets   = daily.get('sunset', [])

    # Collect best afternoon code (10am–5pm) per day from hourly data
    h_times  = hourly.get('time', [])
    h_codes  = hourly.get('weather_code', [])
    pm_codes = {}
    for j, t_str in enumerate(h_times):
        if j >= len(h_codes):
            break
        try:
            dt = datetime.fromisoformat(t_str)
            if 10 <= dt.hour <= 17:
                pm_codes.setdefault(dt.strftime('%Y-%m-%d'), []).append(int(h_codes[j]))
        except Exception:
            continue

    tz_local = ZoneInfo('America/Los_Angeles')
    today_d  = datetime.now(tz_local).date()

    # Extract today's sunrise / sunset from the first forecast slot (today)
    sunrise_today = sunset_today = None
    sunrise_hm = sunset_hm = None
    if fc_times and fc_times[0] == today_d.strftime('%Y-%m-%d'):
        try:
            sr = datetime.fromisoformat(fc_rises[0])
            ss = datetime.fromisoformat(fc_sets[0])
            sunrise_today = sr.strftime('%-I:%M %p')
            sunset_today  = ss.strftime('%-I:%M %p')
            sunrise_hm = sr.strftime('%H:%M')
            sunset_hm  = ss.strftime('%H:%M')
        except Exception:
            pass

    days = []
    for i, date_str in enumerate(fc_times[:10]):
        try:
            d = datetime.strptime(date_str, '%Y-%m-%d').date()
            if d == today_d:
                label = 'Today'
            elif d == today_d + timedelta(days=1):
                label = 'Tmrw'
            else:
                label = d.strftime('%a')
            codes     = pm_codes.get(date_str, [])
            code      = min(codes) if codes else 3
            precip_in = fc_precip[i] if i < len(fc_precip) and fc_precip[i] is not None else 0.0
            days.append({
                'label':  label,
                'emoji':  _WMO_EMOJI.get(code, '🌡️'),
                'desc':   _WMO.get(code, ''),
                'hi':     round(fc_tmax[i]) if i < len(fc_tmax) and fc_tmax[i] is not None else None,
                'lo':     round(fc_tmin[i]) if i < len(fc_tmin) and fc_tmin[i] is not None else None,
                'precip': round(precip_in, 2),
                'speed':  round(fc_speeds[i]) if i < len(fc_speeds) and fc_speeds[i] is not None else None,
                'gust':   round(fc_gusts[i])  if i < len(fc_gusts)  and fc_gusts[i]  is not None else None,
                'today':  d == today_d,
            })
        except Exception:
            continue
    return days, sunrise_today, sunset_today, sunrise_hm, sunset_hm


def _build_chart_data():
    """
    Return JSON string for Chart.js with a full 365-day spine.

    Days before the weather station started logging return null so the chart
    shows a blank gap rather than compressing history into fewer points.
    Reads all gzipped snapshot archives plus the live snapshots file.
    """
    day_data = {}
    data_dir = Path(DEFAULT_SNAPSHOTS_FILE).parent

    def _ingest(filepath, is_gz):
        try:
            if is_gz:
                f = io.TextIOWrapper(gzip.open(filepath, 'rb'), encoding='utf-8')
            else:
                f = open(filepath, 'r', encoding='utf-8')
            with f:
                for row in csv.DictReader(f):
                    try:
                        ts_dt = datetime.fromisoformat(row['timestamp'])
                        day   = ts_dt.strftime('%Y-%m-%d')
                        d     = day_data.setdefault(day, {'temps': [], 'winds': []})
                        t_val = row.get('outdoor_temp_f', '')
                        w_val = row.get('wind_gust_mph', '')
                        if t_val: d['temps'].append(float(t_val))
                        if w_val: d['winds'].append(float(w_val))
                    except Exception:
                        continue
        except Exception:
            pass

    for gz in sorted(data_dir.glob('snapshots-*.csv.gz')):
        _ingest(str(gz), True)
    _ingest(DEFAULT_SNAPSHOTS_FILE, False)

    today = datetime.now().date()
    labels, t_avg, t_min, t_max, w_max = [], [], [], [], []
    for offset in range(364, -1, -1):
        day   = (today - timedelta(days=offset)).strftime('%Y-%m-%d')
        label = (today - timedelta(days=offset)).strftime('%b %-d')
        labels.append(label)
        d = day_data.get(day)
        if d and d['temps']:
            t_avg.append(round(sum(d['temps']) / len(d['temps']), 1))
            t_min.append(round(min(d['temps']), 1))
            t_max.append(round(max(d['temps']), 1))
            w_max.append(round(max(d['winds']), 1) if d['winds'] else None)
        else:
            t_avg.append(None)
            t_min.append(None)
            t_max.append(None)
            w_max.append(None)

    return json.dumps({'labels': labels, 'tAvg': t_avg, 'tMin': t_min, 'tMax': t_max, 'wMax': w_max})


def _slideshow_images(n_recent=2, n_rated=13, min_score=660):
    """
    Return (url, date_label, iso_date) triples.

    Selects n_recent most recent snapshots plus n_rated randomly sampled from
    photos with clip_scores.json score >= min_score.  Falls back to human
    ratings.json averages, then fills with most-recent if scores unavailable.
    """
    import json as _json
    import random as _random
    snap_dir    = Path('/home/pi/timelapses/snapshots')
    clip_file   = Path('/home/pi/timelapses/clip_scores.json')
    human_file  = Path('/home/pi/timelapses/ratings.json')

    try:
        all_dates = sorted([p.stem for p in snap_dir.glob('*.jpg')], reverse=True)
    except Exception:
        return []

    available = set(all_dates)
    recent    = all_dates[:n_recent]
    excluded  = set(recent)

    # Build score map: prefer clip_scores.json, fall back to human ratings avg
    scores: dict = {}
    try:
        clip = _json.loads(clip_file.read_text())
        for date, v in clip.items():
            if date in available and isinstance(v, dict) and v.get('score') is not None:
                scores[date] = float(v['score'])
    except Exception:
        pass
    if not scores:
        try:
            human = _json.loads(human_file.read_text())
            for date, v in human.items():
                if date in available and isinstance(v, dict) and v.get('count', 0) > 0:
                    scores[date] = v['sum'] / v['count']
        except Exception:
            pass

    # Randomly sample n_rated from eligible (score >= min_score, not in recent)
    eligible = [d for d, s in scores.items() if s >= min_score and d not in excluded]
    _random.shuffle(eligible)
    rated_pick = eligible[:n_rated]

    # Fill remaining slots with next most-recent if not enough rated photos
    combined = list(recent) + rated_pick
    if len(rated_pick) < n_rated:
        seen = set(combined)
        for d in all_dates:
            if d not in seen:
                combined.append(d)
                seen.add(d)
                if len(combined) >= n_recent + n_rated:
                    break

    result = []
    for d in combined[:n_recent + n_rated]:
        label = datetime.strptime(d, '%Y-%m-%d').strftime('%b %-d')
        result.append((f'/timelapse/{d}/snapshot', label, d))
    return result


def _forecast_html(forecast_days):
    parts = []
    for fd in forecast_days:
        hi_s    = f"{fd['hi']}°"          if fd['hi']   is not None else '–'
        lo_s    = f"{fd['lo']}°"          if fd['lo']   is not None else '–'
        speed_s = f"{fd['speed']}" if fd['speed'] is not None else '–'
        gust_s  = f"{fd['gust']}"  if fd['gust']  is not None else '–'
        rain_s  = f"{fd['precip']:.2f}\"" if fd['precip'] and fd['precip'] >= 0.01 else ''
        cls     = ' fc-today' if fd['today'] else ''
        parts.append(
            f'<div class="fc-day{cls}">'
            f'<div class="fc-label">{fd["label"]}</div>'
            f'<div class="fc-icon" title="{fd["desc"]}">{fd["emoji"]}</div>'
            f'<div class="fc-temps"><span class="fc-hi">{hi_s}</span>'
            f' <span class="fc-lo">{lo_s}</span></div>'
            f'<div class="fc-wind">{speed_s} mph</div>'
            f'<div class="fc-wind fc-gust-line">{gust_s} gust</div>'
            + (f'<div class="fc-rain">💧{rain_s}</div>' if rain_s else '')
            + '</div>'
        )
    return ''.join(parts)


@weather_bp.route('/weather')
def weather_page():
    cond         = _current_conditions()
    weather_desc = ''
    try:
        weather_desc = current_weather_desc() or ''
    except Exception:
        pass
    forecast_days, sunrise_today, sunset_today, sunrise_hm, sunset_hm = _fetch_forecast()
    chart_json    = _build_chart_data()
    mini_json     = _temp_chart_data(sunrise_hm, sunset_hm)
    tides         = _fetch_tides()

    temp_str     = f'{cond["temp"]:.0f}'      if cond['temp']     is not None else '–'
    humid_str    = f'{cond["humid"]:.0f}'     if cond['humid']    is not None else '–'
    wind_str     = f'{cond["wind"]:.0f}'      if cond['wind']     is not None else '–'
    pressure_str = f'{cond["pressure"]:.2f}'  if cond['pressure'] is not None else '–'
    updated_str  = f'Updated {cond["ts"]}'    if cond['ts']       else ''
    desc_str     = weather_desc or '&nbsp;'
    fc_html      = _forecast_html(forecast_days)
    tide_beach_html = ''
    for hl in tides['hilo'][:6]:
        lbl = '▲ High' if hl['type'] == 'H' else '▼ Low'
        past_cls = ' beach-past' if hl.get('past') else ''
        tide_beach_html += (
            f'<div class="beach-event{past_cls}">'
            f'<div class="beach-event-type">{lbl}</div>'
            f'<div class="beach-event-ht">{hl["height"]}<span class="beach-unit"> ft</span></div>'
            f'<div class="beach-event-time">{hl["time"]}</div></div>'
        )
    now_level = f'{tides["now_level"]:.1f}' if tides.get('now_level') is not None else '–'

    rise_str     = sunrise_today or '–'
    set_str      = sunset_today  or '–'
    slide_pairs  = _slideshow_images()
    slides_html  = ''.join(
        f'<img class="hero-slide" src="{url}" data-date="{label}" data-iso="{iso}"'
        f' alt="Sunset at Blackberry Hill" loading="{"eager" if i == 0 else "lazy"}">'
        for i, (url, label, iso) in enumerate(slide_pairs)
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="1200">
  <title>Blackberry Hill Weather</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #0a1628;
      color: #e2e8f0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      min-height: 100vh;
      padding: 20px 16px 40px;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 20px;
    }}
    .site-header {{ text-align: center; padding-top: 8px; }}
    .site-name {{
      font-size: 0.72rem; font-weight: 700; letter-spacing: 0.18em;
      text-transform: uppercase; color: #38bdf8;
    }}
    .site-location {{ font-size: 0.85rem; color: #64748b; margin-top: 4px; }}
    .card {{
      background: #111d35; border-radius: 16px; width: 100%; max-width: 680px;
      overflow: hidden; box-shadow: 0 8px 32px rgba(0,0,0,0.5);
    }}
    .hero-wrap {{ position: relative; overflow: hidden; height: 260px; }}
    .hero-slide {{
      position: absolute; inset: 0;
      width: 100%; height: 100%;
      object-fit: cover; object-position: center 68%;
      opacity: 0; transition: opacity 1.4s ease-in-out;
      will-change: transform, opacity;
    }}
    .hero-slide.active {{ opacity: 1; }}
    @keyframes kb0 {{ from{{transform:scale(1) translate(0%,0%)}}    to{{transform:scale(1.09) translate(-2%,-1%)}} }}
    @keyframes kb1 {{ from{{transform:scale(1) translate(2%,0%)}}    to{{transform:scale(1.09) translate(0%, 1.5%)}} }}
    @keyframes kb2 {{ from{{transform:scale(1) translate(-1%,1.5%)}} to{{transform:scale(1.09) translate(1.5%,-0.5%)}} }}
    @keyframes kb3 {{ from{{transform:scale(1) translate(0%,1.5%)}}  to{{transform:scale(1.09) translate(-1.5%,-0.5%)}} }}
    .hero-badge {{
      background: rgba(0,0,0,0.55); color: #94a3b8;
      font-size: 0.7rem; padding: 3px 8px; border-radius: 6px;
      text-decoration: none; white-space: nowrap;
    }}
    .hero-badge:hover {{ color: #e2e8f0; }}
    .hero-badge-right {{ position: absolute; left: 12px; top: 10px; }}
    .hero-badges-left {{
      position: absolute; left: 12px; bottom: 10px;
      display: flex; align-items: center; gap: 6px;
    }}
    .conditions {{ padding: 22px 24px 24px; }}
    .conditions-desc {{
      font-size: 0.9rem; color: #94a3b8; text-align: center;
      margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.06em;
    }}
    .temp-row {{
      display: flex; align-items: center; gap: 12px; margin-bottom: 14px;
      min-width: 0;
    }}
    .temp-spark {{
      flex: 1; min-width: 0; position: relative; height: 90px;
    }}
    .temp-now {{
      text-align: center; flex-shrink: 0;
    }}
    .temp-now-val {{
      font-size: 3.2rem; font-weight: 200; color: #f1f5f9; line-height: 1;
    }}
    .temp-now-unit {{ font-size: 1.2rem; color: #94a3b8; vertical-align: super; }}
    .temp-now-updated {{
      font-size: 0.62rem; color: #475569; margin-top: 6px;
    }}
    .stats-grid {{
      display: grid; grid-template-columns: repeat(4, 1fr);
      gap: 1px; background: #1e3058; border-radius: 10px; overflow: hidden;
    }}
    .stat {{ background: #0d1b30; padding: 12px 6px; text-align: center; }}
    .stat-value {{ font-size: 1.3rem; font-weight: 300; color: #e2e8f0; }}
    .stat-unit {{ font-size: 0.75rem; color: #64748b; }}
    .stat-label {{
      font-size: 0.65rem; color: #475569; text-transform: uppercase;
      letter-spacing: 0.08em; margin-top: 4px;
    }}
    .sun-times {{ font-size: 1rem; line-height: 1.6; }}
    .updated {{ text-align: center; font-size: 0.72rem; color: #334155; margin-top: 18px; }}
    .fc-card {{
      background: #111d35; border-radius: 16px; width: 100%; max-width: 680px;
      padding: 18px 16px 20px; box-shadow: 0 8px 32px rgba(0,0,0,0.5);
    }}
    .fc-title {{
      font-size: 0.72rem; font-weight: 700; letter-spacing: 0.14em;
      text-transform: uppercase; color: #475569; margin-bottom: 14px;
    }}
    .fc-scroll {{
      display: flex; gap: 8px; overflow-x: auto;
      padding-bottom: 4px; scrollbar-width: thin; scrollbar-color: #1e3058 transparent;
    }}
    .fc-day {{
      flex: 0 0 auto; min-width: 70px; background: #0d1b30;
      border-radius: 10px; padding: 10px 6px;
      text-align: center; border: 1px solid #1e3058;
    }}
    .fc-today {{ border-color: #38bdf8; background: #0f2340; }}
    .fc-label {{ font-size: 0.72rem; color: #64748b; font-weight: 600; margin-bottom: 6px; }}
    .fc-today .fc-label {{ color: #38bdf8; }}
    .fc-icon {{ font-size: 1.6rem; margin-bottom: 6px; line-height: 1; }}
    .fc-temps {{ font-size: 0.82rem; margin-bottom: 5px; }}
    .fc-hi {{ color: #f97316; font-weight: 600; }}
    .fc-lo {{ color: #64748b; }}
    .fc-wind {{ font-size: 0.75rem; color: #94a3b8; line-height: 1.5; }}
    .fc-gust-line {{ color: #7dd3fc; }}
    .fc-rain {{ font-size: 0.68rem; color: #38bdf8; margin-top: 3px; }}
    .chart-card {{
      background: #111d35; border-radius: 16px; width: 100%; max-width: 680px;
      padding: 20px 20px 24px; box-shadow: 0 8px 32px rgba(0,0,0,0.5);
    }}
    .chart-title {{
      font-size: 0.72rem; font-weight: 700; letter-spacing: 0.14em;
      text-transform: uppercase; color: #475569; margin-bottom: 16px;
    }}
    .chart-wrap {{ position: relative; height: 220px; }}
    .rent-cta {{
      width: 100%; max-width: 680px;
      background: #111d35; border-radius: 16px;
      padding: 18px 24px;
      display: flex; align-items: center; justify-content: space-between; gap: 16px;
      box-shadow: 0 8px 32px rgba(0,0,0,0.5);
    }}
    .rent-text {{ font-size: 0.85rem; color: #94a3b8; }}
    .rent-text strong {{ color: #e2e8f0; font-weight: 500; }}
    .rent-btn {{
      background: #1d4ed8; color: #e2e8f0;
      font-size: 0.8rem; font-weight: 600; letter-spacing: 0.04em;
      padding: 9px 18px; border-radius: 8px;
      text-decoration: none; white-space: nowrap; flex-shrink: 0;
    }}
    .rent-btn:hover {{ background: #2563eb; color: #fff; }}
    footer {{
      font-size: 0.78rem; color: #334155;
      display: flex; gap: 12px; flex-wrap: wrap; justify-content: center;
    }}
    footer a {{ color: #475569; text-decoration: none; }}
    footer a:hover {{ color: #64748b; text-decoration: underline; }}
    /* --- Tide beach strip --- */
    .tide-beach-strip {{
      border-radius: 10px; overflow: hidden; margin-bottom: 16px;
    }}
    .tide-beach {{
      position: relative; overflow: hidden; border-radius: 12px;
      height: 110px; display: flex; align-items: center; justify-content: space-around;
    }}
    .tide-beach-bg {{
      position: absolute; inset: 0;
      background: url('/static/moolack_beach.jpeg') center 38% / cover no-repeat;
    }}
    .tide-beach-overlay {{
      position: absolute; inset: 0;
      background: linear-gradient(90deg,
        rgba(10,22,40,0.78) 0%,
        rgba(10,22,40,0.55) 50%,
        rgba(10,22,40,0.78) 100%);
    }}
    .beach-event {{
      position: relative; z-index: 1; text-align: center; color: #e2e8f0;
    }}
    .beach-event-type {{
      font-size: 0.6rem; text-transform: uppercase; letter-spacing: 0.08em;
      color: rgba(255,255,255,0.55); font-weight: 600;
    }}
    .beach-event-ht {{ font-size: 1.5rem; font-weight: 200; text-shadow: 0 1px 4px rgba(0,0,0,0.5); }}
    .beach-event-ht .beach-unit {{ font-size: 0.7rem; color: rgba(255,255,255,0.5); }}
    .beach-event-time {{ font-size: 0.68rem; color: rgba(255,255,255,0.6); }}
    .beach-past {{ opacity: 0.45; }}
    .tide-beach-header {{
      display: flex; justify-content: space-between; align-items: center;
      padding: 10px 16px 6px; background: #111d35;
    }}
    .tide-beach-name {{
      font-size: 0.72rem; font-weight: 700; letter-spacing: 0.14em;
      text-transform: uppercase; color: #475569;
    }}
    .tide-beach-now {{
      font-size: 0.78rem; color: #94a3b8;
    }}
  </style>
</head>
<body>
  <header class="site-header">
    <div class="site-name">Blackberry Hill</div>
    <div class="site-location">Newport, Oregon</div>
  </header>

  <div class="card">
    <div class="hero-wrap" id="heroWrap">
      {slides_html}
      <div class="hero-badges-left">
        <a class="hero-badge" href="#" id="liveBtn">Live</a>
        <a class="hero-badge" id="heroDate" href="#"></a>
      </div>
    </div>
    <div class="conditions">
      <div class="conditions-desc">{desc_str}</div>
      <div class="temp-row">
        <div class="temp-spark">
          <canvas id="tempMini"></canvas>
        </div>
        <div class="temp-now">
          <div class="temp-now-val">{temp_str}<span class="temp-now-unit">&deg;</span></div>
          <div class="temp-now-updated">{updated_str}</div>
        </div>
      </div>
      <div class="tide-beach-strip">
        <div class="tide-beach-header">
          <span class="tide-beach-name">Moolack Beach</span>
          <span class="tide-beach-now">{now_level} ft now</span>
        </div>
        <div class="tide-beach">
          <div class="tide-beach-bg"></div>
          <div class="tide-beach-overlay"></div>
          {tide_beach_html}
        </div>
      </div>
      <div class="stats-grid">
        <div class="stat">
          <div class="stat-value">{humid_str}<span class="stat-unit">%</span></div>
          <div class="stat-label">Humidity</div>
        </div>
        <div class="stat">
          <div class="stat-value">{wind_str}<span class="stat-unit"> mph</span></div>
          <div class="stat-label">Wind Gust</div>
        </div>
        <div class="stat">
          <div class="stat-value">{pressure_str}<span class="stat-unit"> inHg</span></div>
          <div class="stat-label">Pressure</div>
        </div>
        <div class="stat">
          <div class="stat-value sun-times">
            <div>{rise_str}</div>
            <div>{set_str}</div>
          </div>
          <div class="stat-label">Rise &amp; Set</div>
        </div>
      </div>
      <div class="updated">Ambient Weather station</div>
    </div>
  </div>

  <div class="fc-card">
    <div class="fc-title">10-Day Forecast</div>
    <div class="fc-scroll">{fc_html}</div>
  </div>

  <div class="chart-card">
    <div class="chart-title">12-Month Temperature &amp; Wind Gusts</div>
    <div class="chart-wrap">
      <canvas id="wxChart"></canvas>
    </div>
  </div>

  <div class="rent-cta">
    <div class="rent-text"><strong>Blackberry Hill</strong> is a vacation rental on the Oregon coast — ocean views, sunsets, and stargazing.</div>
    <a class="rent-btn" href="https://www.meredithlodging.com/listings/1830" target="_blank" rel="noopener">Check Availability</a>
  </div>

  <footer>
    <a href="/timelapse">Sunset Timelapses</a>
    <span>&middot;</span>
    <a href="{NATIONAL_WEATHER_URL}" target="_blank" rel="noopener">NWS Newport</a>
    <span>&middot;</span>
    <a href="{AMBIENT_WEATHER_DASHBOARD_URL}" target="_blank" rel="noopener">Weather Station</a>
  </footer>

  <script>
  (function() {{
    var slides = Array.from(document.querySelectorAll('.hero-slide'));
    if (!slides.length) return;
    var kbs    = ['kb0','kb1','kb2','kb3'];
    var dateEl = document.getElementById('heroDate');
    var liveBtn = document.getElementById('liveBtn');
    var wrap    = document.getElementById('heroWrap');
    var cur = 0, prev = -1;
    var timer = null, liveMode = false, liveEl = null;

    function show(i) {{
      var el = slides[i];
      el.style.animation = 'none';
      void el.offsetWidth;
      el.style.animation = kbs[i % kbs.length] + ' 7s ease-out forwards';
      el.classList.add('active');
      if (dateEl) {{
        dateEl.textContent = el.dataset.date || '';
        dateEl.href = el.dataset.iso ? '/timelapse/' + el.dataset.iso : '#';
      }}
    }}
    function advance() {{
      if (liveMode) return;
      prev = cur;
      cur = (cur + 1) % slides.length;
      show(cur);
      var p = prev;
      setTimeout(function() {{ slides[p].classList.remove('active'); }}, 1500);
    }}
    function startSlideshow() {{
      if (timer) clearInterval(timer);
      timer = setInterval(advance, 7000);
    }}
    function exitLive() {{
      liveMode = false;
      if (liveEl) {{
        liveEl.classList.remove('active');
        setTimeout(function() {{
          if (liveEl && liveEl.parentNode) liveEl.parentNode.removeChild(liveEl);
          liveEl = null;
        }}, 1500);
      }}
      if (liveBtn) liveBtn.textContent = 'Live';
      show(cur);
      startSlideshow();
    }}

    show(0);
    startSlideshow();

    if (liveBtn) {{
      liveBtn.addEventListener('click', function(e) {{
        e.preventDefault();
        if (liveMode) {{ exitLive(); return; }}
        liveMode = true;
        if (timer) clearInterval(timer);
        liveBtn.textContent = 'Live ●';
        // Hide current slide
        slides.forEach(function(s) {{ s.classList.remove('active'); }});
        // Create live image
        liveEl = document.createElement('img');
        liveEl.className = 'hero-slide';
        liveEl.src = '/snapshot?info=0&_t=' + Date.now();
        liveEl.alt = 'Live camera';
        wrap.insertBefore(liveEl, wrap.firstChild);
        liveEl.onload = function() {{
          liveEl.style.animation = 'none';
          void liveEl.offsetWidth;
          liveEl.style.animation = 'kb0 30s ease-out forwards';
          liveEl.classList.add('active');
          if (dateEl) {{
            var n = new Date();
            var h = n.getHours(), m = n.getMinutes();
            var ampm = h >= 12 ? 'PM' : 'AM';
            h = h % 12 || 12;
            dateEl.textContent = h + ':' + (m < 10 ? '0' : '') + m + ' ' + ampm;
            dateEl.removeAttribute('href');
          }}
          setTimeout(exitLive, 30000);
        }};
        liveEl.onerror = function() {{ exitLive(); }};
      }});
    }}
  }})();
  </script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <script>
  (function() {{
    var mini = {mini_json};
    if (!mini.hist.length) return;
    var sparkCtx = document.getElementById('tempMini').getContext('2d');
    var tempLabelsPlugin = {{
      id: 'tempLabels',
      afterDraw: function(chart) {{
        var ctx = chart.ctx;
        var xs = chart.scales.x, ys = chart.scales.y;
        var hd = mini.hist;
        var minV = Infinity, maxV = -Infinity, minI, maxI;
        for (var i = 0; i < hd.length; i++) {{
          if (hd[i] === null) continue;
          if (hd[i] < minV) {{ minV = hd[i]; minI = i; }}
          if (hd[i] > maxV) {{ maxV = hd[i]; maxI = i; }}
        }}
        var meta = chart.getDatasetMeta(0);
        function lbl(idx, text, color, above) {{
          var pt = meta.data[idx];
          if (!pt) return;
          ctx.save();
          ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, sans-serif';
          ctx.fillStyle = color;
          // Clamp near edges so text doesn't clip
          var tx = pt.x;
          if (tx < xs.left + 20) ctx.textAlign = 'left';
          else if (tx > xs.right - 20) ctx.textAlign = 'right';
          else ctx.textAlign = 'center';
          ctx.fillText(text, tx, pt.y + (above ? -9 : 15));
          ctx.restore();
        }}
        if (maxV - minV >= 2) {{
          lbl(minI, minV + '°', '#60a5fa', false);
          lbl(maxI, maxV + '°', '#ef4444', true);
        }}
        // Sunrise / sunset icons at top of chart
        function sunIcon(pct, emoji) {{
          if (pct === null) return;
          var x = xs.left + (xs.right - xs.left) * pct / 100;
          ctx.save();
          ctx.font = '16px sans-serif';
          ctx.textAlign = 'center';
          ctx.fillText(emoji, x, ys.top - 3);
          ctx.restore();
        }}
        sunIcon(mini.risePct, '☀️');
        sunIcon(mini.setPct, '🌙');
      }}
    }};
    new Chart(sparkCtx, {{
      type: 'line',
      data: {{
        labels: mini.labels,
        datasets: [
          {{
            data: mini.hist,
            borderColor: '#f97316',
            backgroundColor: 'rgba(249,115,22,0.10)',
            fill: true,
            pointRadius: 0,
            borderWidth: 1.5,
            tension: 0.35,
            spanGaps: false,
          }},
          {{
            data: mini.forecast,
            borderColor: '#f97316',
            backgroundColor: 'rgba(249,115,22,0.05)',
            fill: true,
            pointRadius: 0,
            borderWidth: 1.5,
            borderDash: [5, 3],
            tension: 0.35,
            spanGaps: false,
          }},
        ],
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        layout: {{ padding: {{ top: 22 }} }},
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{
            backgroundColor: '#1e3058', titleColor: '#94a3b8',
            bodyColor: '#e2e8f0', borderColor: '#2d4a7a', borderWidth: 1,
            callbacks: {{
              title: function(items) {{
                var lbl = items[0].label || '';
                var h = parseInt(lbl.split(':')[0]);
                var m = lbl.split(':')[1] || '00';
                return (h === 0 ? '12' : h > 12 ? h-12 : h) + ':' + m + (h >= 12 ? ' PM' : ' AM');
              }},
              label: function(c) {{
                if (c.datasetIndex === 1) return ' ' + c.parsed.y + '°F (forecast)';
                return ' ' + c.parsed.y + '°F';
              }},
            }},
          }},
        }},
        scales: {{
          x: {{
            display: true,
            ticks: {{
              color: '#94a3b8', font: {{ size: 10 }}, maxRotation: 0,
              autoSkip: false,
              callback: function(val, idx) {{
                var lbl = mini.labels[idx] || '';
                var parts = lbl.split(':');
                if (parts[1] !== '00') return '';
                var h = parseInt(parts[0]);
                if (h % 3 !== 0) return '';
                return h === 0 ? '12a' : h === 12 ? '12p' : h > 12 ? (h-12)+'p' : h+'a';
              }},
            }},
            grid: {{ display: false }},
          }},
          y: {{ display: false }},
        }},
      }},
      plugins: [tempLabelsPlugin],
    }});
  }})();
  </script>
  <script>
  (function() {{
    var raw = {chart_json};
    var ctx = document.getElementById('wxChart').getContext('2d');
    new Chart(ctx, {{
      type: 'line',
      data: {{
        labels: raw.labels,
        datasets: [
          {{
            label: 'Temp range',
            data: raw.tMax,
            borderColor: 'transparent',
            backgroundColor: 'rgba(249,115,22,0.15)',
            fill: '+1',
            pointRadius: 0,
            tension: 0.3,
          }},
          {{
            label: '_tMin',
            data: raw.tMin,
            borderColor: 'transparent',
            backgroundColor: 'transparent',
            fill: false,
            pointRadius: 0,
            tension: 0.3,
          }},
          {{
            label: 'Avg Temp (°F)',
            data: raw.tAvg,
            borderColor: '#f97316',
            backgroundColor: 'transparent',
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.3,
            yAxisID: 'yTemp',
          }},
          {{
            label: 'Max Wind Gust (mph)',
            data: raw.wMax,
            borderColor: '#38bdf8',
            backgroundColor: 'transparent',
            borderWidth: 1.5,
            borderDash: [4, 3],
            pointRadius: 0,
            tension: 0.3,
            yAxisID: 'yWind',
          }},
        ],
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        interaction: {{ mode: 'index', intersect: false }},
        plugins: {{
          legend: {{
            display: true,
            labels: {{
              color: '#64748b', boxWidth: 12, font: {{ size: 11 }},
              filter: function(item) {{
                return !item.text.startsWith('_') && item.text !== 'Temp range';
              }},
            }},
          }},
          tooltip: {{
            backgroundColor: '#1e3058', titleColor: '#94a3b8',
            bodyColor: '#e2e8f0', borderColor: '#2d4a7a', borderWidth: 1,
            callbacks: {{
              label: function(c) {{
                if (c.dataset.label.startsWith('_') || c.dataset.label === 'Temp range') return null;
                var i = c.dataIndex;
                if (c.dataset.yAxisID === 'yTemp') {{
                  return ' ' + raw.tMin[i] + '–' + raw.tMax[i] + '°F (avg ' + c.parsed.y + '°)';
                }}
                return ' Gusts: ' + c.parsed.y + ' mph';
              }},
            }},
          }},
        }},
        scales: {{
          x: {{
            ticks: {{
              color: '#475569', maxRotation: 0, font: {{ size: 10 }},
              autoSkip: false,
              callback: function(val, index) {{
                var label = raw.labels[index] || '';
                // label format is "Mon D" (e.g. "Jun 1", "Jul 1").
                // Show only the month name on the 1st of each month.
                return label.endsWith(' 1') ? label.slice(0, 3) : '';
              }},
            }},
            grid: {{ color: 'rgba(255,255,255,0.04)' }},
          }},
          yTemp: {{
            type: 'linear', position: 'left',
            ticks: {{ color: '#f97316', font: {{ size: 10 }}, callback: function(v) {{ return v + '°'; }} }},
            grid: {{ color: 'rgba(255,255,255,0.04)' }},
          }},
          yWind: {{
            type: 'linear', position: 'right', min: 0,
            ticks: {{ color: '#38bdf8', font: {{ size: 10 }}, callback: function(v) {{ return v + ' mph'; }} }},
            grid: {{ drawOnChartArea: false }},
          }},
        }},
      }},
    }});
  }})();
  </script>
</body>
</html>"""

    resp = Response(html, mimetype='text/html')
    resp.headers['Cache-Control'] = 'public, max-age=300, stale-if-error=172800'
    return resp
