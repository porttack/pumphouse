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
    SECRET_PURGE_TOKEN, MANAGEMENT_FEE_PERCENT
)
from monitor.gpio_helpers import read_pressure, read_float_sensor, init_gpio, cleanup_gpio
from monitor.tank import get_tank_data
from monitor.check import read_temp_humidity, format_pressure_state, format_float_state
from monitor.relay import get_all_relay_status, set_supply_override, set_bypass
from monitor.stats import find_last_refill
from monitor.occupancy import (
    get_occupancy_status, get_current_and_upcoming_reservations,
    load_reservations, format_date_short
)

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

        # Import stagnation parameters
        from monitor.config import NOTIFY_WELL_RECOVERY_STAGNATION_HOURS, NOTIFY_WELL_RECOVERY_MAX_STAGNATION_GAIN

        # First pass: collect all data points in time range
        data_points = []
        for row in rows:
            try:
                ts = datetime.fromisoformat(row['timestamp'])
                if ts.timestamp() >= cutoff:
                    data_points.append({
                        'timestamp': ts,
                        'gallons': float(row['tank_gallons'])
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

    # Get occupancy status and reservations
    reservations_csv = 'reservations.csv'
    occupancy_status = get_occupancy_status(reservations_csv)

    # Get cached Ecobee temperature
    ecobee_temp = get_cached_ecobee_temp(max_age_hours=24)

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
                         default_hours=DASHBOARD_DEFAULT_HOURS,
                         ecobee_temp=ecobee_temp)

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
