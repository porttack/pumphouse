# CHANGELOG

All notable changes to the pressure monitoring system.

## [2.13.0] - 2025-12-21

### Fixed
- **Backflush Detection Reliability**: Removed hourly cooldown timer from backflush checks
  - Previous issue: Backflush events (15-30 min duration) were missed due to hourly check interval
  - Root cause: Hourly cooldown meant check could happen before/after brief backflush window
  - Solution: Now checks every snapshot (every 15 min) to catch brief events
  - Duplicate prevention still handled by `backflush_alerted_ts` timestamp tracking
  - Updated comments to explain why backflush needs frequent checking vs recovery/high-flow
- **Tank Read Failure Resilience**: Added retry logic before safety shutdown
  - Previous issue: Single network glitch would immediately shut off override valve
  - Root cause: No tolerance for transient internet failures
  - Solution: Requires 3 consecutive failures (3 minutes) before triggering safety shutdown
  - Tracks failure count and resets on successful read
  - Debug output shows failure progress: "Tank fetch failed (2/3)"
  - Alert message includes attempt count: "after 3 attempts"
  - Dramatically reduces false shutdowns while maintaining safety
- **False Recovery Alerts**: Reduced duplicate well recovery notifications during slow fill
  - Issue: Getting recovery alerts during continuous slow fill + tenant usage
  - Root cause: Slow fill (24 gal/hr) + tenant usage looked like stagnation periods
  - Solution: Increased `NOTIFY_WELL_RECOVERY_MAX_STAGNATION_GAIN` from 15 to 30 gallons
  - Now tolerates slow refill patterns without triggering false recovery alerts
- **Excessive Tank Stopped Filling Events**: Reduced sensitivity to normal slow refill
  - Issue: Getting TANK_STOPPED_FILLING events every 30-60 minutes during slow well recovery
  - Root cause: 60-minute window with 10-gallon threshold too sensitive for 6 gal/15min fill rate
  - Solution: Increased window to 120 minutes and threshold to 15 gallons
  - Now only alerts when tank truly stops filling, not during normal slow periods

### Changed
- **Backflush Detection Window**: Widened from 2 to 3 snapshots (30min → 45min)
  - Provides better detection of backflush events that may span multiple snapshots
  - Helps catch events that start/end between snapshot times
  - Configuration: `NOTIFY_BACKFLUSH_WINDOW_SNAPSHOTS = 3`
- **Backflush Notification Message**: Now includes timestamp of when backflush occurred
  - Old: "Carbon filter backflush used ~152 gallons"
  - New: "Carbon filter backflush at Sat 1:15 AM used ~152 gallons"
- **Well Recovery Notification Message**: Now includes when stagnation period ended
  - Old: "Tank gained 50+ gallons after stagnation period"
  - New: "Tank gained 50+ gallons after stagnation period ended Fri 9:00 PM"

### Technical
- Modified `check_backflush_status()` in notifications.py to remove cooldown check and return timestamp
- Modified `check_backflush_status()` return value: now returns 3-tuple `('backflush', gallons_used, timestamp)`
- Added `tank_fetch_failures` and `max_tank_failures` tracking to SimplifiedMonitor
- Enhanced tank polling logic with consecutive failure counting and recovery detection
- Enhanced notification messages to include human-readable timestamps (e.g., "Sat 1:15 AM")
- All changes maintain backward compatibility with existing configurations

### Investigation Notes
This release addresses issues discovered through log analysis on 2025-12-21:
1. **Missing backflush at 1:15am**: 117 gallons lost but not detected due to hourly check timing
2. **Recovery alert at 7:15am**: False positive from slow fill pattern (1132→1203 gal overnight)
3. **Many TANK_STOPPED_FILLING**: 8 events in 8 hours during normal slow refill
4. **Tank unreadable alert**: Single transient network failure triggered immediate safety shutdown
5. **Suspected high water usage**: Backflush every 2 days suggests 29 gal/hour usage (needs metering)

## [2.12.0] - 2025-12-21

### Added
- **Daily Status Email**: Automatic daily status email at configurable time
  - New configuration: `ENABLE_DAILY_STATUS_EMAIL` (default: True)
  - `DAILY_STATUS_EMAIL_TIME` - Time to send in HH:MM format (default: "06:00")
  - `DAILY_STATUS_EMAIL_CHART_HOURS` - Hours of history in chart (default: 72 = 3 days)
  - Sends comprehensive system status with full tank/sensor information
  - New event type: `DAILY_STATUS_EMAIL` logged when sent
