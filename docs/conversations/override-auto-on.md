# Conversation: Override Auto-On Feature
**Date:** December 20, 2025
**Version:** 2.11.0
**Session:** Add automatic override valve turn-on based on tank level threshold

---

## User Request

**User:** "I want a new configuration key/value. If set to a number (like 1350), if the level of the tank is less than this value, I want to turn on the override relay if it is not yet on. This makes the float just a backup, but that is fine. Doing this will keep my tank full more often by eliminating the large hysteresis defined by the physical float. Then let's update documentation and do a commit."

---

## Problem Analysis

The physical float switch has a large hysteresis:
- Float triggers refill at ~80% tank capacity (CLOSED/CALLING)
- Float stops refill at ~95% tank capacity (OPEN/FULL)

This causes the tank level to fluctuate significantly. The user wants tighter control by using precise tank level measurements from the PT sensor to trigger refills at a specific gallon threshold.

---

## Solution Implemented

### 1. Configuration
Added new configuration option in `monitor/config.py`:

```python
# Override Auto-On Configuration
OVERRIDE_ON_THRESHOLD = None  # Gallons at which to turn on override valve (None = disabled, e.g., 1350)
```

**Default:** `None` (disabled) for backward compatibility
**Example:** `1350` to turn on override when tank drops below 1350 gallons

### 2. Implementation Logic
Added auto-on check in `monitor/poll.py` tank polling section (lines 458-487):

```python
# Check for override auto-on (continuous enforcement)
if self.override_on_threshold is not None and self.relay_control_enabled:
    # Re-read config to allow runtime threshold changes
    from monitor import config
    import importlib
    importlib.reload(config)
    self.override_on_threshold = config.OVERRIDE_ON_THRESHOLD

    if self.override_on_threshold is not None:
        relay_status = self.get_relay_status()
        if (relay_status['supply_override'] == 'OFF' and
            self.state.tank_gallons is not None and
            self.state.tank_gallons < self.override_on_threshold):

            # Turn on override and log event
            from monitor.relay import set_supply_override
            if set_supply_override('ON', debug=self.debug):
                self.log_state_event('OVERRIDE_AUTO_ON',
                    f'Auto-on: tank at {self.state.tank_gallons:.0f} gal (threshold: {self.override_on_threshold})')

                # Send notification
                if NOTIFY_OVERRIDE_SHUTOFF and self.notification_manager.can_notify('override_auto_on'):
                    self.send_alert(
                        'NOTIFY_OVERRIDE_ON',
                        f"💧 Override Auto-On - {self.state.tank_gallons:.0f} gal",
                        f"Tank dropped to {self.state.tank_gallons:.0f} gal (threshold: {self.override_on_threshold}), override turned on",
                        priority='default'
                    )
```

**Key Features:**
- Runs every 60 seconds during tank polling
- Continuous enforcement (checks on every tank poll)
- Runtime threshold changes supported (reloads config)
- Logs all actions with `OVERRIDE_AUTO_ON` event type
- Sends notification alerts when triggered
- Runs BEFORE auto-shutoff check to ensure proper precedence

### 3. Integration with Existing Auto-Shutoff
Works seamlessly with existing `OVERRIDE_SHUTOFF_THRESHOLD`:

**Example Configuration:**
```python
OVERRIDE_ON_THRESHOLD = 1350      # Turn ON when tank < 1350 gallons
OVERRIDE_SHUTOFF_THRESHOLD = 1410 # Turn OFF when tank >= 1410 gallons
```

**Behavior:**
1. Tank drops to 1349 gal → Override turns ON automatically
2. Well fills tank to 1410 gal → Override turns OFF automatically
3. Tank drops to 1349 gal → Override turns ON again
4. Result: Tank stays between 1350-1410 gallons most of the time

**Float Switch Role:**
- Becomes backup safety mechanism
- Still active and functional
- Rarely triggers because tank stays full

---

## Files Modified

### 1. `monitor/config.py`
- Added `OVERRIDE_ON_THRESHOLD = None` configuration option

### 2. `monitor/poll.py`
- Imported `OVERRIDE_ON_THRESHOLD` from config
- Added `self.override_on_threshold` instance variable in `__init__`
- Implemented auto-on logic in tank polling section (before auto-shutoff check)

### 3. `README.md`
- Renamed section to "Automatic Override Control (Keep Tank Full)"
- Documented both auto-on and auto-shutoff features
- Added clear configuration examples
- Explained interaction between thresholds
- Updated configuration file template example
- Added `OVERRIDE_AUTO_ON` to event types list

### 4. `CHANGELOG.md`
- Added version 2.11.0 section
- Documented new feature with examples
- Listed all technical changes

---

## New Event Type

**OVERRIDE_AUTO_ON:** Automatic override valve turn-on when tank drops below threshold

Example event log entry:
```csv
2025-12-20 14:23:15.456,OVERRIDE_AUTO_ON,LOW,OPEN/FULL,1349,55.8,96.2,,OFF,ON,Auto-on: tank at 1349 gal (threshold: 1350)
```

---

## Usage Example

### Enable Auto-On
Edit `monitor/config.py`:
```python
OVERRIDE_ON_THRESHOLD = 1350      # Turn on at 1350 gallons
OVERRIDE_SHUTOFF_THRESHOLD = 1410 # Turn off at 1410 gallons
```

Restart service:
```bash
sudo systemctl restart pumphouse-monitor
```

### Disable Auto-On
Set to `None` in `monitor/config.py`:
```python
OVERRIDE_ON_THRESHOLD = None  # Disabled
```

### Runtime Threshold Adjustment
Simply edit `monitor/config.py` and save:
```python
OVERRIDE_ON_THRESHOLD = 1300  # Changed from 1350
```

The monitor reloads config on every tank poll, so changes take effect within 60 seconds (no restart needed).

---

## Benefits

1. **Fuller Tank:** Eliminates float switch hysteresis, keeps tank between precise thresholds
2. **Float as Backup:** Physical float still works as safety mechanism
3. **Precise Control:** Uses accurate PT sensor readings instead of binary float states
4. **Backward Compatible:** Disabled by default (`None`), opt-in feature
5. **Runtime Adjustable:** Change thresholds without service restart
6. **Well Documented:** README and CHANGELOG updated comprehensively

---

## Testing Recommendations

1. Set `OVERRIDE_ON_THRESHOLD` slightly above current tank level
2. Watch events.csv for `OVERRIDE_AUTO_ON` entry
3. Verify override relay turns on automatically
4. Wait for tank to fill to `OVERRIDE_SHUTOFF_THRESHOLD`
5. Verify override relay turns off automatically
6. Check notifications received (if enabled)

---

## Technical Notes

- Auto-on check runs BEFORE auto-shutoff check to ensure proper precedence
- Uses same notification system as other alerts
- Notification key `override_auto_on` allows independent cooldown tracking
- Config reload uses `importlib.reload()` for runtime changes
- Threshold comparison uses `<` (less than) for auto-on
- Works with existing persistent relay state (states survive restarts)
