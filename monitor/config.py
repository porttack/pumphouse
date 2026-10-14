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
# Calibrated 2026-07-15 after PTdevices depth-meter service + tank re-calibration.
# Tank is a vertical cylinder, 7.2 ft internal diameter (43.2" radius).
#   gal/inch = π·(43.2")² / 231 in³/gal ≈ 25.37
# Overflow occurs at 60" = 1522 US gal; that pair anchors the linear conversion.
TANK_HEIGHT_INCHES = 60
TANK_CAPACITY_GALLONS = 1522  # True physical capacity at 60" (overflow). Used for accurate gallons.
# Operational "full" — the level we normally stop at (override shutoff). Treated as
# 100% for all displays/graphs/percentages. Real capacity (1522) reads as ~105%.
TANK_FULL_GALLONS = 1425
TANK_URL = ""  # Loaded from secrets file - your mypt.in tank monitoring URL

# Dosing (chlorine) Tank Configuration
# ─────────────────────────────────────────────────────────────────────────
# This is the small chemical feed tank the dosatron draws from — separate
# from the 1522-gal main water tank above. Estimated depth is derived from
# dosatron click counts, not a physical sensor, so treat it as a rough gauge.
#
# Geometry, assumed cylindrical (measured 2026-07-25, diameter is an estimate —
# recalibrate if a more precise measurement is taken):
#   diameter 15" → gal/inch = π·(7.5")² / 231 in³/gal ≈ 0.765
DOSING_TANK_DIAMETER_INCHES = 15
DOSING_TANK_GAL_PER_INCH    = 0.765
DOSING_TANK_CAPACITY_GALLONS = 15  # rated capacity (fill line sits below the physical rim)

# Empirical fudge factor: chemical gallons drawn from the dosing tank per
# dosatron click (within pressure-HIGH windows only, same rule as GALLONS_PER_CLICK).
# Derived from real-world calibration points, NOT the theoretical
# injection-ratio math (dial ~0.07% implies far less loss than measured) — the
# gap is believed to be air in the suction line causing the piston to draw air
# instead of liquid on some strokes, so clicks alone understate real
# consumption. Using the *measured* depth change instead of the dial ratio
# bakes that loss in automatically. Two intervals now, pooled (total gallons
# lost / total clicks) rather than either alone, per docs/dosing-tank-calibration.md:
#   6/16/26 23" -> 7/17/26 22"(refill anchor): 4" drop, 3.06 gal, 22,101 clicks
#   7/17/26 22" -> 8/25/26 16.5" (maintenance): 5.5" drop, 4.2075 gal, 65,577 clicks
#   pooled: 7.2675 gal / 87,678 clicks ≈ 0.00008289 gal/click
#   8/25/26 21.5" -> 10/10/26 17" (owner on site): 4.5" drop, 3.4425 gal, 53,879 clicks
#     ≈ 0.0000639 gal/click — matches interval 2 almost exactly, so interval 1 was
#     dropped from the pool (2026-10-10): intervals 2+3 = 7.65 gal / 119,456 clicks
# (interval 2 alone would be ≈0.0000642 gal/click — notably lower than interval
# 1, consistent with the estimate having overstated consumption recently; see docs)
# Revisit once the tank is physically re-measured against a fresh click count —
# see docs/dosing-tank-calibration.md for the full derivation and how to redo it.
DOSING_TANK_CHEM_GAL_PER_CLICK = 0.0000640

# Extra safety margin applied on top of the fudge factor above when estimating
# *current* depth (not applied when back-computing the fudge factor itself).
# Purely a hedge — inflates estimated consumption by 25% so the estimate errs
# toward "less left than you think," since a single calibration point plus a
# known air-in-line issue means the true rate could still be higher than measured.
DOSING_TANK_CONSERVATIVE_MARGIN = 1.25

