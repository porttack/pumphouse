#!/usr/bin/env python3
"""
Web dashboard for pumphouse monitoring system
Serves HTTPS on port 6443 with basic authentication
"""
import os
import csv
import argparse
import io
import threading
import time as _time
from datetime import datetime, timedelta
from flask import Flask, render_template, request, Response, jsonify, send_file
from functools import wraps

from monitor import __version__
from monitor.config import (
    EVENTS_FILE, RESERVATIONS_FILE, DEFAULT_SNAPSHOTS_FILE, DAILY_CSV,
    NOTIFY_BACKFLUSH_TIME_START, NOTIFY_BACKFLUSH_TIME_END,
    TANK_URL, TANK_HEIGHT_INCHES, TANK_CAPACITY_GALLONS,
    EPAPER_CONSERVE_WATER_THRESHOLD, EPAPER_OWNER_STAY_TYPES,
    EPAPER_DEFAULT_HOURS_TENANT, EPAPER_DEFAULT_HOURS_OTHER,
    EPAPER_LOW_WATER_HOURS_THRESHOLD, EPAPER_LOW_WATER_HOURS,
    EPAPER_FORECAST_DAYS, EPAPER_MIN_GRAPH_RANGE_PCT, EPAPER_MAX_GRAPH_PCT,
    DASHBOARD_HIDE_EVENT_TYPES,
    DASHBOARD_MAX_EVENTS, DASHBOARD_DEFAULT_HOURS, DASHBOARD_SNAPSHOT_COUNT,
    SECRET_OVERRIDE_ON_TOKEN, SECRET_OVERRIDE_OFF_TOKEN,
    SECRET_BYPASS_ON_TOKEN, SECRET_BYPASS_OFF_TOKEN,
    SECRET_PURGE_TOKEN, MANAGEMENT_FEE_PERCENT,
    AMBIENT_WEATHER_DASHBOARD_URL,
    CAMERA_USER, CAMERA_PASS,
    RING_TOKEN_FILE, RING_CAMERA_NAME,
    DASHBOARD_URL,
    PRESSURE_LOW_WATCH_FILE,
    OVERRIDE_MANUAL_OFF_FILE,
    BYPASS_TIMER_FILE, PURGE_PENDING_FILE,
    BYPASS_CYCLE_FILE,
    BYPASS_CYCLE_ON_HOURS,
    BYPASS_CYCLE_OFF_HOURS,
    NATIONAL_WEATHER_URL
)
from monitor.gpio_helpers import (
    read_pressure, read_float_sensor, init_gpio, cleanup_gpio,
    FLOAT_STATE_FULL, FLOAT_STATE_CALLING
)
from monitor.tank import get_tank_data
from monitor.check import read_temp_humidity, format_pressure_state, format_float_state
from monitor.relay import get_all_relay_status, set_supply_override, set_bypass
from monitor.stats import find_last_refill
from monitor.occupancy import (
    get_occupancy_status, get_current_and_upcoming_reservations,
    load_reservations, format_date_short, get_next_reservation, get_checkin_datetime
)
from monitor.gph_calculator import get_cached_gph, format_gph_for_display
from monitor import ring_camera

# Matplotlib imports for chart generation
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

app = Flask(__name__)

import logging as _logging  # noqa: E402
_logging.basicConfig(level=_logging.WARNING, format='%(name)s %(levelname)s %(message)s')

from flask_compress import Compress  # noqa: E402
Compress(app)

from monitor.web_timelapse import timelapse_bp  # noqa: E402
from monitor.weather_api import current_weather_desc, get_wind_forecast  # noqa: E402
app.register_blueprint(timelapse_bp)

# Configuration
USERNAME = os.environ.get('PUMPHOUSE_USER', 'admin')
PASSWORD = os.environ.get('PUMPHOUSE_PASS', 'pumphouse')
STARTUP_TIME = datetime.now()  # Track when web server started


# Tokens derived from bypass_on secret — no secrets.conf changes needed
SECRET_BYPASS_TIMED_TOKEN        = (SECRET_BYPASS_ON_TOKEN + '-4h')           if SECRET_BYPASS_ON_TOKEN else ''
SECRET_BYPASS_CANCEL_TIMER_TOKEN = (SECRET_BYPASS_ON_TOKEN + '-cancel')        if SECRET_BYPASS_ON_TOKEN else ''
SECRET_BYPASS_CYCLE_TOKEN        = (SECRET_BYPASS_ON_TOKEN + '-cycle')         if SECRET_BYPASS_ON_TOKEN else ''
SECRET_TEST_FILTER_TOKEN         = (SECRET_BYPASS_ON_TOKEN + '-test-filter')   if SECRET_BYPASS_ON_TOKEN else ''


SECRET_PURGE_NEXT_TOKEN        = (SECRET_PURGE_TOKEN + '-next')   if SECRET_PURGE_TOKEN else ''
SECRET_PURGE_CANCEL_NEXT_TOKEN = (SECRET_PURGE_TOKEN + '-cancel') if SECRET_PURGE_TOKEN else ''


def get_purge_pending():
    """Return True if a pressure-timed purge is waiting for the next pump cycle."""
    return PURGE_PENDING_FILE.exists()


def get_bypass_timer_expiry():
    """Return datetime when bypass auto-off fires, or None if no timer set."""
    try:
        if BYPASS_TIMER_FILE.exists():
            return datetime.fromtimestamp(float(BYPASS_TIMER_FILE.read_text().strip()))
    except Exception:
        pass
    return None


def get_bypass_cycle_info():
    """Return cycle dict {on_hours, off_hours, next_transition (datetime), next_state} or None."""
    import json as _json
    try:
        if BYPASS_CYCLE_FILE.exists():
            d = _json.loads(BYPASS_CYCLE_FILE.read_text())
            d['next_transition'] = datetime.fromtimestamp(d['next_transition'])
            return d
    except Exception:
        pass
    return None


def _write_cycle(on_hours, off_hours, next_transition_dt, next_state):
    import json as _json
    BYPASS_CYCLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BYPASS_CYCLE_FILE.write_text(_json.dumps({
        'on_hours': on_hours,
        'off_hours': off_hours,
        'next_transition': next_transition_dt.timestamp(),
        'next_state': next_state,
    }))


def _cancel_all_bypass_modes():
    """Clear both timer and cycle state files."""
    BYPASS_TIMER_FILE.unlink(missing_ok=True)
    BYPASS_CYCLE_FILE.unlink(missing_ok=True)


def _bypass_timer_watchdog():
    """Background thread: handles one-shot timer expiry and cycle transitions."""
    while True:
        _time.sleep(30)

        # One-shot timer
        expiry = get_bypass_timer_expiry()
        if expiry and datetime.now() >= expiry:
            try:
                BYPASS_TIMER_FILE.unlink(missing_ok=True)
                set_bypass('OFF', debug=False)
            except Exception as e:
                print(f'bypass_timer_watchdog: {e}')

        # Cycle mode
        cycle = get_bypass_cycle_info()
        if cycle and datetime.now() >= cycle['next_transition']:
            try:
                next_state = cycle['next_state']
                set_bypass(next_state, debug=False)
                on_h, off_h = cycle['on_hours'], cycle['off_hours']
                if next_state == 'OFF':
                    new_trans = datetime.now() + timedelta(hours=off_h)
                    new_next  = 'ON'
                else:
                    new_trans = datetime.now() + timedelta(hours=on_h)
                    new_next  = 'OFF'
                _write_cycle(on_h, off_h, new_trans, new_next)
            except Exception as e:
                print(f'bypass_cycle_watchdog: {e}')


threading.Thread(target=_bypass_timer_watchdog, daemon=True, name='bypass-timer').start()

# Custom Jinja filter for human-friendly timestamp formatting
@app.template_filter('human_time')
def human_time_filter(timestamp_str):
    """Convert timestamp to 3-letter day and HH:MM format (e.g., 'Mon 14:23')"""
    try:
        if isinstance(timestamp_str, str):
            dt = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S.%f')
        elif isinstance(timestamp_str, datetime):
            dt = timestamp_str
        else:
            return timestamp_str
        return dt.strftime('%a %-I:%M %p')
    except:
        return timestamp_str

def check_auth(username, password):
    """Check if username/password is valid"""
    return username == USERNAME and password == PASSWORD

def authenticate():
    """Send 401 response for authentication"""
    return Response(
        'Authentication required',
        401,
        {'WWW-Authenticate': 'Basic realm="Pumphouse Monitor"'}
    )

