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
- **Relay Control**: Optional automatic spindown filter purging after water delivery
- **Background Operation**: Runs reliably via nohup with SSH disconnect survival
- **Web Dashboard**: HTTPS web interface on port 6443 for real-time status and historical data

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
```bash
# Run with debug output (15-minute snapshots)
python -m monitor --debug

# Run with debug and 2-minute snapshots
python -m monitor --debug --snapshot-interval 2

# Run with auto-purge enabled
python -m monitor --debug --enable-purge

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
  --enable-purge          Enable automatic filter purging after water delivery
  --version               Show version and exit
  -h, --help              Show help message
```

## Configuration File (Optional)

Create `~/.config/pumphouse/monitor.conf`:
```ini
# Pumphouse Monitor Configuration

# GPIO Pins
PRESSURE_PIN=17
FLOAT_PIN=27

# Polling intervals (seconds)
POLL_INTERVAL=5
TANK_POLL_INTERVAL=60

# Tank configuration
TANK_HEIGHT_INCHES=58
TANK_CAPACITY_GALLONS=1400
TANK_URL=https://www.mypt.in/s/oyd95OEj/qbbBE9Loxo

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

## Manual Filter Purging

You can manually purge the spindown sediment filter:

```bash
# Basic purge (default 10 seconds)
python -m monitor.purge

# Custom duration with debug output
python -m monitor.purge --duration 30 --debug

# Help
python -m monitor.purge --help
```

**Note:** Auto-purge can be enabled with `--enable-purge` flag when running the monitor.

## Project Structure
```
pumphouse/
├── venv/                      # Virtual environment
├── monitor/                   # Main package
│   ├── __init__.py           # Package initialization (version: 2.2.0)
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
- Alert notifications

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

Current version: **2.2.0**

See [CHANGELOG.md](CHANGELOG.md) for version history.