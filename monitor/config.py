"""
Configuration constants for the monitoring system
"""
import os
from pathlib import Path

# GPIO Pin Configuration
PRESSURE_PIN = 17
FLOAT_PIN = 21

# Polling Intervals
POLL_INTERVAL = 10  # Seconds between pressure sensor readings
TANK_POLL_INTERVAL = 300  # Seconds between tank level checks (5 minutes)
SNAPSHOT_INTERVAL = 15  # Minutes between snapshots: 15 (production), 5 or 2 (debug)
MAX_TANK_FETCH_FAILURES = 5  # Consecutive failures before safety shutoff (5 × 5min = 25min tolerance)

# Tank Configuration
TANK_HEIGHT_INCHES = 58
TANK_CAPACITY_GALLONS = 1400
TANK_URL = "https://www.mypt.in/s/oyd95OEj/qbbBE9Loxo"

# Water Volume Estimation Constants
RESIDUAL_PRESSURE_SECONDS = 30  # Last N seconds are residual pressure (not pumping)
SECONDS_PER_GALLON = 10 / 0.14   # 10 seconds = 0.14 gallons

# Purge Configuration
ENABLE_PURGE = False  # Enable automatic filter purging after water delivery
MIN_PURGE_INTERVAL = 3600  # Minimum seconds between purges (default: 3600 = 1 hour)
PURGE_DURATION = 10  # Duration of purge in seconds

# Override Shutoff Configuration
ENABLE_OVERRIDE_SHUTOFF = True  # Enable automatic override shutoff to prevent tank overflow
OVERRIDE_SHUTOFF_THRESHOLD = 1410  # Gallons at which to turn off override valve

# Override Auto-On Configuration
OVERRIDE_ON_THRESHOLD = 1350  # Gallons at which to turn on override valve (None = disabled, e.g., 1350)

# Notification Configuration
ENABLE_NOTIFICATIONS = True  # Master switch (default OFF for safety)
NTFY_SERVER = "https://ntfy.sh"  # Can change to self-hosted later
NTFY_TOPIC = "blackberry_pumphouse"  # User must set unique topic!
DASHBOARD_URL = "https://onblackberryhill2.tplinkdns.com:6443/"  # Dashboard URL to include in notifications
DASHBOARD_EMAIL_URL = None  # Custom URL for emails (default: None uses DASHBOARD_URL with ?hours=DAILY_STATUS_EMAIL_CHART_HOURS)
DASHBOARD_DEFAULT_HOURS = 72  # Default time range for web dashboard (hours)

# Email Notification Configuration
# See EMAIL_SETUP.md and secrets.conf.template for detailed configuration instructions
ENABLE_EMAIL_NOTIFICATIONS = True  # Enable email alerts
EMAIL_TO = "onblackberryhill+alert@gmail.com"  # Recipient email address
EMAIL_FROM = "onblackberryhill+pumphouse@gmail.com"  # Sender email address
EMAIL_FRIENDLY_NAME = "Pumphouse"  # Friendly name shown in email headers and footers
EMAIL_SMTP_SERVER = ""  # SMTP server - loaded from secrets file
EMAIL_SMTP_PORT = 587  # SMTP port - can be overridden in secrets file
EMAIL_SMTP_USER = ""  # SMTP username - loaded from secrets file
EMAIL_SMTP_PASSWORD = ""  # SMTP password - loaded from secrets file

# Daily Status Email Configuration
ENABLE_DAILY_STATUS_EMAIL = True  # Send a daily status email
DAILY_STATUS_EMAIL_TIME = "06:00"  # Time to send daily status email (HH:MM in 24-hour format)
DAILY_STATUS_EMAIL_CHART_HOURS = 72  # Hours of history to show in daily status chart (default: 72 = 3 days)

# Checkout Reminder Configuration
ENABLE_CHECKOUT_REMINDER = True  # Send checkout reminder to turn down thermostat
CHECKOUT_REMINDER_TIME = "11:00"  # Time to send checkout reminder (HH:MM in 24-hour format)

# Notification Rules - Which events trigger notifications
NOTIFY_TANK_DECREASING = [1300, 1150, 1000, 700, 500, 250]  # Alert when tank crosses these levels going DOWN
NOTIFY_TANK_INCREASING = [500, 750, 1000, 1200, 1300]  # Alert when tank crosses these levels going UP
NOTIFY_WELL_RECOVERY_THRESHOLD = 50  # Gallons gained to count as recovery
NOTIFY_WELL_RECOVERY_STAGNATION_HOURS = 6  # Hours of flat/declining before recovery
NOTIFY_WELL_RECOVERY_MAX_STAGNATION_GAIN = 30  # Max gallons gained during stagnation period (to filter slow fill)
NOTIFY_FLOAT_CONFIRMATIONS = 3  # Number of consecutive OPEN readings before alert
NOTIFY_WELL_DRY_DAYS = 4  # Days without refill before "well dry" alert
NOTIFY_OVERRIDE_SHUTOFF = True  # Alert on automatic override shutoff

# High Flow Detection (Fast Fill Mode)
NOTIFY_HIGH_FLOW_ENABLED = True  # Enable high flow alerts
NOTIFY_HIGH_FLOW_GPH = 60  # GPH threshold for fast fill detection
NOTIFY_HIGH_FLOW_WINDOW_HOURS = 6  # How far back to look
NOTIFY_HIGH_FLOW_AVERAGING = 2  # Average over N snapshots (1=no averaging)

# Backflush Detection
NOTIFY_BACKFLUSH_ENABLED = True  # Enable backflush detection
NOTIFY_BACKFLUSH_THRESHOLD = 50  # Gallons lost to trigger detection
NOTIFY_BACKFLUSH_WINDOW_SNAPSHOTS = 3  # Look back N snapshots (2=30min, 3=45min)
NOTIFY_BACKFLUSH_TIME_START = "00:00"  # Start of backflush window (HH:MM)
NOTIFY_BACKFLUSH_TIME_END = "04:30"  # End of backflush window (HH:MM)