def requires_auth(f):
    """Decorator for basic auth"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

def read_csv_tail(filepath, max_rows=20):
    """Read last N rows from CSV file"""
    if not os.path.exists(filepath):
        return [], []

    try:
        with open(filepath, 'r') as f:
            reader = csv.reader(f)
            rows = list(reader)

            if len(rows) == 0:
                return [], []

            headers = rows[0]
            data = rows[1:]  # Skip header

            # Return last max_rows
            return headers, data[-max_rows:]
    except Exception as e:
        return [], []

def read_events_by_time(filepath, hours=72):
    """Read events from CSV file filtered by time window (hours)"""
    if not os.path.exists(filepath):
        return [], []

    try:
        with open(filepath, 'r') as f:
            reader = csv.reader(f)
            rows = list(reader)

            if len(rows) == 0:
                return [], []

            headers = rows[0]
            data = rows[1:]  # Skip header

            # Find timestamp column index
            timestamp_idx = None
            if 'timestamp' in headers:
                timestamp_idx = headers.index('timestamp')

            if timestamp_idx is None:
                # Fallback to last N rows if no timestamp column
                return headers, data[-500:]

            # Filter by time
            now = datetime.now()
            cutoff = now.timestamp() - (hours * 3600)

            filtered_data = []
            for row in data:
                if len(row) <= timestamp_idx:
                    continue

                try:
                    ts = datetime.fromisoformat(row[timestamp_idx])
                    if ts.timestamp() >= cutoff:
                        filtered_data.append(row)
                except:
                    continue

            return headers, filtered_data

    except Exception as e:
        return [], []

def get_hourly_gph(filepath=DEFAULT_SNAPSHOTS_FILE, blocks=6, block_hours=2):
    """Return list of {label, delta} for the last `blocks` complete 2-hour blocks.

    Snaps to even block boundaries and excludes the current (incomplete) block.
    delta is net gallons change in that block (positive = filling, negative = draining).
    Returns [] on error or missing data.
    """
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, 'r') as f:
            rows = list(csv.DictReader(f))
        now = datetime.now()
        # Snap to the start of the current block boundary
        block_start_hour = (now.hour // block_hours) * block_hours
        current_block_start = now.replace(hour=block_start_hour, minute=0, second=0, microsecond=0)
        result = []
        for i in range(blocks, 0, -1):
            start = current_block_start - timedelta(hours=i * block_hours)
            end   = start + timedelta(hours=block_hours)
            bucket = []
            bypass_on = False
            for r in rows:
                if not r.get('tank_gallons'):
                    continue
                ts = datetime.fromisoformat(r['timestamp'])
                if start <= ts < end:
                    bucket.append(float(r['tank_gallons']))
                    if r.get('relay_bypass', '').upper() == 'ON':
                        bypass_on = True
            if len(bucket) >= 2:
                delta = bucket[-1] - bucket[0]
            elif bucket:
                delta = 0.0
            else:
                delta = None
            label = start.strftime('%-I%p').lower()
            result.append({'label': label, 'delta': round(delta, 0) if delta is not None else None, 'bypass': bypass_on})
        return result
    except Exception:
        return []


def get_snapshots_stats(filepath=DEFAULT_SNAPSHOTS_FILE):
    """Calculate aggregate stats from snapshots.csv for 1hr and 24hr windows"""
    if not os.path.exists(filepath):
        return None

    try:
        with open(filepath, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if len(rows) == 0:
            return None

        now = datetime.now()
        one_hour_ago        = now.timestamp() - 3600
        two_hours_ago       = now.timestamp() - 7200
        twelve_hours_ago    = now.timestamp() - 43200
        twenty_four_hours_ago = now.timestamp() - 86400

        stats = {
            'tank_change_1hr': None,
            'tank_change_24hr': None,
            'pressure_high_pct_2hr': None,
            'pressure_high_pct_12hr': None,
            'pressure_high_pct_24hr': None,
            'last_refill_50_days': None,
            'last_refill_50_timestamp': None
        }

        # Parse timestamps and filter rows
        rows_1hr  = []
        rows_2hr  = []
        rows_12hr = []
        rows_24hr = []

        for row in rows:
            try:
                ts = datetime.fromisoformat(row['timestamp']).timestamp()
                if ts >= one_hour_ago:
                    rows_1hr.append(row)
                if ts >= two_hours_ago:
                    rows_2hr.append(row)
                if ts >= twelve_hours_ago:
                    rows_12hr.append(row)
                if ts >= twenty_four_hours_ago:
                    rows_24hr.append(row)
            except:
                continue

        # Calculate tank level changes
        if len(rows_1hr) >= 2:
            try:
                first_gallons = float(rows_1hr[0]['tank_gallons'])
                last_gallons = float(rows_1hr[-1]['tank_gallons'])
                stats['tank_change_1hr'] = last_gallons - first_gallons
            except:
                pass

        if len(rows_24hr) >= 2:
            try:
                first_gallons = float(rows_24hr[0]['tank_gallons'])
                last_gallons = float(rows_24hr[-1]['tank_gallons'])
                stats['tank_change_24hr'] = last_gallons - first_gallons
            except:
                pass

        # Calculate pressure HIGH percentages for 2hr, 12hr, 24hr windows
        # Bypass-on intervals are excluded — pressure reading is meaningless then.
        def _pressure_pct(row_list):
            total, high = 0.0, 0.0
            for row in row_list:
                if row.get('relay_bypass', '').upper() == 'ON':
                    continue
                total += float(row['duration_seconds'])
                high  += float(row['pressure_high_seconds'])
            return (high / total * 100) if total > 0 else None

        try:
            stats['pressure_high_pct_2hr']  = _pressure_pct(rows_2hr)
        except: pass
        try:
            stats['pressure_high_pct_12hr'] = _pressure_pct(rows_12hr)
        except: pass
        try:
            stats['pressure_high_pct_24hr'] = _pressure_pct(rows_24hr)
        except: pass

        # Find last time tank increased by 50+ gallons using shared stats module
        refill_ts, days_ago = find_last_refill(filepath, threshold_gallons=50)
        if refill_ts and days_ago is not None:
            stats['last_refill_50_timestamp'] = refill_ts
            stats['last_refill_50_days'] = days_ago

        # Find the most recent snapshot that had any pressure HIGH seconds
        stats['last_pressure_high'] = None
        for row in reversed(rows):
            try:
                if float(row.get('pressure_high_seconds', 0)) > 0:
                    lph = datetime.fromisoformat(row['timestamp'])
                    delta_days = (now.date() - lph.date()).days
                    if delta_days == 0:
                        prefix = 'Today'
                    elif delta_days == 1:
                        prefix = 'Yesterday'
                    elif delta_days < 7:
                        prefix = lph.strftime('%A')
                    else:
                        prefix = lph.strftime('%b %-d')
                    stats['last_pressure_high'] = f"{prefix} {lph.strftime('%-I:%M %p')}"
                    break
            except:
                continue

        return stats

    except Exception as e:
        return None

def build_calendar_months(all_reservations, num_months=19):
    """Build calendar data for the availability calendar (current month + 18 more).

    Each cell dict: {'day': int|None, 'cls': str, 'style': str|None, 'today': bool}
    Split days use inline linear-gradient style:
      check-in  → right half colored
      check-out → left half colored
      turn day  → left 1/3 (out color) + right 1/3 (in color), middle free
    """
    import calendar as _cal
    from datetime import date as _date

    today = _date.today()

    # Per-type background and foreground colors (must match CSS classes)
    _BG = {'airbnb': '#4a1422', 'vrbo': '#00312e', 'guest': '#1e3a5f', 'owner': '#4a2e00'}
    _FG = {'airbnb': '#e87090', 'vrbo': '#40c8a8', 'guest': '#7ab0e8', 'owner': '#e8a030'}
    _FREE = '#1a1a1a'
    _cls_map = {'owner': 'cal-owner', 'airbnb': 'cal-airbnb', 'vrbo': 'cal-vrbo', 'guest': 'cal-guest'}

    def _res_type(res):
        t = res.get('Type', '')
        if 'Owner' in t:      return 'owner'
        if t.lower() == 'airbnb': return 'airbnb'
        if t.lower() == 'vrbo':   return 'vrbo'
        return 'guest'

    # day_info[date] = {'in': type, 'out': type, 'mid': type}  (keys present only when set)
    day_info = {}
    monthly_income = {}

    for res in all_reservations:
        status = res.get('Status', '').lower()
        if not any(s in status for s in ('confirmed', 'checked in', 'checked out')):
            continue
        try:
            checkin  = _date.fromisoformat(res.get('Check-In', ''))
            checkout = _date.fromisoformat(res.get('Checkout', ''))
        except ValueError:
            continue
        day_type = _res_type(res)

        day_info.setdefault(checkin,  {})['in']  = day_type
        cur = checkin + timedelta(days=1)
        while cur < checkout:
            day_info.setdefault(cur, {})['mid'] = day_type
            cur += timedelta(days=1)
        day_info.setdefault(checkout, {})['out'] = day_type

        if day_type != 'owner':
            try:
                gross = float(res.get('Income', 0) or 0)
                net = gross * (1 - MANAGEMENT_FEE_PERCENT / 100)
                mk = checkout.strftime('%Y-%m')
                monthly_income[mk] = monthly_income.get(mk, 0) + net
            except (ValueError, TypeError):
                pass

    def _cell_appearance(d, is_past, is_current_month):
        """Return (cls, style) for a day cell."""
        # Past days outside the current month: always dim, no booking detail
        if is_past and not is_current_month:
            return 'cal-past', None

        info = day_info.get(d, {})
        mid  = info.get('mid')
        cin  = info.get('in')
        cout = info.get('out')

        # Full stay day (middle of a reservation)
        if mid:
            return _cls_map[mid], None

        # Split days — use inline linear-gradient
        if cin and cout:
            # Turn day: left 1/3 = checkout color, middle = free, right 1/3 = checkin color
            s = (f'background:linear-gradient(to right,'
                 f'{_BG[cout]} 33%,{_FREE} 33% 67%,{_BG[cin]} 67%);color:#bbb')
            return '', s
        if cin:
            s = (f'background:linear-gradient(to right,'
                 f'{_FREE} 50%,{_BG[cin]} 50%);color:{_FG[cin]}')
            return '', s
        if cout:
            s = (f'background:linear-gradient(to right,'
                 f'{_BG[cout]} 50%,{_FREE} 50%);color:{_FG[cout]}')
            return '', s

        return ('cal-past' if is_past else 'cal-free'), None

    months = []
    yr, mo = today.year, today.month
    cal = _cal.Calendar(firstweekday=6)  # Sunday-first
    is_current_month = True
    for _ in range(num_months):
        first = _date(yr, mo, 1)
        week_rows = []
        for week in cal.monthdayscalendar(yr, mo):
            row = []
            for day_num in week:
                if day_num == 0:
                    row.append({'day': None, 'cls': 'cal-pad', 'style': None, 'today': False})
                else:
                    d = _date(yr, mo, day_num)
                    cls, style = _cell_appearance(d, d < today, is_current_month)
                    row.append({'day': day_num, 'cls': cls, 'style': style, 'today': d == today})
            week_rows.append(row)
        mk = first.strftime('%Y-%m')
        income = monthly_income.get(mk)
        months.append({
            'name': first.strftime('%b %Y'),
            'weeks': week_rows,
            'income': f'${income:,.0f}' if income else None,
        })
        is_current_month = False
        mo += 1
        if mo > 12:
            mo = 1
            yr += 1
    return months


def get_sensor_data():
    """Read current sensor states"""
    # Note: We don't call init_gpio() here because the monitor process may be using GPIO.
    # The read functions now have fallback logic using the gpio command-line tool.

    data = {
        'pressure': None,
        'float': None,
        'temp': None,
        'humidity': None,
        'gpio_available': True  # Sensors readable via gpio command fallback
    }

    # Read sensors - will use gpio command fallback if monitor is running
    data['pressure'] = read_pressure()
    data['float'] = read_float_sensor()

    # Read temp/humidity (requires I2C, not GPIO)
    temp_f, humidity = read_temp_humidity()
    data['temp'] = temp_f
    data['humidity'] = humidity

    return data


def get_outdoor_weather():
    """Get outdoor weather data from latest snapshot"""
    try:
        headers, rows = read_csv_tail(DEFAULT_SNAPSHOTS_FILE, max_rows=1)
        if not headers or not rows:
            return None

        row = rows[0]
        data = dict(zip(headers, row))

        outdoor_temp = data.get('outdoor_temp_f')
        outdoor_humidity = data.get('outdoor_humidity')

        if outdoor_temp and outdoor_humidity:
            return {
                'temp': float(outdoor_temp) if outdoor_temp else None,
                'humidity': float(outdoor_humidity) if outdoor_humidity else None
            }
        return None
    except Exception:
        return None


def get_service_uptimes():
    """Return list of {name, state, since} for the three pumphouse services plus the Pi itself."""
    import subprocess
    SERVICES = [
        ('monitor',   'pumphouse-monitor.service'),
        ('web',       'pumphouse-web.service'),
        ('timelapse', 'pumphouse-timelapse.service'),
    ]
    result = []

    # Pi uptime from /proc/uptime (first field = seconds since boot)
    try:
        with open('/proc/uptime') as _f:
            _boot_seconds = float(_f.read().split()[0])
        _boot_since = datetime.now() - timedelta(seconds=_boot_seconds)
        result.append({'name': 'pi', 'state': 'active', 'since': _boot_since})
    except Exception:
        result.append({'name': 'pi', 'state': 'unknown', 'since': None})

    for label, svc in SERVICES:
        try:
            out = subprocess.run(
                ['systemctl', 'show', svc,
                 '--property=ActiveState,ActiveEnterTimestamp'],
                capture_output=True, text=True, timeout=5
            ).stdout
            props = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
            state = props.get('ActiveState', 'unknown')
            ts_str = props.get('ActiveEnterTimestamp', '')
            since = None
            if ts_str:
                try:
                    # systemd format: "Mon 2026-04-05 00:40:43 PDT"
                    since = datetime.strptime(ts_str, '%a %Y-%m-%d %H:%M:%S %Z')
                except Exception:
                    pass
            result.append({'name': label, 'state': state, 'since': since})
        except Exception:
            result.append({'name': label, 'state': 'unknown', 'since': None})
    return result


def get_internet_uptime():
    """Fetch uptime stats from Cloudflare worker and compute 24h and 7d metrics."""
    import urllib.request
    import json
    try:
        url = 'https://onblackberryhill.com/internet.json'
        req = urllib.request.Request(url, headers={'User-Agent': 'pumphouse-dashboard/1.0'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            entries = json.loads(resp.read().decode())
    except Exception:
        return None

    if not entries:
        return None

    import time as _time_mod
    now_ms = _time_mod.time() * 1000

    def compute_window(window_ms):
        cutoff_ms = now_ms - window_ms
        # Sort by timestamp
        sorted_entries = sorted(entries, key=lambda e: e['ts'])
        # Seed: last entry before the window, clamped to window start
        in_window = [e for e in sorted_entries if _ts_ms(e['ts']) >= cutoff_ms]
        seed = next((e for e in reversed(sorted_entries) if _ts_ms(e['ts']) < cutoff_ms), None)
        if seed:
            seeded = [{'ts': datetime.utcfromtimestamp(cutoff_ms / 1000).strftime('%Y-%m-%dT%H:%M:%S.000Z'),
                       'up': seed['up']}] + in_window
        else:
            seeded = in_window

        if not seeded:
            return None

        down_ms = 0
        down_start = None
        for e in seeded:
            t = _ts_ms(e['ts'])
            if not e['up'] and down_start is None:
                down_start = t
            elif e['up'] and down_start is not None:
                down_ms += t - down_start
                down_start = None
        if down_start is not None:
            down_ms += now_ms - down_start

        down_min = round(down_ms / 60000)
        pct = ((window_ms - down_ms) / window_ms * 100) if window_ms > 0 else 100
        return {'down_min': down_min, 'pct': round(pct, 2)}

    def _ts_ms(ts_str):
        # Parse ISO 8601 UTC timestamps — must attach utc timezone before
        # calling .timestamp() so Python doesn't misinterpret as local time.
        from datetime import timezone as _tz
        for fmt in ('%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%SZ'):
            try:
                return datetime.strptime(ts_str, fmt).replace(tzinfo=_tz.utc).timestamp() * 1000
            except ValueError:
                continue
        return 0

    hour_ms = 3600 * 1000
    stats_24h = compute_window(24 * hour_ms)
    stats_7d  = compute_window(7 * 24 * hour_ms)

    # Current status from latest entry
    sorted_entries = sorted(entries, key=lambda e: e['ts'])
    latest = sorted_entries[-1] if sorted_entries else None

    # Qualifying outages — matches Cloudflare worker: skip completed outages < 2 min
    MIN_OUTAGE_MS = 2 * 60 * 1000
    def qualifying_outages(ents):
        intervals = []
        down_start = None
        for e in ents:
            t = _ts_ms(e['ts'])
            if not e['up'] and down_start is None:
                down_start = t
            elif e['up'] and down_start is not None:
                if t - down_start >= MIN_OUTAGE_MS:
                    intervals.append((down_start, t))
                down_start = None
        if down_start is not None:
            intervals.append((down_start, None))  # ongoing
        return intervals

    all_outages = qualifying_outages(sorted_entries)

    since = None
    try:
        if latest and latest['up']:
            ended = [o for o in all_outages if o[1] is not None]
            since_ms = ended[-1][1] if ended else _ts_ms(sorted_entries[0]['ts'])
        elif latest:
            ongoing = next((o for o in all_outages if o[1] is None), None)
            since_ms = ongoing[0] if ongoing else _ts_ms(latest['ts'])
        else:
            since_ms = None
        if since_ms:
            since = datetime.fromtimestamp(since_ms / 1000).strftime('%-I:%M %p %-m/%-d')
    except Exception:
        since = None

    return {
        'stats_24h': stats_24h,
        'stats_7d': stats_7d,
        'currently_up': latest['up'] if latest else None,
        'since': since,
    }


def get_cached_ecobee_temp(max_age_hours=24):
    """Get cached Ecobee temperature data from CSV"""
    try:
        import csv as csv_module
        from pathlib import Path

        cache_file = Path(__file__).parent.parent / 'ecobee_temp_cache.csv'

        if not cache_file.exists():
            return None

        with open(cache_file, 'r') as f:
            reader = csv_module.DictReader(f)
            rows = list(reader)

        if not rows:
            return None

        # Check age using first row's timestamp
        cache_time = datetime.fromisoformat(rows[0]['timestamp'])
        age_hours = (datetime.now() - cache_time).total_seconds() / 3600

        if max_age_hours is not None and age_hours > max_age_hours:
            return None

        # Calculate age string
        age_minutes = (datetime.now() - cache_time).total_seconds() / 60
        if age_minutes < 60:
            age_str = f"{int(age_minutes)}m ago"
        else:
            age_str = f"{age_minutes/60:.1f}h ago"

        # Convert to a dict format for easier use in templates
        # Format: {'timestamp': '...', 'age_str': '...', 'thermostats': {'Name': {'temperature': 72, ...}}}
        result = {
            'timestamp': rows[0]['timestamp'],
            'age_str': age_str,
            'thermostats': {}
        }

        for row in rows:
            result['thermostats'][row['thermostat_name']] = {
                'temperature': float(row['temperature']),
                'heat_setpoint': float(row['heat_setpoint']) if row.get('heat_setpoint') else None,
                'cool_setpoint': float(row['cool_setpoint']) if row.get('cool_setpoint') else None,
                'system_mode': row.get('system_mode'),
                'hold_text': row.get('hold_text'),
                'vacation_mode': row.get('vacation_mode') == 'True'
            }

        return result
    except Exception:
        return None

@app.route('/api/chart_data')
# @requires_auth
def chart_data():
    """API endpoint to serve chart data as JSON"""
    hours = request.args.get('hours', 24, type=int)

    try:
        with open(DEFAULT_SNAPSHOTS_FILE, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if len(rows) == 0:
            return jsonify({'timestamps': [], 'gallons': [], 'pointColors': []})

        # Filter by time range
        now = datetime.now()
        cutoff = now.timestamp() - (hours * 3600)

        # Import stagnation and full-flow parameters
        from monitor.config import (
            NOTIFY_WELL_RECOVERY_STAGNATION_HOURS,
            NOTIFY_WELL_RECOVERY_MAX_STAGNATION_GAIN,
            NOTIFY_FULL_FLOW_PRESSURE_THRESHOLD,
            OVERRIDE_ON_THRESHOLD
        )

        # First pass: collect all data points in time range
        data_points = []
        for row in rows:
            try:
                ts = datetime.fromisoformat(row['timestamp'])
                if ts.timestamp() >= cutoff:
                    pressure_pct = float(row.get('pressure_high_percent', 0))
                    data_points.append({
                        'timestamp': ts,
                        'gallons': float(row['tank_gallons']),
                        'pressure_pct': pressure_pct,
                        'occupied': row.get('occupied', '').upper() == 'YES',
                        'bypass': row.get('relay_bypass', '').upper() == 'ON',
                    })
            except:
                continue

        if len(data_points) == 0:
            return jsonify({'timestamps': [], 'gallons': [], 'pointColors': []})

        # Second pass: identify stagnant periods
        # Use simpler logic: for each point, look back 6 hours and check if gain ≤ 30 gal
        stagnation_window_seconds = NOTIFY_WELL_RECOVERY_STAGNATION_HOURS * 3600

        timestamps = []
        gallons = []
        point_colors = []
        pressure_pcts = []

        for i, point in enumerate(data_points):
            timestamps.append(point['timestamp'].strftime('%a %H:%M'))
            gallons.append(point['gallons'])
            pressure_pcts.append(point['pressure_pct'])

            # PRIORITY 1: Check for full-flow (pressure >= threshold)
            if point['pressure_pct'] >= NOTIFY_FULL_FLOW_PRESSURE_THRESHOLD:
                point_colors.append('#f44336')  # Red - full flow
                continue

            # PRIORITY 2: Color by occupancy
            if point.get('occupied'):
                point_colors.append('#ff9800')  # Orange - occupied
            else:
                point_colors.append('#4CAF50')  # Green - unoccupied

        # Compute 2-hour block GPH for every block in the chart window
        block_hours = 2
        block_gph_map = {}
        earliest = data_points[0]['timestamp']
        # Walk all complete 2-hour blocks from earliest point to now
        bstart = earliest.replace(
            hour=(earliest.hour // block_hours) * block_hours,
            minute=0, second=0, microsecond=0
        )
        while bstart < now:
            bend = bstart + timedelta(hours=block_hours)
            in_block = [p for p in data_points if bstart <= p['timestamp'] < bend]
            bucket = [p['gallons'] for p in in_block]
            if len(bucket) >= 2:
                bypass = any(p['bypass'] for p in in_block)
                block_gph_map[bstart] = {'gph': round((bucket[-1] - bucket[0]) / block_hours, 1), 'bypass': bypass}
            bstart = bend

        block_gph = []
        block_bypass = []
        for point in data_points:
            ts = point['timestamp']
            bstart = ts.replace(hour=(ts.hour // block_hours) * block_hours, minute=0, second=0, microsecond=0)
            entry = block_gph_map.get(bstart)
            block_gph.append(entry['gph'] if entry else None)
            block_bypass.append(entry['bypass'] if entry else False)

        return jsonify({
            'timestamps': timestamps,
            'gallons': gallons,
            'pointColors': point_colors,
            'blockGph': block_gph,
            'blockBypass': block_bypass,
            'pressurePct': pressure_pcts,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chart.png')
def chart_image():
    """
    Generate and serve tank level chart as PNG image (unauthenticated for ntfy)
    Uses matplotlib to generate a chart matching the Chart.js dashboard style
    """
    hours = request.args.get('hours', 24, type=int)

    try:
        # Get chart data
        with open(DEFAULT_SNAPSHOTS_FILE, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if len(rows) == 0:
            return Response('No data', status=404)

        # Filter by time range
        now = datetime.now()
        cutoff = now - timedelta(hours=hours)

        timestamps = []
        gallons = []

        for row in rows:
            try:
                ts = datetime.fromisoformat(row['timestamp'])
                if ts >= cutoff:
                    gal = float(row['tank_gallons'])
                    # Only append if both timestamp and gallons are valid
                    timestamps.append(ts)
                    gallons.append(gal)
            except:
                continue

        if len(timestamps) < 2:
            return Response('Insufficient data', status=404)

        # Create figure with dark theme matching Chart.js dashboard
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(10, 6), dpi=100)
        fig.patch.set_facecolor('#1a1a1a')
        ax.set_facecolor('#1a1a1a')

        # Plot data - line with fill to match Chart.js look
        ax.plot(timestamps, gallons, color='#4CAF50', linewidth=2, zorder=2)
        ax.fill_between(timestamps, gallons, alpha=0.3, color='#4CAF50', zorder=1)

        # Title
        if hours <= 24:
            title = f'Tank Level - Last {hours} Hours'
        elif hours <= 168:
            title = f'Tank Level - Last {hours // 24} Days'
        else:
            title = f'Tank Level - Last {hours} Hours'
        ax.set_title(title, color='#e0e0e0', fontsize=16, pad=20)

        # Labels
        ax.set_xlabel('Time', color='#888', fontsize=12)
        ax.set_ylabel('Gallons', color='#888', fontsize=12)

        # Grid (matching Chart.js grid)
        ax.grid(True, alpha=0.2, color='#333', linewidth=0.8)

        # Format x-axis based on time window
        if hours <= 24:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%-I:%M %p'))
            ax.xaxis.set_major_locator(mdates.HourLocator(interval=max(1, hours // 6)))
        elif hours <= 72:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %-I %p'))
            ax.xaxis.set_major_locator(mdates.HourLocator(interval=12))
        else:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
            ax.xaxis.set_major_locator(mdates.DayLocator())

        plt.xticks(rotation=45, ha='right')

        # Tick colors (matching Chart.js)
        ax.tick_params(colors='#888', labelsize=10)

        # Spine colors
        for spine in ax.spines.values():
            spine.set_color('#444')
            spine.set_linewidth(1)

        # Adjust layout to prevent label cutoff
        plt.tight_layout()

        # Save to BytesIO
        buf = io.BytesIO()
        plt.savefig(buf, format='png', facecolor='#1a1a1a', edgecolor='none',
                   bbox_inches='tight', dpi=100)
        buf.seek(0)
        plt.close(fig)

        return send_file(buf, mimetype='image/png')

    except Exception as e:
        return Response(f'Error: {str(e)}', status=500)

@app.route('/api/epaper.bmp')
def epaper_bmp():
    """
    Generate a 250x122 1-bit BMP for a 2.13" e-Paper display.
    Shows gallons available, percent full, and a water usage graph.
    Unauthenticated so it can be fetched via wget.

    Query params:
        hours     - hours of history for graph (default 3)
        tenant    - override: "yes" = force tenant mode, "no" = force owner/unoccupied mode
        occupied  - override: "yes" = force occupied, "no" = force unoccupied
        threshold - override: percent value for low-water threshold (e.g. 95)
        scale     - integer multiplier for resolution (default 1 = 250x122, 4 = 1000x488)
    """
    from PIL import Image, ImageDraw, ImageFont, ImageChops
    from monitor.occupancy import load_reservations, is_occupied, get_next_reservation, get_checkin_datetime
    from monitor.weather_api import forecast_weather_codes, current_weather_code
    from monitor.weather_icons import draw_weather_icon as _draw_wx_icon

    hours_explicit = request.args.get('hours', type=int)  # None if not provided
    tenant_override = request.args.get('tenant')    # "yes" or "no"
    occupied_override = request.args.get('occupied')  # "yes" or "no"
    threshold_override = request.args.get('threshold', type=int)  # e.g. 95
    scale = max(1, min(8, request.args.get('scale', 1, type=int)))  # 1-8x resolution

    # Cache pre-generated images to reduce load when multiple clients (epaper display,
    # phone, computer) hit simultaneously.  Cache key = (tenant, scale); ad-hoc
    # overrides (hours, occupied, threshold) always bypass the cache.
    _cache_max_age = 8 * 60  # seconds
    _is_cacheable = (hours_explicit is None and occupied_override is None
                     and threshold_override is None)
    if _is_cacheable:
        _parts = []
        if tenant_override:
            _parts.append(f'tenant-{tenant_override}')
        if scale != 1:
            _parts.append(f's{scale}')
        _cache_file = 'epaper_cache' + ('_' + '_'.join(_parts) if _parts else '') + '.bmp'
    else:
        _cache_file = None
    if _cache_file:
        try:
            import time as _time
            if _time.time() - os.path.getmtime(_cache_file) < _cache_max_age:
                with open(_cache_file, 'rb') as _cf:
                    cached_buf = io.BytesIO(_cf.read())
                return send_file(cached_buf, mimetype='image/bmp', download_name='epaper.bmp')
        except OSError:
            pass

    def s(v):
        """Scale a pixel value by the resolution multiplier."""
        return int(v * scale)

    WIDTH, HEIGHT = 250 * scale, 122 * scale
    img = Image.new('1', (WIDTH, HEIGHT), 1)  # 1-bit, white background
    draw = ImageDraw.Draw(img)

    # Load fonts (scaled)
    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", s(22))
        font_medium = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", s(14))
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", s(11))
    except (IOError, OSError):
        font_large = ImageFont.load_default()
        font_medium = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Read snapshot history
    rows = []
    try:
        with open(DEFAULT_SNAPSHOTS_FILE, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception:
        pass

    # Try live tank data first (30s timeout), fall back to latest snapshot
    tank_gallons = None
    tank_pct = None
    live_reading_ts = None
    try:
        live = get_tank_data(TANK_URL, timeout=30)
        if live['status'] == 'success' and live['gallons'] is not None:
            tank_gallons = live['gallons']
            tank_pct = (tank_gallons / TANK_CAPACITY_GALLONS) * 100
            live_reading_ts = live.get('last_updated')
    except Exception:
        pass

    if tank_gallons is None and rows:
        try:
            latest = rows[-1]
            tank_gallons = float(latest['tank_gallons'])
            tank_pct = (tank_gallons / TANK_CAPACITY_GALLONS) * 100
        except Exception:
            pass

    # Determine occupancy and whether current guest is owner
    reservations = load_reservations(RESERVATIONS_FILE)
    occupancy = is_occupied(reservations)
    next_res = get_next_reservation(reservations)

    is_occupied_now = occupancy['occupied']
    is_owner = False
    if is_occupied_now and occupancy.get('current_reservation'):
        res_type = occupancy['current_reservation'].get('Type', '')
        is_owner = any(ot in res_type for ot in EPAPER_OWNER_STAY_TYPES)
    is_tenant = is_occupied_now and not is_owner

    # Apply CGI overrides
    if occupied_override == 'yes':
        is_occupied_now = True
    elif occupied_override == 'no':
        is_occupied_now = False
        is_tenant = False
    if tenant_override == 'yes':
        is_tenant = True
        is_occupied_now = True
    elif tenant_override == 'no':
        is_tenant = False

    # Set hours default based on occupancy
    if hours_explicit is not None:
        hours = hours_explicit
    elif is_tenant:
        hours = EPAPER_DEFAULT_HOURS_TENANT
    else:
        hours = EPAPER_DEFAULT_HOURS_OTHER
        # Extend to longer view when tank is low (helps decide if neighbor water is needed)
        if (EPAPER_LOW_WATER_HOURS_THRESHOLD is not None
                and tank_pct is not None
                and tank_pct <= EPAPER_LOW_WATER_HOURS_THRESHOLD):
            hours = EPAPER_LOW_WATER_HOURS

    # Check if tank is low
    low_threshold = threshold_override if threshold_override is not None else EPAPER_CONSERVE_WATER_THRESHOLD
    tank_is_low = (low_threshold is not None
                   and tank_pct is not None
                   and tank_pct <= low_threshold)

    # -- TENANT + LOW WATER: simplified full-screen warning --
    if is_tenant and tank_is_low:
        # "Save Water" large and centered
        warn_text = "Save Water"
        wb = draw.textbbox((0, 0), warn_text, font=font_large)
        ww, wh = wb[2] - wb[0], wb[3] - wb[1]
        # Scale width to nearly fill the screen
        target_w = WIDTH - s(20)
        text_scale = target_w / ww
        target_h = int(wh * text_scale)
        text_img = Image.new('1', (ww, wh), 1)
        ImageDraw.Draw(text_img).text((-wb[0], -wb[1]), warn_text, font=font_large, fill=0)
        text_img = text_img.resize((target_w, target_h), Image.NEAREST)
        y_top = (HEIGHT // 2 - target_h) // 2 + s(4)
        img.paste(text_img, ((WIDTH - target_w) // 2, y_top))

        # "Tank filling slowly" in medium font below
        sub_text = "Tank filling slowly"
        sb = draw.textbbox((0, 0), sub_text, font=font_medium)
        sw = sb[2] - sb[0]
        draw = ImageDraw.Draw(img)  # refresh draw after paste
        draw.text(((WIDTH - sw) // 2, y_top + target_h + s(10)), sub_text, font=font_medium, fill=0)

        buf = io.BytesIO()
        img.save(buf, format='BMP')
        if _cache_file:
            try:
                with open(_cache_file, 'wb') as _cf:
                    _cf.write(buf.getvalue())
            except Exception:
                pass
        buf.seek(0)
        return send_file(buf, mimetype='image/bmp', download_name='epaper.bmp')

    # -- Normal display: header + graph --

    # Top section: gallons and percent
    y = s(2)
    if tank_gallons is not None:
        gal_text = f"{int(tank_gallons)} gal"
        pct_text = f"{tank_pct:.0f}%"
        draw.text((s(4), y), gal_text, font=font_large, fill=0)
        pct_bbox = draw.textbbox((0, 0), pct_text, font=font_large)
        pct_w = pct_bbox[2] - pct_bbox[0]
        draw.text((WIDTH - pct_w - s(4), y), pct_text, font=font_large, fill=0)
        # "available / water" centered between gallons and percent
        gal_bbox = draw.textbbox((s(4), y), gal_text, font=font_large)
        pct_x = WIDTH - pct_w - s(4)
        gap_cx = (gal_bbox[2] + pct_x) // 2
        for li, line in enumerate(["available", "water"]):
            lb = draw.textbbox((0, 0), line, font=font_small)
            draw.text((gap_cx - (lb[2] - lb[0]) // 2, s(1) + li * s(12)), line, font=font_small, fill=0)
    else:
        draw.text((s(4), y), "No data", font=font_large, fill=0)

    # Separator line
    sep_y = s(28)
    draw.line([(0, sep_y), (WIDTH - 1, sep_y)], fill=0, width=scale)

    # Get snapshot data for the time window
    graph_gallons = []
    try:
        now = datetime.now()
        cutoff = now - timedelta(hours=hours)
        for row in rows:
            try:
                ts = datetime.fromisoformat(row['timestamp'])
                if ts >= cutoff:
                    graph_gallons.append(float(row['tank_gallons']))
            except Exception:
                continue
    except Exception:
        pass

    # ── Adaptive smoothing: large window when flat, raw when changing ─────
    _SMOOTH_WINDOW   = 11   # points (~2.5 hr at 15-min snapshots)
    _NOISE_THRESHOLD = 15   # gallons — above this → real movement, use raw
    if len(graph_gallons) >= _SMOOTH_WINDOW:
        smoothed = []
        half = _SMOOTH_WINDOW // 2
        for i in range(len(graph_gallons)):
            lo = max(0, i - half)
            hi = min(len(graph_gallons), i + half + 1)
            window_vals = graph_gallons[lo:hi]
            if max(window_vals) - min(window_vals) > _NOISE_THRESHOLD:
                smoothed.append(graph_gallons[i])   # real change — use raw
            else:
                smoothed.append(sum(window_vals) / len(window_vals))
        graph_gallons = smoothed

    # Compute Y-axis range
    g_min_raw = min(graph_gallons) if len(graph_gallons) >= 2 else 0
    g_max_raw = max(graph_gallons) if len(graph_gallons) >= 2 else 0
    g_range_raw = g_max_raw - g_min_raw

    # Enforce minimum range as % of tank capacity between top and bottom
    min_range  = TANK_CAPACITY_GALLONS * (EPAPER_MIN_GRAPH_RANGE_PCT / 100)
    max_gallon = TANK_CAPACITY_GALLONS * (EPAPER_MAX_GRAPH_PCT / 100)
    if g_range_raw < min_range:
        mid = (g_min_raw + g_max_raw) / 2
        g_min_raw = mid - min_range / 2
        g_max_raw = mid + min_range / 2
        g_range_raw = min_range

    g_min = g_min_raw - g_range_raw * 0.05
    g_max = min(g_max_raw + g_range_raw * 0.05, max_gallon)
    if g_max - g_min < min_range:   # top was capped — push bottom down
        g_min = g_max - min_range
    g_range = g_max - g_min

    # Y-axis labels (percent)
    y_max_label = f"{int(round(g_max_raw / TANK_CAPACITY_GALLONS * 100))}%"
    y_min_label = f"{int(round(g_min_raw / TANK_CAPACITY_GALLONS * 100))}%"

    y_label_w = max(
        draw.textbbox((0, 0), y_max_label, font=font_small)[2],
        draw.textbbox((0, 0), y_min_label, font=font_small)[2],
    )

    # Graph layout
    graph_left = y_label_w + s(6)
    graph_right = WIDTH - s(4)
    graph_top = s(32)
    graph_bottom = HEIGHT - s(14)
    graph_w = graph_right - graph_left
    graph_h = graph_bottom - graph_top

    # Draw graph border
    draw.rectangle([graph_left, graph_top, graph_right, graph_bottom], outline=0, fill=1)

    # Y-axis labels
    draw.text((graph_left - y_label_w - s(3), graph_top - s(1)), y_max_label, font=font_small, fill=0)
    draw.text((graph_left - y_label_w - s(3), graph_bottom - s(11)), y_min_label, font=font_small, fill=0)

    # X-axis labels
    hours_label = f"{hours // 24}d ago" if hours % 24 == 0 else f"{hours}h ago"
    draw.text((graph_left + s(1), graph_bottom + s(1)), hours_label, font=font_small, fill=0)
    try:
        if live_reading_ts:
            now_label = live_reading_ts.strftime("%-m/%d %-I:%M %p")
        else:
            last_ts = datetime.fromisoformat(rows[-1]['timestamp'])
            data_age = float(rows[-1].get('tank_data_age_seconds', 0))
            reading_ts = last_ts - timedelta(seconds=data_age)
            now_label = reading_ts.strftime("%-m/%d %-I:%M %p")
    except Exception:
        now_label = "now"
    nl_bbox = draw.textbbox((0, 0), now_label, font=font_small)
    draw.text((graph_right - (nl_bbox[2] - nl_bbox[0]) - s(1), graph_bottom + s(1)), now_label, font=font_small, fill=0)

    # Plot graph
    if len(graph_gallons) >= 2:
        points = []
        for i, g in enumerate(graph_gallons):
            x = graph_left + 1 + int(i * (graph_w - 2) / (len(graph_gallons) - 1))
            y_val = graph_bottom - 1 - int((g - g_min) / g_range * (graph_h - 2))
            points.append((x, y_val))

        if points:
            fill_points = [(points[0][0], graph_bottom - 1)]
            fill_points.extend(points)
            fill_points.append((points[-1][0], graph_bottom - 1))
            draw.polygon(fill_points, fill=0)

            for i in range(len(points) - 1):
                draw.line([points[i], points[i + 1]], fill=1, width=2 * scale)

    # Forecast icons: top-right of graph (XOR — inverts over whatever is behind)
    forecast_codes = forecast_weather_codes(EPAPER_FORECAST_DAYS) if EPAPER_FORECAST_DAYS else []
    if forecast_codes:
        live_code = current_weather_code()
        if live_code is not None:
            forecast_codes[0] = live_code
    if forecast_codes:
        icon_sz  = s(13)
        icon_gap = s(2)
        n        = len(forecast_codes)
        strip_w  = n * icon_sz + (n - 1) * icon_gap
        strip_h  = icon_sz
        strip_img  = Image.new('1', (strip_w, strip_h), 0)  # black bg, white icons
        strip_draw = ImageDraw.Draw(strip_img)
        for i, code in enumerate(forecast_codes):
            cx = i * (icon_sz + icon_gap) + icon_sz // 2
            cy = icon_sz // 2
            _draw_wx_icon(strip_draw, code, cx, cy, icon_sz, 1)
        ix = graph_right - strip_w - s(2)
        iy = graph_top + s(2)
        region = img.crop((ix, iy, ix + strip_w, iy + strip_h))
        region = ImageChops.logical_xor(region, strip_img)
        img.paste(region, (ix, iy))

    # Outside temperature + current weather description (inverted) at top-left of graph
    outdoor_temp_f = None
    if rows:
        try:
            outdoor_temp_f = float(rows[-1].get('outdoor_temp_f', ''))
        except (ValueError, TypeError):
            pass
    pad = s(2)
    paste_x = graph_left + 1
    paste_y = graph_top + 1
    if outdoor_temp_f is not None:
        temp_text = f"Outside: {int(round(outdoor_temp_f))}\u00b0"
        tb = draw.textbbox((0, 0), temp_text, font=font_small)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        lbl_img = Image.new('1', (tw + pad * 2, th + pad * 2), 0)  # black bg
        ImageDraw.Draw(lbl_img).text((pad - tb[0], pad - tb[1]), temp_text, font=font_small, fill=1)
        region = img.crop((paste_x, paste_y, paste_x + tw + pad * 2, paste_y + th + pad * 2))
        region = ImageChops.logical_xor(region, lbl_img)
        img.paste(region, (paste_x, paste_y))
        paste_y += th + pad * 2 + 1
    weather_desc = current_weather_desc()
    if weather_desc:
        wd_tb = draw.textbbox((0, 0), weather_desc, font=font_small)
        wd_w, wd_h = wd_tb[2] - wd_tb[0], wd_tb[3] - wd_tb[1]
        wd_img = Image.new('1', (wd_w + pad * 2, wd_h + pad * 2), 0)
        ImageDraw.Draw(wd_img).text((pad - wd_tb[0], pad - wd_tb[1]), weather_desc, font=font_small, fill=1)
        wd_region = img.crop((paste_x, paste_y, paste_x + wd_w + pad * 2, paste_y + wd_h + pad * 2))
        wd_region = ImageChops.logical_xor(wd_region, wd_img)
        img.paste(wd_region, (paste_x, paste_y))
        paste_y += wd_h + pad * 2 + 1

    # "Override ON" label (XOR) when tenant=no is explicit and supply override is active
    if tenant_override == 'no':
        try:
            if get_all_relay_status().get('supply_override') == 'ON':
                ov_text = 'Override ON'
                ov_tb = draw.textbbox((0, 0), ov_text, font=font_small)
                ov_w, ov_h = ov_tb[2] - ov_tb[0], ov_tb[3] - ov_tb[1]
                ov_img = Image.new('1', (ov_w + pad * 2, ov_h + pad * 2), 0)
                ImageDraw.Draw(ov_img).text((pad - ov_tb[0], pad - ov_tb[1]), ov_text, font=font_small, fill=1)
                ov_region = img.crop((paste_x, paste_y, paste_x + ov_w + pad * 2, paste_y + ov_h + pad * 2))
                ov_region = ImageChops.logical_xor(ov_region, ov_img)
                img.paste(ov_region, (paste_x, paste_y))
        except Exception:
            pass

    # "Save Water" XOR overlay when tank is low (non-tenant mode)
    if tank_is_low:
        warn_text = "Save Water"
        test_bbox = draw.textbbox((0, 0), warn_text, font=font_large)
        text_w = test_bbox[2] - test_bbox[0]
        text_h = test_bbox[3] - test_bbox[1]
        text_img = Image.new('1', (text_w, text_h), 0)
        ImageDraw.Draw(text_img).text((-test_bbox[0], -test_bbox[1]), warn_text, font=font_large, fill=1)
        target_w = graph_w - s(8)
        target_h = text_h
        text_img = text_img.resize((target_w, target_h), Image.NEAREST)
        paste_x = graph_left + (graph_w - target_w) // 2
        paste_y = graph_top + (graph_h - target_h) // 2
        region = img.crop((paste_x, paste_y, paste_x + target_w, paste_y + target_h))
        region = ImageChops.logical_xor(region, text_img)
        img.paste(region, (paste_x, paste_y))

    # Occupancy status bar (inverted) at bottom of graph for owner/unoccupied mode
    if not is_tenant:
        def _day_suffix(dt):
            """Return ' (today)' or ' (tomorrow)' if dt matches, else ''."""
            today = datetime.now().date()
            if dt.date() == today:
                return " (today)"
            elif dt.date() == today + timedelta(days=1):
                return " (tomorrow)"
            return ""

        if is_occupied_now and occupancy.get('checkout_date'):
            co = occupancy['checkout_date']
            occ_text = "occupied until " + co.strftime("%-m/%d") + _day_suffix(co)
        elif is_occupied_now:
            occ_text = "occupied"
        elif next_res:
            checkin_dt = get_checkin_datetime(next_res.get('Check-In'))
            if checkin_dt:
                occ_text = "next checkin " + checkin_dt.strftime("%-m/%d") + _day_suffix(checkin_dt)
            else:
                occ_text = "unoccupied"
        else:
            occ_text = "unoccupied"
        ob = draw.textbbox((0, 0), occ_text, font=font_small)
        ow, oh = ob[2] - ob[0], ob[3] - ob[1]
        # Create inverted bar spanning graph width at graph bottom
        bar_h = oh + s(4)
        bar_y = graph_bottom - bar_h
        bar_img = Image.new('1', (graph_w, bar_h), 0)  # black background
        bar_draw = ImageDraw.Draw(bar_img)
        bar_draw.text(((graph_w - ow) // 2, s(2) - ob[1]), occ_text, font=font_small, fill=1)
        # XOR onto graph
        region = img.crop((graph_left, bar_y, graph_right, bar_y + bar_h))
        region = ImageChops.logical_xor(region, bar_img)
        img.paste(region, (graph_left, bar_y))

    # Serve as BMP
    buf = io.BytesIO()
    img.save(buf, format='BMP')
    if _cache_file:
        try:
            with open(_cache_file, 'wb') as _cf:
                _cf.write(buf.getvalue())
        except Exception:
            pass
    buf.seek(0)
    return send_file(buf, mimetype='image/bmp', download_name='epaper.bmp')


@app.route('/api/epaper.jpg')
def epaper_jpg():
    """
    Color JPEG version of the e-paper display at 4× resolution (1000×488).
    Uses a live timelapse or RTSP frame as the graph background.

    Query params: same as /api/epaper.bmp plus scale (default 4).
    """
    from monitor.epaper_jpg import render_epaper_jpg
    buf = render_epaper_jpg(
        hours_explicit=request.args.get('hours', type=int),
        tenant_override=request.args.get('tenant'),
        occupied_override=request.args.get('occupied'),
        threshold_override=request.args.get('threshold', type=int),
        public_mode=(request.args.get('public') == 'yes'),
        scale=max(1, min(8, request.args.get('scale', 4, type=int))),
    )
    from flask import Response as _Resp
    resp = _Resp(buf.read(), mimetype='image/jpeg')
    resp.headers['Content-Disposition'] = 'inline; filename="epaper.jpg"'
    resp.headers['Cache-Control'] = 'public, max-age=600, stale-if-error=172800'
    return resp


@app.route('/water')
def water_status():
    """
    Public-facing water tank status page for tenants and guests.
    Shows current tank level and recent history with no occupancy information.
    Auto-refreshes every 10 minutes.

    Via Cloudflare (CF-Ray header present): public=yes mode, no dashboard link.
    Direct Pi access (no CF-Ray): tenant=no mode, dashboard link shown.
    """
    via_cloudflare = bool(request.headers.get('CF-Ray'))
    dashboard_url = 'https://onblackberryhill2.tplinkdns.com:6443/?hours=120&totals=income'

    # Support ?hours=N or ?days=N to control the graph timespan
    hours_arg = request.args.get('hours', type=int)
    days_arg  = request.args.get('days',  type=int)
    if hours_arg is None and days_arg is not None:
        hours_arg = days_arg * 24

    base_param = 'public=yes' if via_cloudflare else 'tenant=no'
    img_param  = f'{base_param}&hours={hours_arg}' if hours_arg is not None else base_param

    if via_cloudflare:
        img_html = f'<img src="/api/epaper.jpg?{img_param}" alt="Water tank level graph">'
        extra_links = ''
    else:
        img_html = f'<a href="{dashboard_url}" style="display:block;"><img src="/api/epaper.jpg?{img_param}" alt="Water tank level graph"></a>'
        extra_links = (
            '\n    <span>&bull;</span>'
            f'\n    <a href="/api/epaper.jpg?{img_param}">Image only</a>'
            '\n    <span>&bull;</span>'
            '\n    <a href="/">Dashboard</a>'
            '\n    <span>&bull;</span>'
            '\n    <a href="https://onblackberryhill.com/water">Public view</a>'
            '\n    <span>&bull;</span>'
            f'\n    <a href="{AMBIENT_WEATHER_DASHBOARD_URL}" target="_blank">Weather</a>'
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="600">
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
    .card img {{
      width: 100%;
      height: auto;
      border-radius: 6px;
      display: block;
    }}
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
  <div class="links">
    <span>Updates every 10&ndash;30 minutes</span>
    <span>&bull;</span>
    <a href="/timelapse">Timelapse</a>{extra_links}
  </div>
</body>
</html>"""
    from flask import Response
    resp = Response(html, mimetype='text/html')
    if hours_arg is not None:
        resp.headers['Cache-Control'] = 'public, max-age=60'
    else:
        resp.headers['Cache-Control'] = 'public, max-age=600, stale-if-error=172800'
    return resp


