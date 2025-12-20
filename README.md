# Pumphouse Monitor

Simplified event-based monitoring system for remote water treatment facilities. Monitors pressure events, tracks water usage with periodic snapshots, and provides accurate tank data age tracking.

## Features

- **Simplified Event-Based Logging**: Clean event logging on all state changes (no artifacts, no grace periods)
- **Periodic Snapshots**: Aggregated data snapshots at exact clock boundaries (15, 5, or 2 minute intervals)
- **Accurate Tank Data Age**: Tracks actual PT website update time (not fetch time) showing data staleness
- **Smart Gallons Estimation**: Handles both completed pump cycles and in-progress cycles across snapshot boundaries
- **Pressure Monitoring**: Continuous monitoring of 10 PSI pressure switch with retry logic for coastal environment noise
- **Tank Level Monitoring**: Periodic scraping of PT sensor data with depth, percentage, and gallons
- **Float Sensor Integration**: Monitors tank float switch state (HIGH=CLOSED/CALLING, LOW=OPEN/FULL)
- **Relay Control**: Optional automatic spindown filter purging after water delivery (config file setting)
- **Safe Relay Control Script**: `control.sh` for manual relay operations without Python GPIO conflicts
- **Background Operation**: Runs reliably via nohup with SSH disconnect survival
- **Web Dashboard**: HTTPS web interface on port 6443 for real-time status and historical data
- **Email Notifications**: Rich HTML email alerts with full system status, charts, and sensor readings
- **Remote Control via Email**: One-click relay control buttons in email alerts using secret URLs
- **Push Notifications**: Real-time phone alerts via ntfy.sh for critical tank events

## Installation
```bash
# Clone repository
cd ~/pumphouse

# Activate virtual environment
source venv/bin/activate

# Install dependencies (if not already installed)
pip install -r requirements.txt
```

## Quick Start

### Running as systemd services (recommended)

Install and enable the services to run automatically on boot:

```bash
# Install services
./install-services.sh

# Enable and start both services
sudo systemctl enable --now pumphouse-monitor pumphouse-web

# Check status
sudo systemctl status pumphouse-monitor
sudo systemctl status pumphouse-web

# View live logs
sudo journalctl -u pumphouse-monitor -f
sudo journalctl -u pumphouse-web -f

# Stop/start/restart
sudo systemctl stop pumphouse-monitor
sudo systemctl start pumphouse-monitor
sudo systemctl restart pumphouse-web

# Restart both services (useful after code changes)
sudo systemctl restart pumphouse-monitor pumphouse-web
```

**After making code changes:**
1. No need to reinstall - services run from the project directory
2. Simply restart the affected service(s):
   ```bash
   sudo systemctl restart pumphouse-monitor pumphouse-web
   ```
3. Check logs to verify everything started correctly:
   ```bash
   sudo journalctl -u pumphouse-monitor -n 20
   sudo journalctl -u pumphouse-web -n 20
   ```

**If you modify service files** (`.service` files):
1. Copy updated files to systemd:
   ```bash
   sudo cp pumphouse-*.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl restart pumphouse-monitor pumphouse-web
   ```

### Running manually (for testing)

```bash
# Run with debug output (15-minute snapshots)
python -m monitor --debug

# Run with debug and 2-minute snapshots
python -m monitor --debug --snapshot-interval 2

# Run in background
nohup python -m monitor > output.txt 2>&1 &

# Check if running
ps aux | grep monitor

# View logs
tail -f events.csv
tail -f snapshots.csv

# Stop
pkill -f "python -m monitor"
```

## Usage
```
python -m monitor [OPTIONS]

Options:
  --events FILE           Events CSV file (default: events.csv)
  --snapshots FILE        Snapshots CSV file (default: snapshots.csv)
  --debug                 Enable console output
  --poll-interval N       Pressure sensor poll interval in seconds (default: 5)
  --tank-interval N       Tank check interval in minutes (default: 1)
  --snapshot-interval N   Snapshot interval: 15, 5, or 2 minutes (default: 15)
  --tank-url URL          Tank monitoring URL
  --version               Show version and exit
  -h, --help              Show help message
```

**Note:** Automatic purging is now configured in config.py, not via command line.

## Configuration File (Optional)