# Anchor point: the last known *physical* depth reading. Estimates going
# forward are last-known-depth minus (clicks-since-anchor × fudge factor).
# Update both values whenever someone physically checks the tank — this is
# what keeps the estimate from drifting indefinitely on a single stale point.
#
# UPDATED 2026-08-25: maintenance visit measured 16.5" (was 22" on 7/17,
# before this refill-corrected reading). That 16.5" reading became the second
# calibration point used to repool DOSING_TANK_CHEM_GAL_PER_CLICK above.
#
# REFILLED same visit, 2026-08-25: maintenance added chlorine, bringing the
# tank to 21.5" — not a consumption interval, so NOT folded into the fudge
# factor (same treatment as the 7/17 top-off). Anchor reset to this reading.
#
# UPDATED 2026-10-10: owner read 17" on site (estimate said 14.2"). Re-anchored.
#
# REFILLED 2026-10-12: owner added 3.5 gal (~4.6") of the aged ~9% bleach, bringing
# the tank to 22". Not a consumption interval, so not folded into the fudge factor.
# Time of day wasn't logged; 12:00 used.
DOSING_TANK_ANCHOR_DEPTH_IN = 22.0
DOSING_TANK_ANCHOR_TS       = "2026-10-12T12:00:00"

# Ambient Weather Configuration
ENABLE_AMBIENT_WEATHER = True  # Enable Ambient Weather integration
AMBIENT_WEATHER_POLL_INTERVAL = 300  # Seconds between weather checks (5 minutes, respects 1 req/sec API limit)
AMBIENT_WEATHER_API_KEY = ""  # User API key - loaded from secrets file
AMBIENT_WEATHER_APPLICATION_KEY = ""  # Application key - loaded from secrets file
AMBIENT_WEATHER_MAC_ADDRESS = "48:3F:DA:56:12:1E"  # Device MAC address
AMBIENT_WEATHER_DASHBOARD_URL = "https://ambientweather.net/dashboard/ccccbdcb524d5a3735628aec0c03247f"  # Public dashboard link

# National Weather Service forecast URL for the property location
NATIONAL_WEATHER_URL = "https://forecast.weather.gov/MapClick.php?lat=44.64196&lon=-124.04110"

# Water Volume Estimation Constants
RESIDUAL_PRESSURE_SECONDS = 30  # Last N seconds are residual pressure (not pumping)
SECONDS_PER_GALLON = 10 / 0.14   # 10 seconds = 0.14 gallons

# Chlorine ppm estimate while bypassing the dosatron/filter (untreated well water
# feeds the tank directly, tank stays roughly constant-volume via the float valve
# + continuous house draw). Modeled as well-mixed dilution:
#   ppm = CHLORINE_STEADY_STATE_PPM * exp(-bypass_gallons_delivered / tank_gallons)
# bypass_gallons_delivered is real FFT-detected bypass flow time (NOT elapsed
# relay-on/wall-clock time — see monitor/dosatron.py::sum_flow_seconds) × BYPASS_GPM.
#
# UNVALIDATED as of 2026-07-29 — owner pushback: incoming well water isn't
# necessarily "0 ppm inert diluent," it likely carries its own chlorine demand
# (the dosatron's theoretical dose implies ~100ppm but steady-state measures
# only 10ppm, meaning most injected chlorine is being consumed reacting with
# something in the raw water, not just diluted into tank volume) — so real
# decline during bypass could be faster than this pure-dilution curve predicts.
# Owner also reports ppm holds ~10 even after sitting closed/unbypassed for
# 1-2 weeks, which doesn't obviously fit either model, though test strips are
# only accurate to roughly ±5-10ppm so this is hard to pin down either way.
# Owner's call (2026-07-29): leave this formula as a rough placeholder rather
# than have Claude guess-correct it further — same pattern as the dosing tank
# estimate below. Recalibrate once there's a real data point: a controlled
# bypass run of known gallons with a trustworthy before/after ppm reading
# (titration test kit ideally, not strips, given the ±5-10ppm noise floor).
#
# Partial recalibration 2026-10-04: maintenance reported the tank water smelling
# off at 2026-09-28 ~12:00, right when a 4-day stretch of intermittent bypass use
# (9/24-9/28) was ending. Replaying the model from the CHLORINE_ANCHOR_PPM anchor
# with a 3x multiplier on the bypass (untreated-water) term lands at ~4.1 ppm at
# that exact timestamp — a close match for a "stinky" read, and not something a
# bare guess would hit on the nose. Still not a titration reading, so treat 3x as
# a working estimate, not a settled constant. Applies only to bypass water, not
# dosatron-treated water (CHLORINE_STEADY_STATE_PPM recovery is unaffected).
CHLORINE_BYPASS_DEMAND_MULTIPLIER = 3.0
CHLORINE_STEADY_STATE_PPM = 8.0  # dosatron steady-state ppm; was 10.0 (strip-measured, stable as of 2026-07). Lowered 2026-10-13: same aging bleach feeds the dosing tank (~9% effective vs 12% label), so ~10 x 9/10.5 rounds to 8
BYPASS_GPM = 4.0  # owner's rough field estimate of bypass delivery rate (2026-07-29); NOT derived from SECONDS_PER_GALLON above (that constant is known-wrong for this purpose, see docs/tank-calibration.md)
# Fallback only — monitor/bypass_calibration.py derives a real BYPASS_GPM from actual
# tank readings during zero-usage bypass sessions once enough clean data has
# accumulated (see BYPASS_GPM_MIN_CALIBRATION_MINUTES below); this constant is what's
# used until then, and if the calibration file is ever missing/corrupt.
BYPASS_GPM_MIN_CALIBRATION_MINUTES = 60  # min accumulated real flow before trusting the calibrated value over BYPASS_GPM above

