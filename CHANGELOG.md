# CHANGELOG

All notable changes to the pressure monitoring system.

## [2.0.0] - 2025-01-27

## [2.1.0] - 2025-11-28

## [2.1.1] - 2025-11-28

## [2.2.0] - 2025-11-28

### Added
- **Web Dashboard**: HTTPS web interface on port 6443 for real-time monitoring
  - Live sensor readings (pressure, float, temperature, humidity)
  - Tank level visualization with progress bar
  - Interactive Chart.js graph with selectable time ranges (6h, 12h, 24h, 3d, 7d, 14d)
  - Aggregate statistics: tank level changes (1hr/24hr), pressure HIGH percentages, last 50+ gallon refill detection
  - Recent snapshots (last 10) displayed above events
  - Recent events (last 20)
  - Auto-refresh every 5 minutes
  - Dark theme optimized for monitoring
  - Basic authentication with configurable credentials
- `monitor/web.py`: Flask-based HTTPS server with basic auth and `/api/chart_data` endpoint
- `monitor/templates/status.html`: Web dashboard template with Chart.js integration
- `generate_cert.sh`: SSL certificate generation script
- Flask dependency to requirements.txt

### Changed
- README.md: Added comprehensive web dashboard documentation
- Project structure now includes web server components
- Dashboard layout optimized with progress bar at top, consolidated stats boxes, prioritized sensor ordering

### Added
- **Snapshot tank delta**: Added `tank_gallons_delta` column to snapshots.csv showing change since last snapshot with explicit sign (+18, -5, +0) for easy visual scanning

### Changed
- `logger.py`: Updated snapshots CSV header and logging to include tank_gallons_delta
- `poll.py`: Track last snapshot tank gallons and calculate delta for each snapshot
- README.md: Updated snapshots CSV example to show tank_gallons_delta column


### Fixed
- **Tank data age calculation**: Now correctly uses the actual timestamp from PT website "last updated" field instead of fetch time, so age increases from ~5-20 minutes before resetting (was incorrectly counting down)
- **Snapshot gallons estimation**: Snapshots now estimate gallons from accumulated pressure HIGH time when no completed pump cycles occurred during the interval (prevents 0.00 gallons when pressure stays HIGH across snapshot boundaries)

### Changed
- `tank.py`: Added scraping of "updated_on" timestamp from PT website
- `poll.py`: Updated `fetch_tank_data()` to store actual PT timestamp instead of current time
- `poll.py`: Enhanced `get_snapshot_data()` to estimate gallons from `pressure_high_time` when no completed cycles exist

### Removed
- Deleted unused `pressure.py` legacy code (old PressureMonitor class from v2.0.0 architecture)


### Major Refactoring
- **Breaking Change**: Restructured as Python package (`python -m monitor`)
- Split monolithic script into modular components:
  - `config.py` - Configuration constants and file loading
  - `gpio_helpers.py` - GPIO access with proper cleanup
  - `state.py` - Thread-safe shared state
  - `tank.py` - Tank monitoring and scraping
  - `pressure.py` - Pressure monitoring logic
  - `logger.py` - CSV and file logging
  - `main.py` - Entry point and argument parsing

### Added
- **Config file support**: Optional `~/.config/pumphouse/monitor.conf`
- **Graceful degradation**: Pressure monitoring continues even if tank monitoring fails
- **Error tracking**: Counts consecutive tank fetch errors, continues operation
- **Tank data change detection**: Only logs events when tank data actually changes
- **Smart logging**: Waits for tank update after pressure drop (up to 2 minutes)
- **Initial state capture**: Fetches tank level at startup for accurate `gallons_changed`
- **Final state capture**: Fetches tank level on shutdown if pressure active
- **Enhanced CSV columns**: 
  - `float_state` - Current float sensor state
  - `float_last_change` - Timestamp of last float state change
  - `tank_gallons` - Current tank gallons
  - `tank_depth` - Current tank depth (inches)
  - `tank_percentage` - Calculated percentage (based on 58" height)
  - `tank_pt_percentage` - PT sensor reported percentage
  - `gallons_changed` - Delta from last logged event
- **Threading**: Separate threads for pressure (5s) and tank (1min) monitoring
- **Web-ready architecture**: SystemState designed for future web dashboard

### Changed
- Pressure polling interval now configurable via `--poll-interval` (default: 5s)
- Tank polling interval now in minutes via `--tank-interval` (default: 1m)
- GPIO properly released between readings to avoid conflicts
- MAXTIME events only logged if tank data has changed
- Version number reset to 2.0.0 to reflect major architectural change

### Fixed
- GPIO conflicts with other scripts running simultaneously
- Missing tank data in pressure events
- Inaccurate gallons_changed calculations
- Race conditions between pressure and tank monitoring

## [1.2.0] - 2025-01-23

### Added
- Shebang line (`#!/usr/bin/env python3`)
- Signal handlers for graceful shutdown (SIGINT, SIGTERM)
- Four event types in CSV logging:
  - `NORMAL`: Pressure cycle completed normally
  - `SHUTDOWN`: Program stopped while pressure was high
  - `STARTUP`: Pressure was already high at program start
  - `MAXTIME`: Pressure checkpoint after 30 minutes
- `--debug` flag for console output (quiet mode by default)
- `--debug-interval` parameter (default: 60s)
- Automatic logging every 30 minutes when pressure remains high
- Graceful handling of startup when pressure already ≥10 PSI
- `event_type` column in CSV output

### Changed
- **Breaking**: CSV format now includes fifth column: `event_type`
- Console output disabled by default (enable with `--debug`)
- Debug logging interval changed from 5s to 60s (configurable)
- Simplified background operation

### Fixed
- Program now properly logs final event when terminated via kill signal
- Startup with high pressure no longer creates incomplete log entries
- STARTUP event type now correctly logged

## [1.1.0] - 2025-01-23

### Added
- Water volume estimation with configurable parameters
- `RESIDUAL_PRESSURE_SECONDS` constant
- `SECONDS_PER_GALLON` constant
- Estimated gallons column in CSV output
- Event summary output to console
- Documentation for calibration process

### Changed
- CSV format now logs complete events in single row (start, end, duration, gallons)
- Removed interim "pressure activated" CSV entries

## [1.0.0] - 2025-01-22

### Initial Release
- Pressure sensor monitoring on GPIO 17
- Event detection via polling (100ms intervals)
- CSV logging of pressure events
- Main log file with timestamped events
- Background operation support
- Graceful shutdown handling