Create `~/.config/pumphouse/monitor.conf`:
```ini
# Pumphouse Monitor Configuration

# GPIO Pins
PRESSURE_PIN=17
FLOAT_PIN=21

# Purge Configuration
ENABLE_PURGE=False
MIN_PURGE_INTERVAL=3600
PURGE_DURATION=10

# Override Control Configuration
OVERRIDE_ON_THRESHOLD=1350  # Auto-on when tank drops below this (None=disabled)
ENABLE_OVERRIDE_SHUTOFF=True  # Auto-off overflow protection
OVERRIDE_SHUTOFF_THRESHOLD=1410  # Auto-off when tank reaches this

# Notification Configuration
ENABLE_NOTIFICATIONS=True
NTFY_TOPIC=pumphouse-yourname-randomnumber
NTFY_SERVER=https://ntfy.sh

# Polling intervals (seconds)
POLL_INTERVAL=5
TANK_POLL_INTERVAL=60

# Tank configuration
TANK_HEIGHT_INCHES=58
TANK_CAPACITY_GALLONS=1400
TANK_URL=https://www.mypt.in/s/REDACTED-TANK-URL

# Water estimation
RESIDUAL_PRESSURE_SECONDS=30
SECONDS_PER_GALLON=71.43

# Logging
MAX_PRESSURE_LOG_INTERVAL=1800
```

## Hardware Setup

### Pressure Switch
- **Type**: 10 PSI normally-closed (NC) switch
- **Connection**:
  - NC contact → GPIO17 (physical pin 11)
  - C (Common) contact → Ground (physical pin 9)
- **Operation**: NC contact opens when pressure ≥ 10 PSI

### Float Sensor
- **Type**: Normally-open float switch
- **Connection**:
  - Float switch → GPIO27 (physical pin 13)
  - Common → Ground
- **Operation**: 
  - HIGH (open) = Tank full (≥95%)
  - LOW (closed) = Tank calling for water (<80%)

## Output Files

### Events CSV (events.csv)

All state change events:
```csv
timestamp,event_type,pressure_state,float_state,tank_gallons,tank_depth,tank_percentage,estimated_gallons,relay_bypass,relay_supply_override,notes
2025-11-28 10:15:00.123,INIT,HIGH,CLOSED/CALLING,1185,49.09,84.6,,,OFF,OFF,System startup
2025-11-28 10:23:15.456,PRESSURE_LOW,HIGH,CLOSED/CALLING,1185,49.09,84.6,1.20,OFF,OFF,Duration: 330.3s
2025-11-28 10:28:45.789,TANK_LEVEL,LOW,CLOSED/CALLING,1203,49.82,85.9,,OFF,OFF,Changed by +17.6 gal
2025-11-28 10:45:20.234,SHUTDOWN,HIGH,CLOSED/CALLING,1208,50.06,86.3,,OFF,OFF,Clean shutdown
```

### Event Types

- **INIT**: System startup
- **PRESSURE_HIGH**: Pressure went ≥10 PSI (pump started)
- **PRESSURE_LOW**: Pressure went <10 PSI (pump stopped), includes estimated gallons
- **TANK_LEVEL**: Tank level changed (scraped from PT website)
- **FLOAT_CALLING**: Float sensor changed to CLOSED/CALLING (tank needs water)
- **FLOAT_FULL**: Float sensor changed to OPEN/FULL (tank full)
- **PURGE**: Automatic spindown filter purge triggered
- **OVERRIDE_AUTO_ON**: Automatic override valve turn-on when tank drops below threshold
- **OVERRIDE_SHUTOFF**: Automatic override valve shutoff due to tank overflow protection
- **REMOTE_CONTROL**: Remote relay control action via email secret URL
- **NOTIFY_TANK_***: Email notification sent for tank threshold crossing
- **NOTIFY_FLOAT_FULL**: Email notification sent for confirmed tank full
- **NOTIFY_OVERRIDE_OFF**: Email notification sent for override auto-shutoff
- **NOTIFY_WELL_RECOVERY**: Email notification sent for well recovery detection
- **NOTIFY_WELL_DRY**: Email notification sent for potential well dry condition
- **SHUTDOWN**: Clean shutdown

### Snapshots CSV (snapshots.csv)