# ── Chlorine shock-dose calculator (added 2026-08-22) ─────────────────────────
# How much 12% sodium hypochlorite ("liquid chlorine"/pool chlorine) to pour into
# the main tank to bring the estimated residual up to a target. Surfaced on
# /water2, in the daily status email, and in daily.csv so a maintenance person can
# be given a single number over the phone.
#
# CHLORINE_DOSE_PERCENT is read as TRADE percent — grams of available chlorine
# (as Cl2) per 100 mL of product, which is how liquid chlorine is normally sold.
# 12% trade = 120 g/L. If a jug is actually labeled 12% *by weight* it is slightly
# stronger (~136 g/L), so the trade reading errs toward a marginally larger pour —
# deliberate, since an aged jug loses strength and owner prefers overshoot (the
# house carbon filter strips the excess, and getting maintenance on site is hard).
CHLORINE_DOSE_PERCENT = 9.0         # effective strength of the hypochlorite on hand (12% label, ~1 yr old as of 2026-10-13; 11am 10/12 tank reading ran well below the 12% prediction)
CHLORINE_DOSE_TARGET_PPM = 15.0     # primary target — deliberately above steady state
CHLORINE_DOSE_TARGET_PPM_ALT = 10.0 # secondary target, for comparison

# Anchor for replaying the ppm estimate backwards over historical days (needed for
# the per-day column in daily.csv — the live model only persists a single current
# value). Set to the last manually-verified/reconstructed ppm and its timestamp.
# NOTE: 4 tank turnovers wash out ~98% of any error here, so days more than ~10
# days after the anchor barely depend on it (see docs/chlorine-estimate.md).
CHLORINE_ANCHOR_PPM = 9.67
CHLORINE_ANCHOR_TS = '2026-08-01T00:00:00'

# Purge Configuration
ENABLE_PURGE = False  # Enable automatic filter purging after every water delivery
MIN_PURGE_INTERVAL = 3600  # Minimum seconds between purges (default: 3600 = 1 hour)
PURGE_DURATION = 10  # Duration of purge in seconds
ENABLE_DAILY_PURGE = False   # Purge once per day: 15s after first pressure-HIGH after 3am
DAILY_PURGE_HOUR   = 3      # Hour (0-23) after which the daily purge becomes eligible

# Override Shutoff Configuration
ENABLE_OVERRIDE_SHUTOFF = True  # Enable automatic override shutoff to prevent tank overflow
OVERRIDE_SHUTOFF_THRESHOLD = 1450  # Gallons at which to turn off override valve (tank sensor); 60"/1522=overflow

# Override Dosatron Safety Shutoff — catches high-flow events the tank sensor can't respond to fast enough
OVERRIDE_DOSATRON_SHUTOFF_ENABLED = True   # Shut off override when estimated fill reaches threshold
OVERRIDE_DOSATRON_SHUTOFF_GALLONS = 1450   # Est. gallons to trigger shutoff (stop before a real reading arrives)
OVERRIDE_DOSATRON_RUNNING_ESTIMATE = True  # Use running estimate: last real reading + dosatron since (resets each tank fetch)

