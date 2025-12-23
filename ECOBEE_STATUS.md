# Ecobee Integration - Current Status

## Overview

The Ecobee integration provides Python library and CLI tools to control thermostats via web scraping. This approach is necessary because Ecobee stopped accepting new API developer applications in March 2024.

## What Works ✅

### Reading Thermostat Data
```python
from monitor.ecobee import EcobeeController

with EcobeeController() as ecobee:
    # Get all thermostats
    thermostats = ecobee.get_all_thermostats()

    # Get specific thermostat
    living_room = ecobee.get_thermostat('Living Room Ecobee')

    print(f"Temperature: {living_room['temperature']}°F")
    print(f"Setpoint: {living_room['heat_setpoint']}°F")
```

**Returns:**
- ✅ Current temperature
- ✅ Heat/cool setpoints
- ✅ System mode (heat/cool/auto/off)
- ✅ Hold status and text
- ⚠️ Vacation mode detection (unreliable)

### Setting Temperature (Preset Mode)
```python
# Sets thermostat to "Home" comfort setting with indefinite hold
ecobee.set_temperature('Living Room Ecobee')
```

**Limitation:** Uses preset "Home and hold" button. Cannot set precise temperatures.

### Canceling Holds
```python
# Resume schedule (cancel any active hold)
ecobee.cancel_hold('Living Room Ecobee')
```

### CLI Tool
```bash
# Get status
./ecobee_control.py status

# Get specific thermostat
./ecobee_control.py status --thermostat "Living Room Ecobee"

# JSON output
./ecobee_control.py status --json

# Set to Home preset
./ecobee_control.py set --thermostat "Living Room Ecobee"

# Reset all to Home preset
./ecobee_control.py reset
```

## What Doesn't Work (Yet) ⚠️

### Precise Temperature Control
**Problem:** The Ecobee web portal uses an HTML5 canvas-based temperature slider that cannot be easily automated with Selenium.