- **Email Friendly Name**: Configurable sender name in email From field
  - New configuration: `EMAIL_FRIENDLY_NAME` (default: "Pumphouse")
  - Displays as "Pumphouse <email@example.com>" in inbox
  - Used in email footer: "{Name} Monitoring System"
- **Dashboard Link in Emails**: Prominent dashboard link at top of every email
  - Appears immediately below subject for quick access
  - Configurable URL via `DASHBOARD_EMAIL_URL` (optional)
  - Defaults to dashboard with `?hours=DAILY_STATUS_EMAIL_CHART_HOURS` parameter
  - Styled with green accent matching dashboard theme
- **Configurable Dashboard Default Time Range**: Web dashboard defaults to configurable time range
  - New configuration: `DASHBOARD_DEFAULT_HOURS` (default: 72 hours = 3 days)
  - Previously hardcoded to 6 hours
  - User can still override via URL parameter (?hours=24)

### Changed
- **Shortened Email Subject Lines**: More concise subjects with gallons first
  - Tank thresholds: "800 gal - Tank < 1000" (was "🚰 Tank Dropping - 800 gal")
  - Tank full: "1430 gal - Tank Full" (was "💧 Tank Full Confirmed - 1430 gal")
  - Override: "1410 gal - Override OFF" (was "⚠️ Override Auto-Shutoff - 1410 gal")
  - Well recovery: "1200 gal - Well Recovery" (was "💧 Well Recovery Detected - 1200 gal")
  - Well dry: "450 gal - Well May Be Dry" (was "⚠️ Well May Be Dry - 450 gal")
  - High flow: "1100 gal - High Flow 65 GPH" (was "💧 High Flow Detected - 1100 gal")
  - Backflush: "900 gal - Backflush" (was "🔧 Backflush Detected - 900 gal")
  - Prevents current gallons from being cut off on narrow phone displays