@app.route('/water2')
def water2_status():
    """
    Owner-facing water status page with bypass controls.
    Same graph as /water; bypass buttons always shown (URL is obscure enough).
    Designed to work via Cloudflare — uses token-based /control/<token> links.
    Cache-Control is no-store so relay state is always fresh.
    """
    via_cloudflare = bool(request.headers.get('CF-Ray'))
    dashboard_url  = 'https://onblackberryhill2.tplinkdns.com:6443/?hours=120&totals=income'

    hours_arg = request.args.get('hours', type=int)
    days_arg  = request.args.get('days',  type=int)
    if hours_arg is None and days_arg is not None:
        hours_arg = days_arg * 24

    import time as _time
    _ts = int(_time.time())
    img_param = f'tenant=no&hours={hours_arg}&_t={_ts}' if hours_arg is not None else f'tenant=no&_t={_ts}'
    img_tag   = f'<img src="/api/epaper.jpg?{img_param}" alt="Water tank level graph">'
    if via_cloudflare:
        img_html    = img_tag
        extra_links = ''
    else:
        img_html    = f'<a href="{dashboard_url}" style="display:block;">{img_tag}</a>'
        extra_links = (
            '\n    <span>&bull;</span>'
            '\n    <a href="/">Dashboard</a>'
        )

    back = '/water2'

    controls_html = ''
    if SECRET_BYPASS_ON_TOKEN:
        relay_status = get_all_relay_status()
        bypass_on    = relay_status.get('bypass') == 'ON'
        override_on  = relay_status.get('supply_override') == 'ON'

        btn_active  = 'background:#f44336;border-color:#f44336;color:#fff;'
        btn_warning = 'background:#ff9800;border-color:#ff9800;color:#fff;'
        btn_base    = 'display:inline-block;padding:8px 16px;border:1px solid #7aa4d8;border-radius:6px;color:#7aa4d8;text-decoration:none;font-size:0.9rem;'

        if bypass_on:
            bypass_btn = f'<a href="/control/{SECRET_BYPASS_OFF_TOKEN}?back={back}" style="{btn_base}{btn_active}">&#9679; Bypass Filters/Dosatron: ON</a>'
        else:
            bypass_btn = f'<a href="/control/{SECRET_BYPASS_ON_TOKEN}?back={back}" style="{btn_base}">&#9675; Bypass Filters/Dosatron: OFF</a>'

        if override_on:
            override_btn = f'<a href="/control/{SECRET_OVERRIDE_OFF_TOKEN}?back={back}" style="{btn_base}{btn_warning}">&#9679; Force Fill Tank Valve Open: ON</a>'
        else:
            override_btn = f'<a href="/control/{SECRET_OVERRIDE_ON_TOKEN}?back={back}" style="{btn_base}">&#9675; Force Fill Tank Valve Open: OFF</a>'

        purge_btn       = f'<a href="/control/{SECRET_PURGE_TOKEN}?back={back}" style="{btn_base}" onclick="return confirm(\'Trigger a purge cycle?\')">&#9881; Purge</a>'
        test_filter_btn = f'<a href="/control/{SECRET_TEST_FILTER_TOKEN}?back={back}" style="{btn_base}" onclick="return confirm(\'Start test filter mode? This turns Override ON and Bypass OFF.\')">&#128308; Test Filter</a>'

        float_state = read_float_sensor()
        if float_state == FLOAT_STATE_CALLING:
            tank_valve_html = '<div style="color:#ff9800;font-size:0.9rem;text-align:center;">&#9654; Fill Tank Valve: OPEN (tank calling for water)</div>'
        else:
            tank_valve_html = '<div style="color:#ff9800;font-size:0.9rem;text-align:center;">&#9679; Fill Tank Valve: CLOSED (tank full)</div>'

        controls_html = f'{tank_valve_html}<div style="display:flex;gap:10px;flex-wrap:wrap;justify-content:center;margin-top:8px;">{bypass_btn}{override_btn}{purge_btn}{test_filter_btn}</div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="600">
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
    .card img {{
      width: 100%;
      height: auto;
      border-radius: 6px;
      display: block;
    }}
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
  {controls_html}
  <div class="links">
    <span>Updates every 10&ndash;30 minutes</span>
    <span>&bull;</span>
    <a href="/timelapse">Timelapse</a>
    <span>&bull;</span>
    <a href="/water">Public view</a>
    <span>&bull;</span>
    <a href="{AMBIENT_WEATHER_DASHBOARD_URL}" target="_blank">Weather</a>{extra_links}
  </div>