Periodic aggregated summaries at exact clock boundaries:
```csv
timestamp,duration_seconds,tank_gallons,tank_gallons_delta,tank_data_age_seconds,float_state,float_ever_calling,float_always_full,pressure_high_seconds,pressure_high_percent,estimated_gallons_pumped,purge_count,relay_bypass,relay_supply_override
2025-11-28 10:15:00.000,900,1185,,450,CLOSED/CALLING,Yes,No,330,36.7,4.20,0,OFF,OFF
2025-11-28 10:30:00.000,900,1203,+18,120,CLOSED/CALLING,Yes,No,0,0.0,0.00,0,OFF,OFF
2025-11-28 10:45:00.000,900,1198,-5,300,CLOSED/CALLING,Yes,No,45,5.0,0.21,0,OFF,OFF
```

**Note:** `tank_gallons_delta` shows change since last snapshot with explicit sign (+18, -5, +0) for easy visual scanning.

## Logging Behavior

The system uses simplified event-based logging:

1. **Events**: Logs every state change immediately to events.csv
2. **Snapshots**: Aggregates data over interval (15/5/2 min), logs at exact clock boundaries
3. **Tank Data Age**: Tracks actual PT website "last updated" time, shows data staleness
4. **Gallons Estimation**: Uses completed pump cycles when available, falls back to accumulated HIGH time for in-progress cycles

## Water Volume Estimation

### Default Formula
- Last 30 seconds of pressure = residual (not pumping)
- Effective pumping time = duration - 30 seconds
- Gallons = effective time / 71.43 seconds per gallon
- Based on: 10 seconds = 0.14 gallons (2 clicks of Dosatron at 0.08 gal/click)

### Calibration

1. Record actual gallons from tank level change
2. Note `duration_seconds` from CSV
3. Calculate: `SECONDS_PER_GALLON = (duration - 30) / actual_gallons`
4. Update in config file or `monitor/config.py`

## Web Dashboard

View real-time status and historical data via HTTPS web interface:

### Initial Setup

```bash
# Generate self-signed SSL certificate (one-time)
./generate_cert.sh
```

### Running the Web Server

```bash
# Start web server (HTTPS on port 6443)
python -m monitor.web

# Run in background
nohup python -m monitor.web > web.log 2>&1 &

# Run without SSL (HTTP only)
python -m monitor.web --no-ssl

# Custom port
python -m monitor.web --port 8443

# Stop
pkill -f "python -m monitor.web"
```

### Access

- URL: `https://your-pi-ip:6443/`
- Default username: `admin`
- Default password: `pumphouse`

Your browser will warn about the self-signed certificate - click "Advanced" and proceed.

### Custom Credentials

Set environment variables before starting:

```bash
export PUMPHOUSE_USER=yourusername
export PUMPHOUSE_PASS=yourpassword
python -m monitor.web
```

### Features

- Live sensor readings (pressure, float, temperature, humidity)
- Tank level with visual progress bar
- Interactive time-series chart with selectable time ranges (6h, 12h, 24h, 3d, 7d, 14d)
- Aggregate statistics: tank level changes (1hr/24hr), pressure HIGH percentages, last 50+ gallon refill
- Recent snapshots (last 10)
- Recent events (last 20)
- Auto-refresh every 5 minutes
- Dark theme optimized for monitoring

**Note:** The web server runs independently from the monitor daemon. Run both processes to collect data and view it.

## Notifications

Get real-time alerts for important tank events via push notifications and email.

### Email Notifications

Receive rich HTML email alerts with full system status, tank charts, and sensor readings.

**Quick Setup:**

1. **Copy the secrets template:**
   ```bash
   cp secrets.conf.template ~/.config/pumphouse/secrets.conf
   chmod 600 ~/.config/pumphouse/secrets.conf
   ```

2. **For Gmail (recommended):**
   - Go to https://myaccount.google.com/apppasswords
   - Create an App Password for "Mail"
   - Copy the 16-character password

3. **Edit the secrets file:**
   ```bash
   nano ~/.config/pumphouse/secrets.conf
   ```
   Add your App Password:
   ```
   EMAIL_SMTP_PASSWORD=your-16-char-app-password
   ```

4. **Configure email settings in monitor/config.py:**
   ```python
   ENABLE_EMAIL_NOTIFICATIONS = True
   EMAIL_TO = "your-email@gmail.com"
   EMAIL_FROM = "your-email@gmail.com"
   EMAIL_SMTP_USER = "your-email@gmail.com"
   ```

5. **Test it:**
   ```bash
   python -m monitor.check --test-email
   ```

6. **Restart the monitor:**
   ```bash
   sudo systemctl restart pumphouse-monitor
   ```

