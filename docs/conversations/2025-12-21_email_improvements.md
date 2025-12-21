# Email Notification Improvements - 2025-12-21

## Summary

Implemented four major email notification enhancements to improve usability and information access:
1. Daily status email at configurable time (default 6am)
2. Configurable email friendly name for better inbox recognition
3. Shortened email subject lines to prevent cutoff on mobile
4. Dashboard link at top of emails for quick access
5. Configurable default time range for web dashboard

## Changes Made

### 1. Configuration (monitor/config.py)

**New Email Settings:**
```python
EMAIL_FRIENDLY_NAME = "Pumphouse"  # Friendly name in From field
ENABLE_DAILY_STATUS_EMAIL = True   # Send daily status email
DAILY_STATUS_EMAIL_TIME = "06:00"  # Time in HH:MM format
DAILY_STATUS_EMAIL_CHART_HOURS = 72  # 3 days of history
```

**New Dashboard Settings:**
```python
DASHBOARD_EMAIL_URL = None  # Custom URL for email links (optional)
DASHBOARD_DEFAULT_HOURS = 72  # Default time range for web dashboard
```

### 2. Email Notifications (monitor/email_notifier.py)

**Friendly Name Support:**
- Updated From field to use: `"Pumphouse <email@example.com>"` format
- Updated footer to use friendly name: `"{Name} Monitoring System"`

**Dashboard Link:**
- Added prominent dashboard link at top of every email
- Link appears immediately below subject in separate styled box
- Uses configurable URL or defaults to `?hours=DAILY_STATUS_EMAIL_CHART_HOURS`
- Styled with green accent (#4CAF50) matching dashboard theme

### 3. Shortened Subject Lines (monitor/poll.py)

**Before → After:**
- `"🚰 Tank Dropping - 800 gal"` → `"800 gal - Tank < 1000"`
- `"💧 Tank Full Confirmed - 1430 gal"` → `"1430 gal - Tank Full"`
- `"⚠️ Override Auto-Shutoff - 1410 gal"` → `"1410 gal - Override OFF"`
- `"💧 Well Recovery Detected - 1200 gal"` → `"1200 gal - Well Recovery"`
- `"⚠️ Well May Be Dry - 450 gal"` → `"450 gal - Well May Be Dry"`
- `"💧 High Flow Detected - 1100 gal"` → `"1100 gal - High Flow 65 GPH"`
- `"🔧 Backflush Detected - 900 gal"` → `"900 gal - Backflush"`

**Rationale:**
- Gallons now appear first to prevent cutoff on narrow phone displays
- More concise language while preserving essential information
- Removed redundant emoji/text (e.g., "Detected" was implied)

### 4. Daily Status Email (monitor/poll.py)

**Implementation:**
- Added `get_next_daily_status_time()` function to calculate scheduling
- Integrated into main polling loop (checked after snapshot cycle)
- Sends comprehensive status email with:
  - Current tank level
  - Full sensor status
  - Last 3 days of history (configurable)
  - All standard email features (charts, stats, etc.)
- Logs event as `DAILY_STATUS_EMAIL` in events.csv
- Automatically schedules next email for same time tomorrow

**Schedule Calculation:**
- Parses `HH:MM` format from config (default: "06:00")
- If current time is past today's target, schedules for tomorrow
- Handles edge cases (invalid format defaults to 6am)

### 5. Web Dashboard Default (monitor/web.py, monitor/templates/status.html)

**Changes:**
- Web server now passes `DASHBOARD_DEFAULT_HOURS` to template
- Template uses configurable default instead of hardcoded 6 hours
- User can still override via URL parameter (?hours=24)
- Defaults to 72 hours (3 days) for consistency with email charts

## Files Modified

1. **monitor/config.py** - Added 5 new configuration parameters
2. **monitor/email_notifier.py** - Added friendly name, dashboard link, updated footer
3. **monitor/poll.py** - Shortened all subject lines, added daily status email logic
4. **monitor/web.py** - Pass default_hours to template
5. **monitor/templates/status.html** - Use configurable default
6. **README.md** - Updated documentation with new features
7. **CHANGELOG.md** - Added version 2.12.0 entry
8. **monitor/__init__.py** - Bumped version to 2.12.0

## Testing

All Python files pass syntax validation:
```bash
python3 -m py_compile monitor/config.py monitor/email_notifier.py monitor/poll.py monitor/web.py
```

## Configuration Examples

**Minimal (using defaults):**
```python
EMAIL_FRIENDLY_NAME = "Pumphouse"
ENABLE_DAILY_STATUS_EMAIL = True
DAILY_STATUS_EMAIL_TIME = "06:00"
```

**Custom setup:**
```python
EMAIL_FRIENDLY_NAME = "Blackberry Hill Water"
ENABLE_DAILY_STATUS_EMAIL = True
DAILY_STATUS_EMAIL_TIME = "07:30"  # 7:30am
DAILY_STATUS_EMAIL_CHART_HOURS = 168  # 7 days
DASHBOARD_DEFAULT_HOURS = 168  # Match email chart
DASHBOARD_EMAIL_URL = "https://example.com/?hours=168"  # Custom URL
```

## Backward Compatibility

All changes are backward compatible:
- New config parameters have sensible defaults
- Existing configurations continue to work without modification
- Daily status email can be disabled with `ENABLE_DAILY_STATUS_EMAIL = False`
- Friendly name defaults to empty (uses plain email address if not configured)

## User Impact

**Benefits:**
1. **Daily Status Email** - Proactive monitoring without needing to check dashboard
2. **Friendly Name** - Better inbox organization and recognition
3. **Shorter Subjects** - No more cut-off gallons on mobile devices
4. **Dashboard Link** - One-tap access from any email notification
5. **Configurable Defaults** - Dashboard and emails show consistent time range

**Migration:**
No action required - all features work with existing setups. Users can optionally configure new parameters to customize behavior.

## Version

Released as version 2.12.0 on 2025-12-21.