# Override Auto-On Configuration
OVERRIDE_ON_THRESHOLD = 1390  # Gallons at which to turn on override valve (None = disabled, e.g., 1350)

# Notification Configuration
ENABLE_NOTIFICATIONS = True  # Master switch (default OFF for safety)
NTFY_SERVER = "https://ntfy.sh"  # Can change to self-hosted later
NTFY_TOPIC = ""  # Loaded from secrets file - set unique topic per installation!
PUMPHOUSE_HOST = ""  # DDNS hostname - loaded from secrets file
PUMPHOUSE_PORT = 6443  # Web server port - loaded from secrets file
DASHBOARD_URL = ""  # Computed after secrets load: https://{PUMPHOUSE_HOST}:{PUMPHOUSE_PORT}/
DASHBOARD_EMAIL_URL = None  # Custom URL for emails (default: None uses DASHBOARD_URL with ?hours=DAILY_STATUS_EMAIL_CHART_HOURS)
DASHBOARD_DEFAULT_HOURS = 72  # Default time range for web dashboard (hours)

# Email Notification Configuration
# See EMAIL_SETUP.md and secrets.conf.template for detailed configuration instructions
ENABLE_EMAIL_NOTIFICATIONS = True  # Enable email alerts
EMAIL_TO = ""   # Recipient email address - loaded from secrets file
EMAIL_FROM = ""  # Sender email address - loaded from secrets file
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

# Timelapse Email Configuration
ENABLE_TIMELAPSE_EMAIL = False   # Send email after each day's sunset timelapse is assembled
TIMELAPSE_EMAIL_LINK = "https://onblackberryhill.com/timelapse"  # Link target for the inline snapshot image

# Notification Rules - Which events trigger notifications
NOTIFY_TANK_DECREASING = [1000, 700, 500, 250]  # Alert when tank crosses these levels going DOWN
NOTIFY_TANK_INCREASING = [500, 750, 1150]  # Alert when tank crosses these levels going UP
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
NOTIFY_FULL_FLOW_ENABLED = False  # Enable full-flow detection and notifications
NOTIFY_FULL_FLOW_PRESSURE_THRESHOLD = 90.0  # Pressure % to count as full-flow (default: 90%)
NOTIFY_FULL_FLOW_DELAY_MINUTES = 30  # Minutes after full-flow starts before notifying (default: 30)
NOTIFY_FULL_FLOW_LOOKBACK_HOURS = 24  # How far back to check for full-flow periods (default: 24)

# High Pressure Detection (immediate alert when pressure goes high)
NOTIFY_HIGH_PRESSURE_ENABLED = False  # Enable immediate high pressure alerts
NOTIFY_HIGH_PRESSURE_USE_EMAIL = True  # Send email alerts (default: False, use ntfy only)
NOTIFY_PRESSURE_LOW_ENABLED = False  # Send alert when pressure goes LOW with duration info
NOTIFY_PRESSURE_LOW_REQUIRES_FLOAT_CALLING = True  # Only alert if float was calling when pressure went HIGH

# Vehicle Detection (unoccupied property only)
NOTIFY_VEHICLE_DETECTED = True   # Alert when vehicle count changes while property is unoccupied

# Tank Data Outage Detection
NOTIFY_TANK_OUTAGE_ENABLED = False  # Enable tank data outage detection and notification
NOTIFY_TANK_OUTAGE_THRESHOLD_MINUTES = 60  # Minimum outage duration (in minutes) to trigger notification

# Notification Cooldowns (prevent spam)
MIN_NOTIFICATION_INTERVAL = 300  # Minimum 5 minutes between same notification type

## Logging Configuration
MAX_PRESSURE_LOG_INTERVAL = 1800  # Log at least every 30 minutes when pressure is high

