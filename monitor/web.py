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


@app.route('/frame')
def frame():
    """
    Grab a single JPEG frame from the camera RTSP stream via ffmpeg.
    Faster than /sunset (no HTTP digest auth round-trips).

    Query params:
        raw - 1 = return full uncropped frame; default applies CROP_BOTTOM
    """
    import subprocess
    CROP_BOTTOM = 120   # keep in sync with sunset_timelapse.py
    CAMERA_IP   = '192.168.1.81'
    CAMERA_PORT = 554

    raw  = request.args.get('raw', 0, type=int)
    rtsp = (f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{CAMERA_IP}:{CAMERA_PORT}'
            f'/cam/realmonitor?channel=1&subtype=0')

    vf = None if raw or CROP_BOTTOM == 0 else f'crop=iw:ih-{CROP_BOTTOM}:0:0'
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
        return Response(result.stdout, status=200, mimetype='image/jpeg')
    except subprocess.TimeoutExpired:
        return Response('Camera timeout', status=503)
    except Exception as e:
        return Response(f'Error: {e}', status=503)


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


def _open_meteo_weather(date_str):
    """
    Fetch daily weather for date_str.

    Stage 1 – NWS KONP (Newport Municipal Airport): actual station observations.
              Provides weather description, precip, wind, humidity.
    Stage 2 – Open-Meteo ERA5: supplements with cloud cover and radiation;
              used as full fallback when NWS data is unavailable (date too old,
              network error, etc.).

    Results are cached; archive data never changes once a day is complete.
    """
    import json as _json
    import urllib.request as _ureq
    import urllib.parse as _uparse
    from datetime import date as _date, datetime as _dt
    from zoneinfo import ZoneInfo as _ZI

    os.makedirs(WEATHER_CACHE_DIR, exist_ok=True)
    cache = os.path.join(WEATHER_CACHE_DIR, f'{date_str}.json')

    if os.path.exists(cache):
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

        temps, winds, humidities, descs = [], [], [], []
        precip_total = 0.0
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
            if desc:
                descs.append(desc)

        if temps:
            result = {
                'source':       'nws',
                'weather_desc': descs[len(descs) // 2] if descs else None,
                'temp_max':     f'{max(temps):.0f}',
                'temp_min':     f'{min(temps):.0f}',
                'precip':       f'{precip_total:.2f}',
                'wind_max':     f'{max(winds):.0f}'                    if winds      else None,
                'wind_avg':     f'{sum(winds)/len(winds):.0f}'         if winds      else None,
                'humidity':     f'{sum(humidities)/len(humidities):.0f}' if humidities else None,
            }
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


@app.route('/timelapse')
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


@app.route('/timelapse/latest.mp4')
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


@app.route('/timelapse/latest.jpg')
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


@app.route('/timelapse/<date_str>/snapshot')
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


@app.route('/api/ratings/<date_str>')
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


@app.route('/timelapse/<date_str>/rate', methods=['POST'])
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


@app.route('/timelapse/<date_or_file>')
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
        desc_html = f'<div class="wx-desc">{om["weather_desc"]}</div>' if om.get('weather_desc') else ''
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

    video_html = (
        f'<video id="vid" src="/timelapse/{mp4_name}" controls autoplay muted loop playsinline></video>'
        f'<div class="ctrl-row">'
        f'<div class="speed-btns">'
        f'<span class="speed-lbl">Speed:</span>'
        f'<button class="speed-btn" data-rate="0.25">&#188;x</button>'
        f'<button class="speed-btn" data-rate="0.5">&#189;x</button>'
        f'<button class="speed-btn active" data-rate="1">1x</button>'
        f'<button class="speed-btn" data-rate="2">2x</button>'
        f'<button class="speed-btn" data-rate="4">4x</button>'
        f'<button class="speed-btn" data-rate="8">8x</button>'
        f'</div>'
        f'<div class="ctrl-btns">'
        f'<button id="pause-btn" class="speed-btn pause-btn">&#9646;&#9646; Pause</button>'
        f'<button id="dl-btn" class="speed-btn dl-btn">&#8681; Snapshot</button>'
        f'</div>'
        f'</div>'
        if has_video else
        '<p class="no-video">No timelapse recorded for this date.</p>'
    )

    prev_btn = (f'<a class="nav-btn" href="/timelapse/{prev_date}">&#8592;<span class="nav-label">&nbsp;{_short_date(prev_date)}</span></a>'
                if prev_date else '<span class="nav-btn disabled">&#8592;</span>')
    next_btn = (f'<a class="nav-btn" href="/timelapse/{next_date}"><span class="nav-label">{_short_date(next_date)}&nbsp;</span>&#8594;</a>'
                if next_date else '<span class="nav-btn disabled">&#8594;</span>')
    prev_js     = f'"{prev_date}"' if prev_date else 'null'
    next_js     = f'"{next_date}"' if next_date else 'null'

    # List all dates newest-first with snapshot thumbnails, sunset time, and rating
    import re as _re
    all_ratings = _read_ratings()

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
    </span>
  </header>
  <div class="nav">
    {prev_btn}
    <div class="nav-center">
      <h2>Sunset &mdash; {title_date}</h2>
      <div class="swipe-hint">swipe &#8592; &#8594; to change days</div>
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
      const s = parseFloat(localStorage.getItem('tl_speed') || '1');
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
    // Snapshot button: open JPEG page on touch devices; download frame on desktop
    const dlBtn = document.getElementById('dl-btn');
    if (dlBtn && vid) {{
      dlBtn.addEventListener('click', () => {{
        if (navigator.maxTouchPoints > 0) {{
          window.open('/timelapse/{date_str}/snapshot', '_blank');
          return;
        }}
        const canvas = document.createElement('canvas');
        canvas.width  = vid.videoWidth;
        canvas.height = vid.videoHeight;
        canvas.getContext('2d').drawImage(vid, 0, 0);
        const a = document.createElement('a');
        a.download = 'sunset-{date_str}.jpg';
        a.href = canvas.toDataURL('image/jpeg', 0.92);
        a.click();
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
    (function() {{
      const prev    = {prev_js};
      const next    = {next_js};
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
      }});
    }})();
    // Touch swipe: left = newer day, right = older day
    (function() {{
      const prev = {prev_js};
      const next = {next_js};
      var tx = null, ty = null;
      document.addEventListener('touchstart', function(e) {{
        tx = e.touches[0].clientX;
        ty = e.touches[0].clientY;
      }}, {{passive: true}});
      document.addEventListener('touchend', function(e) {{
        if (tx === null) return;
        var dx = e.changedTouches[0].clientX - tx;
        var dy = e.changedTouches[0].clientY - ty;
        tx = null; ty = null;
        if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
        if (dx > 0 && prev) location.href = '/timelapse/' + prev;  // swipe right → older
        if (dx < 0 && next) location.href = '/timelapse/' + next;  // swipe left  → newer
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
    from datetime import date as _date
    is_past = date_str < _date.today().isoformat()
    cache_hdr = ('public, max-age=31536000, immutable' if is_past
                 else 'public, max-age=600, must-revalidate')
    return Response(html, mimetype='text/html', headers={'Cache-Control': cache_hdr})


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
