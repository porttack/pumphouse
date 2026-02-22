# Data Format Reference

CSV file formats, event types, and snapshot fields produced by `pumphouse-monitor`.

---

## Events CSV (`events.csv`)

Logs every state change immediately.

### Header & Example

```csv
timestamp,event_type,pressure_state,float_state,tank_gallons,tank_depth,tank_percentage,estimated_gallons,relay_bypass,relay_supply_override,notes
2025-11-28 10:15:00.123,INIT,HIGH,CLOSED/CALLING,1185,49.09,84.6,,,OFF,OFF,System startup
2025-11-28 10:23:15.456,PRESSURE_LOW,HIGH,CLOSED/CALLING,1185,49.09,84.6,1.20,OFF,OFF,Duration: 330.3s
2025-11-28 10:28:45.789,TANK_LEVEL,LOW,CLOSED/CALLING,1203,49.82,85.9,,OFF,OFF,Changed by +17.6 gal
2025-11-28 10:45:20.234,SHUTDOWN,HIGH,CLOSED/CALLING,1208,50.06,86.3,,OFF,OFF,Clean shutdown
```

### Event Types

| Event Type | Description |
|-----------|-------------|
| `INIT` | System startup |
| `PRESSURE_HIGH` | Pressure went ≥10 PSI (pump started) |
| `PRESSURE_LOW` | Pressure went <10 PSI (pump stopped); includes `estimated_gallons` |
| `TANK_LEVEL` | Tank level changed (scraped from mypt.in) |
| `FLOAT_CALLING` | Float sensor changed to CLOSED/CALLING (tank needs water) |
| `FLOAT_FULL` | Float sensor changed to OPEN/FULL (tank full) |
| `PURGE` | Automatic spindown filter purge triggered |
| `OVERRIDE_AUTO_ON` | Override valve turned on automatically (tank below threshold) |
| `OVERRIDE_SHUTOFF` | Override valve turned off automatically (overflow protection) |
| `REMOTE_CONTROL` | Remote relay control action via email secret URL |
| `NOTIFY_TANK_*` | Email notification sent for tank threshold crossing |
| `NOTIFY_FLOAT_FULL` | Email notification sent for confirmed tank full |
| `NOTIFY_OVERRIDE_OFF` | Email notification sent for override auto-shutoff |
| `NOTIFY_WELL_RECOVERY` | Email notification sent for well recovery detection |
| `NOTIFY_WELL_DRY` | Email notification sent for potential well dry condition |
| `NOTIFY_HIGH_FLOW` | Email notification sent for high flow rate (fast fill mode) |
| `NOTIFY_BACKFLUSH` | Email notification sent for backflush event detection |
| `CHECK-IN` | Guest check-in time reached (logged by reservation cron) |
| `CHECK-OUT` | Guest check-out time reached (logged by reservation cron) |
| `NEW_RESERVATION` | New booking detected in TrackHS |
| `CANCELED_RESERVATION` | Booking removed from TrackHS |
| `CHANGED_RESERVATION` | Booking modified in TrackHS |
| `gph_daily` | Daily well GPH metric (logged by cron at 2 AM) |
| `SHUTDOWN` | Clean shutdown |

---

## Snapshots CSV (`snapshots.csv`)

Periodic aggregated summaries at exact clock boundaries (every 15, 5, or 2 minutes).

### Header & Example

```csv
timestamp,duration_seconds,tank_gallons,tank_gallons_delta,tank_data_age_seconds,float_state,float_ever_calling,float_always_full,pressure_high_seconds,pressure_high_percent,estimated_gallons_pumped,purge_count,relay_bypass,relay_supply_override,occupied
2025-11-28 10:15:00.000,900,1185,,450,CLOSED/CALLING,Yes,No,330,36.7,4.20,0,OFF,OFF,NO
2025-11-28 10:30:00.000,900,1203,+18,120,CLOSED/CALLING,Yes,No,0,0.0,0.00,0,OFF,OFF,NO
2025-11-28 10:45:00.000,900,1198,-5,300,CLOSED/CALLING,Yes,No,45,5.0,0.21,0,OFF,OFF,YES
```

### Field Reference

| Field | Description |
|-------|-------------|
| `timestamp` | Exact clock boundary (e.g., 10:15:00, 10:30:00) |
| `duration_seconds` | Snapshot interval in seconds (900 = 15 min) |
| `tank_gallons` | Tank level at snapshot time |
| `tank_gallons_delta` | Change since last snapshot with explicit sign (+18, -5, +0) |
| `tank_data_age_seconds` | Seconds since mypt.in sensor last updated (staleness) |
| `float_state` | Current float switch state |
| `float_ever_calling` | Was float ever CALLING during this interval? |
| `float_always_full` | Was float always FULL during this interval? |
| `pressure_high_seconds` | Seconds pump was running during interval |
| `pressure_high_percent` | Percentage of interval with pump running |
| `estimated_gallons_pumped` | Estimated water delivered during interval |
| `purge_count` | Number of purge cycles during interval |
| `relay_bypass` | Bypass valve state at snapshot time |
| `relay_supply_override` | Supply override valve state at snapshot time |
| `occupied` | Property occupied at snapshot time (YES/NO) |

---

## Logging Behavior

1. **Events**: Logged immediately on every state change to `events.csv`
2. **Snapshots**: Aggregated over the interval (15/5/2 min), written at exact clock boundaries to `snapshots.csv`
3. **Tank data age**: Tracks actual mypt.in "last updated" time, not fetch time — shows true data staleness
4. **Gallons estimation**: Uses completed pump cycle duration when available; falls back to accumulated HIGH time for in-progress cycles

---

## Water Volume Estimation

### Default Formula

- Last 30 seconds of pressure = residual (not actually pumping)
- Effective pumping time = duration − 30 seconds
- Gallons = effective time ÷ 71.43 seconds per gallon
- Basis: 10 seconds = 0.14 gallons (2 clicks of Dosatron at 0.08 gal/click)

### Calibration

1. Record actual gallons from a known tank level change
2. Note `duration_seconds` from events.csv for that pump cycle
3. Calculate: `SECONDS_PER_GALLON = (duration - 30) / actual_gallons`
4. Update in `~/.config/pumphouse/monitor.conf` or `monitor/config.py`

---

## GPH Cache (`gph_cache.csv`)

Written by `monitor/gph_calculator.py`. Cache lifetime is 24 hours.

```csv
timestamp,slow_fill_gph,fast_fill_gph,slow_fill_samples,fast_fill_samples
2025-12-26 20:38:19.742,30.5,11.8,8,21
```

- **slow_fill_gph**: Wall-clock GPH when supply override is OFF (normal well pump operation)
- **fast_fill_gph**: Wall-clock GPH when supply override is ON

Force recalculation: `rm gph_cache.csv` then reload dashboard or run `python -m monitor.gph_calculator`.