# Web Dashboard Configuration
# List of event types to EXCLUDE from the Recent Events table on the web dashboard
# Common types: TANK_LEVEL, PRESSURE_HIGH, PRESSURE_LOW, INIT, SHUTDOWN, FLOAT_CALLING, FLOAT_FULL
DASHBOARD_HIDE_EVENT_TYPES = ['PRESSURE_HIGH', 'SHUTDOWN', 'FULL_FLOW', 'NOTIFY_FULL_FLOW']
NOTIFY_TANK_LEVEL_MIN_INCREASE = 15  # Notify when a single TANK_LEVEL event shows this many gallons gained (None to disable)
DASHBOARD_MAX_EVENTS = 200  # Maximum number of events to show in dashboard and emails (~3 days)
DASHBOARD_SNAPSHOT_COUNT    = 400  # Raw rows to read (~4 days); older ones aggregated for display
DASHBOARD_SNAPSHOT_RAW      = 5    # Number of recent 15-min rows shown individually
DASHBOARD_SNAPSHOT_AGG_HOURS = 2   # Bucket size (hours) for older rows; use even divisors of 24 (1,2,3,4,6,8,12)

# Reservation/Rental Income Configuration
MANAGEMENT_FEE_PERCENT = 36  # Percentage paid to management company (subtracted from displayed income)
CANCELLATION_STILL_PAID_DAYS = 14  # Cancellations within this many days of check-in are still owed income (owner's cancellation-policy cutoff)

# E-Paper Display Configuration
EPAPER_CONSERVE_WATER_THRESHOLD = 50  # Show "Save Water" when tank percent <= this value (None to disable)
EPAPER_OWNER_STAY_TYPES = ['Owner Stay', 'Owner Stay, Full Clean']  # Reservation Type values that count as owner occupancy
EPAPER_DEFAULT_HOURS_TENANT = 24  # Default graph hours when occupied by a tenant
EPAPER_DEFAULT_HOURS_OTHER = 72  # Default graph hours for owner or unoccupied
EPAPER_LOW_WATER_HOURS_THRESHOLD = 75  # Tank % at or below which switches to extended graph (None to disable)
EPAPER_LOW_WATER_HOURS = 168  # Graph hours when tank is below threshold (168 = 7 days)
EPAPER_FORECAST_DAYS = 7      # Days of forecast icons to show (0 = disabled)
EPAPER_MIN_GRAPH_RANGE_PCT = 12  # Minimum Y-axis span as % of graph-full (prevents compressed graphs)
EPAPER_MAX_GRAPH_PCT = 106       # Hard cap for Y-axis max as % of graph-full; >105% so the real 1522 peak still fits above 100%

# Data directory (XDG Base Directory: ~/.local/share/pumphouse)
DATA_DIR = Path.home() / '.local' / 'share' / 'pumphouse'
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Canonical data file paths
EVENTS_FILE                = DATA_DIR / 'events.csv'
SNAPSHOTS_FILE             = DATA_DIR / 'snapshots.csv'
DAILY_CSV                  = DATA_DIR / 'daily.csv'
RESERVATIONS_FILE          = DATA_DIR / 'reservations.csv'
RESERVATIONS_SNAPSHOT_FILE = DATA_DIR / 'reservations_snapshot.csv'
CANCELED_RESERVATIONS_FILE = DATA_DIR / 'canceled_reservations.csv'
WORK_ORDERS_FILE           = DATA_DIR / 'work_orders.csv'
STATEMENTS_FILE            = DATA_DIR / 'statements.csv'
STATEMENT_RESERVATIONS_FILE = DATA_DIR / 'statement_reservations.csv'

# Default file paths (used by main.py CLI args and legacy callers)
DEFAULT_LOG_FILE       = 'pressure_log.txt'
DEFAULT_EVENTS_FILE    = str(EVENTS_FILE)
DEFAULT_SNAPSHOTS_FILE = str(SNAPSHOTS_FILE)

# Config file path (optional)
CONFIG_FILE = Path.home() / '.config' / 'pumphouse' / 'monitor.conf'

# Temporary watch flag: when this file exists, pressure-LOW ntfy alerts are enabled
# regardless of NOTIFY_PRESSURE_LOW_ENABLED. Toggle via the dashboard.
PRESSURE_LOW_WATCH_FILE  = Path.home() / '.config' / 'pumphouse' / 'watch_pressure_low'

# Same pattern for bypass-mode flow-cycle completion alerts.
BYPASS_FLOW_WATCH_FILE   = Path.home() / '.config' / 'pumphouse' / 'watch_bypass_flow'

