# CHANGELOG

All notable changes to the pressure monitoring system.

## [2.0.0] - 2025-01-27

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