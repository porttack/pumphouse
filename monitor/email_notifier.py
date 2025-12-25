"""
Email notification sender for pumphouse alerts
Sends HTML emails with embedded charts and status information
"""
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from datetime import datetime
import requests


def format_human_time(timestamp_str):
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

from monitor.config import (
    ENABLE_EMAIL_NOTIFICATIONS,
    EMAIL_TO,
    EMAIL_FROM,
    EMAIL_FRIENDLY_NAME,
    EMAIL_SMTP_SERVER,
    EMAIL_SMTP_PORT,
    EMAIL_SMTP_USER,
    EMAIL_SMTP_PASSWORD,
    DASHBOARD_URL,
    DASHBOARD_EMAIL_URL,
    DAILY_STATUS_EMAIL_CHART_HOURS,
    TANK_HEIGHT_INCHES,
    TANK_CAPACITY_GALLONS,
    TANK_URL,
    DASHBOARD_HIDE_EVENT_TYPES,
    DASHBOARD_MAX_EVENTS,
    SECRET_OVERRIDE_ON_TOKEN,
    SECRET_OVERRIDE_OFF_TOKEN,
    SECRET_BYPASS_ON_TOKEN,
    SECRET_BYPASS_OFF_TOKEN,
    SECRET_PURGE_TOKEN,
    SECRET_TOTALS_TOKEN
)


def send_email_notification(subject, message, priority='default', dashboard_url=None, chart_url=None, debug=False, include_status=True):
    """
    Send HTML email notification with embedded chart and system status

    Args:
        subject: Email subject line (the alert reason)
        message: Alert message text
        priority: 'default', 'high', 'urgent' (affects formatting)
        dashboard_url: URL to link to dashboard
        chart_url: URL of chart image to embed
        debug: Print debug info
        include_status: Include current system status in email (default: True)

    Returns:
        True if sent successfully, False otherwise
    """
    if not ENABLE_EMAIL_NOTIFICATIONS:
        if debug:
            print("Email notifications disabled in config", file=sys.stderr)
        return False

    if not EMAIL_FROM or not EMAIL_SMTP_USER or not EMAIL_SMTP_PASSWORD:
        if debug:
            print("Email not configured (missing EMAIL_FROM, EMAIL_SMTP_USER, or EMAIL_SMTP_PASSWORD)", file=sys.stderr)
        return False

    if not EMAIL_TO:
        if debug:
            print("No recipient configured (EMAIL_TO)", file=sys.stderr)
        return False

    try:
        # Create message
        msg = MIMEMultipart('related')
        msg['Subject'] = subject
        # Use friendly name in From field if configured
        if EMAIL_FRIENDLY_NAME:
            msg['From'] = f"{EMAIL_FRIENDLY_NAME} <{EMAIL_FROM}>"
        else:
            msg['From'] = EMAIL_FROM
        msg['To'] = EMAIL_TO
        msg['Date'] = datetime.now().strftime('%a, %d %b %Y %H:%M:%S %z')

        # Priority header
        if priority == 'urgent':
            msg['X-Priority'] = '1'
            msg['Importance'] = 'high'
        elif priority == 'high':
            msg['X-Priority'] = '2'
            msg['Importance'] = 'high'

        # Fetch current system status if requested
        status_data = None
        if include_status:
            status_data = fetch_system_status(debug=debug)

        # Build HTML body
        html_body = build_html_email(subject, message, priority, dashboard_url, chart_url, status_data)

        # Attach HTML
        html_part = MIMEText(html_body, 'html')
        msg.attach(html_part)

        # Fetch and embed chart image if provided
        if chart_url:
            try:
                if debug:
                    print(f"Fetching chart from: {chart_url}")
                response = requests.get(chart_url, timeout=10)
                response.raise_for_status()

                image_part = MIMEImage(response.content)
                image_part.add_header('Content-ID', '<chart_image>')
                image_part.add_header('Content-Disposition', 'inline', filename='chart.png')
                msg.attach(image_part)

                if debug:
                    print(f"Chart image attached ({len(response.content)} bytes)")
            except Exception as e:
                if debug:
                    print(f"Warning: Could not fetch chart image: {e}", file=sys.stderr)

        # Send email
        if debug:
            print(f"Connecting to SMTP server: {EMAIL_SMTP_SERVER}:{EMAIL_SMTP_PORT}")

        with smtplib.SMTP(EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT, timeout=10) as server:
            server.starttls()
            if debug:
                print(f"Logging in as: {EMAIL_SMTP_USER}")
            server.login(EMAIL_SMTP_USER, EMAIL_SMTP_PASSWORD)
            if debug:
                print(f"Sending email to: {EMAIL_TO}")
            server.send_message(msg)

        if debug:
            print(f"✓ Email sent successfully: {subject}")
        return True

    except Exception as e:
        print(f"Failed to send email notification: {e}", file=sys.stderr)
        if debug:
            import traceback
            traceback.print_exc()
        return False