# Manual override-off flag: when this file exists, the auto-on logic will NOT
# re-enable override automatically. Set when user manually turns override OFF via
# the dashboard. Cleared when override is manually turned ON or auto-shutoff fires.
OVERRIDE_MANUAL_OFF_FILE = Path.home() / '.config' / 'pumphouse' / 'override_manual_off'

# One-shot "fill to a higher target" override: when this file exists it contains
# the target gallons (e.g. 1500). While set, the override shutoff (sensor AND
# dosatron-estimate) uses this target instead of the normal thresholds, and the
# file is cleared when the target is reached. Exceptional/manual use only.
OVERRIDE_FILL_TARGET_FILE = Path.home() / '.config' / 'pumphouse' / 'override_fill_target'
OVERRIDE_FILL_EXTRA_GALLONS = 1500  # target for the dashboard "Fill to 1500" button

# Timed bypass: when this file exists, it contains an epoch-timestamp float
# indicating when bypass should automatically be turned off.
BYPASS_TIMER_FILE  = Path.home() / '.config' / 'pumphouse' / 'bypass_timer'
PURGE_PENDING_FILE = Path.home() / '.config' / 'pumphouse' / 'purge_pending'

# Cycle bypass: alternates bypass ON/OFF on a repeating schedule.
# When this file exists (JSON), it stores the active cycle state.
BYPASS_CYCLE_FILE     = Path.home() / '.config' / 'pumphouse' / 'bypass_cycle.json'
BYPASS_CYCLE_ON_HOURS  = 4   # Default ON duration  (hours)
BYPASS_CYCLE_OFF_HOURS = 2   # Default OFF duration (hours)

# "Bypass until a car arrives": holds bypass ON (ignoring the timer/cycle
# watchdog, which would otherwise end it on its own schedule) so an unattended
# flow-rate experiment survives until someone actually shows up on site. When
# this file exists (JSON: {baseline, started_ts}), the watchdog compares the
# live Ring vehicle count against `baseline` (the count at activation time, not
# always 0 — the owner's own car may already be in frame) and turns bypass OFF
# the moment it rises above that. BYPASS_UNTIL_VEHICLE_MAX_HOURS is a safety net
# independent of vehicle detection — added 2026-10-04 because this mode can
# leave the tank on fully untreated water for as long as no one arrives, and a
# dead camera/model should not silently extend that indefinitely.
BYPASS_UNTIL_VEHICLE_FILE      = Path.home() / '.config' / 'pumphouse' / 'bypass_until_vehicle.json'
BYPASS_UNTIL_VEHICLE_MAX_HOURS = 48

# Dosatron running estimate: written by the monitor every poll cycle, read by the web server
# for the current-level display and as the live right-edge graph point.
DOS_ESTIMATE_FILE = Path.home() / '.local/share/pumphouse/dos_estimate.json'

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

# Local camera credentials (for /sunset snapshot proxy)
CAMERA_USER = ''
CAMERA_PASS = ''