**Email Features:**
- HTML emails styled like the web dashboard
- Tank level bar showing current percentage and gallons
- Current sensor readings (float switch, pressure)
- 1-hour and 24-hour tank change statistics
- Embedded tank level history chart
- Link to full web dashboard
- Priority-based formatting (green/orange/red)

See [EMAIL_SETUP.md](EMAIL_SETUP.md) for detailed setup instructions and troubleshooting.

### Remote Control via Email

Every email alert includes one-click action buttons for remote relay control - no command-line access needed!

**Quick Setup:**

1. **Generate secret tokens** (if not already done):
   ```bash
   # Generate 5 random tokens
   python3 -c "import secrets; print('SECRET_OVERRIDE_ON_TOKEN=' + secrets.token_urlsafe(32))"
   python3 -c "import secrets; print('SECRET_OVERRIDE_OFF_TOKEN=' + secrets.token_urlsafe(32))"
   python3 -c "import secrets; print('SECRET_BYPASS_ON_TOKEN=' + secrets.token_urlsafe(32))"
   python3 -c "import secrets; print('SECRET_BYPASS_OFF_TOKEN=' + secrets.token_urlsafe(32))"
   python3 -c "import secrets; print('SECRET_PURGE_TOKEN=' + secrets.token_urlsafe(32))"
   ```

2. **Add tokens to secrets file:**
   ```bash
   nano ~/.config/pumphouse/secrets.conf
   ```
   Add the generated tokens:
   ```
   SECRET_OVERRIDE_ON_TOKEN=your-random-token-here
   SECRET_OVERRIDE_OFF_TOKEN=your-random-token-here
   SECRET_BYPASS_ON_TOKEN=your-random-token-here
   SECRET_BYPASS_OFF_TOKEN=your-random-token-here
   SECRET_PURGE_TOKEN=your-random-token-here
   ```

3. **Restart services:**
   ```bash
   sudo systemctl restart pumphouse-monitor pumphouse-web
   ```

4. **Done!** Every email alert will now include color-coded action buttons:
   - **Override ON** (blue) - Turn on supply override valve
   - **Override OFF** (orange) - Turn off supply override valve
   - **Bypass ON** (blue) - Turn on bypass valve
   - **Bypass OFF** (orange) - Turn off bypass valve
   - **Purge Now** (purple) - Trigger one-time spindown filter purge

**How it works:**
- Secret URLs like `https://your-domain.com/control/<token>` provide unauthenticated access
- Each action has a unique 256-bit random token (keep these secret!)
- Clicking a button executes the action and logs it to events.csv
- Success page redirects to dashboard after 2 seconds
- All remote actions logged with `REMOTE_CONTROL` event type

**Security:**
- Tokens are stored in `~/.config/pumphouse/secrets.conf` (not in git)
- Each token is cryptographically random (43 characters)
- URLs are only known to you via email alerts
- Optional feature - works without tokens configured

### Push Notifications

Get real-time alerts on your phone for important tank events using ntfy.sh push notifications.

### Setup

1. **Install ntfy app on your phone:**
   - iOS: Download "ntfy" from the App Store
   - Android: Download "ntfy" from Google Play Store
   - Web: Visit https://ntfy.sh in your browser

2. **Choose a unique topic name:**
   - Pick something unique like `pumphouse-yourname-randomnumber`
   - Example: `pumphouse-smith-89234`
   - This prevents others from seeing your notifications

3. **Configure in monitor/config.py:**
   ```python
   ENABLE_NOTIFICATIONS = True  # Enable push notifications
   NTFY_TOPIC = "pumphouse-yourname-randomnumber"  # Your unique topic
   NTFY_SERVER = "https://ntfy.sh"  # Public server (or self-hosted)
   ```

4. **Customize notification rules (optional):**
   ```python
   # Tank level thresholds (gallons)
   NOTIFY_TANK_DECREASING = [1000, 750, 500, 250]  # Alert when dropping
   NOTIFY_TANK_INCREASING = [500, 750, 1000, 1200]  # Alert when filling

   # Well monitoring
   NOTIFY_WELL_RECOVERY_THRESHOLD = 50  # Gallons gained in 24hr
   NOTIFY_WELL_DRY_DAYS = 4  # Days without refill before alert

   # Float sensor confirmation
   NOTIFY_FLOAT_CONFIRMATIONS = 3  # Consecutive OPEN readings

   # Other alerts
   NOTIFY_OVERRIDE_SHUTOFF = True  # Alert on auto-shutoff

   # Spam prevention
   MIN_NOTIFICATION_INTERVAL = 300  # Minimum 5 min between same alerts
   ```

