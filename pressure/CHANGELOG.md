# Changelog

All notable changes to the pressure monitoring system.

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