# Ring Camera Configuration
RING_CAMERA_NAME = ''  # Name of Ring device to use (e.g. "Front Door"); first device if blank
RING_TOKEN_FILE = Path.home() / '.config' / 'pumphouse' / 'ring_token.json'
RING_CACHE_FILE = Path.home() / '.config' / 'pumphouse' / 'ring_snapshot_cache.jpg'
RING_CACHE_MINUTES = 10  # How long to cache the Ring snapshot (shared across all processes)
YOLO_CONF_THRESHOLD = 0.20             # YOLO confidence threshold for counting a vehicle
YOLO_MODEL = 'yolov8s'                 # Model name: 'yolov8n' (faster) or 'yolov8s' (more accurate)
YOLO_CLAHE_ENABLED = True              # Apply CLAHE preprocessing to improve glare/low-light detection
YOLO_CLAHE_CLIP_LIMIT = 2.0           # CLAHE clip limit (higher = more contrast enhancement)
YOLO_CLAHE_TILE_SIZE = 8              # CLAHE tile grid size (smaller = more local)
# Driveway zone: only count YOLO detections whose center falls inside this region.
# Fractions of image width/height: (x1, y1, x2, y2). Set to (0,0,1,1) to use full frame.
YOLO_DRIVEWAY_ZONE = (0.30, 0.20, 1.0, 0.90)

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
                    if key == 'EMAIL_TO' and not EMAIL_TO:
                        EMAIL_TO = value
                    elif key == 'EMAIL_FROM' and not EMAIL_FROM:
                        EMAIL_FROM = value
                    elif key == 'EMAIL_SMTP_SERVER' and not EMAIL_SMTP_SERVER:
                        EMAIL_SMTP_SERVER = value
                    elif key == 'EMAIL_SMTP_PORT':
                        EMAIL_SMTP_PORT = int(value)
                    elif key == 'EMAIL_SMTP_USER' and not EMAIL_SMTP_USER:
                        EMAIL_SMTP_USER = value
                    elif key == 'EMAIL_SMTP_PASSWORD' and not EMAIL_SMTP_PASSWORD:
                        EMAIL_SMTP_PASSWORD = value
                    elif key == 'AMBIENT_WEATHER_API_KEY' and not AMBIENT_WEATHER_API_KEY:
                        AMBIENT_WEATHER_API_KEY = value
                    elif key == 'AMBIENT_WEATHER_APPLICATION_KEY' and not AMBIENT_WEATHER_APPLICATION_KEY:
                        AMBIENT_WEATHER_APPLICATION_KEY = value
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
                    elif key == 'RING_CAMERA_NAME' and not RING_CAMERA_NAME:
                        RING_CAMERA_NAME = value
                    elif key == 'CAMERA_USER' and not CAMERA_USER:
                        CAMERA_USER = value
                    elif key == 'CAMERA_PASS' and not CAMERA_PASS:
                        CAMERA_PASS = value
                    elif key == 'PUMPHOUSE_HOST' and not PUMPHOUSE_HOST:
                        PUMPHOUSE_HOST = value
                    elif key == 'PUMPHOUSE_PORT':
                        PUMPHOUSE_PORT = int(value)
                    elif key == 'NTFY_TOPIC' and not NTFY_TOPIC:
                        NTFY_TOPIC = value
                    elif key == 'TANK_URL' and not TANK_URL:
                        TANK_URL = value
    except Exception as e:
        print(f"Warning: Could not load secrets file {SECRETS_FILE}: {e}")

# Compute DASHBOARD_URL from host/port (after secrets load)
if PUMPHOUSE_HOST and not DASHBOARD_URL:
    DASHBOARD_URL = f"https://{PUMPHOUSE_HOST}:{PUMPHOUSE_PORT}/"

# Derived control tokens — variants of the base bypass/purge secrets so no
# secrets.conf changes are needed for the extra remote-control actions.
SECRET_BYPASS_TIMED_TOKEN        = (SECRET_BYPASS_ON_TOKEN + '-4h')          if SECRET_BYPASS_ON_TOKEN else ''
SECRET_BYPASS_CANCEL_TIMER_TOKEN = (SECRET_BYPASS_ON_TOKEN + '-cancel')      if SECRET_BYPASS_ON_TOKEN else ''
SECRET_BYPASS_CYCLE_TOKEN        = (SECRET_BYPASS_ON_TOKEN + '-cycle')       if SECRET_BYPASS_ON_TOKEN else ''
SECRET_BYPASS_UNTIL_VEHICLE_TOKEN = (SECRET_BYPASS_ON_TOKEN + '-until-car')  if SECRET_BYPASS_ON_TOKEN else ''
SECRET_TEST_FILTER_TOKEN         = (SECRET_BYPASS_ON_TOKEN + '-test-filter') if SECRET_BYPASS_ON_TOKEN else ''
SECRET_PURGE_NEXT_TOKEN          = (SECRET_PURGE_TOKEN + '-next')            if SECRET_PURGE_TOKEN else ''
SECRET_PURGE_CANCEL_NEXT_TOKEN   = (SECRET_PURGE_TOKEN + '-cancel')          if SECRET_PURGE_TOKEN else ''
SECRET_FILL_EXTRA_TOKEN          = (SECRET_OVERRIDE_ON_TOKEN + '-fill1500')  if SECRET_OVERRIDE_ON_TOKEN else ''
