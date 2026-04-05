# Data Format Reference

CSV file formats, event types, and snapshot fields produced by `pumphouse-monitor`.

## File Locations

| File | Location |
|------|----------|
| `events.csv` | `~/.local/share/pumphouse/events.csv` |
| `snapshots.csv` | `~/.local/share/pumphouse/snapshots.csv` |
| `snapshots-YYYY-MM.csv.gz` | `~/.local/share/pumphouse/` (monthly archives) |
| `daily.csv` | `~/.local/share/pumphouse/daily.csv` |
| `reservations.csv` | `~/.local/share/pumphouse/reservations.csv` |
| `reservations_snapshot.csv` | `~/.local/share/pumphouse/reservations_snapshot.csv` |
| `gph_cache.csv` | `~/.local/share/pumphouse/gph_cache.csv` |

Paths are defined in `monitor/config.py` as `EVENTS_FILE`, `SNAPSHOTS_FILE`, `DAILY_CSV`, etc.

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
| `outdoor_temp_f` | Outdoor temperature °F (Ambient Weather station) |
| `indoor_temp_f` | Indoor temperature °F (Ecobee thermostat) |
| `outdoor_humidity` | Outdoor relative humidity % |
| `baro_abs_inhg` | Absolute barometric pressure inHg |
| `wind_gust_mph` | Wind gust speed mph |
| `tank_rolling_gph` | 2-hour rolling tank fill rate (positive = filling, negative = consuming). Blank for first few snapshots after startup. |

#### Schema migration

New columns are added automatically by `migrate_snapshots_csv()` on monitor startup. Historical rows are backfilled using `backfill_rolling_gph.py`.

#### Monthly archiving

`rotate_snapshots.py` (cron: 3 AM on the 1st) compresses the previous month's rows into `snapshots-YYYY-MM.csv.gz` and trims `snapshots.csv` to the current month. All tools (`build_daily.py`, `backfill_rolling_gph.py`, `epaper_jpg.py`) read archives transparently.

---

## Daily Summary CSV (`daily.csv`)

One row per completed calendar day. Generated by `build_daily.py` (cron: 2:30 AM daily, incremental append). Rebuild from scratch: `python build_daily.py --rebuild`.

Reads all snapshot archives + `snapshots.csv` and `reservations.csv`.

### Priority columns (leftmost)

| Field | Description |
|-------|-------------|
| `date` | Calendar date (YYYY-MM-DD) |
| `occupied_pct` | % of snapshot windows with property occupied |
| `gallons_end` | Tank level at end of day |
| `gallons_net_change` | Net tank change for the day |
| `tank_rolling_gph_avg` | Average 2h rolling fill rate during calling windows |
| `pressure_high_pct_overall` | % of non-bypass interval with pump running |
| `high_events_pump_gph` | GPH during consecutive high-pressure runs (fast-fill) |
| `low_events_pump_gph` | GPH during normal low-pressure pumping |
| `bypass_hours` | Hours bypass valve was open |
| `backflush_gallons` | Gallons consumed in overnight (00:00–04:30) backflush event, or blank |
| `checkout_net_income` | Net income (after mgmt fee) from reservations checking out this day |
| `net_income_cumulative` | Running net income total for the current month through this day |

### Detail columns

Tank: `gallons_start`, `gallons_min`, `gallons_max`

GPH detail: `tank_rolling_gph_min`, `tank_rolling_gph_max`, `low_events_consume_gph`

Pressure/events: `pressure_high_minutes`, `high_events`, `high_events_total_minutes`, `high_events_avg_minutes`, `low_events_windows`

Float/override: `float_calling_windows`, `float_full_windows`, `override_windows`, `override_minutes`, `total_purges`, `bypass_windows`, `occupied_windows`

Coverage: `n_snapshots`, `hours_covered`

Weather: `outdoor_temp_min/max/avg`, `indoor_temp_min/max/avg`, `humidity_avg`, `baro_avg`, `wind_gust_max`

### Notes

- **`tank_rolling_gph_*`** only includes rows where `float_ever_calling = YES` (excludes idle periods)
- **`backflush_gallons`**: detected when ≥50 gal tank decline occurs across ≥3 consecutive overnight snapshots
- **`net_income_cumulative`** resets to 0 at the start of each month
- **Income** excludes owner stays and maintenance holds; applies `MANAGEMENT_FEE_PERCENT` from config

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