- **Email Template Enhancement**: Added dashboard link section with custom styling
  - Link appears in separate box below header
  - Uses dashboard green color (#4CAF50)
  - Clear visual hierarchy for quick access

### Technical
- Added `get_next_daily_status_time()` function to calculate daily email schedule
- Daily status email integrated into main polling loop (checked after snapshots)
- Email dashboard URL computed dynamically from config settings
- Web dashboard template now uses `{{ default_hours }}` from config
- All configuration changes backward compatible with existing setups

## [2.11.0] - 2025-12-20

### Added
- **Override Auto-On Feature**: Automatic override valve turn-on when tank drops below threshold
  - New configuration: `OVERRIDE_ON_THRESHOLD` (None = disabled, e.g., 1350)
  - Automatically turns ON override valve when tank level drops below threshold
  - Keeps tank fuller by eliminating large hysteresis of physical float switch
  - Physical float switch becomes backup safety mechanism
  - Continuous enforcement during tank polling (every 60 seconds)
  - Runtime threshold changes supported via config reload
  - New event type: `OVERRIDE_AUTO_ON` logged with tank level
  - Notification support: sends alerts when auto-on triggers
  - Works in conjunction with existing auto-shutoff feature
- **Configuration Example**: Set `OVERRIDE_ON_THRESHOLD = 1350` and `OVERRIDE_SHUTOFF_THRESHOLD = 1410`
  - Tank drops to 1349 gal → Override turns ON automatically
  - Tank fills to 1410 gal → Override turns OFF automatically
  - Result: Tank stays between 1350-1410 gallons most of the time
- **High Flow Detection (Fast Fill Mode)**: Detects when shared well's float activates
  - Alerts when tank filling rate exceeds configurable threshold (default: 60 GPH)
  - Averages flow rate over multiple snapshots to filter noise
  - Helps decide whether to manually adjust bypass relay based on occupancy
  - New event type: `NOTIFY_HIGH_FLOW`
  - Configuration: `NOTIFY_HIGH_FLOW_GPH`, `NOTIFY_HIGH_FLOW_WINDOW_HOURS`, `NOTIFY_HIGH_FLOW_AVERAGING`
- **Backflush Detection**: Automatically detects carbon filter backflush events
  - Detects large water usage (default: 50+ gallons) during configured time window
  - Default window: 12:00 AM - 4:30 AM (configurable)
  - Logs estimated water usage for tracking filter efficiency
  - New event type: `NOTIFY_BACKFLUSH`
  - Configuration: `NOTIFY_BACKFLUSH_THRESHOLD`, `NOTIFY_BACKFLUSH_TIME_START`, `NOTIFY_BACKFLUSH_TIME_END`
- **Tank Stopped Filling Event**: Tracks when tank transitions from filling to flat/declining
  - Uses 60-minute rolling window to smooth out sensor noise (±6 gallon fluctuations)
  - Only logs transition events (no alerts)
  - New event type: `TANK_STOPPED_FILLING`
  - Configuration: `TANK_FILLING_WINDOW_MINUTES`, `TANK_FILLING_THRESHOLD`

### Fixed
- **Well Recovery Alert Frequency**: Fixed duplicate well recovery alerts every 15-30 minutes
  - Root cause: Algorithm had NO actual stagnation check - only found low points in sliding window
  - During continuous slow fill, every snapshot showed different "low point", bypassing state tracking
  - New algorithm: Searches 24-hour window for 6+ hour stagnation periods (max 15 gal gain)
  - Then checks if tank gained 50+ gallons AFTER that stagnation
  - Returns stagnation start timestamp (stays constant for entire recovery event)
  - Separated core algorithm into `_find_recovery_in_data()` with comprehensive doctests
  - Added TEST 1: Slow continuous fill (should NOT trigger)
  - Added TEST 2: True recovery with stagnation followed by significant gain (SHOULD trigger)
  - Configuration: `NOTIFY_WELL_RECOVERY_THRESHOLD` (50 gal), `NOTIFY_WELL_RECOVERY_STAGNATION_HOURS` (6 hrs), `NOTIFY_WELL_RECOVERY_MAX_STAGNATION_GAIN` (15 gal)

### Changed
- **README Documentation**: Enhanced notification events section
  - Documents new high flow, backflush, and tank stopped filling features
  - Updated configuration examples with new parameters
  - Explains smart well recovery detection algorithm
- **Event Types**: Added `NOTIFY_HIGH_FLOW`, `NOTIFY_BACKFLUSH`, `TANK_STOPPED_FILLING`
- **Well Recovery Message**: Changed from "in last 24 hours" to "after stagnation period"

### Technical
- Enhanced `find_last_refill()` in stats.py with stagnation detection
  - Scans for minimum gallons during stagnation period
  - Returns low point timestamp (constant for entire recovery event)
  - Added `stagnation_hours` parameter
- Added `find_high_flow_event()` to stats.py
  - Calculates GPH using `tank_gallons_delta` from snapshots
  - Sliding window averaging over N snapshots
  - Returns first snapshot timestamp where high flow detected
- Added `find_backflush_event()` to stats.py
  - Detects negative `tank_gallons_delta` during time window
  - Time-of-day filtering for backflush window
  - Returns gallons used estimate
- Added high flow and backflush tracking to NotificationManager
  - `high_flow_alerted_ts` and `backflush_alerted_ts` state variables
  - Persistent state storage in `notification_state.json`
  - `check_high_flow_status()` and `check_backflush_status()` methods
- Added tank stopped filling logic to poll.py
  - Tracks `tank_gallons_history` over configurable window
  - Calculates net change to determine filling state
  - Logs transition from filling to not filling
- Added configuration parameters to config.py for all new features

## [2.10.0] - 2025-12-20

### Added
- **Persistent Relay State**: Relay states now survive service restarts
  - Override and bypass valve states saved to `relay_state.json`
  - States automatically restored on monitor startup
  - Prevents accidental override/bypass turn-off during service restarts or system reboots
  - State manager tracks last update timestamp for debugging
- **Persistent Notification State**: Notification tracking survives service restarts
  - Well recovery and well dry alert states saved to `notification_state.json`
  - Prevents duplicate "Well Recovery Detected" alerts after service restarts
  - Each unique 50+ gallon refill event triggers exactly ONE alert
- **Safety Override Shutoff**: Automatic override turn-off when tank level cannot be read
  - Prevents tank overflow during internet outages or PT sensor failures
  - Override valve turns off immediately if tank data fetch fails
  - Sends urgent notification explaining safety shutoff
  - Logs event as 'Safety shutoff: cannot read tank level'

### Fixed
- **Duplicate Well Recovery Alerts**: No longer sends repeated recovery alerts
  - Previous behavior: Alert sent every hour for same recovery after service restart
  - Root cause: Notification state only in memory, reset on restart
  - Solution: Persistent state tracks specific refill timestamps
  - Now: One alert per unique recovery event, regardless of restarts
- **Lost Relay States on Restart**: Relay states now persist across restarts
  - Previous behavior: Override/bypass would turn off on service restart
  - Solution: States saved to disk and restored automatically
  - Critical for maintaining override valve state during planned/unplanned restarts

### Technical
- Added `RelayStateManager` class for relay state persistence
- Added `_load_state()` and `_save_state()` to NotificationManager
- Modified `set_supply_override()` and `set_bypass()` to save state after changes
- Added `restore_relay_states()` function called during relay initialization
- Safety check added to tank polling loop for override shutoff
- Both state files excluded from git (added to .gitignore)

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