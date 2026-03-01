#!/usr/bin/env python3
"""
Timelapse and snapshot routes for the pumphouse web dashboard.
Extracted from web.py as a Flask Blueprint.
"""
import os

from flask import Blueprint, Response, request, redirect, send_file
from monitor.config import CAMERA_USER, CAMERA_PASS
from monitor.weather_api import _WMO, _QUIPS, current_weather_desc

timelapse_bp = Blueprint('timelapse', __name__)

SNAPSHOT_CROP_BOTTOM = 120   # keep in sync with sunset_timelapse.py CROP_BOTTOM
SNAPSHOT_CAMERA_IP   = '192.168.1.81'
SNAPSHOT_CAMERA_PORT = 554


@timelapse_bp.route('/frame')
@timelapse_bp.route('/snapshot')
def snapshot():
    """
    Return a live camera frame, with an HTML weather-info wrapper by default.

    If a timelapse is currently being recorded (frames exist in
    /tmp/timelapse-frames/YYYY-MM-DD/) the most recent completed frame is
    served directly instead of opening a competing RTSP connection.

    Query params:
        info - 0 = return raw JPEG only (no HTML wrapper); default shows info page
        crop - 1 = apply SNAPSHOT_CROP_BOTTOM pixels from bottom; ignored when
                   serving a timelapse frame (already cropped at capture time)
    """
    import subprocess
    import base64 as _base64
    from datetime import date as _date, datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    from pathlib import Path as _Path

    info = request.args.get('info', 1, type=int)
    crop = request.args.get('crop', 0, type=int)

    # ------------------------------------------------------------------
    # Frame acquisition: prefer in-progress timelapse frames to avoid
    # fighting the timelapse daemon for the RTSP stream.
    # ------------------------------------------------------------------
    today     = _date.today()
    date_str  = today.isoformat()
    frames_dir = _Path('/tmp/timelapse-frames') / date_str
    jpeg_bytes = None
    from_timelapse = False

    if frames_dir.exists():
        frames = sorted(frames_dir.glob('frame_*.jpg'))
        # Skip the last frame — it may still be mid-write by ffmpeg
        candidate = frames[-2] if len(frames) >= 2 else (frames[0] if frames else None)
        if candidate:
            try:
                jpeg_bytes = candidate.read_bytes()
                from_timelapse = True
            except Exception:
                pass  # fall through to RTSP grab

    if jpeg_bytes is None:
        # Live RTSP grab
        rtsp = (f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{SNAPSHOT_CAMERA_IP}:{SNAPSHOT_CAMERA_PORT}'
                f'/cam/realmonitor?channel=1&subtype=0')
        vf = f'crop=iw:ih-{SNAPSHOT_CROP_BOTTOM}:0:0' if (crop and SNAPSHOT_CROP_BOTTOM) else None
        cmd = [
            'ffmpeg', '-y',
            '-rtsp_transport', 'tcp',
            '-i', rtsp,
            '-vframes', '1',
            '-f', 'image2pipe',
            '-vcodec', 'mjpeg',
        ]
        if vf:
            cmd += ['-vf', vf]
        cmd.append('pipe:1')

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=15)
            if result.returncode != 0 or not result.stdout:
                return Response('Frame grab failed', status=503)
            jpeg_bytes = result.stdout
        except subprocess.TimeoutExpired:
            return Response('Camera timeout', status=503)
        except Exception as e:
            return Response(f'Error: {e}', status=503)

    if info == 0:
        return Response(jpeg_bytes, status=200, mimetype='image/jpeg')

    # --- HTML page with weather panel ---
    try:
        title_date = today.strftime('%A, %B %-d, %Y')
    except Exception:
        title_date = date_str

    tz      = _ZI('America/Los_Angeles')
    now_str = _dt.now(tz).strftime('%-I:%M %p')
    src_note = 'from timelapse' if from_timelapse else 'live grab'
    img_b64 = _base64.b64encode(jpeg_bytes).decode()

    # Weather data
    wx = _day_weather_summary(date_str)
    om = _open_meteo_weather(date_str)
    cc = _current_conditions()

    # Sunrise / sunset from astral (reliable for today; archive API lags)
    sunrise_str = sunset_str = None
    try:
        from astral import LocationInfo as _LI
        from astral.sun import sun as _sun
        _loc = _LI('Newport, OR', 'Oregon', 'America/Los_Angeles', 44.6368, -124.0535)
        _st  = _sun(_loc.observer, date=today, tzinfo=tz)
        sunrise_str = _st['sunrise'].strftime('%-I:%M %p')
        sunset_str  = _st['sunset'].strftime('%-I:%M %p')
    except Exception:
        sunrise_str = om.get('sunrise') if om else None
        sunset_str  = (wx.get('sunset') if wx else None) or (om.get('sunset') if om else None)

    def stat(label, val, unit=''):
        if val is None:
            return ''
        return (f'<div class="stat"><span class="lbl">{label}</span>'
                f'<span class="val">{val}{unit}</span></div>')

    # Weather description: prefer real-time current conditions over daily summary
    # so the description is consistent with the current cloud % shown below it.
    desc_html = ''
    _desc = cc.get('weather_desc') or (om.get('weather_desc') if om else None)
    if _desc:
        import random as _random
        _opts      = _QUIPS.get(_desc.lower(), [])
        _quip      = _random.choice(_opts) if _opts else ''
        _quip_html = f' <span class="wx-quip">{_quip}</span>' if _quip else ''
        desc_html  = f'<div class="wx-desc">{_desc}{_quip_html}</div>'

    wind_label = cc.get('wind_label', 'Wind')
    humidity   = (wx.get('humidity_avg') if wx else None) or (om.get('humidity') if om else None)

    wx_html = f"""
        <div class="weather">
          {desc_html}
          <div class="wx-group">
            {stat('Now',     now_str,    '')}
            {stat('Sunrise', sunrise_str,'')}
            {stat('Sunset',  sunset_str, '')}
          </div>
          <div class="wx-group">
            {stat(wind_label, cc.get('wind'),  ' mph')}
            {stat('Cloud',    cc.get('cloud'), '%')}
            {stat('Humidity', humidity,        '%')}
          </div>
        </div>"""

    # Most-recent timelapse date for the "Timelapses" link
    tl_dates  = _timelapse_dates()
    tl_link   = f'/timelapse/{tl_dates[-1]}' if tl_dates else '/timelapse'

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Snapshot &mdash; {title_date} &mdash; {now_str}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: monospace; background:#1a1a1a; color:#e0e0e0;
           margin:0; padding:16px; }}
    h2   {{ margin:0; color:#fff; }}
    .site-header {{ max-width:960px; padding:4px 0 10px; margin-bottom:4px;
                    border-bottom:1px solid #333; display:flex;
                    flex-wrap:wrap; align-items:baseline; gap:0 10px; }}
    .site-name {{ font-size:1.2em; color:#fff; font-weight:bold; }}
    .site-sub  {{ font-size:0.85em; color:#888; }}
    .site-sub a {{ color:#4CAF50; text-decoration:none; }}
    .site-sub a:hover {{ color:#fff; }}
    .page-header {{ max-width:960px; display:flex; justify-content:center;
                    align-items:center; margin:12px 0; }}
    .snapshot-wrap {{ max-width:860px; margin:12px 0; }}
    .snapshot-wrap img {{ width:100%; display:block; border-radius:4px;
                          cursor:pointer; }}
    .snapshot-wrap img:hover {{ opacity:0.92; }}
    .weather {{ max-width:860px; background:#222; border:1px solid #333;
                border-radius:4px; padding:12px 16px; margin:12px 0;
                display:flex; flex-direction:column; gap:8px; }}
    .wx-desc  {{ font-size:1.05em; color:#aed6f1; font-weight:bold; }}
    .wx-quip  {{ font-size:0.9em; color:#888; font-weight:normal; font-style:italic; }}
    .wx-group {{ display:flex; flex-wrap:wrap; gap:12px; }}
    .stat {{ display:flex; flex-direction:column; min-width:80px; }}
    .lbl  {{ font-size:0.75em; color:#888; text-transform:uppercase; }}
    .val  {{ font-size:1.1em; color:#e0e0e0; }}
    .actions {{ max-width:860px; display:flex; flex-wrap:wrap;
                align-items:center; gap:10px; margin:10px 0; }}
    .btn {{ background:#2a2a2a; color:#4CAF50; border:1px solid #444;
            padding:6px 16px; border-radius:4px; text-decoration:none;
            font-family:monospace; font-size:0.95em; cursor:pointer; }}
    .btn:hover {{ background:#333; color:#fff; }}
    .captured-at {{ color:#666; font-size:0.85em; }}
    @media (max-width:600px) {{
      .page-header h2 {{ font-size:1.0em; }}
      .site-sub {{ font-size:0.78em; }}
    }}
  </style>
</head>
<body>
  <header class="site-header">
    <span class="site-name">On Blackberry Hill</span>
    <span class="site-sub">Newport, OR &middot; Available via
      <a href="https://www.meredithlodging.com/listings/1830" target="_blank" rel="noopener">Meredith</a>
      &middot; <a href="https://www.airbnb.com/rooms/894278114876445404" target="_blank" rel="noopener">Airbnb</a>
      &middot; <a href="https://www.vrbo.com/9829179ha" target="_blank" rel="noopener">Vrbo</a>
    </span>
  </header>
  <div class="page-header">
    <h2>Snapshot &mdash; {title_date} &mdash; {now_str}</h2>
  </div>
  <div class="snapshot-wrap">
    <a href="/snapshot?info=0" target="_blank" title="Open raw image">
      <img src="data:image/jpeg;base64,{img_b64}" alt="Live snapshot">
    </a>
  </div>
  {wx_html}
  <div class="actions">
    <button class="btn" onclick="location.reload()">&#8635; New Snapshot</button>
    <a class="btn" href="{tl_link}">Timelapses</a>
    <a class="btn" href="/">Dashboard</a>
    <a class="btn" href="data:image/jpeg;base64,{img_b64}" download="snapshot-{date_str}.jpg">&#8681; Download</a>
    <span class="captured-at">Captured {now_str} &middot; {src_note}</span>
  </div>
</body>
</html>"""

    return Response(html, status=200, mimetype='text/html')


TIMELAPSE_DIR     = '/home/pi/timelapses'
WEATHER_CACHE_DIR = os.path.join(TIMELAPSE_DIR, 'weather')
RATINGS_FILE      = os.path.join(TIMELAPSE_DIR, 'ratings.json')
SNAPSHOT_DIR      = os.path.join(TIMELAPSE_DIR, 'snapshots')
THUMB_WIDTH       = 240   # px — thumbnail width in the "All timelapses" list (height auto-computed 16:9)

import threading as _threading
_ratings_lock = _threading.Lock()

def _load_cf_config():
    """Load Cloudflare credentials from secrets.conf."""
    import os as _os
    secrets = _os.path.join(_os.path.expanduser('~'), '.config', 'pumphouse', 'secrets.conf')
    cfg = {}
    try:
        with open(secrets) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                cfg[k.strip()] = v.strip()
    except Exception:
        pass
    return cfg

_CF_CONFIG = _load_cf_config()
_RATINGS_BACKEND = _CF_CONFIG.get('RATINGS_BACKEND', 'local')


def _kv_write_rating(date_str, entry):
    """Write a single rating entry to Cloudflare KV. Returns True on success."""
    import json as _j, urllib.request as _ureq
    account_id   = _CF_CONFIG.get('CLOUDFLARE_ACCOUNT_ID', '')
    namespace_id = _CF_CONFIG.get('CLOUDFLARE_KV_NAMESPACE_ID', '')
    api_token    = _CF_CONFIG.get('CLOUDFLARE_KV_API_TOKEN', '')
    if not all([account_id, namespace_id, api_token]):
        return False
    url = (f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
           f"/storage/kv/namespaces/{namespace_id}/values/{date_str}")
    req = _ureq.Request(url, data=_j.dumps(entry).encode(), method='PUT',
                        headers={'Authorization': f'Bearer {api_token}',
                                 'Content-Type': 'application/json'})
    try:
        with _ureq.urlopen(req, timeout=5) as r:
            return r.status in (200, 201)
    except Exception:
        return False


def _kv_read_rating(date_str):
    """Read a single rating entry from Cloudflare KV. Returns dict or None on error."""
    import json as _j, urllib.request as _ureq, urllib.error as _uerr
    account_id   = _CF_CONFIG.get('CLOUDFLARE_ACCOUNT_ID', '')
    namespace_id = _CF_CONFIG.get('CLOUDFLARE_KV_NAMESPACE_ID', '')
    api_token    = _CF_CONFIG.get('CLOUDFLARE_KV_API_TOKEN', '')
    if not all([account_id, namespace_id, api_token]):
        return None
    url = (f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
           f"/storage/kv/namespaces/{namespace_id}/values/{date_str}")
    req = _ureq.Request(url, headers={'Authorization': f'Bearer {api_token}'})
    try:
        with _ureq.urlopen(req, timeout=5) as r:
            return _j.loads(r.read().decode())
    except _uerr.HTTPError as e:
        if e.code == 404:
            return {'count': 0, 'sum': 0}
        return None
    except Exception:
        return None


def _kv_delete_rating(date_str):
    """Delete a single rating entry from Cloudflare KV. Returns True on success."""
    import urllib.request as _ureq
    account_id   = _CF_CONFIG.get('CLOUDFLARE_ACCOUNT_ID', '')
    namespace_id = _CF_CONFIG.get('CLOUDFLARE_KV_NAMESPACE_ID', '')
    api_token    = _CF_CONFIG.get('CLOUDFLARE_KV_API_TOKEN', '')
    if not all([account_id, namespace_id, api_token]):
        return False
    url = (f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
           f"/storage/kv/namespaces/{namespace_id}/values/{date_str}")
    req = _ureq.Request(url, method='DELETE',
                        headers={'Authorization': f'Bearer {api_token}'})
    try:
        with _ureq.urlopen(req, timeout=5) as r:
            return r.status in (200, 201)
    except Exception:
        return False


def _read_ratings():
    import json as _j
    try:
        with open(RATINGS_FILE) as f:
            return _j.load(f)
    except Exception:
        return {}

def _write_ratings(data):
    import json as _j
    with open(RATINGS_FILE, 'w') as f:
        _j.dump(data, f, indent=2)

def _open_meteo_weather(date_str):
    """
    Fetch daily weather for date_str.

    Stage 1 – NWS KONP (Newport Municipal Airport): actual station observations.
              Provides weather description, precip, wind, humidity, and cloud cover
              derived from cloudLayers (CLR/FEW/SCT/BKN/OVC).  Description and
              cloud cover are taken from the observation closest to sunset so they
              reflect timelapse conditions rather than midday.
    Stage 2 – Open-Meteo ERA5: supplements with radiation and times;
              used as full fallback when NWS data is unavailable (date too old,
              network error, etc.).  ERA5 cloud_cover_mean is only used when NWS
              cloudLayers data is absent (ERA5 models high-altitude cloud that
              can disagree with ground observations).

    Results are cached; past days' data never changes once complete.
    Today's data is re-fetched after 30 minutes so in-progress days stay fresh.
    """
    import json as _json
    import urllib.request as _ureq
    import urllib.parse as _uparse
    import time as _time
    from datetime import date as _date, datetime as _dt
    from zoneinfo import ZoneInfo as _ZI

    # NWS cloud layer coverage codes → approximate percent
    _COV = {'CLR': 0, 'SKC': 0, 'FEW': 13, 'SCT': 38, 'BKN': 63, 'OVC': 100, 'VV': 100}

    os.makedirs(WEATHER_CACHE_DIR, exist_ok=True)
    cache = os.path.join(WEATHER_CACHE_DIR, f'{date_str}.json')

    is_today = (date_str == _date.today().isoformat())
    cache_age = _time.time() - os.path.getmtime(cache) if os.path.exists(cache) else float('inf')
    if cache_age < (1800 if is_today else float('inf')):
        try:
            return _json.loads(open(cache).read())
        except Exception:
            pass

    tz = _ZI('America/Los_Angeles')
    result = {}

    # ------------------------------------------------------------------
    # Stage 1: NWS KONP – actual station observations
    # ------------------------------------------------------------------
    try:
        day   = _date.fromisoformat(date_str)
        start = _dt(day.year, day.month, day.day,  0,  0,  0, tzinfo=tz).isoformat()
        end   = _dt(day.year, day.month, day.day, 23, 59, 59, tzinfo=tz).isoformat()
        url   = ('https://api.weather.gov/stations/KONP/observations'
                 f'?start={_uparse.quote(start)}&end={_uparse.quote(end)}')
        req   = _ureq.Request(url, headers={
            'User-Agent': 'pumphouse-monitor/1.0',
            'Accept':     'application/geo+json',
        })
        with _ureq.urlopen(req, timeout=15) as resp:
            features = _json.loads(resp.read()).get('features', [])

        # Compute sunset time so we can pick the closest observation
        try:
            from astral import LocationInfo as _LI
            from astral.sun import sun as _sun
            _loc = _LI('Newport, OR', 'Oregon', 'America/Los_Angeles', 44.6368, -124.0535)
            sunset_dt = _sun(_loc.observer, date=day, tzinfo=tz)['sunset']
        except Exception:
            sunset_dt = _dt(day.year, day.month, day.day, 18, 0, tzinfo=tz)

        temps, winds, humidities = [], [], []
        precip_total = 0.0
        timed_obs = []   # (timestamp dt, desc, cloud_pct | None)
        for f in features:
            p = f.get('properties', {})
            t = p.get('temperature', {}).get('value')
            if t is not None:
                temps.append(t * 9/5 + 32)                   # °C → °F
            ws = p.get('windSpeed', {}).get('value')          # km/h
            wg = p.get('windGust',  {}).get('value')          # km/h
            w  = max((v for v in [ws, wg] if v is not None), default=None)
            if w is not None:
                winds.append(w * 0.621371)                    # km/h → mph
            h = p.get('relativeHumidity', {}).get('value')
            if h is not None:
                humidities.append(h)
            prec = p.get('precipitationLastHour', {}).get('value')
            if prec is not None:
                precip_total += prec / 25.4                   # mm → inches
            desc = p.get('textDescription', '').strip()
            layers = p.get('cloudLayers') or []
            pcts = [_COV[l['amount']] for l in layers if l.get('amount') in _COV]
            cloud_pct = max(pcts) if pcts else None
            ts_str = p.get('timestamp', '')
            if ts_str:
                try:
                    timed_obs.append((_dt.fromisoformat(ts_str), desc, cloud_pct))
                except Exception:
                    pass

        if temps:
            # Pick the observation closest to sunset for description + cloud cover
            best_desc, best_cloud = None, None
            if timed_obs:
                timed_obs.sort(key=lambda x: abs((x[0] - sunset_dt).total_seconds()))
                for _, desc, cloud_pct in timed_obs:
                    if best_desc is None and desc:
                        best_desc = desc
                    if best_cloud is None and cloud_pct is not None:
                        best_cloud = cloud_pct
                    if best_desc and best_cloud is not None:
                        break
            result = {
                'source':       'nws',
                'weather_desc': best_desc,
                'temp_max':     f'{max(temps):.0f}',
                'temp_min':     f'{min(temps):.0f}',
                'precip':       f'{precip_total:.2f}',
                'wind_max':     f'{max(winds):.0f}'                    if winds      else None,
                'wind_avg':     f'{sum(winds)/len(winds):.0f}'         if winds      else None,
                'humidity':     f'{sum(humidities)/len(humidities):.0f}' if humidities else None,
            }
            if best_cloud is not None:
                result['cloud'] = str(best_cloud)   # observed; ERA5 won't overwrite
    except Exception:
        pass  # network error or date outside NWS retention → fall through

    # ------------------------------------------------------------------
    # Stage 2: Open-Meteo ERA5 – cloud cover, radiation, times;
    #          full fallback if NWS returned nothing.
    # ------------------------------------------------------------------
    try:
        url = (
            'https://archive-api.open-meteo.com/v1/archive'
            '?latitude=44.6368&longitude=-124.0535'
            f'&start_date={date_str}&end_date={date_str}'
            '&daily=weather_code,temperature_2m_max,temperature_2m_min,'
            'precipitation_sum,wind_speed_10m_max,wind_speed_10m_mean,'
            'cloud_cover_mean,shortwave_radiation_sum,sunrise,sunset'
            '&temperature_unit=fahrenheit&wind_speed_unit=mph'
            '&precipitation_unit=inch&timezone=America%2FLos_Angeles'
        )
        with _ureq.urlopen(url, timeout=10) as resp:
            d = _json.loads(resp.read()).get('daily', {})

        def _safe(key, fmt='{:.0f}'):
            v = d.get(key, [None])[0]
            return fmt.format(v) if v is not None else None

        def _fmt_time(s):
            try:
                return _dt.strptime(s, '%Y-%m-%dT%H:%M').strftime('%-I:%M %p')
            except Exception:
                return None

        # Always take cloud/radiation/times from ERA5 (NWS doesn't have these)
        result.setdefault('cloud',    _safe('cloud_cover_mean'))
        result.setdefault('radiation', _safe('shortwave_radiation_sum', '{:.1f}'))
        result.setdefault('sunset',   _fmt_time((d.get('sunset')  or [None])[0]))
        result.setdefault('sunrise',  _fmt_time((d.get('sunrise') or [None])[0]))

        if not result.get('source'):
            # Full ERA5 fallback – NWS had no data for this date
            raw_code = d.get('weather_code', [None])[0]
            code = int(raw_code) if raw_code is not None else None
            result.update({
                'source':       'era5',
                'weather_code': code,
                'weather_desc': _WMO.get(code, f'Code {code}') if code is not None else None,
                'temp_max':     _safe('temperature_2m_max'),
                'temp_min':     _safe('temperature_2m_min'),
                'precip':       _safe('precipitation_sum', '{:.2f}'),
                'wind_max':     _safe('wind_speed_10m_max'),
                'wind_avg':     _safe('wind_speed_10m_mean'),
            })
    except Exception:
        pass

    if result:
        open(cache, 'w').write(_json.dumps(result))
        return result
    return None


def _timelapse_dates():
    """Return sorted list of date strings (YYYY-MM-DD) that have MP4 files."""
    import glob as _glob
    files = (_glob.glob(os.path.join(TIMELAPSE_DIR, '????-??-??_????.mp4')) +
             _glob.glob(os.path.join(TIMELAPSE_DIR, '????-??-??.mp4')))
    # Extract the date portion (first 10 chars) and deduplicate
    return sorted(set(os.path.basename(f)[:10] for f in files))


def _mp4_for_date(date_str):
    """Return the MP4 filename (basename) for a given YYYY-MM-DD date, or None."""
    import glob as _glob
    # Prefer new-style name with sunset time embedded
    files = _glob.glob(os.path.join(TIMELAPSE_DIR, f'{date_str}_????.mp4'))
    if files:
        return os.path.basename(files[0])
    # Fall back to legacy name
    legacy = os.path.join(TIMELAPSE_DIR, f'{date_str}.mp4')
    return os.path.basename(legacy) if os.path.exists(legacy) else None


def _day_weather_summary(date_str):
    """
    Read snapshots.csv and compute a one-day weather summary.
    Returns a dict or None if no data.
    """
    import csv as _csv
    from datetime import date as _date, datetime as _datetime
    from zoneinfo import ZoneInfo
    path = 'snapshots.csv'
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            rows = [r for r in _csv.DictReader(f) if r.get('timestamp', '').startswith(date_str)]
        if not rows:
            return None

        def floats(col):
            return [float(r[col]) for r in rows if r.get(col) not in ('', None)]

        out_temps  = floats('outdoor_temp_f')
        humidity   = floats('outdoor_humidity')
        wind_gusts = floats('wind_gust_mph')

        # Sunset time for this date
        sunset_str = None
        try:
            from astral import LocationInfo
            from astral.sun import sun
            tz = ZoneInfo('America/Los_Angeles')
            loc = LocationInfo('Newport, OR', 'Oregon', 'America/Los_Angeles', 44.6368, -124.0535)
            s = sun(loc.observer, date=_date.fromisoformat(date_str), tzinfo=tz)
            sunset_str = s['sunset'].strftime('%-I:%M %p')
        except Exception:
            pass

        # Outdoor temp from the snapshot closest to sunset
        sunset_temp = None
        try:
            from astral import LocationInfo
            from astral.sun import sun
            tz = ZoneInfo('America/Los_Angeles')
            loc = LocationInfo('Newport, OR', 'Oregon', 'America/Los_Angeles', 44.6368, -124.0535)
            sunset_dt = sun(loc.observer, date=_date.fromisoformat(date_str), tzinfo=tz)['sunset']
            best, best_diff = None, float('inf')
            for r in rows:
                try:
                    ts = _datetime.fromisoformat(r['timestamp'])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=tz)
                    diff = abs((ts - sunset_dt).total_seconds())
                    if diff < best_diff and r.get('outdoor_temp_f') not in ('', None):
                        best_diff, best = diff, r
                except Exception:
                    continue
            if best:
                sunset_temp = f"{float(best['outdoor_temp_f']):.0f}"
        except Exception:
            pass

        return {
            'sunset':       sunset_str,
            'out_temp_lo':  f"{min(out_temps):.0f}" if out_temps else None,
            'out_temp_hi':  f"{max(out_temps):.0f}" if out_temps else None,
            'sunset_temp':  sunset_temp,
            'humidity_avg': f"{sum(humidity)/len(humidity):.0f}" if humidity else None,
            'wind_gust':    f"{max(wind_gusts):.0f}" if wind_gusts else None,
        }
    except Exception:
        return None


def _current_conditions():
    """
    Fetch point-in-time wind speed and cloud cover for the snapshot page.

    Wind  – most recent wind_gust_mph from snapshots.csv (local Ambient Weather station).
    Cloud – Open-Meteo forecast current conditions (cloud_cover %).
            Also supplies wind as a fallback when the local sensor reading is absent.
    """
    import csv as _csv
    import urllib.request as _ureq
    import json as _json

    result = {}

    # Most recent local sensor wind reading
    try:
        with open('snapshots.csv') as f:
            rows = list(_csv.DictReader(f))
        if rows:
            v = rows[-1].get('wind_gust_mph', '')
            if v not in ('', None):
                result['wind']       = f'{float(v):.0f}'
                result['wind_label'] = 'Gust'
    except Exception:
        pass

    # Cloud cover, weather code, and wind from Open-Meteo current forecast
    try:
        url = ('https://api.open-meteo.com/v1/forecast'
               '?latitude=44.6368&longitude=-124.0535'
               '&current=cloud_cover,wind_speed_10m,weather_code'
               '&wind_speed_unit=mph&timezone=America%2FLos_Angeles')
        with _ureq.urlopen(url, timeout=8) as resp:
            cur = _json.loads(resp.read()).get('current', {})
        cc = cur.get('cloud_cover')
        if cc is not None:
            result['cloud'] = str(int(round(cc)))
        wc = cur.get('weather_code')
        if wc is not None:
            result['weather_desc'] = _WMO.get(int(wc), f'Code {int(wc)}')
        ws = cur.get('wind_speed_10m')
        if ws is not None and 'wind' not in result:
            result['wind']       = f'{ws:.0f}'
            result['wind_label'] = 'Wind'
    except Exception:
        pass

    return result


@timelapse_bp.route('/timelapse')
def timelapse_index():
    """Redirect to a timelapse date.

    ?today  → today if available, yesterday if today not yet generated, else latest
    default → most recent date with avg rating >= 4.5, else latest
    """
    from flask import redirect, request as _req
    from datetime import date as _date, timedelta as _td
    dates = _timelapse_dates()
    if not dates:
        return Response('No timelapses available yet.', status=404, mimetype='text/plain')
    dates_set = set(dates)
    if 'today' in _req.args:
        today = _date.today().isoformat()
        yesterday = (_date.today() - _td(days=1)).isoformat()
        for d in (today, yesterday):
            if d in dates_set:
                return redirect(f'/timelapse/{d}')
    ratings = _read_ratings()
    for d in reversed(dates):
        r = ratings.get(d, {})
        if r.get('count', 0) > 0 and r['sum'] / r['count'] >= 4.5:
            return redirect(f'/timelapse/{d}')
    return redirect(f'/timelapse/{dates[-1]}')


@timelapse_bp.route('/timelapse/latest.mp4')
def timelapse_latest_mp4():
    """Redirect to the most recent timelapse MP4 (for embedding / direct links)."""
    from flask import redirect
    dates = _timelapse_dates()
    if not dates:
        return Response('No timelapses available yet.', status=404)
    mp4_name = _mp4_for_date(dates[-1])
    if not mp4_name:
        return Response('No MP4 found.', status=404)
    return redirect(f'/timelapse/{mp4_name}')


@timelapse_bp.route('/timelapse/latest.jpg')
def timelapse_latest_jpg():
    """Redirect to the most recent sunset snapshot JPEG.
    Intended for Scriptable widgets and other clients that need a direct image URL.
    Tap target: /timelapse (the HTML viewer)."""
    from flask import redirect
    dates = _timelapse_dates()
    for d in reversed(dates):
        if os.path.exists(os.path.join(SNAPSHOT_DIR, f'{d}.jpg')):
            return redirect(f'/timelapse/{d}/snapshot')
    return Response('No snapshot available yet.', status=404)


@timelapse_bp.route('/timelapse/<date_str>/snapshot')
def timelapse_snapshot(date_str):
    """Return the sunset snapshot JPEG for a given date, for use as a thumbnail."""
    import re
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_str):
        return Response('Invalid date', status=400)
    path = os.path.join(SNAPSHOT_DIR, f'{date_str}.jpg')
    if not os.path.exists(path):
        return Response('No snapshot for this date.', status=404)
    return send_file(path, mimetype='image/jpeg',
                     max_age=365 * 24 * 3600)   # immutable once written


@timelapse_bp.route('/timelapse/<date_str>/frame-view-client')
def timelapse_frame_view_client(date_str):
    """Static frame viewer for Cloudflare visitors.

    The timelapse JS stores the extracted canvas frame in localStorage under
    'tl_frame_YYYY-MM-DD', then opens this page.  The page reads the image
    entirely client-side — no image data is ever sent to the Pi, so there is
    no upload DoS vector.  Cloudflare can cache this template freely.
    """
    import re as _re
    if not _re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_str):
        return Response('Invalid date', status=400)

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Frame &mdash; {date_str}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: monospace; background: #1a1a1a; color: #e0e0e0; margin: 0; padding: 16px; }}
    h2 {{ color: #fff; margin: 0 0 12px; font-size: 1.1em; font-weight: normal; }}
    .frame-wrap {{ margin: 0 auto; }}
    .frame-wrap img {{ width: 100%; height: auto; display: block; border-radius: 4px; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 12px 0; }}
    .btn {{ background: #2a2a2a; color: #4CAF50; border: 1px solid #444;
            padding: 6px 16px; border-radius: 4px; text-decoration: none;
            font-family: monospace; font-size: .95em; cursor: pointer; }}
    .btn:hover {{ background: #333; color: #fff; }}
    #msg {{ color: #888; font-style: italic; margin: 8px 0; }}
  </style>
</head>
<body>
  <h2>Timelapse Frame &mdash; {date_str}</h2>
  <p id="msg">Loading&hellip;</p>
  <div class="frame-wrap"><img id="frame-img" alt="Frame" style="display:none"></div>
  <div class="actions" id="actions" style="display:none">
    <a class="btn" href="/timelapse/{date_str}">&#9654; Timelapse</a>
    <a id="dl-link" class="btn" href="#">&#8681; Download</a>
  </div>
  <script>
  (function() {{
    var key = 'tl_frame_{date_str}';
    var dataUrl;
    try {{ dataUrl = localStorage.getItem(key); }} catch(e) {{}}
    if (!dataUrl) {{
      document.getElementById('msg').textContent =
        'No frame found \u2014 go back and click Snapshot again.';
      return;
    }}
    try {{ localStorage.removeItem(key); }} catch(e) {{}}
    var img = document.getElementById('frame-img');
    img.src = dataUrl;
    img.style.display = '';
    document.getElementById('msg').style.display = 'none';
    var dl = document.getElementById('dl-link');
    dl.href = dataUrl;
    dl.setAttribute('download', 'frame-{date_str}.jpg');
    document.getElementById('actions').style.display = 'flex';
  }})();
  </script>
</body>
</html>"""
    return Response(html, status=200, mimetype='text/html',
                    headers={'Cache-Control': 'public, max-age=3600'})


@timelapse_bp.route('/timelapse/<date_str>/frame-view', methods=['POST'])
def timelapse_frame_view(date_str):
    """Display a POSTed JPEG frame in a styled page (no weather panel)."""
    import re as _re, base64 as _b64
    if not _re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_str):
        return Response('Invalid date', status=400)
    b64_data = request.form.get('image', '')
    if not b64_data:
        return Response('No image data', status=400)
    try:
        jpeg_bytes = _b64.b64decode(b64_data)
    except Exception as e:
        return Response(f'Invalid image data: {e}', status=400)
    img_b64 = _b64.b64encode(jpeg_bytes).decode()

    is_direct = 'onblackberryhill.com' not in request.host.lower()
    dash_btn = '<a class="btn" href="/">Dashboard</a>' if is_direct else ''
    set_key_btn = (
        '<button id="set-key-btn" class="btn" onclick="setKeySnapshot()">Set key snapshot</button>'
        if is_direct else ''
    )

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Frame &mdash; {date_str}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: monospace; background: #1a1a1a; color: #e0e0e0; margin: 0; padding: 16px; }}
    h2 {{ color: #fff; margin: 0 0 12px; font-size: 1.1em; font-weight: normal; }}
    .frame-wrap {{ margin: 0 auto; }}
    .frame-wrap img {{ width: 100%; height: auto; display: block; border-radius: 4px; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 12px 0; }}
    .btn {{ background: #2a2a2a; color: #4CAF50; border: 1px solid #444;
            padding: 6px 16px; border-radius: 4px; text-decoration: none;
            font-family: monospace; font-size: .95em; cursor: pointer; }}
    .btn:hover {{ background: #333; color: #fff; }}
    .btn:disabled {{ opacity: .5; cursor: default; }}
  </style>
</head>
<body>
  <h2>Timelapse Frame &mdash; {date_str}</h2>
  <div class="frame-wrap">
    <img id="frame-img" src="data:image/jpeg;base64,{img_b64}" alt="Frame">
  </div>
  <div class="actions">
    <a class="btn" href="/timelapse/{date_str}">&#9654; Timelapse</a>
    {dash_btn}
    <a class="btn" href="data:image/jpeg;base64,{img_b64}" download="frame-{date_str}.jpg">&#8681; Download</a>
    {set_key_btn}
  </div>
  <script>
  function setKeySnapshot() {{
    var btn = document.getElementById('set-key-btn');
    btn.disabled = true;
    btn.textContent = 'Saving...';
    var b64 = document.getElementById('frame-img').src.split(',')[1];
    fetch('/timelapse/{date_str}/set-snapshot', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{image: b64}})
    }}).then(function(r) {{ return r.json(); }}).then(function(d) {{
      btn.textContent = d.ok ? 'Saved!' : ('Error: ' + (d.error || '?'));
    }}).catch(function() {{ btn.textContent = 'Failed'; }});
  }}
  </script>
</body>
</html>"""
    return Response(html, status=200, mimetype='text/html')


@timelapse_bp.route('/timelapse/<date_str>/set-snapshot', methods=['POST'])
def timelapse_set_snapshot(date_str):
    """Save a POSTed base64 JPEG as the key snapshot for the given date.
    Only available via direct (non-Cloudflare) access."""
    import re as _re, base64 as _b64, json as _json
    if not _re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_str):
        return Response(_json.dumps({'ok': False, 'error': 'Invalid date'}),
                        status=400, mimetype='application/json')
    if 'onblackberryhill.com' in request.host.lower():
        return Response(_json.dumps({'ok': False, 'error': 'Not available via CDN'}),
                        mimetype='application/json')
    try:
        data = request.get_json(force=True)
        b64_data = (data or {}).get('image', '')
        if not b64_data:
            raise ValueError('No image data provided')
        jpeg_bytes = _b64.b64decode(b64_data)
    except Exception as e:
        return Response(_json.dumps({'ok': False, 'error': str(e)}),
                        mimetype='application/json')
    path = os.path.join(SNAPSHOT_DIR, f'{date_str}.jpg')
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(jpeg_bytes)
    return Response(_json.dumps({'ok': True, 'message': f'Snapshot saved for {date_str}'}),
                    mimetype='application/json')


@timelapse_bp.route('/api/ratings/<date_str>')
def api_ratings(date_str):
    """Return aggregate rating for a date as JSON {count, avg}. Used by the client-side widget."""
    import re, json as _j
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_str):
        return Response('Invalid date', status=400)
    if _RATINGS_BACKEND == 'cloudflare_kv':
        data = _kv_read_rating(date_str) or {'count': 0, 'sum': 0}
    else:
        data = _read_ratings().get(date_str, {'count': 0, 'sum': 0})
    count = data.get('count', 0)
    avg   = round(data['sum'] / count, 1) if count else None
    return Response(_j.dumps({'count': count, 'avg': avg}),
                    mimetype='application/json',
                    headers={'Cache-Control': 'public, max-age=60'})


@timelapse_bp.route('/api/ratings/<date_str>', methods=['DELETE'])
def api_ratings_delete(date_str):
    """Zero out ratings for a date. Only permitted from direct Pi access, not via Cloudflare."""
    import re
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_str):
        return Response('Invalid date', status=400)
    if 'onblackberryhill.com' in request.host.lower():
        return Response('Forbidden', status=403)
    with _ratings_lock:
        data = _read_ratings()
        if date_str in data:
            del data[date_str]
            _write_ratings(data)
    if _RATINGS_BACKEND == 'cloudflare_kv':
        _kv_delete_rating(date_str)
    return Response('', status=204)


@timelapse_bp.route('/timelapse/<date_str>/rate', methods=['POST'])
def timelapse_rate(date_str):
    """Accept a 3–5 star rating for a timelapse date, update the ratings file,
    and set a cookie so the user can only rate once per date."""
    import re, json as _j
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_str):
        return Response('Invalid date', status=400)
    try:
        rating = int(request.get_json(force=True).get('rating', 0))
    except Exception:
        return Response('Bad request', status=400)
    if rating not in (3, 4, 5):
        return Response('Rating must be 3, 4, or 5', status=400)

    with _ratings_lock:
        data  = _read_ratings()
        entry = data.get(date_str, {'count': 0, 'sum': 0})
        entry['count'] += 1
        entry['sum']   += rating
        data[date_str]  = entry
        _write_ratings(data)

    if _RATINGS_BACKEND == 'cloudflare_kv':
        _kv_write_rating(date_str, entry)

    avg  = entry['sum'] / entry['count']
    resp = Response(_j.dumps({'count': entry['count'], 'avg': round(avg, 1)}),
                    mimetype='application/json')
    resp.set_cookie(f'tl_rated_{date_str}', str(rating),
                    max_age=365 * 24 * 3600, samesite='Lax')
    return resp


@timelapse_bp.route('/timelapse/test.mp4')
def timelapse_test_mp4():
    """Serve the test timelapse produced by test_timelapse.py."""
    path = os.path.join(TIMELAPSE_DIR, 'test_timelapse.mp4')
    if not os.path.exists(path):
        return Response(
            'No test timelapse found. Run test_timelapse.py first.',
            status=404, mimetype='text/plain'
        )
    return send_file(path, mimetype='video/mp4', max_age=0)


@timelapse_bp.route('/timelapse/<date_or_file>')
def timelapse_view(date_or_file):
    """
    YYYY-MM-DD              → HTML viewer page with prev/next nav and weather summary
    YYYY-MM-DD_HHMM.mp4    → serve the raw MP4 (new-style name with sunset time)
    YYYY-MM-DD.mp4          → serve the raw MP4 (legacy name)
    """
    import re
    # Raw MP4 request (new-style or legacy)
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}(_\d{4})?\.mp4', date_or_file):
        path = os.path.join(TIMELAPSE_DIR, date_or_file)
        if not os.path.exists(path):
            return Response(f'Not found: {date_or_file}', status=404)
        from datetime import date as _date
        mp4_date = date_or_file[:10]
        mp4_max_age = 365 * 24 * 3600 if mp4_date < _date.today().isoformat() else 600
        return send_file(path, mimetype='video/mp4', max_age=mp4_max_age)

    # HTML viewer
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_or_file):
        return Response('Invalid date', status=400)

    date_str  = date_or_file
    dates     = _timelapse_dates()
    mp4_name  = _mp4_for_date(date_str)
    has_video = mp4_name is not None

    idx  = dates.index(date_str) if date_str in dates else -1
    prev_date = dates[idx - 1] if idx > 0 else None
    next_date = dates[idx + 1] if idx >= 0 and idx < len(dates) - 1 else None

    # Human-readable title and short nav labels
    try:
        from datetime import date as _date
        d = _date.fromisoformat(date_str)
        title_date = d.strftime('%A, %B %-d, %Y')
    except Exception:
        title_date = date_str

    def _short_date(ds):
        try:
            return _date.fromisoformat(ds).strftime('%a %b %-d')
        except Exception:
            return ds

    wx  = _day_weather_summary(date_str)   # local snapshots (humidity)
    om  = _open_meteo_weather(date_str)    # Open-Meteo archive

    def stat(label, val, unit=''):
        if val is None:
            return ''
        return f'<div class="stat"><span class="lbl">{label}</span><span class="val">{val}{unit}</span></div>'

    wx_html = ''
    if om:
        precip_val = float(om['precip']) if om.get('precip') else 0
        precip_str = f'{precip_val:.2f}"' if precip_val > 0 else '—'
        # Local sensor humidity preferred; NWS station as fallback
        humidity = (wx.get('humidity_avg') if wx else None) or om.get('humidity')
        wind_str = None
        if om.get('wind_avg') and om.get('wind_max'):
            wind_str = f"{om['wind_avg']} avg / {om['wind_max']} max"
        elif om.get('wind_max'):
            wind_str = om['wind_max']
        radiation_str = f"{om['radiation']} MJ/m²" if om.get('radiation') else None
        # Prefer local sensor hi/lo (accurate); fall back to reanalysis model
        if wx and wx.get('out_temp_hi') and wx.get('out_temp_lo'):
            hi_lo_str = f"{wx['out_temp_hi']}–{wx['out_temp_lo']}"
        elif om.get('temp_max') and om.get('temp_min'):
            hi_lo_str = f"{om['temp_max']}–{om['temp_min']}"
        else:
            hi_lo_str = None
        desc_html = ''
        if om.get('weather_desc'):
            import random as _random
            _opts = _QUIPS.get(om['weather_desc'].lower(), [])
            _quip = _random.choice(_opts) if _opts else ''
            _quip_html = f' <span class="wx-quip">{_quip}</span>' if _quip else ''
            desc_html = f'<div class="wx-desc">{om["weather_desc"]}{_quip_html}</div>'
        wx_html = f"""
        <div class="weather">
          {desc_html}
          <div class="wx-group">
            {stat('Sunset',    om['sunset'],  '')}
            {stat('High/Low',  hi_lo_str,     '°F')}
            {stat('Rain',      precip_str,    '')}
            {stat('Wind',      wind_str,      ' mph')}
            {stat('Cloud',     om['cloud'],   '%')}
            {stat('Radiation', radiation_str, '')}
            {stat('Humidity',  humidity,      '%')}
          </div>
        </div>"""
    elif wx:
        # Fallback to local snapshot data if Open-Meteo unavailable
        wx_html = f"""
        <div class="weather">
          <div class="wx-group">
            {stat('Sunset',    wx['sunset'],      '')}
            {stat('High/Low',  f"{wx['out_temp_lo']}–{wx['out_temp_hi']}", '°F') if wx['out_temp_lo'] else ''}
            {stat('Humidity',  wx['humidity_avg'],'%')}
            {stat('Wind gust', wx['wind_gust'],   ' mph')}
          </div>
        </div>"""

    is_direct    = 'onblackberryhill.com' not in request.host.lower()
    is_direct_js = 'true' if is_direct else 'false'
    now_btn      = '<a href="/snapshot" class="speed-btn dl-btn">Now</a>' if is_direct else ''
    dash_btn     = '<a href="/" class="speed-btn dl-btn">Dashboard</a>' if is_direct else ''
    public_link  = (f'&middot; (<a href="https://onblackberryhill.com/timelapse/{date_str}">public site</a>)'
                    if is_direct else '')

    video_html = (
        f'<video id="vid" src="/timelapse/{mp4_name}" controls autoplay muted loop playsinline></video>'
        f'<div class="ctrl-row">'
        f'<div class="speed-btns">'
        f'<span class="speed-lbl">Speed:</span>'
        f'<button class="speed-btn" data-rate="0.25">&#188;x</button>'
        f'<button class="speed-btn" data-rate="0.5">&#189;x</button>'
        f'<button class="speed-btn" data-rate="1">1x</button>'
        f'<button class="speed-btn active" data-rate="2">2x</button>'
        f'<button class="speed-btn" data-rate="4">4x</button>'
        f'<button class="speed-btn" data-rate="8">8x</button>'
        f'</div>'
        f'<div class="ctrl-btns">'
        f'<button id="pause-btn" class="speed-btn pause-btn">&#9646;&#9646; Pause</button>'
        f'<button id="dl-btn" class="speed-btn dl-btn">&#8681; Snapshot</button>'
        f'{now_btn}'
        f'{dash_btn}'
        f'</div>'
        f'</div>'
        if has_video else
        '<p class="no-video">No timelapse recorded for this date.</p>'
    )

    prev_btn = (f'<a class="nav-btn" href="/timelapse/{prev_date}">&#8592;<span class="nav-label">&nbsp;{_short_date(prev_date)}</span></a>'
                if prev_date else '<span class="nav-btn disabled">&#8592;</span>')
    next_btn = (f'<a class="nav-btn" href="/timelapse/{next_date}"><span class="nav-label">{_short_date(next_date)}&nbsp;</span>&#8594;</a>'
                if next_date else '<span class="nav-btn disabled">&#8594;</span>')
    prev_js      = f'"{prev_date}"' if prev_date else 'null'
    next_js      = f'"{next_date}"' if next_date else 'null'

    # List all dates newest-first with snapshot thumbnails, sunset time, and rating
    import re as _re, json as _json
    all_ratings = _read_ratings()

    # Dates with average rating >= 3 stars — used for vertical swipe/scroll navigation
    star_dates = [
        d for d in dates
        if all_ratings.get(d, {}).get('count', 0) > 0
        and all_ratings[d]['sum'] / all_ratings[d]['count'] >= 3.0
    ]
    star_dates_js = _json.dumps(star_dates)

    def _list_html(ds):
        import json as _json
        # Line 1: Date + sunset time
        try:
            line1 = _date.fromisoformat(ds).strftime('%a, %b %-d, %Y')
        except Exception:
            line1 = ds
        mp4 = _mp4_for_date(ds)
        sunset_str = None
        if mp4:
            m = _re.search(r'_(\d{2})(\d{2})\.mp4$', mp4)
            if m:
                h, mn = int(m.group(1)), int(m.group(2))
                sunset_str = f'{h % 12 or 12}:{mn:02d} {"AM" if h < 12 else "PM"}'
        if not sunset_str:
            try:
                from astral import LocationInfo as _LI
                from astral.sun import sun as _sun
                from zoneinfo import ZoneInfo as _ZI
                _tz  = _ZI('America/Los_Angeles')
                _loc = _LI('Newport, OR', 'Oregon', 'America/Los_Angeles', 44.6368, -124.0535)
                sunset_str = _sun(_loc.observer, date=_date.fromisoformat(ds), tzinfo=_tz)['sunset'].strftime('%-I:%M %p')
            except Exception:
                pass
        if sunset_str:
            line1 += f'&nbsp;&nbsp;{sunset_str}'
        out = f'<div class="list-line1">{line1}</div>'
        # Rating: numeric score + yellow/grey stars + count (Amazon-style)
        r = all_ratings.get(ds, {})
        if r.get('count', 0) > 0:
            avg = r['sum'] / r['count']
            n_lit = min(5, max(0, round(avg)))
            stars = ''.join(
                f'<span class="ls{" lit" if i <= n_lit else ""}">&#9733;</span>'
                for i in range(1, 6)
            )
            out += (f'<div class="list-rating">'
                    f'{avg:.1f}&thinsp;<span class="list-stars">{stars}</span>'
                    f'&thinsp;({r["count"]})</div>')
        # Conditions: from weather cache only (no API call during list render)
        try:
            wx = _json.loads(open(os.path.join(WEATHER_CACHE_DIR, f'{ds}.json')).read())
            desc = wx.get('weather_desc')
            if desc:
                out += f'<div class="list-cond">{desc}</div>'
        except Exception:
            pass
        return out

    list_items = ''.join(
        f'<li{"  class=\"current\"" if d == date_str else ""}>'
        f'<a href="/timelapse/{d}" class="list-main">'
        f'<img class="thumb" src="/timelapse/{d}/snapshot" loading="lazy"'
        f' onerror="this.style.display=\'none\'">'
        f'<div class="list-info">{_list_html(d)}</div>'
        f'</a>'
        f'<a href="/timelapse/{d}/snapshot" target="_blank" class="snap-link">(snapshot)</a>'
        f'</li>'
        for d in reversed(dates)
    )

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sunset {title_date}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: monospace; background:#1a1a1a; color:#e0e0e0;
           margin:0; padding:16px; }}
    h2   {{ margin:0; color:#fff; }}
    video {{ width:100%; max-width:960px; display:block;
             background:#000; border-radius:4px; }}
    .no-video {{ color:#888; font-style:italic; }}
    .site-header {{ max-width:960px; padding:4px 0 10px; margin-bottom:4px;
                    border-bottom:1px solid #333; display:flex;
                    flex-wrap:wrap; align-items:baseline; gap:0 10px; }}
    .site-name {{ font-size:1.2em; color:#fff; font-weight:bold; }}
    .site-sub  {{ font-size:0.85em; color:#888; }}
    .site-sub a {{ color:#4CAF50; text-decoration:none; }}
    .site-sub a:hover {{ color:#fff; }}
    .nav {{ display:flex; justify-content:space-between; align-items:center;
            max-width:960px; margin:12px 0; }}
    .nav-center {{ flex:1; text-align:center; }}
    .swipe-hint {{ display:none; color:#666; font-size:0.8em; margin-top:3px; }}
    .nav-btn {{ background:#2a2a2a; color:#4CAF50; border:1px solid #444;
                padding:8px 20px; border-radius:4px; text-decoration:none;
                white-space:nowrap; font-size:1.05em; }}
    .nav-btn.disabled {{ color:#555; border-color:#333; cursor:default; }}
    .nav-btn:hover:not(.disabled) {{ background:#333; }}
    .weather {{ max-width:960px; background:#222; border:1px solid #333;
                border-radius:4px; padding:12px 16px; margin:12px 0;
                display:flex; flex-direction:column; gap:8px; }}
    .wx-desc  {{ font-size:1.05em; color:#aed6f1; font-weight:bold; }}
    .wx-quip  {{ font-size:0.9em; color:#888; font-weight:normal; font-style:italic; }}
    .wx-group {{ display:flex; flex-wrap:wrap; gap:12px; }}
    .stat {{ display:flex; flex-direction:column; min-width:80px; }}
    .lbl  {{ font-size:0.75em; color:#888; text-transform:uppercase; }}
    .val  {{ font-size:1.1em; color:#e0e0e0; }}
    .ctrl-row   {{ max-width:960px; display:flex; flex-wrap:wrap;
                   align-items:center; gap:6px; margin:8px 0; }}
    .speed-btns {{ display:contents; }}
    .ctrl-btns  {{ display:contents; }}
    .speed-lbl  {{ color:#888; font-size:0.85em; margin-right:4px; }}
    .speed-btn  {{ background:#2a2a2a; color:#4CAF50; border:1px solid #444;
                   padding:4px 10px; border-radius:4px; cursor:pointer;
                   font-family:monospace; font-size:0.9em; }}
    .speed-btn:hover  {{ background:#333; }}
    .speed-btn.active {{ background:#4CAF50; color:#000; border-color:#4CAF50; }}
    .pause-btn        {{ color:#aaa; border-color:#555; }}
    .pause-btn.paused {{ background:#e57373; color:#000; border-color:#e57373; }}
    .dl-btn           {{ color:#aaa; border-color:#555; }}
    details {{ max-width:960px; margin-top:16px; }}
    summary {{ cursor:pointer; color:#4CAF50; }}
    ul {{ list-style:none; padding:0; margin:8px 0; }}
    li {{ display:flex; align-items:center; padding:4px 0; }}
    li a.list-main {{ display:flex; align-items:center; gap:10px; flex:1;
                      color:#4CAF50; text-decoration:none; }}
    li.current a.list-main {{ color:#fff; font-weight:bold; }}
    li a.list-main:hover {{ color:#fff; }}
    li.kbd-focus a.list-main {{ color:#fff; background:#2a2a2a; border-radius:3px;
                                padding:2px 4px; }}
    .snap-link {{ color:#555; font-size:0.85em; white-space:nowrap;
                  text-decoration:none; padding:2px 8px; }}
    .snap-link:hover {{ color:#aaa; }}
    .thumb {{ width:{THUMB_WIDTH}px; height:{THUMB_WIDTH * 9 // 16}px; object-fit:cover; border-radius:3px;
               opacity:0.8; flex-shrink:0; background:#111; }}
    li.current .thumb {{ opacity:1; outline:2px solid #4CAF50; }}
    .list-info  {{ display:flex; flex-direction:column; gap:3px; }}
    .list-line1 {{ font-size:1.0em; }}
    .list-rating {{ font-size:1.0em; color:#ccc; }}
    .list-stars {{ letter-spacing:1px; }}
    .ls {{ font-size:2em; color:#555; }}
    .ls.lit {{ color:#f5c518; }}
    .list-cond  {{ font-size:0.9em; color:#888; font-style:italic; }}
    .rating {{ max-width:960px; display:flex; align-items:center; gap:10px; margin:8px 0; }}
    .rating-label {{ color:#888; font-size:0.9em; white-space:nowrap; }}
    .stars {{ display:flex; gap:1px; line-height:1; }}
    .star {{ font-size:2em; color:#666; cursor:default;
             transition:color 0.1s; user-select:none; }}
    .star.clickable {{ cursor:pointer; }}
    .star.clickable:hover {{ color:#f5c518; }}
    .star.lit {{ color:#f5c518; }}
    .rating-info {{ color:#aaa; font-size:0.85em; }}
    .newport-links {{ max-width:960px; margin:8px 0; font-size:0.85em;
                      color:#888; flex-wrap:wrap; }}
    .newport-lbl {{ margin-right:4px; }}
    .newport-links a {{ color:#4CAF50; text-decoration:none; }}
    .newport-links a:hover {{ color:#fff; }}
    @media (max-width:600px) {{
      .nav-label  {{ display:none; }}
      .nav-center h2 {{ font-size:1.0em; }}
      .swipe-hint {{ display:block; }}
      .thumb {{ width:44vw; height:calc(44vw * 9 / 16); }}
      .site-sub {{ font-size:0.78em; }}
      .ctrl-row   {{ flex-direction:column; align-items:flex-start; }}
      .speed-btns {{ display:flex; align-items:center; gap:6px; }}
      .ctrl-btns  {{ display:flex; gap:6px; }}
    }}
  </style>
</head>
<body>
  <header class="site-header">
    <span class="site-name">On Blackberry Hill</span>
    <span class="site-sub">Newport, OR &middot; Available via
      <a href="https://www.meredithlodging.com/listings/1830" target="_blank" rel="noopener">Meredith</a>
      &middot; <a href="https://www.airbnb.com/rooms/894278114876445404" target="_blank" rel="noopener">Airbnb</a>
      &middot; <a href="https://www.vrbo.com/9829179ha" target="_blank" rel="noopener">Vrbo</a>
      {public_link}
    </span>
  </header>
  <div class="nav">
    {prev_btn}
    <div class="nav-center">
      <h2>Sunset &mdash; {title_date}</h2>
      <div class="swipe-hint">swipe to change days</div>
    </div>
    {next_btn}
  </div>
  {video_html}
  {wx_html}
  <div class="rating" id="rating-widget">
    <span class="rating-label">Rate:</span>
    <div class="stars" id="stars">
      <span class="star" data-val="1" title="Min rating is 3 stars">&#9733;</span>
      <span class="star" data-val="2" title="Min rating is 3 stars">&#9733;</span>
      <span class="star clickable" data-val="3" title="3 stars">&#9733;</span>
      <span class="star clickable" data-val="4" title="4 stars">&#9733;</span>
      <span class="star clickable" data-val="5" title="5 stars">&#9733;</span>
    </div>
    <span id="rating-info"></span>
  </div>
  <div class="newport-links">
    <span class="newport-lbl">Links:</span>
    <a href="https://www.pinesnvines.com/adventures/things-to-do-newport-or" target="_blank" rel="noopener">Things To Do</a>
    &middot; <a href="https://www.livebeaches.com/webcams/aerial-tour-yaquina-head-lighthouse/" target="_blank" rel="noopener">Lighthouse Video</a>
    &middot; <a href="https://www.skylinewebcams.com/en/webcam/united-states/oregon/newport/yaquina-bay-newport-oregon-coast/timelapse.html" target="_blank" rel="noopener">Newport Timelapse</a>
    &middot; <a href="https://aquarium.org/live-cameras/seabird-cam/" target="_blank" rel="noopener">Seabird Cam</a>
    &middot; <a href="https://www.surfline.com/surf-report/agate-beach/584204214e65fad6a7709d27" target="_blank" rel="noopener">Beach Cam</a>
  </div>
  <details>
    <summary>All timelapses ({len(dates)})</summary>
    <ul>{list_items}</ul>
  </details>
  <script>
    const vid = document.getElementById('vid');

    // Speed control — persisted in localStorage so it survives day navigation
    // (localStorage is client-side only; unaffected by Cloudflare page caching)
    function setSpeed(rate) {{
      if (!vid) return;
      vid.playbackRate = rate;
      document.querySelectorAll('.speed-btn[data-rate]').forEach(b =>
        b.classList.toggle('active', parseFloat(b.dataset.rate) === rate));
      try {{ localStorage.setItem('tl_speed', rate); }} catch(e) {{}}
    }}
    (function() {{
      const s = parseFloat(localStorage.getItem('tl_speed') || '2');
      if (vid && !isNaN(s)) setSpeed(s);
    }})();
    document.querySelectorAll('.speed-btn[data-rate]').forEach(btn =>
      btn.addEventListener('click', () => setSpeed(parseFloat(btn.dataset.rate))));

    // Pause / play — persisted in localStorage
    const pauseBtn = document.getElementById('pause-btn');
    function setPause(paused) {{
      if (!vid) return;
      if (paused) {{
        vid.pause();
        if (pauseBtn) {{ pauseBtn.innerHTML = '&#9654; Play'; pauseBtn.classList.add('paused'); }}
      }} else {{
        vid.play();
        if (pauseBtn) {{ pauseBtn.innerHTML = '&#9646;&#9646; Pause'; pauseBtn.classList.remove('paused'); }}
      }}
      try {{ localStorage.setItem('tl_paused', paused ? 'true' : 'false'); }} catch(e) {{}}
    }}
    if (pauseBtn && vid) {{
      pauseBtn.addEventListener('click', () => setPause(!vid.paused));
      if (localStorage.getItem('tl_paused') === 'true') {{
        vid.addEventListener('canplay', () => setPause(true), {{ once: true }});
      }}
    }}
    // Snapshot button:
    //   Direct Pi access → POST frame to server (full viewer + Set key snapshot)
    //   Cloudflare       → store frame in localStorage, open static client-side viewer
    //                      (zero data sent to Pi; no DoS vector)
    const dlBtn = document.getElementById('dl-btn');
    if (dlBtn && vid) {{
      dlBtn.addEventListener('click', () => {{
        const canvas = document.createElement('canvas');
        canvas.width  = vid.videoWidth;
        canvas.height = vid.videoHeight;
        canvas.getContext('2d').drawImage(vid, 0, 0);
        if ({is_direct_js}) {{
          const b64 = canvas.toDataURL('image/jpeg', 0.92).split(',')[1];
          const form = document.createElement('form');
          form.method = 'POST';
          form.action = '/timelapse/{date_str}/frame-view';
          form.target = '_blank';
          const inp = document.createElement('input');
          inp.type = 'hidden';
          inp.name = 'image';
          inp.value = b64;
          form.appendChild(inp);
          document.body.appendChild(form);
          form.submit();
          document.body.removeChild(form);
        }} else {{
          try {{ localStorage.setItem('tl_frame_{date_str}', canvas.toDataURL('image/jpeg', 0.92)); }} catch(e) {{}}
          window.open('/timelapse/{date_str}/frame-view-client', '_blank');
        }}
      }});
    }}
    // Keyboard navigation
    // ← / →      : previous / next day
    // ↓           : open chevron (or move down in list)
    // ↑           : move up in list (close chevron from top)
    // Escape      : close chevron
    // Enter       : navigate to kbd-focused list item
    // Space       : pause / play video
    // 1 2 4 8     : set playback speed (1x 2x 4x 8x)
    // Shift+X     : reset all ratings for this date (direct Pi access only)
    (function() {{
      const prev          = {prev_js};
      const next          = {next_js};
      const isDirectAccess = {is_direct_js};
      const details = document.querySelector('details');
      const items   = details ? Array.from(details.querySelectorAll('li')) : [];
      let kbdIdx    = items.findIndex(li => li.classList.contains('current'));
      if (kbdIdx < 0) kbdIdx = 0;

      function setKbdFocus(idx) {{
        items.forEach(li => li.classList.remove('kbd-focus'));
        if (idx >= 0 && idx < items.length) {{
          items[idx].classList.add('kbd-focus');
          items[idx].scrollIntoView({{block: 'nearest'}});
          kbdIdx = idx;
        }}
      }}

      document.addEventListener('keydown', function(e) {{
        const open = details && details.open;

        if (e.key === 'ArrowLeft'  && prev) {{ location.href = '/timelapse/' + prev; return; }}
        if (e.key === 'ArrowRight' && next) {{ location.href = '/timelapse/' + next; return; }}

        if (e.key === 'ArrowDown') {{
          e.preventDefault();
          if (!open) {{
            if (details) {{ details.open = true; setKbdFocus(kbdIdx); }}
          }} else {{
            setKbdFocus(Math.min(kbdIdx + 1, items.length - 1));
          }}
        }}

        if (e.key === 'ArrowUp') {{
          e.preventDefault();
          if (!open) {{
            if (details) {{ details.open = true; setKbdFocus(kbdIdx); }}
          }} else {{
            if (kbdIdx > 0) {{
              setKbdFocus(kbdIdx - 1);
            }} else {{
              details.open = false;
              items.forEach(li => li.classList.remove('kbd-focus'));
            }}
          }}
        }}

        if (e.key === 'Escape' && open) {{
          e.preventDefault();
          details.open = false;
          items.forEach(li => li.classList.remove('kbd-focus'));
        }}

        if (e.key === 'Enter' && open) {{
          const link = items[kbdIdx] && items[kbdIdx].querySelector('a.list-main');
          if (link) location.href = link.href;
        }}

        if (e.key === ' ' && vid) {{
          e.preventDefault();
          setPause(!vid.paused);
        }}
        if (['1','2','4','8'].includes(e.key) && e.target.tagName !== 'INPUT') {{
          setSpeed(parseFloat(e.key));
        }}
        if (e.key === 'X' && e.shiftKey && !e.ctrlKey && !e.metaKey && isDirectAccess) {{
          e.preventDefault();
          if (!confirm('Clear all ratings for {date_str}?')) return;
          fetch('/api/ratings/{date_str}', {{method: 'DELETE'}})
            .then(function(r) {{
              if (r.ok) {{
                document.cookie = 'tl_rated_{date_str}=; max-age=0; path=/; samesite=lax';
                location.reload();
              }} else {{
                alert('Delete failed (' + r.status + ')');
              }}
            }})
            .catch(function() {{ alert('Delete failed \u2014 network error'); }});
        }}
      }});
    }})();
    // Touch swipe navigation
    //   left/right : any day (ignore star rating)    left → older, right → newer
    //   up/down    : 3+ star days only               up → older, down → newer
    //   Vertical swipes: velocity-gated (≥ 0.4 px/ms), must start or end in video area,
    //   and are disabled when the All Timelapses chevron is open.
    (function() {{
      const prev      = {prev_js};
      const next      = {next_js};
      const starDates = {star_dates_js};  // dates with avg rating >= 3 stars
      const details   = document.querySelector('details');
      const vidEl     = document.getElementById('vid');

      // Find prev/next among star-rated dates from current page's date
      const curDate   = '{date_str}';
      const si        = starDates.indexOf(curDate);
      const starPrev  = si > 0 ? starDates[si - 1] : null;
      const starNext  = si >= 0 && si < starDates.length - 1 ? starDates[si + 1] : null;

      function inVideoArea(x, y) {{
        if (!vidEl) return false;
        const r = vidEl.getBoundingClientRect();
        return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
      }}

      var tx = null, ty = null, tt = null, startInVid = false;
      document.addEventListener('touchstart', function(e) {{
        tx = e.touches[0].clientX;
        ty = e.touches[0].clientY;
        tt = Date.now();
        startInVid = inVideoArea(tx, ty);
      }}, {{passive: true}});
      document.addEventListener('touchend', function(e) {{
        if (tx === null) return;
        var ex  = e.changedTouches[0].clientX;
        var ey  = e.changedTouches[0].clientY;
        var dx  = ex - tx;
        var dy  = ey - ty;
        var dt  = Math.max(1, Date.now() - tt);
        tx = null; ty = null; tt = null;
        var adx = Math.abs(dx), ady = Math.abs(dy);
        // Horizontal swipe (left/right): 60px, more horizontal than vertical
        // Any day, no star filter, no video area requirement
        if (adx >= 60 && adx > ady * 1.5) {{
          if (dx < 0 && next) location.href = '/timelapse/' + next;  // left  → newer
          if (dx > 0 && prev) location.href = '/timelapse/' + prev;  // right → older
          return;
        }}
        // Vertical swipe (up/down): 80px, more vertical than horizontal,
        // fast enough (≥ 0.4 px/ms), must start or end in video area,
        // and chevron list must be closed.
        var endInVid = inVideoArea(ex, ey);
        if (ady >= 80 && ady > adx * 1.5 && ady / dt >= 0.4
            && (startInVid || endInVid) && !(details && details.open)) {{
          if (dy > 0 && starNext) location.href = '/timelapse/' + starNext;  // down → newer
          if (dy < 0 && starPrev) location.href = '/timelapse/' + starPrev;  // up   → older
        }}
      }}, {{passive: true}});

      // Mouse wheel / trackpad scroll navigation (desktop)
      // Navigates between 3+ star days. Only at page top; disabled when chevron open.
      var wheelCooldown = 0;
      document.addEventListener('wheel', function(e) {{
        if (details && details.open) return;
        if (window.scrollY > 10) return;
        var now = Date.now();
        if (now - wheelCooldown < 600) return;
        if (Math.abs(e.deltaY) < 40) return;
        if (e.deltaY > 0 && starNext) {{ wheelCooldown = now; location.href = '/timelapse/' + starNext; }}
        if (e.deltaY < 0 && starPrev) {{ wheelCooldown = now; location.href = '/timelapse/' + starPrev; }}
      }}, {{passive: true}});
    }})();
    // Star rating widget (3–5 stars only; one rating per day via cookie)
    // Fully client-side so the HTML page is cacheable by Cloudflare.
    (function() {{
      const dateStr = '{date_str}';
      function getCookie(name) {{
        const m = document.cookie.match('(?:^|;)\\s*' + name + '=([^;]*)');
        return m ? decodeURIComponent(m[1]) : null;
      }}
      let userRated = parseInt(getCookie('tl_rated_' + dateStr)) || null;
      const starsEl = document.querySelectorAll('#stars .star');
      const infoEl  = document.getElementById('rating-info');

      function setLit(upTo) {{
        starsEl.forEach(function(s) {{
          s.classList.toggle('lit', +s.dataset.val <= upTo);
        }});
      }}
      function showInfo(rated, count, avg) {{
        if (!count || avg === null) {{
          infoEl.innerHTML = rated ? 'You rated ' + rated + '\u2605' : '';
          infoEl.style.color = '#f5c518';
          return;
        }}
        const nLit = Math.min(5, Math.max(0, Math.round(avg)));
        const stars = [1,2,3,4,5].map(function(i) {{
          return '<span class="ls' + (i <= nLit ? ' lit' : '') + '">&#9733;</span>';
        }}).join('');
        infoEl.innerHTML = avg.toFixed(1) + '&thinsp;<span class="list-stars">' + stars + '</span>&thinsp;(' + count + ')';
        infoEl.style.color = '#ccc';
      }}
      function freeze(val) {{
        starsEl.forEach(function(s) {{ s.style.pointerEvents = 'none'; }});
        setLit(val);
      }}

      showInfo(userRated, null, null);
      if (userRated) freeze(userRated);

      // Fetch live aggregate from API (served by Cloudflare Worker or Pi /api/ratings/DATE)
      fetch('/api/ratings/' + dateStr)
        .then(function(r) {{ return r.json(); }})
        .then(function(d) {{ showInfo(userRated, d.count || 0, d.avg); }})
        .catch(function() {{}});

      if (!userRated) {{
        starsEl.forEach(function(s) {{
          if (!s.classList.contains('clickable')) return;
          s.addEventListener('mouseenter', function() {{ setLit(+s.dataset.val); }});
          s.addEventListener('mouseleave', function() {{ setLit(0); }});
          s.addEventListener('click', function() {{
            const v = +s.dataset.val;
            fetch('/timelapse/' + dateStr + '/rate', {{
              method: 'POST',
              headers: {{'Content-Type': 'application/json'}},
              body: JSON.stringify({{rating: v}})
            }})
            .then(function(r) {{ return r.json(); }})
            .then(function(d) {{ freeze(v); userRated = v; showInfo(v, d.count, d.avg); }})
            .catch(function() {{
              infoEl.textContent = 'Rating failed \u2014 try again';
              infoEl.style.color = '#e57373';
            }});
          }});
        }});
      }}
    }})();
  </script>
</body>
</html>"""
    from datetime import date as _date, timedelta as _td
    _today     = _date.today().isoformat()
    _yesterday = (_date.today() - _td(days=1)).isoformat()
    if date_str >= _yesterday:
        # Today:     page changes during recording (new preview MP4, chevron grows)
        # Yesterday: "next" button must appear within minutes of today's first MP4
        cache_hdr = 'public, max-age=300, must-revalidate'
    else:
        # Older pages: content is stable, but 1-hour TTL lets the "All Timelapses"
        # chevron slowly propagate to cached pages without meaningful DoS risk
        # (Cloudflare caches per-URL, so a single user browsing old pages primes
        # the cache for everyone else).
        cache_hdr = 'public, max-age=3600'
    return Response(html, mimetype='text/html', headers={'Cache-Control': cache_hdr})