</body>
</html>"""

    resp = Response(html, mimetype='text/html')
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@app.route('/')
# @requires_auth
def index():
    """Main status page"""
    # ?owner / ?manager mode: unlocks control buttons and shows Ring snapshot
    owner_mode   = 'owner'   in request.args
    manager_mode = 'manager' in request.args
    show_ring    = owner_mode or manager_mode

    # Get hours parameter for filtering events by time; days=n is an alias
    hours = request.args.get('hours', type=int)
    days  = request.args.get('days',  type=int)
    if hours is None:
        hours = days * 24 if days is not None else (120 if owner_mode else DASHBOARD_DEFAULT_HOURS)

    # Get sensor data
    sensor_data = get_sensor_data()

    # Get outdoor weather from latest snapshot
    outdoor_weather = get_outdoor_weather()

    # Get tank data
    tank_data = get_tank_data(TANK_URL)

    # Calculate tank data age if available
    tank_age_minutes = None
    if tank_data['status'] == 'success' and tank_data.get('last_updated'):
        age_seconds = (datetime.now() - tank_data['last_updated']).total_seconds()
        tank_age_minutes = int(age_seconds / 60)

    # Read CSV files
    snapshot_headers, snapshot_rows = read_csv_tail(DEFAULT_SNAPSHOTS_FILE, max_rows=DASHBOARD_SNAPSHOT_COUNT)
    daily_headers, daily_rows = read_csv_tail(str(DAILY_CSV), max_rows=62)
    pumpoff_headers, pumpoff_rows = read_csv_tail(str(DAILY_CSV.parent / 'pumpoff.csv'), max_rows=50)
    event_headers, event_rows = read_events_by_time(EVENTS_FILE, hours=hours)

    # Filter events based on DASHBOARD_HIDE_EVENT_TYPES
    if event_headers and event_rows and 'event_type' in event_headers:
        event_type_idx = event_headers.index('event_type')
        event_rows = [row for row in event_rows if len(row) > event_type_idx and row[event_type_idx] not in DASHBOARD_HIDE_EVENT_TYPES]

    # Get aggregate stats from snapshots
    stats = get_snapshots_stats(DEFAULT_SNAPSHOTS_FILE)

    # Get relay status
    relay_status = get_all_relay_status()

    # Get occupancy status and reservations
    reservations_csv = RESERVATIONS_FILE
    occupancy_status = get_occupancy_status(reservations_csv)

    # Get cached Ecobee temperature
    ecobee_temp = get_cached_ecobee_temp(max_age_hours=24)

    # Get GPH metrics (cached, recalculated daily)
    gph_metrics = get_cached_gph(max_age_hours=24)

    # Hourly tank fill rate for sparkline
    hourly_gph = get_hourly_gph()

    # Predict next pump cycle from recent PRESSURE_HIGH events
    next_pump_cycle = None
    try:
        _high_times = []
        with open(EVENTS_FILE) as _f:
            for _row in csv.DictReader(_f):
                if _row.get('event_type') == 'PRESSURE_HIGH':
                    try:
                        _high_times.append(datetime.fromisoformat(_row['timestamp']))
                    except Exception:
                        pass
        if len(_high_times) >= 4:
            _intervals = [(_high_times[i+1] - _high_times[i]).total_seconds()
                          for i in range(len(_high_times) - 1)]
            _median = sorted(_intervals)[len(_intervals) // 2]
            _typical = [iv for iv in _intervals if abs(iv - _median) < 120]
            if _typical:
                _avg_interval = sum(_typical) / len(_typical)
                _predicted = _high_times[-1] + timedelta(seconds=_avg_interval)
                if _predicted > datetime.now():
                    next_pump_cycle = _predicted
    except Exception:
        pass

    # Predict next backflush from historical NOTIFY_BACKFLUSH events in the overnight window
    next_backflush = None
    try:
        _bf_start = sum(int(x) * m for x, m in zip(NOTIFY_BACKFLUSH_TIME_START.split(':'), (60, 1)))
        _bf_end   = sum(int(x) * m for x, m in zip(NOTIFY_BACKFLUSH_TIME_END.split(':'),   (60, 1)))
        _bf_dates = []
        with open(EVENTS_FILE) as _f:
            for _row in csv.DictReader(_f):
                if _row.get('event_type') == 'NOTIFY_BACKFLUSH':
                    try:
                        _ts = datetime.fromisoformat(_row['timestamp'])
                        _t  = _ts.hour * 60 + _ts.minute
                        if _bf_start <= _t <= _bf_end:
                            _d = _ts.date()
                            # Deduplicate: skip if within 3 days of the last recorded date
                            if not _bf_dates or (_d - _bf_dates[-1]).days > 3:
                                _bf_dates.append(_d)
                    except Exception:
                        pass
        if len(_bf_dates) >= 2:
            _intervals = [(_bf_dates[i+1] - _bf_dates[i]).days for i in range(len(_bf_dates) - 1)]
            _median_interval = sorted(_intervals)[len(_intervals) // 2]
            from datetime import date as _date
            next_backflush = datetime.combine(_bf_dates[-1], datetime.min.time()) + timedelta(days=_median_interval)
    except Exception:
        pass

    # Estimate time to reach full tank (1400 gal) based on 24h fill rate
    time_to_full = None
    try:
        current_gallons = tank_data.get('gallons') if tank_data.get('status') == 'success' else None
        if current_gallons is not None and current_gallons < TANK_CAPACITY_GALLONS:
            if stats and stats.get('tank_change_24hr') is not None and stats['tank_change_24hr'] > 0:
                fill_rate_gph = stats['tank_change_24hr'] / 24.0
                hours_to_full = (TANK_CAPACITY_GALLONS - current_gallons) / fill_rate_gph
                if 0 < hours_to_full < 168:  # Only show if within 1 week
                    time_to_full = datetime.now() + timedelta(hours=hours_to_full)
    except Exception:
        pass

    # Estimate tank gallons at next guest check-in (only when unoccupied)
    gallons_at_checkin = None
    try:
        if not occupancy_status['occupied']:
            current_gallons = tank_data.get('gallons') if tank_data.get('status') == 'success' else None
            if current_gallons is not None and stats and stats.get('tank_change_24hr') is not None:
                fill_rate_gph = stats['tank_change_24hr'] / 24.0
                next_res = get_next_reservation(load_reservations(reservations_csv))
                if next_res:
                    checkin_dt = get_checkin_datetime(next_res.get('Check-In'))
                    if checkin_dt:
                        hours_until = (checkin_dt - datetime.now()).total_seconds() / 3600
                        if hours_until > 0:
                            projected = current_gallons + fill_rate_gph * hours_until
                            gallons_at_checkin = min(round(projected), TANK_CAPACITY_GALLONS)
    except Exception:
        pass

    # Get internet uptime stats from Cloudflare worker
    internet_uptime  = get_internet_uptime()
    service_uptimes  = get_service_uptimes()

    # Get wind forecast for tonight and tomorrow
    wind_forecast = get_wind_forecast()

    # Get all reservations (for repeat guest detection across all reservations)
    reservations = load_reservations(reservations_csv)

    # Also load ALL reservations including checked out ones (for the table display)
    all_reservations = []
    import csv as csv_module
    if os.path.exists(reservations_csv):
        try:
            with open(reservations_csv, 'r') as f:
                reader = csv_module.DictReader(f)
                all_reservations = list(reader)
        except Exception as e:
            print(f"Error loading all reservations: {e}")

    # Add repeat guest detection using ALL reservations
    guest_counts = {}
    for res in all_reservations:
        guest = res.get('Guest', '')
        if guest:
            guest_counts[guest] = guest_counts.get(guest, 0) + 1

    # Get reservations with checkout in current month or next month
    from monitor.occupancy import parse_date
    now = datetime.now()

    # Calculate current month (full month from day 1) and next month ranges
    current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.month == 12:
        next_month_start = current_month_start.replace(year=now.year + 1, month=1)
    else:
        next_month_start = current_month_start.replace(month=now.month + 1)

    if next_month_start.month == 12:
        month_after_next = next_month_start.replace(year=next_month_start.year + 1, month=1)
    else:
        month_after_next = next_month_start.replace(month=next_month_start.month + 1)

    # Check if totals parameter matches secret (?owner implicitly enables it)
    from monitor.config import SECRET_TOTALS_TOKEN
    totals_arg = request.args.get('totals') or (SECRET_TOTALS_TOKEN if owner_mode else None)
    show_totals = totals_arg == SECRET_TOTALS_TOKEN if SECRET_TOTALS_TOKEN else False

    # Filter reservations - ONLY based on checkout month, using ALL reservations including checked out
    reservation_list = []
    for res in all_reservations:
        checkout_date = parse_date(res.get('Checkout'))

        if checkout_date:
            # Include ONLY if checkout is in current or next month
            if current_month_start <= checkout_date < month_after_next:

                # Calculate net income (subtract management fee)
                try:
                    gross_income = float(res.get('Income', '0'))
                    net_income = gross_income * (1 - MANAGEMENT_FEE_PERCENT / 100)
                    res['gross_income'] = gross_income
                    res['net_income'] = net_income
                except:
                    res['gross_income'] = 0
                    res['net_income'] = 0

                # Mark repeat guests
                guest = res.get('Guest', '')
                if guest and guest_counts.get(guest, 0) > 1:
                    res['repeat_guest'] = 'Yes'
                else:
                    res['repeat_guest'] = 'No'

                reservation_list.append(res)

    # Sort by checkout date
    reservation_list.sort(key=lambda x: parse_date(x.get('Checkout')) or datetime.min)

    # Calculate monthly totals based on checkout month
    monthly_totals = {}
    for res in reservation_list:
        checkout_date = parse_date(res.get('Checkout'))
        if checkout_date:
            month_key = checkout_date.strftime('%Y-%m')
            if month_key not in monthly_totals:
                monthly_totals[month_key] = {'gross': 0, 'net': 0, 'month_name': checkout_date.strftime('%B %Y')}

            try:
                gross = float(res.get('Income', '0'))
                monthly_totals[month_key]['gross'] += gross
                monthly_totals[month_key]['net'] += res.get('net_income', 0)
            except:
                pass

    # Add running total field to each reservation
    running_total = 0
    current_checkout_month = None
    for res in reservation_list:
        checkout_date = parse_date(res.get('Checkout'))
        if checkout_date:
            checkout_month = checkout_date.strftime('%Y-%m')

            # Reset running total on month change
            if current_checkout_month != checkout_month:
                running_total = 0
                current_checkout_month = checkout_month

            running_total += res.get('net_income', 0)
            res['monthly_total'] = running_total

    return render_template('status.html',
                         version=__version__,
                         sensor_data=sensor_data,
                         outdoor_weather=outdoor_weather,
                         weather_dashboard_url=AMBIENT_WEATHER_DASHBOARD_URL,
                         tank_data=tank_data,
                         tank_age_minutes=tank_age_minutes,
                         tank_height=TANK_HEIGHT_INCHES,
                         tank_capacity=TANK_CAPACITY_GALLONS,
                         tank_url=TANK_URL,
                         snapshot_headers=snapshot_headers,
                         snapshot_rows=snapshot_rows,
                         daily_headers=daily_headers,
                         daily_rows=daily_rows,
                         pumpoff_headers=pumpoff_headers,
                         pumpoff_rows=pumpoff_rows,
                         event_headers=event_headers,
                         event_rows=event_rows,
                         stats=stats,
                         relay_status=relay_status,
                         occupancy_status=occupancy_status,
                         reservation_list=reservation_list,
                         show_totals=show_totals,
                         format_pressure_state=format_pressure_state,
                         format_float_state=format_float_state,
                         format_date_short=format_date_short,
                         now=datetime.now(),
                         startup_time=STARTUP_TIME,
                         hours=hours,
                         epaper_cache_bust=int(_time.time()),
                         default_hours=DASHBOARD_DEFAULT_HOURS,
                         ecobee_temp=ecobee_temp,
                         gph_metrics=gph_metrics,
                         hourly_gph=hourly_gph,
                         next_pump_cycle=next_pump_cycle,
                         next_backflush=next_backflush,
                         time_to_full=time_to_full,
                         gallons_at_checkin=gallons_at_checkin,
                         internet_uptime=internet_uptime,
                         service_uptimes=service_uptimes,
                         wind_forecast=wind_forecast,
                         national_weather_url=NATIONAL_WEATHER_URL,
                         FLOAT_STATE_FULL=FLOAT_STATE_FULL,
                         FLOAT_STATE_CALLING=FLOAT_STATE_CALLING,
                         snapshot_url=DASHBOARD_URL.rstrip('/') + '/snapshot' if DASHBOARD_URL else None,
                         pressure_low_watch=PRESSURE_LOW_WATCH_FILE.exists(),
                         owner_mode=owner_mode,
                         show_ring=show_ring,
                         token_override_on=SECRET_OVERRIDE_ON_TOKEN,
                         token_override_off=SECRET_OVERRIDE_OFF_TOKEN,
                         token_bypass_on=SECRET_BYPASS_ON_TOKEN,
                         token_bypass_off=SECRET_BYPASS_OFF_TOKEN,
                         token_bypass_timed=SECRET_BYPASS_TIMED_TOKEN,
                         token_bypass_cancel_timer=SECRET_BYPASS_CANCEL_TIMER_TOKEN,
                         token_bypass_cycle=SECRET_BYPASS_CYCLE_TOKEN,
                         bypass_timer_expiry=get_bypass_timer_expiry(),
                         bypass_cycle=get_bypass_cycle_info(),
                         bypass_cycle_on_hours=BYPASS_CYCLE_ON_HOURS,
                         bypass_cycle_off_hours=BYPASS_CYCLE_OFF_HOURS,
                         token_purge=SECRET_PURGE_TOKEN,
                         token_purge_next=SECRET_PURGE_NEXT_TOKEN,
                         token_purge_cancel_next=SECRET_PURGE_CANCEL_NEXT_TOKEN,
                         purge_pending=get_purge_pending(),
                         calendar_months=build_calendar_months(all_reservations))

@app.route('/sunset')
def sunset():
    """
    Proxy a JPEG snapshot from the Amcrest camera at 192.168.1.81.
    Unauthenticated so it can be embedded directly in pages/widgets.
    Uses verify=False to accept the camera's self-signed certificate.

    Query params:
        enhance - 1 = apply CLAHE contrast enhancement via OpenCV; default 0
    """
    import urllib.request
    import ssl
    import time

    enhance = request.args.get('enhance', 0, type=int)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    last_err = None
    for attempt in range(3):
        if attempt > 0:
            time.sleep(2)
        try:
            mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            mgr.add_password(None, 'https://192.168.1.81/', CAMERA_USER, CAMERA_PASS)
            handler = urllib.request.HTTPDigestAuthHandler(mgr)
            opener = urllib.request.build_opener(handler, urllib.request.HTTPSHandler(context=ctx))
            cam_resp = opener.open('https://192.168.1.81/cgi-bin/snapshot.cgi', timeout=30)
            content = cam_resp.read()
            content_type = cam_resp.headers.get('Content-Type', 'image/jpeg')

            if enhance:
                import cv2
                import numpy as np
                data = np.frombuffer(content, dtype=np.uint8)
                img = cv2.imdecode(data, cv2.IMREAD_COLOR)
                # Percentile stretch on L channel only (no tile artifacts, handles
                # bright sky + dark foreground by clipping 1st/99th percentile
                # and stretching to full range)
                lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
                l, a, b = cv2.split(lab)
                l_float = l.astype(np.float32)
                p_lo, p_hi = np.percentile(l_float, (1, 99))
                if p_hi > p_lo:
                    l_float = np.clip((l_float - p_lo) / (p_hi - p_lo) * 255, 0, 255)
                lab = cv2.merge([l_float.astype(np.uint8), a, b])
                img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
                _, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 85])
                return Response(buf.tobytes(), status=200, mimetype='image/jpeg')

            return Response(content, status=200, mimetype=content_type)
        except Exception as e:
            last_err = e

    return Response(f'Camera unavailable: {last_err}', status=503)


@app.route('/ring-snapshot')
def ring_snapshot():
    """
    Proxy a Ring camera snapshot with a shared file-based cache.
    All processes share the same cache file so Ring is only called once
    per RING_CACHE_MINUTES regardless of how many workers handle requests.
    """
    img_bytes = ring_camera.get_snapshot(RING_TOKEN_FILE, RING_CAMERA_NAME)
    if not img_bytes:
        app.logger.warning('Ring snapshot unavailable (get_snapshot returned None)')
        return Response('Ring snapshot unavailable', status=503)
    mtime = ring_camera.get_cache_mtime()
    headers = {'X-Ring-Time': str(int(mtime))} if mtime else {}
    return Response(img_bytes, status=200, mimetype='image/jpeg', headers=headers)


@app.route('/ping')
def ping():
    return 'ok', 200


@app.route('/watch/pressure_low')
def watch_pressure_low():
    """Toggle the temporary pressure-LOW ntfy alert on or off."""
    enable = request.args.get('enable', '1')
    back = request.args.get('back', '/?owner')
    if enable == '1':
        PRESSURE_LOW_WATCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        PRESSURE_LOW_WATCH_FILE.touch()
        action = "Pressure LOW watch: ON"
    else:
        PRESSURE_LOW_WATCH_FILE.unlink(missing_ok=True)
        action = "Pressure LOW watch: OFF"
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Watch</title>
        <meta http-equiv="refresh" content="2;url={back}" />
        <style>
            body {{ font-family: monospace; background: #1a1a1a; color: #4CAF50;
                   display: flex; align-items: center; justify-content: center;
                   height: 100vh; margin: 0; }}
            .message {{ text-align: center; padding: 40px; background: #2a2a2a;
                       border: 2px solid #4CAF50; border-radius: 8px; }}
        </style>
    </head>
    <body>
        <div class="message">
            <h1>✓ {action}</h1>
            <p>Redirecting to dashboard...</p>
        </div>
    </body>
    </html>
    """