**What we tried:**
- Click-and-drag simulation (inconsistent results)
- Offset calculations (canvas doesn't respond properly)
- JavaScript events (slider uses complex AngularJS event handlers)

**Current workaround:** Use "Home and hold" preset button

**Possible solutions:**
1. Reverse engineer AngularJS controller and call methods directly via JavaScript injection
2. Obtain Ecobee API developer access (waiting list)
3. Use presets for now and adjust thermostat schedules via web UI

### Vacation Mode Creation
**Problem:** Date picker is a complex AngularJS component.

**Current status:**
- ✅ Can navigate to vacation page
- ✅ Can detect "New Vacation" button
- ❌ Cannot fill in start/end dates
- ❌ Cannot submit vacation form

**Workaround:** Script opens the vacation modal, user completes manually.

### Vacation Mode Detection
**Problem:** Cannot reliably detect vacation mode status from thermostat data.

**Current status:**
- ❌ Vacation mode detection not working reliably
- Marked as TODO (low priority)

### Vacation Mode Deletion
**Problem:** Cannot reliably find and delete vacation items.

**Current status:**
- ❌ Vacation deletion implementation attempted but not working
- Requires manual deletion via web portal or mobile app
- Marked as TODO (low priority)

**Workaround:** Delete vacations manually via Ecobee web portal or mobile app

## Files Created

### Python Library
- `monitor/ecobee.py` - Core library with `EcobeeController` class

### CLI Tool
- `ecobee_control.py` - Manual control interface

### Investigation Scripts (for development)
- `scrape_ecobee_selenium.py` - Standalone data scraper
- `investigate_ecobee_controls.py` - UI element investigation
- `test_vacation_controls.py` - Vacation modal testing
- `test_temperature_setting.py` - Temperature control testing
- `test_slider_drag.py` - Slider drag testing

### Documentation
- `ECOBEE_SETUP.md` - Setup instructions
- `ECOBEE_STATUS.md` - This file
- `docs/conversations/2024-12-23-ecobee-integration.md` - Development notes

## Use Cases

### ✅ Currently Supported
1. **Dashboard Display**
   - Display Living Room temperature
   - Display current setpoints
   - Monitor all thermostats

2. **Basic Control**
   - Set thermostats to Home comfort settings
   - Cancel holds to resume schedule
   - Quick status checks

3. **Monitoring**
   - Poll thermostat data hourly
   - Track temperature history

### ⚠️ Requires Manual Intervention
1. **Precise Temperature Setting**
   - User must use physical thermostat or web portal
   - Or adjust thermostat schedule definitions

2. **Vacation Mode Management**
   - Cannot reliably detect or control vacation mode
   - Use Ecobee web portal or mobile app for vacation management

### ❌ Not Feasible with Web Scraping
1. **Complex Scheduling**
   - Multi-day custom schedules
   - Comfort setting temperature changes
   - Requires API access

2. **Advanced Features**
   - Smart home/away
   - Occupancy sensors
   - Energy reports
   - Requires API access

## Recommended Usage

### For Now (Web Scraping)
```python
# Dashboard - read temperatures hourly
with EcobeeController() as ecobee:
    thermostats = ecobee.get_all_thermostats()
    # Display on dashboard

# Simple automation - use presets
ecobee.set_temperature('Living Room Ecobee')  # Home preset
ecobee.set_temperature('Hallway')  # Home preset

# Check vacation status
if living_room['vacation_mode']:
    print("House is in vacation mode")
```

### Future (With API Access)
Once Ecobee API access is obtained:
1. Replace `monitor/ecobee.py` implementation with API calls
2. Keep same interface so existing code doesn't break
3. Gain precise temperature control and vacation scheduling

## Performance

- **Login:** ~15-20 seconds (includes 2FA)
- **Data scraping:** ~8-10 seconds
- **Temperature setting:** ~10-12 seconds
- **Total operation:** ~30-40 seconds

Acceptable for:
- Hourly monitoring
- Manual control
- Infrequent automation

Not suitable for:
- Real-time control
- Rapid polling
- High-frequency adjustments

## Next Steps

### Short Term
1. Test library with hourly cron job
2. Integrate temperature display into web dashboard
3. Use for basic automation (post-checkout comfort settings)

### Medium Term
1. Investigate AngularJS JavaScript injection for precise temp control
2. Reverse engineer date picker for vacation automation
3. Add caching to reduce login frequency

### Long Term
1. Apply for Ecobee API developer access
2. Migrate to official API when available
3. Maintain backward compatibility with current interface

## Known Limitations

1. **Canvas slider** - Cannot set precise temperatures
2. **Date picker** - Cannot automate vacation date selection
3. **Session duration** - Each operation requires fresh login (no session persistence)
4. **Multiple thermostats** - Must set individually (no bulk operations)
5. **Schedules** - Cannot modify comfort settings or schedules
6. **Cool mode** - Limited testing (house only uses heat mode currently)

## Security Notes

- TOTP secret stored in `~/.config/pumphouse/secrets.conf` (600 permissions)
- Browser runs in headless mode
- No credentials logged
- Session closed after each operation
- "Remember device" option used to reduce 2FA frequency

## Support

For issues or questions:
1. Check `ECOBEE_SETUP.md` for setup instructions
2. Review `docs/conversations/2024-12-23-ecobee-integration.md` for technical details
3. Run with `--debug` flag for detailed output
4. Use `--show-browser` to see what Selenium is doing

## Example Session

```bash
$ ./ecobee_control.py status

Found 2 thermostat(s):
============================================================

Hallway:
  Current Temperature: 67.0°F
  Heat Setpoint: 69.0°F
  System Mode: heat
  Hold: Hold
  Vacation Mode: No

Living Room Ecobee:
  Current Temperature: 67.0°F
  Heat Setpoint: 72.0°F
  System Mode: heat
  Hold: Hold
  Vacation Mode: No
```

## Summary

The Ecobee integration successfully provides:
- ✅ Temperature reading for dashboard display
- ✅ Vacation mode detection
- ✅ Basic temperature control via presets
- ⚠️ Vacation mode UI access (manual completion required)

This meets the core requirements for monitoring and basic automation. Precise control will require either:
1. Advanced JavaScript injection techniques
2. Ecobee API access (preferred, waiting for developer program reopening)