# Full-Flow Detection (pressure_high_percent ~100%)
NOTIFY_FULL_FLOW_ENABLED = True  # Enable full-flow detection and notifications
NOTIFY_FULL_FLOW_PRESSURE_THRESHOLD = 90.0  # Pressure % to count as full-flow (default: 90%)
NOTIFY_FULL_FLOW_DELAY_MINUTES = 30  # Minutes after full-flow starts before notifying (default: 30)
NOTIFY_FULL_FLOW_LOOKBACK_HOURS = 24  # How far back to check for full-flow periods (default: 24)

# High Pressure Detection (immediate alert when pressure goes high)
NOTIFY_HIGH_PRESSURE_ENABLED = False  # Enable immediate high pressure alerts
NOTIFY_HIGH_PRESSURE_USE_EMAIL = True  # Send email alerts (default: False, use ntfy only)
NOTIFY_PRESSURE_LOW_ENABLED = False  # Send alert when pressure goes LOW with duration info

# Tank Data Outage Detection
NOTIFY_TANK_OUTAGE_ENABLED = True  # Enable tank data outage detection and notification
NOTIFY_TANK_OUTAGE_THRESHOLD_MINUTES = 60  # Minimum outage duration (in minutes) to trigger notification

# Notification Cooldowns (prevent spam)
MIN_NOTIFICATION_INTERVAL = 300  # Minimum 5 minutes between same notification type

## Logging Configuration
MAX_PRESSURE_LOG_INTERVAL = 1800  # Log at least every 30 minutes when pressure is high

# Web Dashboard Configuration
# List of event types to EXCLUDE from the Recent Events table on the web dashboard
# Common types: TANK_LEVEL, PRESSURE_HIGH, PRESSURE_LOW, INIT, SHUTDOWN, FLOAT_CALLING, FLOAT_FULL
DASHBOARD_HIDE_EVENT_TYPES = ['TANK_LEVEL', 'PRESSURE_LOW', 'PRESSURE_HIGH', 'SHUTDOWN']  # Hide noisy tank level change events
DASHBOARD_MAX_EVENTS = 500  # Maximum number of events to show in dashboard and emails (~7 days)

# Reservation/Rental Income Configuration
MANAGEMENT_FEE_PERCENT = 36  # Percentage paid to management company (subtracted from displayed income)

# Default file paths
DEFAULT_LOG_FILE = 'pressure_log.txt'
DEFAULT_EVENTS_FILE = 'events.csv'
DEFAULT_SNAPSHOTS_FILE = 'snapshots.csv'  # Now has a default!

# Config file path (optional)
CONFIG_FILE = Path.home() / '.config' / 'pumphouse' / 'monitor.conf'

def load_config_file():
    """
    Load configuration from file if it exists.
    Returns dict of config values or empty dict if file doesn't exist.
    """
    if not CONFIG_FILE.exists():
        return {}

    config = {}
    try:
        with open(CONFIG_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()

                    # Convert to appropriate type
                    if value.lower() in ('true', 'false'):
                        config[key] = value.lower() == 'true'
                    elif value.isdigit():
                        config[key] = int(value)
                    else:
                        try:
                            config[key] = float(value)
                        except ValueError:
                            config[key] = value

        return config
    except Exception as e:
        print(f"Warning: Could not load config file {CONFIG_FILE}: {e}")
        return {}

# Secret URL tokens for remote control (loaded from secrets file)
SECRET_OVERRIDE_ON_TOKEN = ''
SECRET_OVERRIDE_OFF_TOKEN = ''
SECRET_BYPASS_ON_TOKEN = ''
SECRET_BYPASS_OFF_TOKEN = ''
SECRET_PURGE_TOKEN = ''
SECRET_TOTALS_TOKEN = ''  # Token to show income totals on dashboard

# Load secrets from secrets file
SECRETS_FILE = Path.home() / '.config' / 'pumphouse' / 'secrets.conf'
if SECRETS_FILE.exists():
    try:
        with open(SECRETS_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()

                        # Load secrets into module-level variables
                    if key == 'EMAIL_SMTP_SERVER' and not EMAIL_SMTP_SERVER:
                        EMAIL_SMTP_SERVER = value
                    elif key == 'EMAIL_SMTP_PORT':
                        EMAIL_SMTP_PORT = int(value)
                    elif key == 'EMAIL_SMTP_USER' and not EMAIL_SMTP_USER:
                        EMAIL_SMTP_USER = value
                    elif key == 'EMAIL_SMTP_PASSWORD' and not EMAIL_SMTP_PASSWORD:
                        EMAIL_SMTP_PASSWORD = value
                    elif key == 'SECRET_OVERRIDE_ON_TOKEN':
                        SECRET_OVERRIDE_ON_TOKEN = value
                    elif key == 'SECRET_OVERRIDE_OFF_TOKEN':
                        SECRET_OVERRIDE_OFF_TOKEN = value
                    elif key == 'SECRET_BYPASS_ON_TOKEN':
                        SECRET_BYPASS_ON_TOKEN = value
                    elif key == 'SECRET_BYPASS_OFF_TOKEN':
                        SECRET_BYPASS_OFF_TOKEN = value
                    elif key == 'SECRET_PURGE_TOKEN':
                        SECRET_PURGE_TOKEN = value
                    elif key == 'SECRET_TOTALS_TOKEN':
                        SECRET_TOTALS_TOKEN = value
    except Exception as e:
        print(f"Warning: Could not load secrets file {SECRETS_FILE}: {e}")
