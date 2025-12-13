# CHANGELOG

All notable changes to the pressure monitoring system.

## [2.4.0] - 2025-12-12

### Added
- **systemd Service Files**: Production-ready service configurations for monitor and web dashboard
  - `pumphouse-monitor.service` - Monitor daemon with auto-restart
  - `pumphouse-web.service` - Web dashboard service
  - Auto-start on boot, auto-restart on failure
  - Proper logging via journalctl
  - Runs in virtual environment from project directory
- **Installation Script**: `install-services.sh` for easy service installation
  - Copies service files to /etc/systemd/system/
  - Provides clear instructions for enabling and managing services

### Changed
- **README**: Added systemd services as recommended deployment method
  - Documented service installation and management
  - Separated manual running into testing section

## [2.3.3] - 2025-12-12

### Added
- **Relay Control Script**: New `control.sh` script for safe relay control without Python GPIO conflicts
  - Uses `gpio` command-line tool instead of RPi.GPIO library
  - Commands: purge, bypass on/off, override on/off, status
  - Safe to use while monitor is running
  - Example: `./control.sh purge 15` or `./control.sh bypass on`
- **PURGE_DURATION Configuration**: Added configurable purge duration in config.py (default: 10 seconds)

### Changed
- **Purge Duration**: relay.py now uses PURGE_DURATION from config instead of hardcoded DEFAULT_PURGE_DURATION
- **purge_spindown_filter()**: Now defaults to config PURGE_DURATION when duration parameter is None

## [2.3.2] - 2025-12-12

### Fixed
- **Critical Purge Bug**: Fixed unwanted automatic purging on every water delivery
  - Purging was enabled by default because relay_control_enabled was set for status monitoring
  - Now separates relay monitoring (always on) from purge control (config setting)
  - Purge now respects ENABLE_PURGE config setting (default: False)
  - Added MIN_PURGE_INTERVAL to prevent excessive purging (default: 3600s = 1 hour)

### Changed
- **Purge Configuration**: Moved purge control from command-line flag to config file
  - Removed `--enable-purge` command-line argument
  - Added `ENABLE_PURGE` config setting (default: False)
  - Added `MIN_PURGE_INTERVAL` config setting (default: 3600 seconds)
  - Set ENABLE_PURGE=True in monitor.conf or config.py to enable purging
  - Purge now enforces minimum interval between purges
- **Startup Messages**: Debug mode now shows purge status and minimum interval

## [2.3.1] - 2025-12-12

### Fixed
- **Float Pin Configuration**: Corrected FLOAT_PIN from BCM 27 to BCM 21 in monitor/config.py
  - BCM 21 is the actual wired float sensor pin
  - BCM 27 was incorrectly configured and goes nowhere
- **Float Sensor Logic**: Fixed inverted float sensor logic in monitor/gpio_helpers.py
  - HIGH (1) now correctly means OPEN/FULL (tank is full)
  - LOW (0) now correctly means CLOSED/CALLING (tank needs water)
  - Logic now matches check.py behavior
- **Web Dashboard Sensors**: Fixed missing SENSORS section on web dashboard
  - Changed gpio_available from False to True in web.py
  - Sensors are readable via gpio command fallback even when monitor owns GPIO
  - Dashboard now displays Float Switch, Pressure, Pressure HIGH stats, and Temp/Humidity

### Added
- **Relay Status Warnings on Dashboard**: Web dashboard now displays relay warnings in sensor boxes
  - "⚠ OVERRIDE ON" appears in Float Switch box when supply override relay is active
  - "⚠ BYPASS ON" appears in Pressure box when bypass relay is active
  - Red warning badges provide immediate visibility of relay states

## [2.3.0] - 2025-12-09

### Added
- **Relay Status Monitoring**: Monitor now always reads and logs relay states to snapshots.csv
  - Added `get_all_relay_status()` function to relay.py for comprehensive relay reporting
  - Four relay channels: Bypass (BCM 26), Supply Override (BCM 19), Purge (BCM 13), Reserved (BCM 6)
- **Relay Reporting in check.py**: New RELAYS section displays all four relay channel states
  - Shows BCM pin numbers for easy hardware debugging
  - Uses `gpio` command fallback for multi-process compatibility
- **Multi-Process GPIO Support**: Implemented fallback mechanism using `gpio` command-line tool
  - Allows check.py and web.py to read sensors/relays while monitor is running
  - Prevents GPIO conflicts and "GPIO busy" errors
  - Added `_read_pin_via_gpio_command()` helper to gpio_helpers.py and relay.py

### Changed
- **poll.py**: Monitor now calls `enable_relay_control()` at startup regardless of `--enable-purge` flag
- **gpio_helpers.py**: `read_pressure()` and `read_float_sensor()` now fall back to `gpio` command when RPi.GPIO unavailable
- **relay.py**: `get_all_relay_status()` attempts RPi.GPIO first, falls back to `gpio` command
- **check.py**: Always displays SENSORS section even when GPIO init fails (uses fallback)

### Fixed
- **Critical Race Condition**: Fixed unreliable sensor readings caused by GPIO cleanup conflicts
  - web.py was calling `cleanup_gpio()` on every page load, releasing ALL GPIO pins system-wide
  - This would break the monitor's GPIO setup, causing intermittent sensor reading failures
  - web.py now uses `gpio` command fallback instead of initializing/cleaning up GPIO
- **Relay Status Reporting**: Fixed CSV logging showing "OFF" for all relays when bypass was actually ON
  - Monitor was only enabling relay control when `--enable-purge` was specified
  - Relay pins at 0 (LOW/ON) were being reported as OFF due to lack of GPIO access

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