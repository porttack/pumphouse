# Conversation: Persistent State Management and Safety Features
**Date:** December 20, 2025
**Version:** 2.10.0
**Session:** Fix duplicate well recovery alerts, persistent relay states, and safety override shutoff

---

## User Requests and Changes Made

### 1. Fix Duplicate Well Recovery Alerts
**User:** "Why am I getting 'Well Recovery Detected' messages every hour or so? I just want one message once the tank goes from not getting water for some period of time (defined by either flat or decreasing water level) to having received 50 or more gallons. But once that state is reached (having received 50 gallons), I don't need the message again every hour. We might want to write state to disk."

**Problem Analysis:**
- `check_refill_status()` was checking if last refill occurred within the last hour
- `find_last_refill()` returns the MOST RECENT 50+ gallon refill (could be 8 hours ago)
- `well_recovery_alerted_ts` was only stored in memory
- Service restarts cleared the timestamp, causing re-alerts for old refills
- Hourly checks kept finding the same refill event

**Solution Implemented:**
- Added persistent state storage to `notification_state.json`
- Created `_load_state()` and `_save_state()` methods in NotificationManager
- Store timestamp of last alerted recovery as ISO string (JSON-compatible)
- Removed 1-hour window check (was causing false positives)
- Now tracks specific refill events by their unique timestamp
- Alert triggers only once per unique refill, regardless of service restarts

**Files Modified:**
- `monitor/notifications.py` - Added persistent state management, load/save methods
- `.gitignore` - Added notification_state.json

**Result:** Each 50+ gallon recovery will trigger exactly ONE alert, even across service restarts.

---

### 2. Persistent Relay States Across Restarts
**User:** "Can we store the state of the override and bypass settings in our state file too? That way the override will not get turned off everytime we restart our daemon."

**Changes:**
- Created new `monitor/relay_state.py` module with `RelayStateManager` class
- State stored in `relay_state.json` with supply_override, bypass, and last_updated
- Modified `set_supply_override()` to call `get_state_manager().set_supply_override(state)`
- Modified `set_bypass()` to call `get_state_manager().set_bypass(state)`
- Created `restore_relay_states()` function to restore states on startup
- Updated `enable_relay_control()` in poll.py to call `restore_relay_states()`

**Files Created:**
- `monitor/relay_state.py` - New state management module

**Files Modified:**
- `monitor/relay.py` - Added state manager import, save state after each change, restore function
- `monitor/poll.py` - Call restore_relay_states() during relay initialization
- `.gitignore` - Added relay_state.json

**Result:** Override and bypass states persist across service restarts and system reboots.

---

### 3. Safety Override Shutoff on Tank Read Failure
**User:** "Also, if we ever cannot read the tank level (maybe because the internet is out), please turn off override -- otherwise we risk overflowing the tank."

**Changes:**
- Added safety check in tank polling loop after `fetch_tank_data()`
- If fetch fails AND override is ON, immediately turn off override
- Logs event as 'Safety shutoff: cannot read tank level (possible internet outage)'
- Sends urgent priority notification explaining safety shutoff
- Uses separate notification key 'override_shutoff_safety' to avoid cooldown conflicts

**Files Modified:**
- `monitor/poll.py` - Added safety check after tank fetch failure

**Safety Logic:**
```python
if not tank_fetch_success and self.relay_control_enabled:
    relay_status = self.get_relay_status()
    if relay_status['supply_override'] == 'ON':
        # Turn off override and send urgent alert
```

**Result:** Override automatically turns off if tank level cannot be read, preventing overflow during network outages.

---

## Technical Summary

### New Files
- `monitor/relay_state.py` - Relay state persistence manager

### Modified Files (7 total)
1. `monitor/notifications.py` - Persistent notification state
2. `monitor/relay.py` - Save/restore relay states
3. `monitor/relay_state.py` - New state manager class
4. `monitor/poll.py` - Restore states on startup, safety shutoff check
5. `.gitignore` - Exclude state files from git
6. `TODO.md` - Mark completed items
7. `CHANGELOG.md` - Document v2.10.0 changes

### State Files Created
Both files are excluded from git:
- `notification_state.json` - Well recovery/dry alert state
- `relay_state.json` - Override and bypass valve states

### Version Update
- Updated from 2.9.0 to 2.10.0
- All version files updated (monitor/__init__.py, README.md, CHANGELOG.md)

---

## Testing
Service restarted successfully:
- `notification_state.json` created at startup with existing recovery timestamp
- `relay_state.json` created with OFF/OFF states
- Both states persist across service restarts
- No errors in service logs

---

## Future Enhancements (from user)
User mentioned wanting:
- Support for alerting on 200+ gallon recovery (secondary threshold)
- Currently marked as lower priority
