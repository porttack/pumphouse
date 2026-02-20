#!/usr/bin/env python3
"""
Web dashboard for pumphouse monitoring system
Serves HTTPS on port 6443 with basic authentication
"""
import os
import csv
import argparse
import io
from datetime import datetime, timedelta
from flask import Flask, render_template, request, Response, jsonify, send_file
from functools import wraps

from monitor import __version__
from monitor.config import (
    TANK_URL, TANK_HEIGHT_INCHES, TANK_CAPACITY_GALLONS,
    EPAPER_CONSERVE_WATER_THRESHOLD, EPAPER_OWNER_STAY_TYPES,
    EPAPER_DEFAULT_HOURS_TENANT, EPAPER_DEFAULT_HOURS_OTHER,
    EPAPER_LOW_WATER_HOURS_THRESHOLD, EPAPER_LOW_WATER_HOURS,
    DASHBOARD_HIDE_EVENT_TYPES,
    DASHBOARD_MAX_EVENTS, DASHBOARD_DEFAULT_HOURS, DASHBOARD_SNAPSHOT_COUNT,
    SECRET_OVERRIDE_ON_TOKEN, SECRET_OVERRIDE_OFF_TOKEN,
    SECRET_BYPASS_ON_TOKEN, SECRET_BYPASS_OFF_TOKEN,
    SECRET_PURGE_TOKEN, MANAGEMENT_FEE_PERCENT,
    AMBIENT_WEATHER_DASHBOARD_URL,
    CAMERA_USER, CAMERA_PASS
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
    load_reservations, format_date_short
)
from monitor.gph_calculator import get_cached_gph, format_gph_for_display

# Matplotlib imports for chart generation
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

app = Flask(__name__)

# Configuration
USERNAME = os.environ.get('PUMPHOUSE_USER', 'admin')
PASSWORD = os.environ.get('PUMPHOUSE_PASS', 'pumphouse')
STARTUP_TIME = datetime.now()  # Track when web server started

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
        return dt.strftime('%a %H:%M')
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

