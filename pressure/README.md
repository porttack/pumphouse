# Pressure Monitor

Monitor and log pressure sensor state changes on Raspberry Pi with water volume estimation.

## Overview

This system monitors a 10 PSI pressure switch connected to GPIO, logs pressure events, and estimates water volume pumped based on pressure duration. Designed for remote monitoring with minimal console output and automatic checkpoint logging for long-duration pressure events.

## Hardware Setup

- **Pressure Switch**: 10 PSI normally-closed (NC) switch
- **Connection**:
  - NC contact → GPIO17 (physical pin 11)
  - C (Common) contact → Ground (physical pin 9)
- **Operation**: NC contact opens when pressure ≥ 10 PSI

## Files

### pressure_monitor.py

Primary monitoring program. Logs pressure events and estimates water volume.

**Features:**
- Continuous pressure monitoring
- Quiet mode by default (no console output)
- Optional debug mode with configurable logging interval
- Records complete events (start/end/duration/gallons/type) to CSV
- Water volume estimation with configurable parameters
- Automatic checkpoint logging every 30 minutes for long pressure events
- Graceful shutdown handling (Ctrl+C or kill signal)
- Background operation support (survives SSH disconnect)

**Quick Start:**
```bash
# Make executable (first time only)
chmod +x pressure_monitor.py

# Run quietly (recommended)
./pressure_monitor.py --changes pressure_events.csv

# Run with debug output
./pressure_monitor.py --changes pressure_events.csv --debug

# Run with debug output every 10 seconds
./pressure_monitor.py --changes pressure_events.csv --debug --debug-interval 10

# View help
./pressure_monitor.py --help
```

**Running in Background:**
```bash
# Quiet mode (no output redirection needed!)
nohup ./pressure_monitor.py --changes events.csv &

# With debug output
nohup ./pressure_monitor.py --changes events.csv --debug > output.txt 2>&1 &

# Check if running
ps aux | grep pressure_monitor

# View logs
tail -f events.csv

# Stop
pkill -f pressure_monitor.py
```

### test_pressure.py

Simple test utility for verifying hardware connection and sensor operation.

**Features:**
- Quick hardware verification
- Real-time state monitoring
- No configuration required

**Usage:**
```bash
python3 test_pressure.py
```

Expected output when working correctly:
- Shows "LOW" when pressure < 10 PSI (normal, NC closed)
- Shows "HIGH" when pressure ≥ 10 PSI (NC open)
- Displays changes immediately as they occur

## Output Files

### Main Log (pressure_log.txt)

Text file with all events and status updates:
```
=== Pressure Monitor Started at 2025-01-23 20:30:00 ===
2025-01-23 20:30:00.123 - <10 PSI (NC CLOSED)
2025-01-23 20:30:12.789 - >>> CHANGE: ≥10 PSI (NC OPEN)
2025-01-23 20:30:20.123 - >>> CHANGE: <10 PSI (NC CLOSED)
✓ Pressure Monitor stopped at 2025-01-23 21:00:00
```

### Events CSV (pressure_events.csv)

CSV file with complete pressure events:
```csv
pressure_on_time,pressure_off_time,duration_seconds,estimated_gallons,event_type
2025-01-23 20:30:12.345,2025-01-23 20:30:20.123,7.778,0.00,NORMAL
2025-01-23 20:45:05.123,2025-01-23 21:30:15.456,2710.333,114.86,NORMAL
2025-01-23 21:30:15.456,2025-01-23 22:00:15.456,1800.000,75.86,MAXTIME
2025-01-23 22:00:15.456,2025-01-23 22:15:30.789,915.333,37.93,SHUTDOWN
```

### Event Types

- **NORMAL**: Pressure cycle completed normally (pressure dropped below 10 PSI)
- **SHUTDOWN**: Program stopped while pressure was still high (Ctrl+C or kill signal)
- **STARTUP**: Pressure was already high when program started (logged when it eventually drops)
- **MAXTIME**: Pressure has been high continuously for 30+ minutes, checkpoint logged and timer reset

The `MAXTIME` event type is useful for:
- Detecting if pump is stuck running
- Tracking long fill cycles
- Breaking up very long events into manageable chunks
- Monitoring system health

## Water Volume Estimation

The system estimates gallons pumped based on pressure duration.

**Default Formula:**
- Last 30 seconds of pressure = residual (not pumping)
- Effective pumping time = duration - 30 seconds
- Gallons = effective time / 23.33 seconds per gallon
- Default assumes: 350 seconds = 15 gallons

**Tuning the Estimation:**