def get_snapshots_stats(filepath='snapshots.csv'):
    """Calculate aggregate stats from snapshots.csv for 1hr and 24hr windows"""
    import os
    import csv
    from monitor.stats import find_last_refill

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
        refill_ts, days_ago = find_last_refill(filepath, threshold_gallons=50)
        if refill_ts and days_ago is not None:
            stats['last_refill_50_timestamp'] = refill_ts
            stats['last_refill_50_days'] = days_ago

        return stats
    except Exception:
        return None


def get_recent_events(filepath='events.csv', max_rows=None, hide_types=None):
    """Get recent events with optional filtering"""
    import os
    import csv

    if max_rows is None:
        max_rows = DASHBOARD_MAX_EVENTS
    if hide_types is None:
        hide_types = DASHBOARD_HIDE_EVENT_TYPES

    if not os.path.exists(filepath):
        return None, None

    try:
        with open(filepath, 'r') as f:
            reader = csv.reader(f)
            headers = next(reader, None)
            if not headers:
                return None, None

            # Read all rows and filter
            rows = list(reader)

            # Filter by event type if specified
            if 'event_type' in headers and hide_types:
                event_type_idx = headers.index('event_type')
                rows = [row for row in rows if len(row) > event_type_idx and row[event_type_idx] not in hide_types]

            # Take last N rows
            rows = rows[-max_rows:] if len(rows) > max_rows else rows

            return headers, rows
    except Exception:
        return None, None


