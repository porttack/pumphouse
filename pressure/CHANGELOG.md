# CHANGELOG

All notable changes to the pressure monitoring system.

## [1.2.0] - 2025-01-23

**Development conversation**: https://claude.ai/share/5a41cbd7-563e-4a13-8891-07fe08c14df7

### Added
- Shebang line (`#!/usr/bin/env python3`) - can now run as `./pressure_monitor.py`
- Signal handlers for graceful shutdown on SIGINT (Ctrl+C) and SIGTERM (kill)
- Four event types in CSV logging:
  - `NORMAL`: Pressure cycle completed normally
  - `SHUTDOWN`: Program stopped while pressure was high
  - `STARTUP`: Pressure was already high at program start
  - `MAXTIME`: Pressure checkpoint after 30 minutes of continuous high pressure
- `--debug` flag for console output (quiet mode by default)
- `--debug-interval SECONDS` parameter to configure console logging interval (default: 60s)
- Automatic logging every 30 minutes (1800s) when pressure remains high
- Graceful handling of startup when pressure is already ≥10 PSI
- `event_type` column in CSV output
- Internal tracking flag for startup activations to ensure correct event type logging

### Changed
- **Breaking**: CSV format now includes fifth column: `event_type`
- Console output now disabled by default (enable with `--debug`)
- When pressure stays high for 30+ minutes, logs checkpoint and resets timer
- Debug logging interval changed from 5 seconds to 60 seconds (configurable)
- Simplified background operation - no need for `2>&1` redirection in quiet mode
- Signal handling moved to PressureMonitor class for cleaner architecture

### Fixed
- Program now properly logs final event when terminated via kill signal
- Startup with high pressure no longer creates incomplete log entries
- STARTUP event type now correctly logged (was being logged as NORMAL)

## [1.1.0] - 2025-01-23

### Added
- Water volume estimation with configurable parameters
- `RESIDUAL_PRESSURE_SECONDS` constant for residual pressure time
- `SECONDS_PER_GALLON` constant for pumping rate calculation
- Estimated gallons column in CSV output
- Event summary output to console showing duration and gallons
- Documentation for calibration process

### Changed
- CSV format now logs complete events in single row (start, end, duration, gallons)
- Removed separate PRESSURE_ON and PRESSURE_OFF rows
- CSV header updated: `pressure_on_time, pressure_off_time, duration_seconds, estimated_gallons`
- Improved help text with water estimation details

### Fixed
- CSV file creation now uses 'x' mode to avoid overwriting headers

## [1.0.0] - 2025-01-23

### Added
- Initial release of `pressure_monitor.py`
- Continuous pressure monitoring on GPIO17
- Text log with 5-second intervals
- CSV event logging with timestamps and durations
- State change detection and alerts
- Terminal beep on pressure changes
- Background operation support (nohup, screen, tmux)
- Command line arguments for log file configuration
- `test_pressure.py` for hardware verification
- Comprehensive help documentation
- Support for graceful shutdown (Ctrl+C)

### Hardware
- NC pressure switch on GPIO17 (physical pin 11)
- Ground connection on physical pin 9
- Internal pull-up resistor configuration
- Support for 100-foot wire runs

## Format

This changelog follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format.

### Categories
- **Added** for new features
- **Changed** for changes in existing functionality
- **Deprecated** for soon-to-be removed features
- **Removed** for now removed features
- **Fixed** for any bug fixes
- **Security** for vulnerability fixes