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
    TANK_URL, TANK_HEIGHT_INCHES, TANK_CAPACITY_GALLONS, DASHBOARD_HIDE_EVENT_TYPES,
    DASHBOARD_MAX_EVENTS, DASHBOARD_DEFAULT_HOURS,
    SECRET_OVERRIDE_ON_TOKEN, SECRET_OVERRIDE_OFF_TOKEN,
    SECRET_BYPASS_ON_TOKEN, SECRET_BYPASS_OFF_TOKEN,
    SECRET_PURGE_TOKEN
)
from monitor.gpio_helpers import read_pressure, read_float_sensor, init_gpio, cleanup_gpio
from monitor.tank import get_tank_data
from monitor.check import read_temp_humidity, format_pressure_state, format_float_state
from monitor.relay import get_all_relay_status, set_supply_override, set_bypass
from monitor.stats import find_last_refill

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
            return jsonify({'timestamps': [], 'gallons': []})

        # Filter by time range
        now = datetime.now()
        cutoff = now.timestamp() - (hours * 3600)

        timestamps = []
        gallons = []

        for row in rows:
            try:
                ts = datetime.fromisoformat(row['timestamp'])
                if ts.timestamp() >= cutoff:
                    timestamps.append(ts.strftime('%a %H:%M')) # Format as Day HH:MM
                    gallons.append(float(row['tank_gallons']))
            except:
                continue

        return jsonify({
            'timestamps': timestamps,
            'gallons': gallons
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
                    timestamps.append(ts)
                    gallons.append(float(row['tank_gallons']))
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

@app.route('/')
# @requires_auth
def index():
    """Main status page"""
    # Get sensor data
    sensor_data = get_sensor_data()

    # Get tank data
    tank_data = get_tank_data(TANK_URL)

    # Calculate tank data age if available
    tank_age_minutes = None
    if tank_data['status'] == 'success' and tank_data.get('last_updated'):
        age_seconds = (datetime.now() - tank_data['last_updated']).total_seconds()
        tank_age_minutes = int(age_seconds / 60)

    # Read CSV files
    snapshot_headers, snapshot_rows = read_csv_tail('snapshots.csv', max_rows=10)
    event_headers, event_rows = read_csv_tail('events.csv', max_rows=DASHBOARD_MAX_EVENTS)

    # Filter events based on DASHBOARD_HIDE_EVENT_TYPES
    if event_headers and event_rows and 'event_type' in event_headers:
        event_type_idx = event_headers.index('event_type')
        event_rows = [row for row in event_rows if len(row) > event_type_idx and row[event_type_idx] not in DASHBOARD_HIDE_EVENT_TYPES]

    # Get aggregate stats from snapshots
    stats = get_snapshots_stats('snapshots.csv')

    # Get relay status
    relay_status = get_all_relay_status()

    return render_template('status.html',
                         version=__version__,
                         sensor_data=sensor_data,
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
                         format_pressure_state=format_pressure_state,
                         format_float_state=format_float_state,
                         now=datetime.now(),
                         startup_time=STARTUP_TIME,
                         default_hours=DASHBOARD_DEFAULT_HOURS)

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

    app.run(host=args.host, port=args.port, ssl_context=ssl_context, debug=args.debug)

if __name__ == "__main__":
    main()
