#!/usr/bin/env python3
"""
Web dashboard for pumphouse monitoring system
Serves HTTPS on port 6443 with basic authentication
"""
import os
import csv
import argparse
from datetime import datetime
from flask import Flask, render_template, request, Response
from functools import wraps

from monitor.config import TANK_URL, TANK_HEIGHT_INCHES, TANK_CAPACITY_GALLONS
from monitor.gpio_helpers import read_pressure, read_float_sensor, init_gpio, cleanup_gpio
from monitor.tank import get_tank_data
from monitor.check import read_temp_humidity, format_pressure_state, format_float_state

app = Flask(__name__)

# Configuration
USERNAME = os.environ.get('PUMPHOUSE_USER', 'admin')
PASSWORD = os.environ.get('PUMPHOUSE_PASS', 'pumphouse')

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

        # Find last time tank increased by 50+ gallons
        # Scan forward through all snapshots looking for cumulative gain of 50+
        if len(rows) >= 2:
            try:
                cumulative_gain = 0
                refill_start_idx = None
                last_refill_idx = None
                last_refill_gain = 0

                for i in range(1, len(rows)):
                    try:
                        prev_gallons = float(rows[i - 1]['tank_gallons'])
                        curr_gallons = float(rows[i]['tank_gallons'])
                        delta = curr_gallons - prev_gallons

                        if delta > 0:
                            # Tank increased
                            if cumulative_gain == 0:
                                refill_start_idx = i  # Mark where refill started
                            cumulative_gain += delta

                            # Check if we hit 50+ gallons
                            if cumulative_gain >= 50:
                                last_refill_idx = refill_start_idx
                                last_refill_gain = cumulative_gain
                        else:
                            # Tank decreased or stayed same, reset
                            cumulative_gain = 0
                            refill_start_idx = None
                    except (ValueError, KeyError):
                        # Skip rows with invalid data
                        continue

                # Use the most recent 50+ gallon refill we found
                if last_refill_idx is not None:
                    refill_time = datetime.fromisoformat(rows[last_refill_idx]['timestamp'])
                    days_ago = (now - refill_time).total_seconds() / 86400
                    stats['last_refill_50_days'] = days_ago
                    stats['last_refill_50_timestamp'] = refill_time
            except Exception as e:
                # Silently ignore errors in refill calculation
                pass

        return stats

    except Exception as e:
        return None

def get_sensor_data():
    """Read current sensor states"""
    gpio_available = init_gpio()

    data = {
        'pressure': None,
        'float': None,
        'temp': None,
        'humidity': None,
        'gpio_available': gpio_available
    }

    if gpio_available:
        data['pressure'] = read_pressure()
        data['float'] = read_float_sensor()

    # Read temp/humidity
    temp_f, humidity = read_temp_humidity()
    data['temp'] = temp_f
    data['humidity'] = humidity

    # Cleanup GPIO after reading
    cleanup_gpio()

    return data

@app.route('/')
@requires_auth
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
    event_headers, event_rows = read_csv_tail('events.csv', max_rows=20)

    # Get aggregate stats from snapshots
    stats = get_snapshots_stats('snapshots.csv')

    return render_template('status.html',
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
                         format_pressure_state=format_pressure_state,
                         format_float_state=format_float_state,
                         now=datetime.now())

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