5. **Subscribe to your topic in the ntfy app:**
   - Open the ntfy app
   - Tap "+" to add a subscription
   - Enter your topic name (e.g., `pumphouse-smith-89234`)
   - Done! You'll now receive notifications

6. **Test the integration:**
   ```bash
   python -m monitor.check --test-notification
   ```
   You should receive a test notification on your phone!

7. **Restart the monitor to enable notifications:**
   ```bash
   sudo systemctl restart pumphouse-monitor
   ```

### Notification Events

The system sends notifications for:

1. **Tank Level Thresholds** - Alerts when crossing configured levels
   - Going DOWN: "Tank Dropping - crossed 1000 gallons down"
   - Going UP: "Tank Filling - crossed 750 gallons up"
   - Bounce protection: won't re-alert if water fluctuates

2. **Well Recovery** - Detects when well starts producing water again
   - "Well Recovery Detected - Tank gained 50+ gallons in last 24 hours!"

3. **Well Dry** - Warns if no significant refill in several days
   - "Well May Be Dry - No 50+ gallon refill in 4.2 days"

4. **Float Confirmation** - Tank full confirmation
   - "Tank Full Confirmed - Float sensor confirmed FULL for 3+ readings"

5. **Override Shutoff** - Overflow protection triggered
   - "Override Auto-Shutoff - Tank reached 1410 gal, override turned off"

### Self-Hosting ntfy (Optional)

You can run your own ntfy server instead of using the public service:

```bash
# On a server with Docker
docker run -d --name ntfy -p 8080:80 binwiederhier/ntfy serve

# Update config.py
NTFY_SERVER = "http://your-server-ip:8080"
```

