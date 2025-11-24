# Pressure Monitor

Monitor and log pressure sensor state changes on Raspberry Pi with water volume estimation.

## Overview

This system monitors a 10 PSI pressure switch connected to GPIO, logs pressure events, and estimates water volume pumped based on pressure duration.

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
- Logs every 5 seconds to text file
- Records complete events (start/end/duration/gallons) to CSV
- Water volume estimation with configurable parameters
- Terminal beep alerts on state changes
- Background operation support (survives SSH disconnect)

**Quick Start:**
```bash
# Basic monitoring
python3 pressure_monitor.py

# With CSV event logging (recommended)
python3 pressure_monitor.py --changes pressure_events.csv

# View help
python3 pressure_monitor.py --help
```

**Running in Background:**
```bash
# Using nohup (survives SSH disconnect)
nohup python3 pressure_monitor.py --changes events.csv > output.txt 2>&1 &

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

Text file with all events, logged every 5 seconds:
```
2025-01-23 20:30:00.123 - <10 PSI (NC CLOSED)
2025-01-23 20:30:05.456 - <10 PSI (NC CLOSED)
2025-01-23 20:30:12.789 - >>> CHANGE: ≥10 PSI (NC OPEN)
2025-01-23 20:30:20.123 - >>> CHANGE: <10 PSI (NC CLOSED)
  → Event summary: 7.3s duration, ~0.0 gallons
```

### Events CSV (pressure_events.csv)

CSV file with complete pressure events:
```csv
pressure_on_time,pressure_off_time,duration_seconds,estimated_gallons
2025-01-23 20:30:12.345,2025-01-23 20:30:20.123,7.778,0.00
2025-01-23 20:45:05.123,2025-01-23 21:30:15.456,2710.333,114.86
```

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
usage: pressure_monitor.py [-h] [--log FILE] [--changes FILE] [--version]

optional arguments:
  -h, --help       show this help message and exit
  --log FILE       Main log file (default: pressure_log.txt)
  --changes FILE   CSV file for pressure events (e.g., pressure_events.csv)
  --version        show program's version number and exit
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

### Missing events in log
```bash
# Check if process is running
ps aux | grep pressure_monitor

# View output for errors (if using nohup)
tail -f output.txt

# Check disk space
df -h
```

### Inaccurate water volume estimates
1. Verify actual water pumped vs. estimates
2. Adjust `RESIDUAL_PRESSURE_SECONDS` if pressure lingers longer/shorter
3. Adjust `SECONDS_PER_GALLON` based on actual pumping rate
4. Consider factors: pump efficiency, pipe diameter, elevation

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

- Use `--changes` flag for long-term monitoring (smaller files)
- CSV format enables easy import to spreadsheets/databases
- Run in `screen` or `tmux` for interactive background monitoring
- Use `nohup` for fully automated background operation
- Monitor logs with `tail -f` to watch events in real-time
- Keep backup copies of calibrated constants

## Known Limitations

- Water volume is an estimate based on pressure duration
- Assumes constant pump flow rate
- Does not account for pump wear, partial cycles, or variable conditions
- Residual pressure time may vary with system configuration
- Requires calibration for accurate volume measurements