"""Shared weather API utilities for pumphouse monitor.

Provides WMO weather code descriptions, weather quips, and the current
weather description used by the e-paper display and timelapse pages.
"""
import os
import json as _json
import time as _time
import urllib.request as _ureq

# WMO Weather Interpretation Codes → human description
_WMO = {
    0: "Clear sky",
    1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Heavy freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Heavy freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow",
    77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    85: "Light snow showers", 86: "Snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm w/ hail", 99: "Heavy thunderstorm w/ hail",
}

# Load weather quips from CSV: {description_lower: [quip, ...]}
_QUIPS: dict = {}
try:
    import csv as _csv
    _quips_path = os.path.join(os.path.dirname(__file__), 'weather_quips.csv')
    with open(_quips_path, newline='') as _qf:
        for _row in _csv.DictReader(_qf):
            _QUIPS[_row['Description'].lower()] = [
                _row[k] for k in ('Roast 1', 'Roast 2', 'Roast 3') if _row.get(k)
            ]
except Exception:
    pass

_weather_desc_cache: dict = {'desc': None, 'ts': 0.0}
_forecast_cache: dict = {'codes': None, 'ts': 0.0}
_wind_forecast_cache: dict = {'data': None, 'ts': 0.0}
_current_code_cache: dict = {'code': None, 'ts': 0.0}


def _degrees_to_compass(deg: float) -> str:
    dirs = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW']
    return dirs[round(deg / 22.5) % 16]


def _summarise_wind_hours(hours: list) -> dict | None:
    """Summarise a list of hourly dicts {speed, gust, dir_deg} into a display dict."""
    if not hours:
        return None
    speeds = [h['speed'] for h in hours]
    gusts  = [h['gust']  for h in hours]
    # Weighted-average direction (weight by speed so calm hours don't dominate)
    import math
    sin_sum = sum(h['speed'] * math.sin(math.radians(h['dir_deg'])) for h in hours)
    cos_sum = sum(h['speed'] * math.cos(math.radians(h['dir_deg'])) for h in hours)
    avg_dir = math.degrees(math.atan2(sin_sum, cos_sum)) % 360
    return {
        'speed_min': round(min(speeds)),
        'speed_max': round(max(speeds)),
        'gust_max':  round(max(gusts)),
        'direction': _degrees_to_compass(avg_dir),
    }