Benefits of self-hosting:
- Complete privacy (notifications don't go through public server)
- No rate limits
- Full control

Note: Self-hosting requires port forwarding if monitoring from outside your network.

## Relay Control

Use the `control.sh` script for safe relay control without GPIO conflicts:

```bash
# Purge spindown filter (default 10 seconds)
./control.sh purge

# Purge with custom duration
./control.sh purge 15

# Enable/disable bypass valve
./control.sh bypass on
./control.sh bypass off

# Enable/disable supply override
./control.sh override on
./control.sh override off

# Show current relay states
./control.sh status
```

This script uses the `gpio` command instead of Python GPIO, so it's safe to run while the monitor is active.

### Automatic Purging

To enable automatic purging after water delivery, edit `monitor/config.py`:

```python
ENABLE_PURGE = True  # Enable automatic purging
MIN_PURGE_INTERVAL = 3600  # Minimum 1 hour between purges
PURGE_DURATION = 10  # Purge for 10 seconds
```

Or set in your config file at `~/.config/pumphouse/monitor.conf`.

### Automatic Override Control (Keep Tank Full)

The monitor can automatically manage the override valve to keep your tank full by eliminating the large hysteresis of the physical float switch.

**Override Auto-On** (Optional):
- When enabled, automatically turns ON the override valve when tank drops below a threshold
- Keeps tank fuller by triggering refill sooner than the physical float would
- The physical float switch becomes a backup safety mechanism

**Override Auto-Shutoff** (Overflow Protection):
- Automatically turns OFF the override valve when tank reaches the shutoff threshold
- Prevents overflow and ensures tank doesn't overfill
- Enabled by default for safety

**How it works:**
- Checks tank level every 60 seconds during tank polling
- If auto-on is enabled and override is OFF and tank < on-threshold, automatically turns it on
- If auto-shutoff is enabled and override is ON and tank >= shutoff-threshold, automatically turns it off
- Logs all automatic actions with tank level to events.csv
- Continuous enforcement: thresholds are checked on every tank poll
- Reloads config on each check, so thresholds can be changed without restarting the service

**Configuration** (`monitor/config.py`):
```python
# Override Auto-On Configuration
OVERRIDE_ON_THRESHOLD = None  # Gallons to turn ON override (None = disabled, e.g., 1350)

# Override Shutoff Configuration
ENABLE_OVERRIDE_SHUTOFF = True  # Enable overflow protection (default: True)
OVERRIDE_SHUTOFF_THRESHOLD = 1410  # Gallons to turn OFF override
```

**Example:** With `OVERRIDE_ON_THRESHOLD = 1350` and `OVERRIDE_SHUTOFF_THRESHOLD = 1410`:
1. Tank drops to 1349 gallons → Override automatically turns ON
2. Tank fills to 1410 gallons → Override automatically turns OFF
3. Float switch is still active as backup, but rarely needed

**Disabling auto-on:** Set to `None` (default) to disable automatic turn-on:
```python
OVERRIDE_ON_THRESHOLD = None  # Disabled
```

**Disabling auto-shutoff:** Set to `False` if you need to fill past the threshold:
```python
ENABLE_OVERRIDE_SHUTOFF = False
```
Then restart the service: `sudo systemctl restart pumphouse-monitor`

## Project Structure
```
pumphouse/
├── venv/                      # Virtual environment
├── monitor/                   # Main package
│   ├── __init__.py           # Package initialization (version: 2.6.0)
│   ├── __main__.py           # Entry point for python -m
│   ├── config.py             # Configuration constants
│   ├── gpio_helpers.py       # GPIO access with retry logic
│   ├── state.py              # Simple state tracking
│   ├── tank.py               # Web scraping (with timestamp parsing)
│   ├── poll.py               # Simplified polling loop
│   ├── relay.py              # Relay control (optional)
│   ├── logger.py             # CSV logging (events + snapshots)
│   ├── main.py               # Entry point
│   ├── check.py              # Status checker command
│   ├── purge.py              # Standalone purge script
│   ├── web.py                # HTTPS web dashboard server
│   ├── ntfy.py               # ntfy.sh notification sender
│   ├── notifications.py      # Notification rule engine
│   ├── stats.py              # Shared statistics module
│   └── templates/
│       └── status.html       # Web dashboard template
├── generate_cert.sh           # SSL certificate generator
├── cert.pem                   # SSL certificate (generated)
├── key.pem                    # SSL private key (generated)
├── requirements.txt
├── README.md
└── CHANGELOG.md
```

## Architecture

### Simplified Design
- **Single-threaded**: Simple polling loop, no threading complexity
- **Event-based**: Logs all state changes to events.csv
- **Snapshot-based**: Periodic summaries to snapshots.csv
- **No artifacts**: No grace periods, no complex heuristics

### Error Handling
- GPIO retry logic filters coastal environment noise (3 reads with 1s pauses)
- Tank scraping continues even if PT website is slow
- Relay control is optional and fails gracefully

### Future Expansion
The simplified architecture makes it easy to add:
- MRTG-style graphs for time-series visualization
- Additional sensors (flow meter, leak detection)
- Web Push notifications (notification infrastructure is already modular)

## Troubleshooting

### "GPIO busy" error
```bash
# Stop all monitor instances
pkill -f "python -m monitor"

# Or cleanup GPIO manually
python3 -c "import RPi.GPIO as GPIO; GPIO.cleanup()"
```

### Tank monitoring failures
- Check network connectivity to tank URL
- Verify tank URL is still valid
- Monitor will continue pressure logging with stale tank data
- Check `tank_error_count` in debug output

### Not detecting pressure changes
1. Check physical connections (GPIO 17 to pin 11, ground to pin 9)
2. Test with multimeter (should be < 10Ω when pressure < 10 PSI)
3. Verify pressure switch is NC (normally closed) type
4. Run with `--debug` to see polling activity

### Inaccurate water volume estimates
1. Compare `estimated_gallons` vs `gallons_changed` in CSV
2. Adjust `RESIDUAL_PRESSURE_SECONDS` if pressure lingers longer/shorter
3. Adjust `SECONDS_PER_GALLON` based on actual measurements
4. Ensure tank sensor is updating (check `tank_last_updated` in debug output)

## Development

### Running Tests
```bash
# Run with debug to verify operation
python -m monitor --changes test_events.csv --debug --tank-interval 1

# Check status
python -c "from monitor.state import SystemState; import pickle; print(vars(pickle.loads(open('/tmp/monitor_state.pkl','rb').read())))"
```

### Adding Features
1. Modify appropriate module (tank.py, pressure.py, etc.)
2. Update SystemState in state.py if adding new data
3. Update logger.py if changing CSV format
4. Update config.py for new configuration options
5. Update main.py for new command-line arguments

## License

Internal use only - Pumphouse monitoring system

## Version

Current version: **2.10.0**

See [CHANGELOG.md](CHANGELOG.md) for version history.