# Conversation: Version 2.9.0 Enhancements
**Date:** December 14, 2025
**Version:** 2.9.0
**Session:** Enhanced email experience and human-friendly formatting

---

## User Requests and Changes Made

### 1. Camera Link in Emails
**User:** "Can we add this link in the emails too? It is a link to the camera that I have on the filters. I just want quick access. https://my.wyze.com/live"

**Changes:**
- Added camera button (📹 View Camera) next to dashboard link in all emails
- Button styled in blue-grey (#607D8B) to distinguish from other action buttons
- Links directly to Wyze camera feed

**Files Modified:**
- `monitor/email_notifier.py` - Added camera link button in email template

---

### 2. Configurable Event Count & Display Improvements
**User:** "In both cases, can we set the default number of events be set to 50?"

**Follow-up:** "That is 50 AFTER filtering, correct? What I really want is 7 days worth of events in the dashboard and emails."

**Changes:**
- Increased `DASHBOARD_MAX_EVENTS` from 20 to 500 events (~7 days after filtering)
- Calculated based on actual event frequency: ~674 events/day * 7 days
- Applied to both dashboard and email events tables
- Filtering excludes TANK_LEVEL events by default

**Files Modified:**
- `monitor/config.py` - Updated DASHBOARD_MAX_EVENTS = 500

---

### 3. Vertical Table Headers & Column Sizing
**User:** "Is there anyway to make the number of events shown in the dashboard configurable as well as make the labels vertical (so they take up less width) and scale based on the width of the values?"

**Changes:**
- Added vertical table headers using CSS `writing-mode: vertical-rl` with rotation
- First column (timestamps) now auto-sizes to content using `width: 1%` and `white-space: nowrap`
- Applied to both events and snapshots tables
- Works in both dashboard and emails

**Files Modified:**
- `monitor/templates/status.html` - Added vertical text CSS and updated table headers
- `monitor/email_notifier.py` - Added vertical header styles and table formatting

---

### 4. Recent Events in Emails
**User:** "And can we put the filtered events in the emails too?"

**Changes:**
- Created `get_recent_events()` function with filtering support
- Added events table section in email HTML with vertical headers
- Shows up to 500 events (configurable via DASHBOARD_MAX_EVENTS)
- Uses same filtering as dashboard (excludes TANK_LEVEL by default)
- Events appear after chart and before dashboard/camera links

**Files Modified:**
- `monitor/email_notifier.py` - Added get_recent_events(), updated fetch_system_status(), added events table HTML
- `monitor/config.py` - Imported DASHBOARD_HIDE_EVENT_TYPES and DASHBOARD_MAX_EVENTS

---

### 5. Gallons in Email Subject Lines
**User:** "I think all alerts should have the gallons in the subject line. It is what I care about most."

**Changes:**
All email notifications now include current tank gallons in subject line:
- Tank Filling: "🚰 Tank Filling - 1203 gal"
- Tank Dropping: "🚰 Tank Dropping - 875 gal"
- Tank Full: "💧 Tank Full Confirmed - 1450 gal"
- Override Shutoff: "⚠️ Override Auto-Shutoff - 1450 gal"
- Well Recovery: "💧 Well Recovery Detected - 1125 gal"
- Well Dry: "⚠️ Well May Be Dry - 450 gal"
- Test Email: "🏠 Pumphouse Email Test - 1319 gal"

**Files Modified:**
- `monitor/poll.py` - Updated all notification calls to include current_gal in subjects
- `monitor/email_notifier.py` - Updated test_email() to fetch and include tank gallons

---

### 6. Threshold Direction Indicators
**User:** "And the tank increasing and decreasing alerts should prominently say that the gallons is greater or less than (> or <) whatever threshold was crossed."

**Changes:**
- Decreasing: "Tank is now **< 1000 gallons** (currently at 875 gal)"
- Increasing: "Tank is now **> 750 gallons** (currently at 820 gal)"
- Makes threshold crossing direction immediately clear

**Files Modified:**
- `monitor/poll.py` - Updated tank threshold alert messages with > and < operators

---

### 7. Email Header Matches Subject
**User:** "All emails start with a big green bar with ℹ️ PUMPHOUSE ALERT. Maybe instead that green bar should be similar or the same as the subject of the alert."

**Changes:**
- Removed generic "ℹ️ PUMPHOUSE ALERT" from email header
- Subject now displayed directly in header (e.g., "🚰 Tank Filling - 1203 gal")
- Removed duplicate subject from alert box (now shows only message)
- Cleaner, less redundant email layout

**Files Modified:**
- `monitor/email_notifier.py` - Updated email HTML template header section

---

### 8. Remote Control Event Logging
**User:** "And when I clicked to set override on in the email, I expected a follow up email saying I did a REMOTE_CONTROL. We don't have that?"

**Investigation:**
- Verified REMOTE_CONTROL events ARE being logged to events.csv
- Found 3 recent events in log (all "Supply Override turned ON")
- Events appear in dashboard and emails with 500-event limit
- No follow-up email sent (added to TODO for future enhancement)

**Files Modified:**
- None (already working, added to TODO.md for email confirmation feature)

---

### 9. Human-Friendly Timestamps
**User:** "Finally, lets mutate the timestamp field in emails and the web interface to be 3-letter day-of-week and HH:MM. That is easier for me to process as a human."

**Changes:**
- Changed timestamp format from "2025-12-14 17:08:14.666" to "Sun 17:08"
- Format: 3-letter day abbreviation + HH:MM (24-hour)
- Applied to all events and snapshots tables
- Works in both dashboard and emails

**Implementation:**
- Created `human_time` Jinja filter for dashboard templates
- Created `format_human_time()` helper function for emails
- Applied to first column of all tables

**Files Modified:**
- `monitor/web.py` - Added human_time Jinja filter
- `monitor/email_notifier.py` - Added format_human_time() function, applied to events table
- `monitor/templates/status.html` - Applied filter to events and snapshots tables

---

### 10. TODO List Creation
**User:** "There are a few other things I want to do, but will wait until another day -- just need a TODO.md (or something similar)."

**Changes:**
- Created TODO.md with high/medium/low priority sections
- Included remote control email confirmation idea
- Added completed items from v2.9.0
- User added many additional enhancement ideas

**Files Modified:**
- `TODO.md` - Created new file with enhancement tracking

---

## Technical Summary

### New Functions
- `format_human_time(timestamp_str)` - Converts timestamps to "Day HH:MM" format
- `get_recent_events(filepath, max_rows, hide_types)` - Fetches filtered events for emails
- `human_time` Jinja filter - Template filter for timestamp formatting

### Configuration Changes
- `DASHBOARD_MAX_EVENTS` increased from 20 to 500
- Added comments explaining ~7 days coverage

### CSS Improvements
- Vertical text headers: `writing-mode: vertical-rl`, `transform: rotate(180deg)`
- Auto-sizing first column: `width: 1%`, `white-space: nowrap`
- Applied to both dashboard and email tables

### Files Modified (9 total)
1. `monitor/config.py` - Event count configuration
2. `monitor/poll.py` - Alert subject lines and message formatting
3. `monitor/email_notifier.py` - Camera link, events table, timestamp formatting, test email
4. `monitor/web.py` - Jinja filter for timestamps, config imports
5. `monitor/templates/status.html` - Vertical headers, timestamp filtering
6. `CHANGELOG.md` - v2.9.0 release notes
7. `README.md` - Version update to 2.9.0
8. `monitor/__init__.py` - Version update to 2.9.0
9. `TODO.md` - New file for tracking enhancements

---

## Service Restarts
Both services were restarted to apply changes:
```bash
sudo systemctl restart pumphouse-monitor pumphouse-web
```

## Testing
- Test email sent successfully showing new format
- Subject line: "🏠 Pumphouse Email Test - 1319 gal"
- Confirmed all features working

---

## Follow-up: Security Configuration Cleanup (December 16, 2025)

### 11. Remove Sensitive Data from config.py
**User:** "When I pushed the latest set of commits, gitguardian flagged config.py lines 44,45,46,48 as security risks. Can we reduce these to one comment (in a way that probably wont get flagged) and just document things in README and secrets.conf.template and any other relevant docs (like EMAIL_SETUP, etc). We are safe, but I'd rather not have this thing being flagged."

**Follow-up:** "Is config.py the only thing that needs to change? Doesn't EMAIL_SMTP_SERVER,PORT,USER also need to be removed and put into secrets.conf.template?"

**Changes:**
- Moved EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT, EMAIL_SMTP_USER from hardcoded values to secrets loading
- Updated secrets.conf.template to include all SMTP configuration fields
- Removed Gmail-specific references and App Password instructions from config.py
- Updated secrets loading code to handle EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT, EMAIL_SMTP_USER
- Updated user's actual secrets file with SMTP settings
- Conversation file renamed from date-based to descriptive: `email-ux-timestamps-events.md`

**Files Modified:**
- `monitor/config.py` - Removed hardcoded SMTP values, updated secrets loading
- `secrets.conf.template` - Added EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT, EMAIL_SMTP_USER
- `~/.config/pumphouse/secrets.conf` - Added SMTP settings (not in git)
- `docs/conversations/` - Renamed file for better discoverability

**Rationale:**
- Security scanners flag SMTP server/user patterns as potential credentials
- Moving to secrets file prevents false positives in security scans
- Better separation of sensitive configuration from code
- Consistent with existing pattern for EMAIL_SMTP_PASSWORD

---

## Future Work (from TODO.md)

### High Priority
- Email confirmation for remote control actions
- Turn off override if tank level cannot be read
- Daily summary email option
- Automatic override control based on tank level
- Fix duplicate "Recovery Detected" alerts

### Medium Priority
- Change email from address
- Shorten email subject lines
- Improve float confirmation logic (3 tank reads vs heartbeats)
- Clarify "Tank Filling" alert meaning
- Log backflush events
- Estimate GPH and chlorine use
- Various UI improvements (6-day view, graph improvements, mobile widget)
