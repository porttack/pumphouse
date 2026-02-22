# GPH (Gallons Per Hour) Tracking

Automated tracking of well pump performance metrics integrated into the pumphouse monitoring system.

---

## Overview

The system calculates **wall-clock GPH** for two fill modes:

- **Slow-fill GPH**: Fill rate when supply override is OFF (normal well pump operation with natural cycling)
- **Fast-fill GPH**: Fill rate when supply override is ON (auxiliary supply engaged)

**Wall-clock GPH** answers: *"How fast does the tank fill in real calendar time?"*

This accounts for pump duty cycle, well recovery time between cycles, and concurrent household water usage.

---

## Where It Appears

- **Web dashboard**: "Well GPH (3-week avg)" in the main status panel: `Slow: 30 • Fast: 12`
- **Email notifications**: All status emails include GPH in the system status section
- **Daily event log**: `gph_daily` events in `events.csv` for historical tracking

---

## How It Works

### Calculation method

1. Load recent snapshots (default: past 21 days)
2. Group into time windows (default: 12-hour windows)
3. Filter valid windows:
   - Must have ≥18 gallon tank increase (3× sensor accuracy of ±6 gal)
   - Must span ≥80% of target window time
   - Must have ≥3 snapshot samples
4. Categorize by override relay status (OFF = slow-fill, ON = fast-fill)
5. Calculate: `GPH = tank_delta_gallons / wall_clock_hours`
6. Return median (robust against outliers)

### Why these parameters?

- **18 gallon minimum**: Tank sensor has ±6 gal accuracy; 18 gal = 3× error threshold ensures signal >> noise
- **12-hour windows**: Large enough to capture typical daily fill patterns with intermittent pumping
- **21-day lookback**: ~3 weeks gives good sample size while staying reasonably current
- **Median vs. average**: Reduces impact of outliers from sensor noise or unusual events

---

## Setup: Daily Logging Cron

Build a historical record of GPH by running the daily logger:

```bash
crontab -e
# Add (runs at 2 AM daily):
0 2 * * * cd /home/pi/src/pumphouse && /home/pi/src/pumphouse/venv/bin/python3 log_daily_gph.py >> /home/pi/src/pumphouse/gph_log.txt 2>&1
```

Or run manually anytime:

```bash
cd ~/src/pumphouse
python3 log_daily_gph.py
```

---

## Manual Calculation

```bash
cd ~/src/pumphouse
source venv/bin/activate
python -m monitor.gph_calculator
```

Output:
```
Calculating GPH from snapshots.csv...

Results:
  Slow-fill GPH: 30.5 (8 samples)
  Fast-fill GPH: 11.8 (21 samples)
  Last updated: 2025-12-26 20:38:19
```

---

## Configuration

In `monitor/gph_calculator.py`:

```python
def calculate_gph_from_snapshots(
    filepath: str = 'snapshots.csv',
    lookback_days: int = 21,       # How far back to analyze
    min_delta_gallons: int = 18,   # Minimum tank increase (don't change)
    window_hours: int = 12         # Time window size
)
```

| Parameter | Default | When to Change |
|-----------|---------|----------------|
| `lookback_days` | 21 | Increase to 28–30 if seeing <5 samples |
| `min_delta_gallons` | 18 | Don't change (based on sensor physics) |
| `window_hours` | 12 | Decrease to 8 for higher GPH; increase to 16–24 for lower |

---

## Caching

Results cached in `gph_cache.csv` for 24 hours:

```csv
timestamp,slow_fill_gph,fast_fill_gph,slow_fill_samples,fast_fill_samples
2025-12-26 20:38:19.742,30.5,11.8,8,21
```

Force recalculation: `rm gph_cache.csv`

---

## Typical Values

- **Slow-fill GPH**: 5–10 GPH (wall-clock, 12-hour windows)
  - Represents daily fill rate including pump-off time and well recovery
  - Shorter windows (4–8 hours) show higher GPH (20–60) during active pumping periods

- **Fast-fill GPH**: 10–15 GPH (net)
  - NET delivery (pump GPH minus household usage GPH)
  - Can appear slower than slow-fill because household usage is higher during occupied fast-fill periods

### Sample count confidence

| Samples | Confidence |
|---------|-----------|
| <5 | Not reliable — need more data |
| 5–10 | Fair |
| 10–20 | Good |
| >20 | Excellent |

---

## Historical Queries

```bash
# View all GPH history
grep "gph_daily" events.csv

# Last 30 days
grep "gph_daily" events.csv | tail -30
```

Use trends over weeks/months to detect declining well capacity or seasonal water table changes.

---

## Troubleshooting

### Dashboard shows "N/A"
- Not enough valid data windows (need ≥18 gal tank increases)
- Try increasing `lookback_days` or `window_hours`
- Check `snapshots.csv` has recent data

### GPH seems too low (<5 GPH)
- Likely correct during high household usage or slow well recovery
- Wall-clock GPH includes all pump-off time

### GPH varies widely day-to-day
- Normal — well performance depends on many factors
- Look at trends over weeks, not individual days

### Daily logger not running
```bash
crontab -l | grep gph
tail -20 /home/pi/src/pumphouse/gph_log.txt
```