Edit constants at the top of `pressure_monitor.py`:
```python
# Adjust residual pressure time
RESIDUAL_PRESSURE_SECONDS = 30  # Change if needed

# Adjust pumping rate
SECONDS_PER_GALLON = 350 / 15   # Modify based on actual measurements

# Adjust checkpoint interval (default 30 minutes)
MAX_PRESSURE_LOG_INTERVAL = 1800  # seconds
```

**Calibration Process:**
1. Record actual gallons pumped (meter reading or tank level)
2. Note pressure duration from CSV
3. Calculate: `SECONDS_PER_GALLON = (duration - 30) / actual_gallons`
4. Update constant and test

## Data Analysis

### Python/Pandas
```python
import pandas as pd

df = pd.read_csv('pressure_events.csv')

# Summary statistics
print(f"Total events: {len(df)}")
print(f"Total water: {df['estimated_gallons'].sum():.1f} gallons")
print(f"Average per event: {df['estimated_gallons'].mean():.1f} gallons")
print(f"Average duration: {df['duration_seconds'].mean():.1f} seconds")

# Event types breakdown
print("\nEvents by type:")
print(df['event_type'].value_counts())

# Filter only normal events (exclude checkpoints)
normal_events = df[df['event_type'] == 'NORMAL']
print(f"\nNormal events: {len(normal_events)}")
print(f"Average normal event: {normal_events['estimated_gallons'].mean():.1f} gallons")

# Events by date
df['date'] = pd.to_datetime(df['pressure_on_time']).dt.date
daily = df.groupby('date')['estimated_gallons'].sum()
print("\nDaily water usage:")
print(daily)
```

### Excel
1. Open Excel
2. File → Import → CSV
3. Select pressure_events.csv
4. Data appears in columns for analysis

## Command Line Options
```
usage: pressure_monitor.py [-h] [--log FILE] [--changes FILE] [--debug]
                           [--debug-interval SECONDS] [--version]

optional arguments:
  -h, --help            show this help message and exit
  --log FILE            Main log file (default: pressure_log.txt)
  --changes FILE        CSV file for pressure events (e.g., pressure_events.csv)
  --debug               Enable console output (quiet mode by default)
  --debug-interval SECONDS
                        Console logging interval in debug mode (default: 60)
  --version             show program's version number and exit
```

## Troubleshooting

### "GPIO busy" error
```bash
# Stop any running instances
pkill -f pressure_monitor

# Or cleanup GPIO
python3 -c "import RPi.GPIO as GPIO; GPIO.cleanup()"
```

### Not detecting pressure changes
1. Run `test_pressure.py` to verify hardware
2. Check physical connections (pins 9 and 11)
3. Test continuity with multimeter:
   - Should show continuity (< 10Ω) when pressure < 10 PSI
   - Should show open circuit when pressure ≥ 10 PSI

### Program not running in background
```bash
# Check if running
ps aux | grep pressure_monitor

# If not found, check for errors in nohup.out
cat nohup.out
```

### Inaccurate water volume estimates
1. Verify actual water pumped vs. estimates
2. Adjust `RESIDUAL_PRESSURE_SECONDS` if pressure lingers longer/shorter
3. Adjust `SECONDS_PER_GALLON` based on actual pumping rate
4. Consider factors: pump efficiency, pipe diameter, elevation

### Too many MAXTIME events
- If pump runs continuously for hours, you'll see many MAXTIME checkpoints
- This is normal - each represents a 30-minute interval
- Consider investigating if pump runs longer than expected

## System Requirements

- Raspberry Pi (any model with GPIO)
- Python 3.7+
- RPi.GPIO library (usually pre-installed)

**Install dependencies if needed:**
```bash
sudo apt-get update
sudo apt-get install python3-rpi.gpio
```

## Tips

- **Quiet mode** (default) produces no console output - ideal for background operation
- **Debug mode** (`--debug`) shows real-time status - useful for initial testing
- Use `--changes` flag for long-term monitoring (smaller files, easier to analyze)
- CSV format enables easy import to spreadsheets/databases
- Run in `screen` or `tmux` for interactive background monitoring
- Use `nohup` for fully automated background operation (no output redirection needed!)
- Monitor logs with `tail -f` to watch events in real-time
- Keep backup copies of calibrated constants
- Review `MAXTIME` events to detect unusual pump behavior

## Known Limitations

- Water volume is an estimate based on pressure duration
- Assumes constant pump flow rate
- Does not account for pump wear, partial cycles, or variable conditions
- Residual pressure time may vary with system configuration
- Requires calibration for accurate volume measurements
- Long-running pressure events generate multiple MAXTIME checkpoints (by design)