def get_snapshots_stats(filepath='snapshots.csv'):
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
        one_hour_ago = now.timestamp() - 3600
        twenty_four_hours_ago = now.timestamp() - 86400

        stats = {
            'tank_change_1hr': None,
            'tank_change_24hr': None,
            'pressure_high_pct_1hr': None,
            'pressure_high_min_24hr': None,
            'last_refill_50_days': None,
            'last_refill_50_timestamp': None
        }

        # Parse timestamps and filter rows
        rows_1hr = []
        rows_24hr = []

        for row in rows:
            try:
                ts = datetime.fromisoformat(row['timestamp']).timestamp()
                if ts >= one_hour_ago:
                    rows_1hr.append(row)
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

        # Calculate pressure HIGH percentages/minutes
        if len(rows_1hr) > 0:
            try:
                total_seconds = 0
                high_seconds = 0
                for row in rows_1hr:
                    duration = float(row['duration_seconds'])
                    high = float(row['pressure_high_seconds'])
                    total_seconds += duration
                    high_seconds += high
                if total_seconds > 0:
                    stats['pressure_high_pct_1hr'] = (high_seconds / total_seconds) * 100
            except:
                pass

        if len(rows_24hr) > 0:
            try:
                total_high_seconds = 0
                for row in rows_24hr:
                    high = float(row['pressure_high_seconds'])
                    total_high_seconds += high
                stats['pressure_high_min_24hr'] = total_high_seconds / 60
            except:
                pass

        # Find last time tank increased by 50+ gallons using shared stats module
        refill_ts, days_ago = find_last_refill(filepath, threshold_gallons=50)
        if refill_ts and days_ago is not None:
            stats['last_refill_50_timestamp'] = refill_ts
            stats['last_refill_50_days'] = days_ago

        return stats

    except Exception as e:
        return None

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
        headers, rows = read_csv_tail('snapshots.csv', max_rows=1)
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
        with open('snapshots.csv', 'r') as f:
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
                        'pressure_pct': pressure_pct
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

        for i, point in enumerate(data_points):
            timestamps.append(point['timestamp'].strftime('%a %H:%M'))
            gallons.append(point['gallons'])

            # PRIORITY 1: Check for full-flow (pressure >= threshold)
            if point['pressure_pct'] >= NOTIFY_FULL_FLOW_PRESSURE_THRESHOLD:
                point_colors.append('#f44336')  # Red - full flow
                continue

            # PRIORITY 2: Check for stagnation (skip if tank >= OVERRIDE_ON_THRESHOLD)
            # If tank is full enough, we shouldn't mark as stagnant since override would be on
            if OVERRIDE_ON_THRESHOLD and point['gallons'] >= OVERRIDE_ON_THRESHOLD:
                point_colors.append('#4CAF50')  # Green - tank at override threshold
                continue

            # Find the earliest point within the 6-hour lookback window
            lookback_cutoff = point['timestamp'].timestamp() - stagnation_window_seconds
            lookback_gallons = None

            for j in range(i, -1, -1):  # Search backwards from current point
                if data_points[j]['timestamp'].timestamp() >= lookback_cutoff:
                    lookback_gallons = data_points[j]['gallons']
                else:
                    break  # Found the earliest point in window

            # If we have 6+ hours of history
            if lookback_gallons is not None and i > 0:
                time_span = point['timestamp'].timestamp() - data_points[max(0, i - 25)]['timestamp'].timestamp()

                # Only consider it stagnant if we have close to 6 hours of data
                if time_span >= stagnation_window_seconds * 0.9:  # At least 90% of 6 hours
                    gain = point['gallons'] - lookback_gallons
                    if gain <= NOTIFY_WELL_RECOVERY_MAX_STAGNATION_GAIN:
                        point_colors.append('#ff9800')  # Orange - stagnant
                    else:
                        point_colors.append('#4CAF50')  # Green - filling
                else:
                    point_colors.append('#4CAF50')  # Not enough history
            else:
                point_colors.append('#4CAF50')  # Not enough history

        return jsonify({
            'timestamps': timestamps,
            'gallons': gallons,
            'pointColors': point_colors
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
        with open('snapshots.csv', 'r') as f:
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
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
            ax.xaxis.set_major_locator(mdates.HourLocator(interval=max(1, hours // 6)))
        elif hours <= 72:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
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

    hours_explicit = request.args.get('hours', type=int)  # None if not provided
    tenant_override = request.args.get('tenant')    # "yes" or "no"
    occupied_override = request.args.get('occupied')  # "yes" or "no"
    threshold_override = request.args.get('threshold', type=int)  # e.g. 95
    scale = max(1, min(8, request.args.get('scale', 1, type=int)))  # 1-8x resolution

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
        with open('snapshots.csv', 'r') as f:
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
    reservations = load_reservations('reservations.csv')
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

    # Compute Y-axis range
    g_min_raw = min(graph_gallons) if len(graph_gallons) >= 2 else 0
    g_max_raw = max(graph_gallons) if len(graph_gallons) >= 2 else 0
    g_range_raw = g_max_raw - g_min_raw

    # Enforce minimum 5% of tank capacity between top and bottom
    min_range = TANK_CAPACITY_GALLONS * 0.05
    if g_range_raw < min_range:
        mid = (g_min_raw + g_max_raw) / 2
        g_min_raw = mid - min_range / 2
        g_max_raw = mid + min_range / 2
        g_range_raw = min_range

    g_min = g_min_raw - g_range_raw * 0.05
    g_max = g_max_raw + g_range_raw * 0.05
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
            now_label = live_reading_ts.strftime("%-m/%d %H:%M")
        else:
            last_ts = datetime.fromisoformat(rows[-1]['timestamp'])
            data_age = float(rows[-1].get('tank_data_age_seconds', 0))
            reading_ts = last_ts - timedelta(seconds=data_age)
            now_label = reading_ts.strftime("%-m/%d %H:%M")
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

    # Outside temperature label (inverted) at top-left of graph
    outdoor_temp_f = None
    if rows:
        try:
            outdoor_temp_f = float(rows[-1].get('outdoor_temp_f', ''))
        except (ValueError, TypeError):
            pass
    if outdoor_temp_f is not None:
        temp_text = f"Outside: {int(round(outdoor_temp_f))}\u00b0"
        tb = draw.textbbox((0, 0), temp_text, font=font_small)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        pad = s(2)
        lbl_img = Image.new('1', (tw + pad * 2, th + pad * 2), 0)  # black bg
        ImageDraw.Draw(lbl_img).text((pad - tb[0], pad - tb[1]), temp_text, font=font_small, fill=1)
        paste_x = graph_left + 1
        paste_y = graph_top + 1
        region = img.crop((paste_x, paste_y, paste_x + tw + pad * 2, paste_y + th + pad * 2))
        region = ImageChops.logical_xor(region, lbl_img)
        img.paste(region, (paste_x, paste_y))

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
    buf.seek(0)
    return send_file(buf, mimetype='image/bmp', download_name='epaper.bmp')


@app.route('/')
# @requires_auth
def index():
    """Main status page"""
    # Get hours parameter for filtering events by time
    hours = request.args.get('hours', DASHBOARD_DEFAULT_HOURS, type=int)

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
    snapshot_headers, snapshot_rows = read_csv_tail('snapshots.csv', max_rows=DASHBOARD_SNAPSHOT_COUNT)
    event_headers, event_rows = read_events_by_time('events.csv', hours=hours)

    # Filter events based on DASHBOARD_HIDE_EVENT_TYPES
    if event_headers and event_rows and 'event_type' in event_headers:
        event_type_idx = event_headers.index('event_type')
        event_rows = [row for row in event_rows if len(row) > event_type_idx and row[event_type_idx] not in DASHBOARD_HIDE_EVENT_TYPES]

    # Get aggregate stats from snapshots
    stats = get_snapshots_stats('snapshots.csv')

    # Get relay status
    relay_status = get_all_relay_status()

    # Get occupancy status and reservations
    reservations_csv = 'reservations.csv'
    occupancy_status = get_occupancy_status(reservations_csv)

    # Get cached Ecobee temperature
    ecobee_temp = get_cached_ecobee_temp(max_age_hours=24)

    # Get GPH metrics (cached, recalculated daily)
    gph_metrics = get_cached_gph(max_age_hours=24)

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

    # Check if totals parameter matches secret
    from monitor.config import SECRET_TOTALS_TOKEN
    show_totals = request.args.get('totals') == SECRET_TOTALS_TOKEN if SECRET_TOTALS_TOKEN else False

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
                         default_hours=DASHBOARD_DEFAULT_HOURS,
                         ecobee_temp=ecobee_temp,
                         gph_metrics=gph_metrics,
                         FLOAT_STATE_FULL=FLOAT_STATE_FULL,
                         FLOAT_STATE_CALLING=FLOAT_STATE_CALLING)

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


TIMELAPSE_DIR = '/home/pi/timelapses'


def _timelapse_dates():
    """Return sorted list of date strings (YYYY-MM-DD) that have MP4 files."""
    import glob as _glob
    files = _glob.glob(os.path.join(TIMELAPSE_DIR, '????-??-??.mp4'))
    dates = sorted([os.path.basename(f)[:-4] for f in files])
    return dates


def _day_weather_summary(date_str):
    """
    Read snapshots.csv and compute a one-day weather/pump summary.
    Returns a dict or None if no data.
    """
    import csv as _csv
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

        out_temps   = floats('outdoor_temp_f')
        in_temps    = floats('indoor_temp_f')
        humidity    = floats('outdoor_humidity')
        wind_gusts  = floats('wind_gust_mph')
        tank        = floats('tank_gallons')
        pump_secs   = floats('pressure_high_seconds')
        gal_pumped  = floats('estimated_gallons_pumped')

        return {
            'out_temp_lo':  f"{min(out_temps):.0f}" if out_temps else None,
            'out_temp_hi':  f"{max(out_temps):.0f}" if out_temps else None,
            'in_temp_avg':  f"{sum(in_temps)/len(in_temps):.0f}" if in_temps else None,
            'humidity_avg': f"{sum(humidity)/len(humidity):.0f}" if humidity else None,
            'wind_gust':    f"{max(wind_gusts):.0f}" if wind_gusts else None,
            'tank_lo':      f"{min(tank):.0f}" if tank else None,
            'tank_hi':      f"{max(tank):.0f}" if tank else None,
            'pump_min':     f"{sum(pump_secs)/60:.0f}" if pump_secs else None,
            'gal_pumped':   f"{sum(gal_pumped):.0f}" if gal_pumped else None,
        }
    except Exception:
        return None


@app.route('/timelapse')
def timelapse_index():
    """Redirect to the latest available timelapse date page."""
    dates = _timelapse_dates()
    if not dates:
        return Response('No timelapses available yet.', status=404, mimetype='text/plain')
    from flask import redirect
    return redirect(f'/timelapse/{dates[-1]}')


@app.route('/timelapse/<date_or_file>')
def timelapse_view(date_or_file):
    """
    YYYY-MM-DD      → HTML viewer page with prev/next nav and weather summary
    YYYY-MM-DD.mp4  → serve the raw MP4
    """
    import re
    # Raw MP4 request
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.mp4', date_or_file):
        path = os.path.join(TIMELAPSE_DIR, date_or_file)
        if not os.path.exists(path):
            return Response(f'Not found: {date_or_file}', status=404)
        return send_file(path, mimetype='video/mp4')

    # HTML viewer
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_or_file):
        return Response('Invalid date', status=400)

    date_str = date_or_file
    dates    = _timelapse_dates()
    mp4_path = os.path.join(TIMELAPSE_DIR, f'{date_str}.mp4')
    has_video = os.path.exists(mp4_path)

    idx  = dates.index(date_str) if date_str in dates else -1
    prev_date = dates[idx - 1] if idx > 0 else None
    next_date = dates[idx + 1] if idx >= 0 and idx < len(dates) - 1 else None

    # Human-readable title
    try:
        from datetime import date as _date
        d = _date.fromisoformat(date_str)
        title_date = d.strftime('%B %-d, %Y')
    except Exception:
        title_date = date_str

    wx = _day_weather_summary(date_str)

    def stat(label, val, unit=''):
        if val is None:
            return ''
        return f'<div class="stat"><span class="lbl">{label}</span><span class="val">{val}{unit}</span></div>'

    wx_html = ''
    if wx:
        wx_html = f"""
        <div class="weather">
          <div class="wx-group">
            {stat('Outdoor', f"{wx['out_temp_lo']}–{wx['out_temp_hi']}", '°F') if wx['out_temp_lo'] else ''}
            {stat('Indoor',  wx['in_temp_avg'],   '°F')}
            {stat('Humidity',wx['humidity_avg'],  '%')}
            {stat('Wind gust',wx['wind_gust'],    ' mph')}
          </div>
          <div class="wx-group">
            {stat('Tank',    f"{wx['tank_lo']}–{wx['tank_hi']}", ' gal') if wx['tank_lo'] else ''}
            {stat('Pumped',  wx['gal_pumped'],    ' gal')}
            {stat('Pump on', wx['pump_min'],      ' min')}
          </div>
        </div>"""

    video_html = (
        f'<video src="/timelapse/{date_str}.mp4" controls autoplay muted loop></video>'
        if has_video else
        '<p class="no-video">No timelapse recorded for this date.</p>'
    )

    prev_btn = (f'<a class="nav-btn" href="/timelapse/{prev_date}">&#8592; {prev_date}</a>'
                if prev_date else '<span class="nav-btn disabled">&#8592; older</span>')
    next_btn = (f'<a class="nav-btn" href="/timelapse/{next_date}">{next_date} &#8594;</a>'
                if next_date else '<span class="nav-btn disabled">newer &#8594;</span>')

    # List all dates newest-first
    list_items = ''.join(
        f'<li{"  class=\"current\"" if d == date_str else ""}>'
        f'<a href="/timelapse/{d}">{d}</a></li>'
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
    h2   {{ margin:0 0 12px; color:#fff; }}
    video {{ width:100%; max-width:960px; display:block;
             background:#000; border-radius:4px; }}
    .no-video {{ color:#888; font-style:italic; }}
    .nav {{ display:flex; justify-content:space-between; align-items:center;
            max-width:960px; margin:12px 0; }}
    .nav h2 {{ flex:1; text-align:center; }}
    .nav-btn {{ background:#2a2a2a; color:#4CAF50; border:1px solid #444;
                padding:6px 14px; border-radius:4px; text-decoration:none;
                white-space:nowrap; }}
    .nav-btn.disabled {{ color:#555; border-color:#333; cursor:default; }}
    .nav-btn:hover:not(.disabled) {{ background:#333; }}
    .weather {{ max-width:960px; background:#222; border:1px solid #333;
                border-radius:4px; padding:12px 16px; margin:12px 0;
                display:flex; gap:24px; flex-wrap:wrap; }}
    .wx-group {{ display:flex; flex-wrap:wrap; gap:12px; }}
    .stat {{ display:flex; flex-direction:column; min-width:80px; }}
    .lbl  {{ font-size:0.75em; color:#888; text-transform:uppercase; }}
    .val  {{ font-size:1.1em; color:#e0e0e0; }}
    details {{ max-width:960px; margin-top:16px; }}
    summary {{ cursor:pointer; color:#4CAF50; }}
    ul {{ list-style:none; padding:0; margin:8px 0; line-height:2; }}
    li a {{ color:#4CAF50; text-decoration:none; }}
    li.current a {{ color:#fff; font-weight:bold; }}
    li a:hover {{ text-decoration:underline; }}
  </style>
</head>
<body>
  <div class="nav">
    {prev_btn}
    <h2>Sunset &mdash; {title_date}</h2>
    {next_btn}
  </div>
  {video_html}
  {wx_html}
  <details>
    <summary>All timelapses ({len(dates)})</summary>
    <ul>{list_items}</ul>
  </details>
</body>
</html>"""
    return Response(html, mimetype='text/html')


@app.route('/control/<token>')
def control(token):
    """
    Unauthenticated control endpoint using secret tokens.
    Allows remote control via email links without authentication.
    """
    action_taken = None
    success = False

    # Check which action to perform based on token
    if token == SECRET_OVERRIDE_ON_TOKEN and SECRET_OVERRIDE_ON_TOKEN:
        success = set_supply_override('ON', debug=False)
        action_taken = "Supply Override turned ON"
    elif token == SECRET_OVERRIDE_OFF_TOKEN and SECRET_OVERRIDE_OFF_TOKEN:
        success = set_supply_override('OFF', debug=False)
        action_taken = "Supply Override turned OFF"
    elif token == SECRET_BYPASS_ON_TOKEN and SECRET_BYPASS_ON_TOKEN:
        success = set_bypass('ON', debug=False)
        action_taken = "Bypass turned ON"
    elif token == SECRET_BYPASS_OFF_TOKEN and SECRET_BYPASS_OFF_TOKEN:
        success = set_bypass('OFF', debug=False)
        action_taken = "Bypass turned OFF"
    elif token == SECRET_PURGE_TOKEN and SECRET_PURGE_TOKEN:
        # Trigger one-time purge
        from monitor.purge import trigger_purge
        success = trigger_purge(debug=False)
        action_taken = "Purge triggered"
    else:
        return Response('Invalid token', status=403)

    if success:
        # Log the action to events.csv
        from monitor.logger import log_event
        relay_status = get_all_relay_status()
        log_event(
            'events.csv',
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
            <meta http-equiv="refresh" content="2;url=/" />
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
