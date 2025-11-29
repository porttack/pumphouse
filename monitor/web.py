#!/usr/bin/env python3
"""
Web dashboard for pumphouse monitoring system
Serves HTTPS on port 6443 with basic authentication
"""
import web
import os
import csv
import argparse
from datetime import datetime
from flask import Flask, render_template, request, Response
from functools import wraps

from monitor.config import TANK_URL, TANK_HEIGHT_INCHES, TANK_CAPACITY_GALLONS
from monitor.gpio_helpers import read_pressure, read_float_sensor, init_gpio
from monitor.tank import get_tank_data
from monitor.check import read_temp_humidity, format_pressure_state, format_float_state
# Define the URL mappings
urls = (
    '/', 'status'
)

app = Flask(__name__)
# --- Web Page Class ---
class status:
    def GET(self):
        # This is a placeholder.
        # You would add your logic here to read sensor data
        # and render it in a template.
        return "Pumphouse Monitor is running!"

# Configuration
USERNAME = os.environ.get('PUMPHOUSE_USER', 'admin')
PASSWORD = os.environ.get('PUMPHOUSE_PASS', 'pumphouse')

def check_auth(username, password):
    """Check if username/password is valid"""
    return username == USERNAME and password == PASSWORD
# --- Main Application Setup ---
def main():
    app = web.application(urls, globals())

def authenticate():
    """Send 401 response for authentication"""
    return Response(
        'Authentication required',
        401,
        {'WWW-Authenticate': 'Basic realm="Pumphouse Monitor"'}
    )
    # Certificate paths for Let's Encrypt
    domain = 'REDACTED-HOST'
    cert_path = f'/etc/letsencrypt/live/{domain}/fullchain.pem'
    key_path = f'/etc/letsencrypt/live/{domain}/privkey.pem'

def requires_auth(f):
    """Decorator for basic auth"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated
    # Check if the certificate files exist
    if not (os.path.exists(cert_path) and os.path.exists(key_path)):
        print("SSL certificate files not found.")
        print(f"Please ensure '{cert_path}' and '{key_path}' exist.")
        print("You can obtain them by following SETUP_SSL_CERT.md.")
        return

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

    return render_template('status.html',
                         sensor_data=sensor_data,
                         tank_data=tank_data,
                         tank_age_minutes=tank_age_minutes,
                         tank_height=TANK_HEIGHT_INCHES,
                         tank_capacity=TANK_CAPACITY_GALLONS,
                         snapshot_headers=snapshot_headers,
                         snapshot_rows=snapshot_rows,
                         event_headers=event_headers,
                         event_rows=event_rows,
                         format_pressure_state=format_pressure_state,
                         format_float_state=format_float_state,
                         now=datetime.now())

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        prog='monitor.web',
        description='Web dashboard for pumphouse monitoring'
    # Start the web.py HTTPS server
    web.httpserver.runsimple(
        app.wsgifunc(),
        ('0.0.0.0', 6443),
        certfile=cert_path,
        keyfile=key_path
    )
    parser.add_argument('--host', default='0.0.0.0',
                       help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=6443,
                       help='Port to listen on (default: 6443)')
    
    # Default to Let's Encrypt certificate paths
    domain = 'REDACTED-HOST'
    parser.add_argument('--cert', default=f'/etc/letsencrypt/live/{domain}/fullchain.pem',
                       help='SSL certificate file')
    parser.add_argument('--key', default=f'/etc/letsencrypt/live/{domain}/privkey.pem',
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
            print(f"   Follow SETUP_SSL_CERT.md to generate it or use --no-ssl.")
            return
    else:
        print(f"Starting HTTP server on http://{args.host}:{args.port}/")

    print(f"Username: {USERNAME}")
    print(f"Password: {PASSWORD}")
    print("\nSet credentials with environment variables:")
    print("  export PUMPHOUSE_USER=yourusername")
    print("  export PUMPHOUSE_PASS=yourpassword")

    app.run(host=args.host, port=args.port, ssl_context=ssl_context, debug=args.debug)

if __name__ == '__main__':
if __name__ == "__main__":
    main()
