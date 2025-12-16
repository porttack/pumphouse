# CHANGELOG

All notable changes to the pressure monitoring system.

## [2.9.0] - 2025-12-14

### Added
- **Camera Link in Emails**: Quick access button to Wyze camera feed in all email alerts
- **Recent Events Table in Emails**: Full events table with filtering in every email
  - Shows up to 500 events (~7 days) after filtering
  - Same filtering as dashboard (excludes TANK_LEVEL by default)
  - Vertical column headers to save horizontal space
  - Auto-sizing first column for compact display
- **Human-Friendly Timestamps**: Easier to read date/time format throughout
  - Format: "Mon 14:23" (3-letter day + HH:MM)
  - Applied to all events and snapshots tables in dashboard and emails
  - More intuitive for quick scanning

### Changed
- **Gallons in All Email Subjects**: Tank level prominently displayed in subject lines
  - Tank Filling: "🚰 Tank Filling - 1203 gal"
  - Tank Dropping: "🚰 Tank Dropping - 875 gal"
  - Tank Full: "💧 Tank Full Confirmed - 1450 gal"
  - Override Shutoff: "⚠️ Override Auto-Shutoff - 1450 gal"
  - Well Recovery: "💧 Well Recovery Detected - 1125 gal"
  - Well Dry: "⚠️ Well May Be Dry - 450 gal"
  - Test Email: "🏠 Pumphouse Email Test - 1319 gal"
- **Improved Tank Threshold Alerts**: Clear indication of threshold direction
  - Decreasing: "Tank is now < 1000 gallons (currently at 875 gal)"
  - Increasing: "Tank is now > 750 gallons (currently at 820 gal)"
- **Email Header Optimization**: Subject now displayed in header instead of generic "PUMPHOUSE ALERT"
  - Reduces redundancy in email layout
  - Makes subject immediately visible
- **Dashboard Table Improvements**: Better space utilization
  - Vertical column headers reduce width requirements
  - First column (timestamp) auto-sizes to content
  - Tables can fit more data on screen
- **Increased Event History**: Dashboard and emails now show up to 500 events (previously 20)
  - Covers approximately 7 days of filtered event history
  - Configurable via `DASHBOARD_MAX_EVENTS` in config.py

### Technical
- Added `human_time` Jinja filter for timestamp formatting in web dashboard
- Added `format_human_time()` helper function for email timestamp formatting
- Enhanced `fetch_system_status()` to include recent events data
- Updated `get_recent_events()` to support filtering and configurable limits
- CSS improvements for vertical text and auto-sizing columns in both dashboard and emails

## [2.8.0] - 2025-12-14

### Added
- **Remote Control via Email**: One-click control buttons in email alerts
  - Secret URL tokens for unauthenticated access to relay controls
  - Five quick action buttons in every email: Override ON/OFF, Bypass ON/OFF, Purge Now
  - Secure 256-bit random tokens stored in `~/.config/pumphouse/secrets.conf`
  - Web endpoint `/control/<token>` for executing actions
  - All remote actions logged to events.csv with `REMOTE_CONTROL` event type
  - Success page with auto-redirect to dashboard after action
- **Enhanced Email Notifications**: Relay status warnings and full system status
  - Prominent warning boxes when Override or Bypass is ON
  - Full system status in all email alerts (tank, sensors, stats, relays)
  - Fixed email alerts to include same rich content as test emails
- **Dashboard Improvements**: Event filtering and startup time tracking
  - `DASHBOARD_HIDE_EVENT_TYPES` config to filter noisy events from Recent Events table
  - TANK_LEVEL events now hidden by default (can be configured)
  - Web server startup time displayed at bottom of dashboard
- **Event Logging**: All notifications now create events.csv entries
  - New event types: `NOTIFY_TANK_*`, `NOTIFY_FLOAT_FULL`, `NOTIFY_OVERRIDE_OFF`, `NOTIFY_WELL_RECOVERY`, `NOTIFY_WELL_DRY`
  - `REMOTE_CONTROL` event type for all secret URL actions
  - Consolidated `send_alert()` helper ensures all alerts are logged

### Changed
- **Relay Control**: Added `set_bypass()` function matching `set_supply_override()` pattern
  - Uses gpio command to avoid multi-process conflicts
  - Includes debug logging and error handling
- **Secrets Configuration**: Extended to include remote control tokens
  - Five new optional secret tokens for relay/purge control
  - Template updated with token generation instructions
  - Tokens only loaded if configured (optional feature)
- **Notification System**: Improved alert logging consistency
  - All alerts logged before sending (prevents missed events)
  - Full status data included in all email notifications via `include_status=True`

### Fixed
- Email alerts now include full system status (previously only test emails had this)
- Dashboard Recent Events table respects event type filter configuration

## [2.7.0] - 2025-12-14

### Added
- **Email Notifications**: Full HTML email alerts with comprehensive system status
  - Rich HTML emails styled like the web dashboard with dark theme
  - Includes tank level bar, current stats (depth, gallons, percentage)
  - Shows 1-hour and 24-hour tank changes with color coding
  - Displays sensor status (float switch, pressure) with visual indicators
  - Embeds tank level history chart directly in email
  - Link to full web dashboard
  - Sent for all alert types: tank thresholds, well recovery, well dry, override shutoff, float confirmations
  - Test command: `python -m monitor.check --test-email`
- **Secure Credential Storage**: Email SMTP password stored in `~/.config/pumphouse/secrets.conf`
  - Template file provided: `secrets.conf.template`
  - Secrets file excluded from git via `.gitignore`
  - Automatic loading from secure location
- **Email Setup Guide**: New `EMAIL_SETUP.md` with Gmail App Password instructions
  - Step-by-step Gmail configuration
  - Support for other email providers (Outlook, Yahoo, custom)
  - Troubleshooting tips

