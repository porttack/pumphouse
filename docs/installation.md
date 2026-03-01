# Installation & Configuration

Setup guide for the Python environment, configuration file, and running the monitor.

For full Pi OS setup from scratch, see [docs/pi-setup.md](pi-setup.md).

---

## Python Environment

```bash
cd ~/src/pumphouse

# Create virtual environment (one-time)
python3 -m venv venv

# Activate
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## Running as Systemd Services (Recommended)

```bash
# Install and enable all services (one-time)
./install-services.sh
sudo systemctl enable --now pumphouse-monitor pumphouse-web pumphouse-timelapse

# Check status
sudo systemctl status pumphouse-monitor
sudo systemctl status pumphouse-web
sudo systemctl status pumphouse-timelapse

# View live logs
sudo journalctl -u pumphouse-monitor -f
sudo journalctl -u pumphouse-web -f
sudo journalctl -u pumphouse-timelapse -f

# Restart after code changes
sudo systemctl restart pumphouse-monitor pumphouse-web pumphouse-timelapse
```

**After making code changes:**
1. No reinstall needed — services run from the project directory.
2. Restart the affected service(s).
3. Check logs to verify startup.

**If you modify `.service` files:**
```bash
bin/install-services.sh   # copies from terraform/services/ to /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart pumphouse-monitor pumphouse-web
```

---

## Running Manually (for Testing)

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
tail -f ~/.local/share/pumphouse/events.csv
tail -f snapshots.csv

# Stop
pkill -f "python -m monitor"
```

---

## Command-Line Options

```
python -m monitor [OPTIONS]

Options:
  --events FILE           Events CSV file (default: ~/.local/share/pumphouse/events.csv)
  --snapshots FILE        Snapshots CSV file (default: snapshots.csv in project dir)
  --debug                 Enable console output
  --poll-interval N       Pressure sensor poll interval in seconds (default: 5)
  --tank-interval N       Tank check interval in minutes (default: 1)
  --snapshot-interval N   Snapshot interval: 15, 5, or 2 minutes (default: 15)
  --tank-url URL          Tank monitoring URL
  --version               Show version and exit
  -h, --help              Show help message
```

**Note:** Automatic purging is configured in `monitor/config.py`, not via command line.

---

## Configuration File

Create `~/.config/pumphouse/monitor.conf` to override defaults without editing `config.py`:

```ini
# GPIO Pins
PRESSURE_PIN=17
FLOAT_PIN=21

# Purge Configuration
ENABLE_PURGE=False
MIN_PURGE_INTERVAL=3600
PURGE_DURATION=10

# Override Control Configuration
OVERRIDE_ON_THRESHOLD=1350      # Auto-on when tank drops below this (None=disabled)
ENABLE_OVERRIDE_SHUTOFF=True    # Auto-off overflow protection
OVERRIDE_SHUTOFF_THRESHOLD=1410 # Auto-off when tank reaches this

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
TANK_URL=https://www.mypt.in/s/your-tank-url-here

# Water estimation
RESIDUAL_PRESSURE_SECONDS=30
SECONDS_PER_GALLON=71.43

# Logging
MAX_PRESSURE_LOG_INTERVAL=1800
```

---

## Secrets File

All sensitive credentials live in `~/.config/pumphouse/secrets.conf` (mode 600, never committed to git):

```bash
cp secrets.conf.template ~/.config/pumphouse/secrets.conf
chmod 600 ~/.config/pumphouse/secrets.conf
nano ~/.config/pumphouse/secrets.conf
```

Key secrets:

| Key | Description |
|-----|-------------|
| `EMAIL_SMTP_PASSWORD` | Gmail App Password |
| `CAMERA_USER` / `CAMERA_PASS` | Amcrest camera credentials |
| `SECRET_*_TOKEN` | Remote relay control tokens |
| `NTFY_TOPIC` | ntfy.sh topic name |
| `TANK_URL` | mypt.in tank sensor URL |
| `PUMPHOUSE_HOST` / `PUMPHOUSE_PORT` | Hostname for email links |
| `TRACKHS_USERNAME` / `TRACKHS_PASSWORD` | Reservation scraper |
| `CLOUDFLARE_*` | Cloudflare KV credentials |

---

## Testing

```bash
source venv/bin/activate

# Test push notification
python -m monitor.check --test-notification

# Test email
python -m monitor.check --test-email

# Check current system status
python -m monitor.check
```

---

## Water Volume Calibration

The system estimates gallons pumped from pump cycle duration.

**Default formula:**
- Last 30 seconds of pressure = residual (not pumping)
- Effective pumping time = duration − 30 seconds
- Gallons = effective time ÷ 71.43 seconds per gallon

**To recalibrate:**
1. Record actual gallons from tank level change
2. Note `duration_seconds` from `~/.local/share/pumphouse/events.csv`
3. Calculate: `SECONDS_PER_GALLON = (duration - 30) / actual_gallons`
4. Update in `~/.config/pumphouse/monitor.conf` or `monitor/config.py`

---

## Development

### Module structure

| Module | Purpose |
|--------|---------|
| `monitor/config.py` | All configuration constants |
| `monitor/poll.py` | Main monitoring loop |
| `monitor/web.py` | Flask web server & API routes |
| `monitor/tank.py` | mypt.in scraping & timestamp parsing |
| `monitor/gpio_helpers.py` | GPIO access with retry logic |
| `monitor/relay.py` | Relay control |
| `monitor/logger.py` | CSV event & snapshot logging |
| `monitor/notifications.py` | Notification rule engine |
| `monitor/email_notifier.py` | HTML email construction |
| `monitor/ntfy.py` | ntfy.sh push notifications |
| `monitor/stats.py` | Shared statistics |
| `monitor/occupancy.py` | Occupancy detection |
| `monitor/gph_calculator.py` | Well GPH calculation |
| `monitor/ecobee.py` | Ecobee thermostat (web scraping) |
| `monitor/state.py` | Simple state tracking |
| `monitor/check.py` | Status checker & test runner |

### Adding features

1. Modify the appropriate module
2. Update `SystemState` in `state.py` if adding new data
3. Update `logger.py` if changing CSV format
4. Update `config.py` for new configuration options
5. Update `main.py` for new command-line arguments