@app.route('/control/<token>')
def control(token):
    """
    Unauthenticated control endpoint using secret tokens.
    Allows remote control via email links without authentication.
    """
    back = request.args.get('back', '/?owner')
    action_taken = None
    success = False

    # Check which action to perform based on token
    if token == SECRET_OVERRIDE_ON_TOKEN and SECRET_OVERRIDE_ON_TOKEN:
        OVERRIDE_MANUAL_OFF_FILE.unlink(missing_ok=True)
        success = set_supply_override('ON', debug=False)
        action_taken = "Supply Override turned ON"
    elif token == SECRET_OVERRIDE_OFF_TOKEN and SECRET_OVERRIDE_OFF_TOKEN:
        OVERRIDE_MANUAL_OFF_FILE.parent.mkdir(parents=True, exist_ok=True)
        OVERRIDE_MANUAL_OFF_FILE.touch()
        success = set_supply_override('OFF', debug=False)
        action_taken = "Supply Override turned OFF"
    elif token == SECRET_BYPASS_ON_TOKEN and SECRET_BYPASS_ON_TOKEN:
        _cancel_all_bypass_modes()
        success = set_bypass('ON', debug=False)
        action_taken = "Bypass turned ON"
    elif token == SECRET_BYPASS_OFF_TOKEN and SECRET_BYPASS_OFF_TOKEN:
        _cancel_all_bypass_modes()
        success = set_bypass('OFF', debug=False)
        action_taken = "Bypass turned OFF"
    elif token == SECRET_BYPASS_TIMED_TOKEN and SECRET_BYPASS_TIMED_TOKEN:
        _cancel_all_bypass_modes()
        expiry = datetime.now() + timedelta(hours=4)
        BYPASS_TIMER_FILE.parent.mkdir(parents=True, exist_ok=True)
        BYPASS_TIMER_FILE.write_text(str(expiry.timestamp()))
        success = set_bypass('ON', debug=False)
        action_taken = f"Bypass ON until {expiry.strftime('%-I:%M %p')}"
    elif token == SECRET_BYPASS_CANCEL_TIMER_TOKEN and SECRET_BYPASS_CANCEL_TIMER_TOKEN:
        _cancel_all_bypass_modes()
        success = True
        action_taken = "Bypass timer cancelled (bypass stays ON)"
    elif token == SECRET_BYPASS_CYCLE_TOKEN and SECRET_BYPASS_CYCLE_TOKEN:
        cycle_info = get_bypass_cycle_info()
        if cycle_info:
            # Cycle is already running — stop it, leave bypass as-is
            BYPASS_CYCLE_FILE.unlink(missing_ok=True)
            success = True
            action_taken = "Bypass cycle stopped"
        else:
            # Start cycle: ON phase first
            _cancel_all_bypass_modes()
            on_h  = float(request.args.get('on',  BYPASS_CYCLE_ON_HOURS))
            off_h = float(request.args.get('off', BYPASS_CYCLE_OFF_HOURS))
            next_trans = datetime.now() + timedelta(hours=on_h)
            _write_cycle(on_h, off_h, next_trans, 'OFF')
            success = set_bypass('ON', debug=False)
            action_taken = f"Bypass cycle started ({on_h:.4g}h ON / {off_h:.4g}h OFF)"
    elif token == SECRET_PURGE_TOKEN and SECRET_PURGE_TOKEN:
        # Trigger one-time purge immediately
        from monitor.purge import trigger_purge
        success = trigger_purge(debug=False)
        action_taken = "Purge triggered"
    elif token == SECRET_PURGE_NEXT_TOKEN and SECRET_PURGE_NEXT_TOKEN:
        # Schedule purge to fire ~15s after next PRESSURE_HIGH
        PURGE_PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
        PURGE_PENDING_FILE.touch()
        success = True
        action_taken = "Purge scheduled for next pump cycle"
    elif token == SECRET_PURGE_CANCEL_NEXT_TOKEN and SECRET_PURGE_CANCEL_NEXT_TOKEN:
        PURGE_PENDING_FILE.unlink(missing_ok=True)
        success = True
        action_taken = "Pending purge cancelled"
    elif token == SECRET_TEST_FILTER_TOKEN and SECRET_TEST_FILTER_TOKEN:
        # Test filter mode: override ON (pump runs) + bypass OFF (water through filter)
        _cancel_all_bypass_modes()
        ok1 = set_bypass('OFF', debug=False)
        OVERRIDE_MANUAL_OFF_FILE.unlink(missing_ok=True)
        ok2 = set_supply_override('ON', debug=False)
        success = ok1 and ok2
        action_taken = "Test filter mode: Override ON, Bypass OFF"
    else:
        return Response('Invalid token', status=403)

    if success:
        # Log the action to events.csv
        from monitor.logger import log_event
        relay_status = get_all_relay_status()
        log_event(
            EVENTS_FILE,
            'REMOTE_CONTROL',
            read_pressure(),
            read_float_sensor(),
            None,  # tank_gallons
            None,  # tank_depth
            None,  # tank_percentage
            None,  # duration
            relay_status,
            action_taken
        )

        # Return simple HTML with redirect
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Control Action</title>
            <meta http-equiv="refresh" content="2;url={back}" />
            <style>
                body {{ font-family: monospace; background: #1a1a1a; color: #4CAF50;
                       display: flex; align-items: center; justify-content: center;
                       height: 100vh; margin: 0; }}
                .message {{ text-align: center; padding: 40px; background: #2a2a2a;
                           border: 2px solid #4CAF50; border-radius: 8px; }}
            </style>
        </head>
        <body>
            <div class="message">
                <h1>✓ {action_taken}</h1>
                <p>Redirecting to dashboard...</p>
            </div>
        </body>
        </html>
        """
    else:
        return Response(f'Failed to execute: {action_taken}', status=500)

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        prog='monitor.web',
        description='Web dashboard for pumphouse monitoring'
    )
    parser.add_argument('--host', default='0.0.0.0',
                       help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=6443,
                       help='Port to listen on (default: 6443)')
    
    # Default to the locally copied certificate paths
    cert_dir = os.path.join(os.path.dirname(__file__), '..', 'certs')
    parser.add_argument('--cert', default=os.path.join(cert_dir, 'fullchain.pem'),
                       help='SSL certificate file')
    parser.add_argument('--key', default=os.path.join(cert_dir, 'privkey.pem'),
                       help='SSL key file')
    parser.add_argument('--no-ssl', action='store_true',
                       help='Run without SSL (HTTP only)')
    parser.add_argument('--debug', action='store_true',
                       help='Enable debug mode')

    args = parser.parse_args()

    # Check for SSL files
    ssl_context = None
    if not args.no_ssl:
        if os.path.exists(args.cert) and os.path.exists(args.key):
            ssl_context = (args.cert, args.key)
            print(f"Starting HTTPS server on https://{args.host}:{args.port}/")
        else:
            print(f"⚠️  SSL certificate not found at {args.cert}")
            print(f"   Run 'sudo certbot renew --force-renewal' to deploy it, or use --no-ssl.")
            return
    else:
        print(f"Starting HTTP server on http://{args.host}:{args.port}/")

    print(f"Username: {USERNAME}")
    print(f"Password: {PASSWORD}")
    print("\nSet credentials with environment variables:")
    print("  export PUMPHOUSE_USER=yourusername")
    print("  export PUMPHOUSE_PASS=yourpassword")

    app.run(host=args.host, port=args.port, ssl_context=ssl_context, debug=args.debug, threaded=True)

if __name__ == "__main__":
    main()
