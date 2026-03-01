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
