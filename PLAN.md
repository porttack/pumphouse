# Implementation Plan: Automatic Override Shutoff

## Overview
Implement automatic override valve shutoff when tank reaches configurable threshold to prevent overflow.

## Requirements
- Continuous enforcement: Auto-shutoff override whenever tank >= threshold
- Check every 60 seconds (existing tank polling frequency)
- Configurable threshold (default: 1450 gallons)
- Log shutoff events
- Re-read config on each check (allows runtime threshold changes)

## Implementation Steps

### 1. Add Configuration (monitor/config.py)
Add new constants after existing relay configuration:
```python
# Override Shutoff Configuration
ENABLE_OVERRIDE_SHUTOFF = True  # Enable automatic override shutoff to prevent overflow
OVERRIDE_SHUTOFF_THRESHOLD = 1450  # Gallons at which to turn off override valve
```

### 2. Add Relay Control Function (monitor/relay.py)
Add function to control override relay using gpio command (not RPi.GPIO):
```python
def set_supply_override(state, debug=False):
    """
    Turn supply override valve ON or OFF using gpio command.

    Args:
        state: 'ON' or 'OFF'
        debug: Print debug messages

    Returns:
        True if successful, False otherwise
    """
    import subprocess

    if state not in ['ON', 'OFF']:
        return False

    # Active-low relay: 0=ON, 1=OFF
    value = '0' if state == 'ON' else '1'

    try:
        # Use gpio command to avoid multi-process conflicts
        subprocess.run(['gpio', '-g', 'write', '19', value],
                      capture_output=True, text=True, check=True, timeout=2)
        if debug:
            print(f"Supply override turned {state}")
        return True
    except Exception as e:
        print(f"Error setting supply override: {e}", file=sys.stderr)
        return False
```

### 3. Update Monitor Main Loop (monitor/poll.py)

**A. Add instance variables in __init__():**
```python
# Override shutoff control
self.enable_override_shutoff = ENABLE_OVERRIDE_SHUTOFF
self.override_shutoff_threshold = OVERRIDE_SHUTOFF_THRESHOLD
```

**B. Add shutoff check in run() loop after tank data fetch (around line 340):**
```python
# Check for override shutoff (continuous enforcement)
if self.enable_override_shutoff and self.relay_control_enabled:
    # Re-read config to allow runtime changes
    from monitor import config
    import importlib
    importlib.reload(config)
    self.override_shutoff_threshold = config.OVERRIDE_SHUTOFF_THRESHOLD

    relay_status = self.get_relay_status()
    if (relay_status['supply_override'] == 'ON' and
        self.state.tank_gallons is not None and
        self.state.tank_gallons >= self.override_shutoff_threshold):

        if self.debug:
            print(f"  → Tank at {self.state.tank_gallons} gal (>= {self.override_shutoff_threshold}), turning off override...")

        from monitor.relay import set_supply_override
        if set_supply_override('OFF', debug=self.debug):
            self.log_state_event('OVERRIDE_SHUTOFF',
                f'Auto-shutoff: tank at {self.state.tank_gallons} gal (threshold: {self.override_shutoff_threshold})')
```

### 4. Update CHANGELOG.md
Add v2.5.0 entry documenting new overflow protection feature.

### 5. Update README.md
Document the new configuration options and behavior.

## Key Design Decisions

1. **Use gpio Command, Not RPi.GPIO**: Avoids multi-process GPIO conflicts by using subprocess to call `gpio -g write 19 <value>`.

2. **Continuous Enforcement**: Checks happen every 60 seconds; if override is ON and tank >= threshold, it gets turned off. User can manually turn it back on, but it will be turned off again on next check.

3. **Config Reload**: Reload config.py on each check so threshold can be changed without restarting service.

4. **Null Safety**: Check `tank_gallons is not None` before comparison (handles PT website being down).

5. **Uses Existing Patterns**:
   - Follows purge logic pattern for automatic control
   - Uses existing relay status checking and logging infrastructure
   - Respects `relay_control_enabled` flag
   - Similar to gpio command approach used in gpio_helpers.py for reading pins

6. **Location**: Implemented in tank polling section (lines ~340) for 60-second responsiveness.

## Files to Modify
1. `/home/pi/src/pumphouse/monitor/config.py` - Add configuration
2. `/home/pi/src/pumphouse/monitor/relay.py` - Add set_supply_override() using gpio command
3. `/home/pi/src/pumphouse/monitor/poll.py` - Add shutoff logic
4. `/home/pi/src/pumphouse/CHANGELOG.md` - Document feature
5. `/home/pi/src/pumphouse/README.md` - Document configuration

## Testing Plan
1. Set threshold to low value (e.g., 100 gallons)
2. Turn on override manually: `./control.sh override on`
3. Wait 60 seconds for next tank poll
4. Verify override is turned off automatically
5. Check events.csv for OVERRIDE_SHUTOFF log entry
6. Check `./control.sh status` to confirm override is OFF
7. Change threshold in config.py
8. Verify new threshold takes effect on next check (no restart needed)

## Safety Considerations
- Hardwired float switch still provides ultimate overflow protection
- This adds software-level safety for earlier intervention
- Does not prevent manual override - provides continuous enforcement
- Logging provides audit trail of all shutoff events
- Uses gpio command approach proven to work in systemd service context