### Changed
- **Config**: Added email notification configuration
  - `ENABLE_EMAIL_NOTIFICATIONS` - Enable email alerts (default: True)
  - `EMAIL_TO` - Recipient email address
  - `EMAIL_FROM` - Sender email address
  - `EMAIL_SMTP_SERVER` - SMTP server (default: smtp.gmail.com)
  - `EMAIL_SMTP_PORT` - SMTP port (default: 587 for TLS)
  - `EMAIL_SMTP_USER` - SMTP username
  - `EMAIL_SMTP_PASSWORD` - Now loaded from secrets file instead of being in config
- **Notification System**: Improved well recovery alert logic
  - Now tracks specific refill event timestamps
  - Only sends alert once per recovery event (no hourly repeats)
  - Prevents notification spam when recovery is detected

### Fixed
- Well recovery alerts no longer sent every hour for the same refill event

## [2.6.0] - 2025-12-12

### Added
- **Push Notifications via ntfy.sh**: Real-time alerts on your phone for critical tank events
  - Tank level threshold crossings (both increasing and decreasing) with bounce protection
  - Well recovery detection (50+ gallon gain in 24 hours)
  - Well dry alert (no refill in configurable days, default: 4)
  - Float sensor confirmation (CLOSED→OPEN for 3+ consecutive readings)
  - Override shutoff alerts (when automatic overflow protection triggers)
  - Configurable notification rules and cooldown periods (default: 5 min between same alerts)
  - Support for both public ntfy.sh and self-hosted ntfy servers
  - Test command: `python -m monitor.check --test-notification`
- **Shared Statistics Module**: New `monitor/stats.py` module for reusable analytics
  - `find_last_refill()` function extracts refill detection logic from web.py
  - Used by both web dashboard and notification system
- **Notification Rule Engine**: New `monitor/notifications.py` with `NotificationManager` class
  - Intelligent state tracking to prevent notification spam
  - Bounce protection: won't re-alert if tank level fluctuates near thresholds
  - Separation of concerns: rule evaluation separate from sending mechanism
  - Designed for future expansion (Web Push, email, SMS, etc.)
- **ntfy.sh Sender**: New `monitor/ntfy.py` module for ntfy.sh integration
  - Simple HTTP POST client for push notifications
  - Configurable server URL and topic
  - `test_ping()` function for testing integration

### Changed
- **Config**: Added comprehensive notification configuration
  - `ENABLE_NOTIFICATIONS` - Master switch (default: False for safety)
  - `NTFY_SERVER` - Server URL (default: https://ntfy.sh)
  - `NTFY_TOPIC` - Unique topic name (must be configured)
  - `NOTIFY_TANK_DECREASING` - Alert levels when tank drops (default: [1000, 750, 500, 250])
  - `NOTIFY_TANK_INCREASING` - Alert levels when tank fills (default: [500, 750, 1000, 1200])
  - `NOTIFY_WELL_RECOVERY_THRESHOLD` - Gallons gained to count as recovery (default: 50)
  - `NOTIFY_FLOAT_CONFIRMATIONS` - Consecutive OPEN readings before alert (default: 3)
  - `NOTIFY_WELL_DRY_DAYS` - Days without refill before alert (default: 4)
  - `NOTIFY_OVERRIDE_SHUTOFF` - Alert on auto-shutoff (default: True)
  - `MIN_NOTIFICATION_INTERVAL` - Cooldown between same alerts (default: 300 seconds)
- **Poll Module**: Integrated notification checks at key points
  - After tank level changes: checks threshold crossings
  - After float state changes: tracks confirmation pattern
  - In snapshot cycle: checks well recovery/dry status
  - After override shutoff: sends alert notification
- **Web Dashboard**: Refactored to use shared stats module
  - Removed duplicate refill detection code
  - Now uses `find_last_refill()` from stats.py
- **Check Command**: Added `--test-notification` flag
  - Sends test notification to verify ntfy integration
  - Provides helpful setup instructions if test fails
- **README**: Extensive documentation for push notifications
  - Setup instructions for ntfy app (iOS, Android, web)
  - Configuration examples and customization options
  - Notification event descriptions
  - Self-hosting ntfy server instructions

### Technical Details
- Notification system runs in same process as monitor (no threading)
- All notifications also logged to events.csv for audit trail
- Notification failures don't crash monitoring loop
- State tracking persists across notification manager lifecycle
- Uses existing `requests` library (already dependency for tank scraping)

## [2.5.0] - 2025-12-12

### Added
- **Automatic Override Shutoff**: Overflow protection to prevent tank overflow when override valve is enabled
  - Automatically turns off override valve when tank reaches configurable threshold
  - Default threshold: 1450 gallons (configurable via `OVERRIDE_SHUTOFF_THRESHOLD`)
  - Continuous enforcement: checks every 60 seconds during tank polling
  - Runtime threshold changes: reloads config on each check (no restart needed)
  - Uses gpio command for relay control to avoid multi-process GPIO conflicts
  - Logs all automatic shutoff events with tank level for audit trail
  - Enabled by default (disable via `ENABLE_OVERRIDE_SHUTOFF = False`)

### Changed
- **Config**: Added `ENABLE_OVERRIDE_SHUTOFF` and `OVERRIDE_SHUTOFF_THRESHOLD` settings
- **Relay Module**: Added `set_supply_override()` function using gpio command for safe multi-process relay control

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

### Fixed
- **systemd Service GPIO Access**: Fixed web dashboard unable to read sensors when running as service
  - Disabled `NoNewPrivileges=true` in both service files to allow SUID gpio command to work
  - gpio command requires root privileges via SUID to access GPIO pins
  - Web dashboard sensors now display correctly when services are running

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