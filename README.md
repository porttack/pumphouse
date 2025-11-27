# Pumphouse Monitor

Comprehensive pressure and tank level monitoring system for remote water treatment facilities. Monitors pressure events, tracks water usage, and correlates pumped water with actual tank level changes.

## Features

- **Pressure Monitoring**: Continuous monitoring of 10 PSI pressure switch with event detection
- **Tank Level Monitoring**: Periodic scraping of PT sensor data with depth, percentage, and gallons
- **Float Sensor Integration**: Monitors tank float switch state (80%/95% hysteresis)
- **Smart Logging**: Only logs events when tank data actually changes
- **Water Usage Tracking**: Estimates gallons pumped and compares to actual tank level changes
- **Graceful Degradation**: Pressure monitoring continues even if tank monitoring fails
- **Thread-Safe Architecture**: Ready for web dashboard integration
- **GPIO Management**: Properly releases GPIO between readings to avoid conflicts
- **Background Operation**: Runs reliably via nohup with SSH disconnect survival

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
# Run with debug output
python -m monitor --changes events.csv --debug

# Run in background
nohup venv/bin/python -m monitor --changes events.csv --debug > output.txt 2>&1 &

# Check if running
ps aux | grep monitor

# View logs
tail -f events.csv

# Stop
pkill -f "python -m monitor"
```

## Usage
```
python -m monitor [OPTIONS]

Options:
  --log FILE              Main log file (default: pressure_log.txt)
  --changes FILE          CSV file for pressure events
  --debug                 Enable console output
  --debug-interval N      Console logging interval in debug mode (default: 60s)
  --poll-interval N       Pressure sensor poll interval (default: 5s)
  --tank-interval N       Tank check interval in minutes (default: 1m)
  --tank-url URL          Tank monitoring URL
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

### Events CSV (pressure_events.csv)

Complete pressure events with tank correlation:
```csv
pressure_on_time,pressure_off_time,duration_seconds,estimated_gallons,event_type,float_state,float_last_change,tank_gallons,tank_depth,tank_percentage,tank_pt_percentage,gallons_changed
2025-01-27 14:23:15.123,2025-01-27 14:28:45.456,330.333,13.21,NORMAL,CLOSED/CALLING,2025-01-27 14:15:30,850,35.12,60.6,61,12.5
2025-01-27 15:45:20.789,2025-01-27 16:52:10.234,4009.445,167.68,NORMAL,OPEN/FULL,2025-01-27 16:50:15,1375,79.74,137.5,95,525.0
```

### Event Types

- **NORMAL**: Pressure cycle completed normally (pressure dropped below 10 PSI)
- **SHUTDOWN**: Program stopped while pressure was still high (Ctrl+C or kill signal)
- **STARTUP**: Pressure was already high when program started (logs when it drops)
- **MAXTIME**: Pressure has been high for 30+ minutes, checkpoint logged (only if tank data changed)

### Main Log (pressure_log.txt)

Text file with all events and status updates:
```
=== Monitor v2.0.0 Started at 2025-01-27 14:15:00 ===
2025-01-27 14:15:00.123 - <10 PSI (NC CLOSED)
2025-01-27 14:23:15.456 - >>> CHANGE: ≥10 PSI (NC OPEN)
2025-01-27 14:28:45.789 - >>> CHANGE: <10 PSI (NC CLOSED)
```

## Logging Behavior

The system uses smart logging to correlate pressure events with actual tank level changes:

1. **Pressure Activated**: Starts timer, clears tank change flag
2. **Pressure Deactivated**: Waits up to 2 minutes for fresh tank data
3. **Tank Data Updates**: Logs event immediately with current tank state
4. **Timeout**: If no tank update within 2 minutes, logs with current data
5. **Long Events (30+ min)**: Only logs MAXTIME checkpoint if tank data has changed

This ensures accurate correlation between estimated gallons pumped and actual `gallons_changed` in the tank.

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

## Project Structure
```
pumphouse/
├── venv/                      # Virtual environment
├── monitor/                   # Main package
│   ├── __init__.py           # Package initialization
│   ├── __main__.py           # Entry point for python -m
│   ├── config.py             # Configuration constants
│   ├── gpio_helpers.py       # GPIO access functions
│   ├── state.py              # Thread-safe shared state
│   ├── tank.py               # Tank monitoring
│   ├── pressure.py           # Pressure monitoring
│   ├── logger.py             # CSV/file logging
│   └── main.py               # Argument parsing & main loop
├── requirements.txt
├── README.md
└── CHANGELOG.md
```

## Architecture

### Threading Model
- **Main Thread**: Pressure monitoring (polls every 5 seconds)
- **Tank Thread**: Tank monitoring (polls every 1 minute)
- **Shared State**: Thread-safe SystemState object for coordination

### Error Handling
- Tank monitoring failures don't stop pressure monitoring
- Consecutive errors are tracked and logged
- After 5 consecutive tank errors, warning is displayed but operation continues
- GPIO conflicts are handled gracefully with retry logic

### Future Expansion
The architecture is designed to easily add:
- Web dashboard (reads from SystemState)
- REST API (exposes SystemState via HTTP)
- Additional sensors (add to SystemState and create monitor threads)
- Alerts/notifications (subscribe to state changes)

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

Current version: **2.0.0**

See [CHANGELOG.md](CHANGELOG.md) for version history.