def get_wind_forecast() -> dict | None:
    """Return wind forecast for tonight and tomorrow.

    Uses Open-Meteo hourly forecast (same source as forecast_weather_codes).
    Cached for 30 minutes.

    Returns dict with keys 'tonight' and 'tomorrow', each:
        {'speed_min': int, 'speed_max': int, 'gust_max': int, 'direction': str}
    or None on failure.
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    now = _time.time()
    if _wind_forecast_cache['data'] is not None and now - _wind_forecast_cache['ts'] < 1800:
        return _wind_forecast_cache['data']

    try:
        url = (
            'https://api.open-meteo.com/v1/forecast'
            '?latitude=44.6368&longitude=-124.0535'
            '&hourly=windspeed_10m,windgusts_10m,winddirection_10m'
            '&wind_speed_unit=mph'
            '&timezone=America%2FLos_Angeles'
            '&forecast_days=3'
        )
        with _ureq.urlopen(url, timeout=10) as resp:
            data = _json.loads(resp.read())

        hourly = data.get('hourly', {})
        times  = hourly.get('time', [])
        speeds = hourly.get('windspeed_10m', [])
        gusts  = hourly.get('windgusts_10m', [])
        dirs   = hourly.get('winddirection_10m', [])

        tz = ZoneInfo('America/Los_Angeles')
        local_now = datetime.now(tz)
        today     = local_now.date()
        tomorrow  = today + timedelta(days=1)

        tonight_hours  = []
        tomorrow_hours = []

        for i, t_str in enumerate(times):
            if i >= len(speeds):
                break
            dt = datetime.fromisoformat(t_str).replace(tzinfo=tz)
            entry = {'speed': speeds[i] or 0, 'gust': gusts[i] or 0, 'dir_deg': dirs[i] or 0}
            if dt.date() == today and dt >= local_now and dt.hour <= 23:
                tonight_hours.append(entry)
            elif dt.date() == tomorrow and 6 <= dt.hour <= 22:
                tomorrow_hours.append(entry)

        result = {
            'tonight':  _summarise_wind_hours(tonight_hours),
            'tomorrow': _summarise_wind_hours(tomorrow_hours),
        }
        _wind_forecast_cache['data'] = result
        _wind_forecast_cache['ts']   = now
        return result

    except Exception:
        return None


def current_weather_code() -> int | None:
    """Return the current WMO weather code from Open-Meteo's current endpoint.

    More accurate than today's daily forecast code (which pessimistically
    covers the whole day). Cached for 30 minutes.
    """
    now = _time.time()
    if _current_code_cache['code'] is not None and now - _current_code_cache['ts'] < 1800:
        return _current_code_cache['code']
    try:
        url = (
            'https://api.open-meteo.com/v1/forecast'
            '?latitude=44.6368&longitude=-124.0535'
            '&current=weather_code'
            '&timezone=America%2FLos_Angeles'
        )
        with _ureq.urlopen(url, timeout=10) as resp:
            code = _json.loads(resp.read()).get('current', {}).get('weather_code')
        if code is not None:
            _current_code_cache['code'] = int(code)
            _current_code_cache['ts']   = now
            return int(code)
    except Exception:
        pass
    return None


def forecast_weather_codes(days: int = 5) -> list:
    """Return a list of WMO weather codes for today + next (days-1) days.

    Fetches from the Open-Meteo daily forecast endpoint (same lat/lon already
    used by current_weather_desc).  Cached for 30 minutes.  Returns an empty
    list on failure.
    """
    now = _time.time()
    if _forecast_cache['codes'] is not None and now - _forecast_cache['ts'] < 1800:
        return _forecast_cache['codes'][:days]

    codes: list = []
    try:
        url = (
            'https://api.open-meteo.com/v1/forecast'
            '?latitude=44.6368&longitude=-124.0535'
            f'&daily=weather_code&forecast_days={days}'
            '&timezone=America%2FLos_Angeles'
        )
        with _ureq.urlopen(url, timeout=10) as resp:
            data = _json.loads(resp.read())
        codes = [int(c) for c in data.get('daily', {}).get('weather_code', [])]
    except Exception:
        pass

    if codes:
        _forecast_cache['codes'] = codes
        _forecast_cache['ts'] = now
    return codes[:days]


def current_weather_desc():
    """
    Fetch the current weather condition description from NWS KONP latest observation.
    Falls back to Open-Meteo current weather code mapped through _WMO.
    Result is cached for 30 minutes.
    """
    now = _time.time()
    if _weather_desc_cache['desc'] is not None and now - _weather_desc_cache['ts'] < 1800:
        return _weather_desc_cache['desc']

    desc = None
    try:
        req = _ureq.Request(
            'https://api.weather.gov/stations/KONP/observations/latest',
            headers={'User-Agent': 'pumphouse-monitor/1.0', 'Accept': 'application/geo+json'},
        )
        with _ureq.urlopen(req, timeout=10) as resp:
            props = _json.loads(resp.read()).get('properties', {})
        desc = props.get('textDescription', '').strip() or None
    except Exception:
        pass

    if not desc:
        try:
            url = (
                'https://api.open-meteo.com/v1/forecast'
                '?latitude=44.6368&longitude=-124.0535'
                '&current=weather_code'
                '&timezone=America%2FLos_Angeles'
            )
            with _ureq.urlopen(url, timeout=10) as resp:
                code = _json.loads(resp.read()).get('current', {}).get('weather_code')
            if code is not None:
                desc = _WMO.get(int(code))
        except Exception:
            pass

    if desc:
        _weather_desc_cache['desc'] = desc
        _weather_desc_cache['ts'] = now
    return desc