def get_cached_ecobee_temp(max_age_hours=24):
    """Get cached Ecobee temperature data from CSV"""
    try:
        import csv
        from pathlib import Path

        cache_file = Path(__file__).parent.parent / 'ecobee_temp_cache.csv'

        if not cache_file.exists():
            return None

        with open(cache_file, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if not rows:
            return None

        # Check age using first row's timestamp
        if max_age_hours is not None:
            cache_time = datetime.fromisoformat(rows[0]['timestamp'])
            age_hours = (datetime.now() - cache_time).total_seconds() / 3600

            if age_hours > max_age_hours:
                return None

        # Convert to a dict format for easier use in templates
        # Format: {'timestamp': '...', 'thermostats': {'Name': {'temperature': 72, ...}}}
        result = {
            'timestamp': rows[0]['timestamp'],
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


def fetch_system_status(debug=False):
    """Fetch current system status (tank, sensors, stats, relays, events, occupancy, reservations)"""
    try:
        from monitor.tank import get_tank_data
        from monitor.gpio_helpers import read_pressure, read_float_sensor
        from monitor.relay import get_all_relay_status
        from monitor.occupancy import get_occupancy_status, load_reservations, get_current_and_upcoming_reservations, parse_date, format_date_short
        import os
        import csv

        # Fetch tank data
        tank_data = get_tank_data(TANK_URL)

        # Read sensors (will work even without GPIO init using command fallback)
        pressure = read_pressure()
        float_state = read_float_sensor()

        # Get relay status
        relay_status = get_all_relay_status()

        # Get stats from snapshots
        stats = None
        try:
            stats = get_snapshots_stats('snapshots.csv')
        except Exception as e:
            if debug:
                print(f"Warning: Could not get stats: {e}", file=sys.stderr)

        # Get recent events
        event_headers, event_rows = get_recent_events()

        # Get occupancy status and reservations
        occupancy_status = None
        reservation_list = []
        try:
            reservations_csv = 'reservations.csv'
            occupancy_status = get_occupancy_status(reservations_csv)

            # Load all reservations including checked out
            all_reservations = []
            if os.path.exists(reservations_csv):
                with open(reservations_csv, 'r') as f:
                    reader = csv.DictReader(f)
                    all_reservations = list(reader)

            # Filter for current and next month checkouts
            now = datetime.now()
            current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if now.month == 12:
                next_month_start = current_month_start.replace(year=now.year + 1, month=1)
            else:
                next_month_start = current_month_start.replace(month=now.month + 1)

            if next_month_start.month == 12:
                month_after_next = next_month_start.replace(year=next_month_start.year + 1, month=1)
            else:
                month_after_next = next_month_start.replace(month=next_month_start.month + 1)

            for res in all_reservations:
                checkout_date = parse_date(res.get('Checkout'))
                if checkout_date and current_month_start <= checkout_date < month_after_next:
                    reservation_list.append(res)

            # Sort by checkout date
            reservation_list.sort(key=lambda x: parse_date(x.get('Checkout')) or datetime.min)

        except Exception as e:
            if debug:
                print(f"Warning: Could not get occupancy/reservations: {e}", file=sys.stderr)

        # Get cached Ecobee temperature
        ecobee_temp = None
        try:
            ecobee_temp = get_cached_ecobee_temp(max_age_hours=24)
        except Exception as e:
            if debug:
                print(f"Warning: Could not get Ecobee temperature: {e}", file=sys.stderr)

        return {
            'tank': tank_data,
            'pressure': pressure,
            'float': float_state,
            'relay': relay_status,
            'stats': stats,
            'events': {'headers': event_headers, 'rows': event_rows},
            'occupancy': occupancy_status,
            'reservations': reservation_list,
            'ecobee_temp': ecobee_temp
        }
    except Exception as e:
        if debug:
            print(f"Warning: Could not fetch system status: {e}", file=sys.stderr)
        return None


def format_float_state(state):
    """Format float state with explanation"""
    if state == 'CLOSED/CALLING':
        return "CLOSED/CALLING (tank needs water)"
    elif state == 'OPEN/FULL':
        return "OPEN/FULL (tank is full)"
    else:
        return state if state else "UNKNOWN"


def format_pressure_state(state):
    """Format pressure state with explanation"""
    if state is None:
        return "UNKNOWN"
    elif state:
        return "HIGH (≥10 PSI)"
    else:
        return "LOW (<10 PSI)"


def build_html_email(subject, message, priority, dashboard_url, chart_url, status_data=None):
    """Build HTML email body with styling similar to status.html and full system status"""

    # Determine priority color
    priority_color = '#4CAF50'  # default (green)
    priority_emoji = 'ℹ️'
    if priority == 'urgent':
        priority_color = '#f44336'  # red
        priority_emoji = '🚨'
    elif priority == 'high':
        priority_color = '#ff9800'  # orange
        priority_emoji = '⚠️'

    # Parse status data
    tank_data = status_data.get('tank') if status_data else None
    pressure = status_data.get('pressure') if status_data else None
    float_state = status_data.get('float') if status_data else None
    relay_status = status_data.get('relay') if status_data else None
    stats = status_data.get('stats') if status_data else None
    events_data = status_data.get('events') if status_data else None
    occupancy_status = status_data.get('occupancy') if status_data else None
    reservation_list = status_data.get('reservations') if status_data else None
    ecobee_temp = status_data.get('ecobee_temp') if status_data else None

    # Get the dashboard link to use in the email
    # Add totals parameter if secret token is configured
    if DASHBOARD_EMAIL_URL:
        email_dashboard_url = DASHBOARD_EMAIL_URL
        # Add totals parameter if not already present and token exists
        if SECRET_TOTALS_TOKEN and 'totals=' not in email_dashboard_url:
            separator = '&' if '?' in email_dashboard_url else '?'
            email_dashboard_url = f"{email_dashboard_url}{separator}totals={SECRET_TOTALS_TOKEN}"
    else:
        email_dashboard_url = f"{dashboard_url}?hours={DAILY_STATUS_EMAIL_CHART_HOURS}"
        if SECRET_TOTALS_TOKEN:
            email_dashboard_url += f"&totals={SECRET_TOTALS_TOKEN}"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{subject}</title>
    <style>
        body {{
            font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
            background: #1a1a1a;
            color: #e0e0e0;
            padding: 20px;
            line-height: 1.6;
            margin: 0;
        }}
        .container {{
            max-width: 700px;
            margin: 0 auto;
            background: #2a2a2a;
            border: 1px solid #444;
            border-radius: 4px;
            overflow: hidden;
        }}
        .header {{
            background: {priority_color};
            color: white;
            padding: 20px;
            text-align: center;
        }}
        .header h1 {{
            margin: 0;
            font-size: 20px;
            font-weight: bold;
        }}
        .dashboard-link {{
            background: #1a1a1a;
            border: 1px solid #444;
            padding: 12px;
            margin: 20px 0 0 0;
            border-radius: 4px;
            text-align: center;
        }}
        .dashboard-link a {{
            color: #4CAF50;
            text-decoration: none;
            font-weight: bold;
            font-size: 14px;
        }}
        .dashboard-link a:hover {{
            text-decoration: underline;
        }}
        .content {{
            padding: 20px;
        }}
        .alert-box {{
            background: #1a1a1a;
            border-left: 4px solid {priority_color};
            padding: 15px;
            margin: 20px 0;
            border-radius: 4px;
        }}
        .alert-message {{
            font-size: 16px;
            margin: 0;
        }}
        .chart {{
            margin: 20px 0;
            text-align: center;
        }}
        .chart img {{
            max-width: 100%;
            height: auto;
            border: 1px solid #444;
            border-radius: 4px;
        }}
        .button {{
            display: inline-block;
            background: #4CAF50;
            color: white;
            padding: 12px 24px;
            text-decoration: none;
            border-radius: 4px;
            margin: 10px 0;
            font-weight: bold;
        }}
        .button:hover {{
            background: #45a049;
        }}
        .footer {{
            background: #1a1a1a;
            padding: 15px;
            text-align: center;
            font-size: 12px;
            color: #888;
            border-top: 1px solid #444;
        }}
        .timestamp {{
            color: #888;
            font-size: 14px;
            margin-top: 10px;
        }}
        .section {{
            background: #1a1a1a;
            border: 1px solid #444;
            padding: 15px;
            margin: 15px 0;
            border-radius: 4px;
        }}
        .section h2 {{
            color: #4CAF50;
            font-size: 16px;
            margin: 0 0 12px 0;
            border-bottom: 1px solid #444;
            padding-bottom: 6px;
        }}
        .status-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 12px;
        }}
        .status-item {{
            background: #2a2a2a;
            padding: 10px;
            border-radius: 4px;
            border-left: 3px solid #4CAF50;
        }}
        .status-label {{
            color: #888;
            font-size: 11px;
            margin-bottom: 4px;
        }}
        .status-value {{
            color: #e0e0e0;
            font-size: 14px;
            font-weight: bold;
        }}
        .positive {{
            color: #4CAF50;
        }}
        .negative {{
            color: #ff9800;
        }}
        .tank-bar {{
            background: #1a1a1a;
            height: 30px;
            border-radius: 4px;
            overflow: hidden;
            position: relative;
            margin: 10px 0;
        }}
        .tank-fill {{
            background: linear-gradient(to right, #2196F3, #4CAF50);
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-weight: bold;
            font-size: 13px;
        }}
        .tank-label {{
            position: absolute;
            width: 100%;
            text-align: center;
            line-height: 30px;
            color: #e0e0e0;
            font-weight: bold;
            font-size: 13px;
            z-index: 1;
        }}
        .relay-warning {{
            background: #f44336;
            color: white;
            padding: 12px;
            margin: 15px 0;
            border-radius: 4px;
            font-weight: bold;
            font-size: 15px;
            text-align: center;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 11px;
            background: #1a1a1a;
            margin-top: 10px;
        }}
        th {{
            background: #333;
            color: #4CAF50;
            padding: 8px 4px;
            text-align: left;
            border-bottom: 2px solid #444;
            font-size: 10px;
            vertical-align: bottom;
            white-space: nowrap;
        }}
        th .vertical-text {{
            writing-mode: vertical-rl;
            transform: rotate(180deg);
            text-align: left;
            min-height: 60px;
            display: inline-block;
        }}
        td {{
            padding: 6px 4px;
            border-bottom: 1px solid #333;
            font-size: 10px;
        }}
        td:first-child {{
            width: 1%;
            white-space: nowrap;
        }}
        tr:hover {{
            background: #2a2a2a;
        }}
        .table-container {{
            overflow-x: auto;
            max-height: 400px;
            overflow-y: auto;
            border: 1px solid #444;
            border-radius: 4px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{subject}</h1>
        </div>

        <div class="content">
"""

    # Add dashboard link at the top if available
    if dashboard_url:
        html += f"""
            <div class="dashboard-link">
                <a href="{email_dashboard_url}">📊 View Dashboard</a>
            </div>
"""

    html += f"""
            <div class="alert-box">
                <p class="alert-message">{message}</p>
            </div>
"""

    # Add relay warnings if any are ON
    if relay_status:
        warnings = []
        if relay_status.get('supply_override') == 'ON':
            warnings.append("⚠️ SUPPLY OVERRIDE IS ON")
        if relay_status.get('bypass') == 'ON':
            warnings.append("⚠️ BYPASS IS ON")

        for warning in warnings:
            html += f"""
            <div class="relay-warning">{warning}</div>
"""

    # Add tank level status if available
    if tank_data and tank_data.get('status') == 'success' and tank_data.get('gallons') is not None:
        gallons = tank_data['gallons']
        percentage = tank_data['percentage']
        depth = tank_data['depth']

        html += f"""
            <div class="section">
                <h2>TANK LEVEL</h2>
                <div class="tank-bar">
                    <div class="tank-label">{percentage:.1f}% ({gallons:.0f} gal)</div>
                    <div class="tank-fill" style="width: {percentage:.1f}%;"></div>
                </div>
                <div class="status-grid">
                    <div class="status-item">
                        <div class="status-label">Depth</div>
                        <div class="status-value">{depth:.2f}" / {TANK_HEIGHT_INCHES}" ({percentage:.1f}%)</div>
                    </div>
                    <div class="status-item">
                        <div class="status-label">Gallons</div>
                        <div class="status-value">{gallons:.0f} / {TANK_CAPACITY_GALLONS} gal</div>
                    </div>
"""

        # Add stats if available
        if stats:
            if stats.get('tank_change_1hr') is not None:
                change_1hr = stats['tank_change_1hr']
                change_1hr_class = 'positive' if change_1hr >= 0 else 'negative'
                html += f"""
                    <div class="status-item">
                        <div class="status-label">Change (1 hour)</div>
                        <div class="status-value"><span class="{change_1hr_class}">{change_1hr:+.0f} gal</span></div>
                    </div>
"""
            if stats.get('tank_change_24hr') is not None:
                change_24hr = stats['tank_change_24hr']
                change_24hr_class = 'positive' if change_24hr >= 0 else 'negative'
                html += f"""
                    <div class="status-item">
                        <div class="status-label">Change (24 hours)</div>
                        <div class="status-value"><span class="{change_24hr_class}">{change_24hr:+.0f} gal</span></div>
                    </div>
"""
            if stats.get('last_refill_50_days') is not None:
                refill_days = stats['last_refill_50_days']
                if refill_days < 1:
                    refill_str = f"{refill_days * 24:.1f} hours ago"
                else:
                    refill_str = f"{refill_days:.1f} days ago"
                html += f"""
                    <div class="status-item">
                        <div class="status-label">Last refill 50+ gal</div>
                        <div class="status-value">{refill_str}</div>
                    </div>
"""

        html += """
                </div>
            </div>
"""

    # Add sensor status if available
    if pressure is not None or float_state is not None:
        html += """
            <div class="section">
                <h2>SENSORS</h2>
                <div class="status-grid">
"""
        if float_state is not None:
            float_color = '#4CAF50' if float_state == 'OPEN/FULL' else '#ff9800'
            html += f"""
                    <div class="status-item" style="border-left-color: {float_color};">
                        <div class="status-label">Float Switch</div>
                        <div class="status-value">{format_float_state(float_state)}</div>
                    </div>
"""
        if pressure is not None:
            pressure_color = '#4CAF50' if pressure else '#ff9800'
            html += f"""
                    <div class="status-item" style="border-left-color: {pressure_color};">
                        <div class="status-label">Pressure</div>
                        <div class="status-value">{format_pressure_state(pressure)}</div>
                    </div>
"""
        if stats and stats.get('pressure_high_pct_1hr') is not None:
            html += f"""
                    <div class="status-item">
                        <div class="status-label">Pressure HIGH (1 hour)</div>
                        <div class="status-value">{stats['pressure_high_pct_1hr']:.1f}%</div>
                    </div>
"""
        if stats and stats.get('pressure_high_min_24hr') is not None:
            html += f"""
                    <div class="status-item">
                        <div class="status-label">Pressure HIGH (24 hours)</div>
                        <div class="status-value">{stats['pressure_high_min_24hr']:.0f} min</div>
                    </div>
"""
        if occupancy_status:
            occupancy_color = '#ff9800' if occupancy_status.get('occupied') else '#4CAF50'
            html += f"""
                    <div class="status-item" style="border-left-color: {occupancy_color};">
                        <div class="status-label">Occupancy</div>
                        <div class="status-value">{occupancy_status.get('status_text', 'UNKNOWN')}</div>
"""
            if occupancy_status.get('next_checkin'):
                html += f"""
                        <div class="status-label" style="margin-top: 8px; font-size: 11px;">
                            Next check-in: {occupancy_status.get('next_checkin')}
                        </div>
"""
            html += """
                    </div>
"""
        # Add Ecobee temperature if available
        if ecobee_temp and ecobee_temp.get('thermostats'):
            cache_time = datetime.fromisoformat(ecobee_temp['timestamp'])
            age_minutes = (datetime.now() - cache_time).total_seconds() / 60
            age_str = f"{int(age_minutes)}m ago" if age_minutes < 60 else f"{age_minutes/60:.1f}h ago"

            # Combine all temps into one box
            temps = []
            for name, data in ecobee_temp['thermostats'].items():
                temp = data.get('temperature')
                if temp is not None:
                    temps.append(f"{temp:.0f}°F")

            if temps:
                temps_str = " / ".join(temps)
                html += f"""
                    <div class="status-item" style="border-left-color: #FF9800;">
                        <div class="status-label">House Temps</div>
                        <div class="status-value">{temps_str}</div>
                        <div class="status-label" style="margin-top: 4px; font-size: 10px; color: #666;">
                            Updated {age_str}
                        </div>
                    </div>
"""
        html += """
                </div>
            </div>
"""

    # Add chart if available
    if chart_url:
        html += """
            <div class="section">
                <h2>TANK LEVEL HISTORY</h2>
                <div style="text-align: center;">
                    <img src="cid:chart_image" alt="Tank Level Chart" style="max-width: 100%; height: auto; border: 1px solid #444; border-radius: 4px;">
                </div>
            </div>
"""

    # Add reservations table if available
    if reservation_list:
        from monitor.occupancy import format_date_short
        html += f"""
            <div class="section">
                <h2>RESERVATIONS - CURRENT & NEXT MONTH</h2>
                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th>Check-In</th>
                                <th>Check-Out</th>
                                <th>Nights</th>
                                <th>Guest Type</th>
                                <th>Booking</th>
                            </tr>
                        </thead>
                        <tbody>
"""
        for res in reservation_list:
            guest_type = 'Owner' if 'Owner' in res.get('Type', '') else 'Guest'
            html += f"""
                            <tr>
                                <td>{format_date_short(res.get('Check-In', ''))}</td>
                                <td>{format_date_short(res.get('Checkout', ''))}</td>
                                <td style="text-align: center;">{res.get('Nights', '')}</td>
                                <td>{guest_type}</td>
                                <td>{res.get('Type', '')}</td>
                            </tr>
"""
        html += """
                        </tbody>
                    </table>
                </div>
            </div>
"""

    # Add recent events table if available
    if events_data and events_data.get('headers') and events_data.get('rows'):
        event_headers = events_data['headers']
        event_rows = events_data['rows']
        html += f"""
            <div class="section">
                <h2>RECENT EVENTS (Last {len(event_rows)})</h2>
                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
"""
        for header in event_headers:
            html += f"""                                <th><div class="vertical-text">{header}</div></th>
"""
        html += """                            </tr>
                        </thead>
                        <tbody>
"""
        # Reverse rows to show most recent first
        for row in reversed(event_rows):
            html += """                            <tr>
"""
            for i, cell in enumerate(row):
                formatted_cell = format_human_time(cell) if i == 0 else cell
                html += f"""                                <td>{formatted_cell}</td>
"""
            html += """                            </tr>
"""
        html += """                        </tbody>
                    </table>
                </div>
            </div>
"""

    # Add dashboard and camera links if available
    if dashboard_url:
        # Add totals parameter to dashboard button if configured
        full_dashboard_url = dashboard_url
        if SECRET_TOTALS_TOKEN:
            separator = '&' if '?' in full_dashboard_url else '?'
            full_dashboard_url = f"{full_dashboard_url}{separator}totals={SECRET_TOTALS_TOKEN}"

        html += f"""
            <div style="text-align: center; margin: 20px 0;">
                <a href="{full_dashboard_url}" class="button">View Full Dashboard</a>
                <a href="https://my.wyze.com/live" class="button" style="background: #607D8B; margin-left: 10px;">📹 View Camera</a>
            </div>
"""

    # Add quick action buttons if secret tokens are configured
    action_buttons = []
    if SECRET_OVERRIDE_ON_TOKEN:
        action_buttons.append(f'<a href="{dashboard_url}control/{SECRET_OVERRIDE_ON_TOKEN}" class="button" style="background: #2196F3;">Override ON</a>')
    if SECRET_OVERRIDE_OFF_TOKEN:
        action_buttons.append(f'<a href="{dashboard_url}control/{SECRET_OVERRIDE_OFF_TOKEN}" class="button" style="background: #ff9800;">Override OFF</a>')
    if SECRET_BYPASS_ON_TOKEN:
        action_buttons.append(f'<a href="{dashboard_url}control/{SECRET_BYPASS_ON_TOKEN}" class="button" style="background: #2196F3;">Bypass ON</a>')
    if SECRET_BYPASS_OFF_TOKEN:
        action_buttons.append(f'<a href="{dashboard_url}control/{SECRET_BYPASS_OFF_TOKEN}" class="button" style="background: #ff9800;">Bypass OFF</a>')
    if SECRET_PURGE_TOKEN:
        action_buttons.append(f'<a href="{dashboard_url}control/{SECRET_PURGE_TOKEN}" class="button" style="background: #9C27B0;">Purge Now</a>')

    if action_buttons and dashboard_url:
        html += f"""
            <div style="text-align: center; margin: 20px 0;">
                <p style="color: #888; font-size: 13px; margin-bottom: 10px;">Quick Actions:</p>
                <div style="display: flex; flex-wrap: wrap; gap: 10px; justify-content: center;">
                    {' '.join(action_buttons)}
                </div>
            </div>
"""

    # Use friendly name in footer
    footer_text = f"{EMAIL_FRIENDLY_NAME} Monitoring System" if EMAIL_FRIENDLY_NAME else "Pumphouse Monitoring System"

    html += f"""
            <div class="timestamp">
                Sent: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            </div>
        </div>

        <div class="footer">
            {footer_text}
        </div>
    </div>
</body>
</html>"""

    return html


def test_email(debug=True):
    """Send a test email to verify configuration"""
    # Get current tank gallons for the subject
    status_data = fetch_system_status(debug=debug)
    current_gal = 0
    if status_data and status_data.get('tank') and status_data['tank'].get('gallons'):
        current_gal = status_data['tank']['gallons']

    return send_email_notification(
        subject=f"🏠 Pumphouse Email Test - {current_gal:.0f} gal",
        message="Email notification system is configured and working!",
        priority='default',
        dashboard_url=DASHBOARD_URL,
        chart_url=f"{DASHBOARD_URL}api/chart.png?hours=24",
        debug=debug
    )


if __name__ == "__main__":
    # Test the email system
    print("Testing email notification system...")
    if test_email(debug=True):
        print("\n✓ Test email sent successfully!")
    else:
        print("\n✗ Failed to send test email. Check configuration.")
        sys.exit(1)
