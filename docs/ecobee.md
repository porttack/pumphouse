# Ecobee Thermostat Integration

Integration with on-site Ecobee thermostats (Living Room + Hallway) for temperature monitoring and basic control.

---

## Overview

The Ecobee API developer program stopped accepting new applicants in March 2024. The integration uses **web scraping via Selenium** as a fallback.

### Capabilities

| Feature | Status |
|---------|--------|
| Read current temperature | ✅ Working |
| Read heat/cool setpoints | ✅ Working |
| Read system mode (heat/cool/auto/off) | ✅ Working |
| Read hold status | ✅ Working |
| Set temperature (Home preset) | ✅ Working |
| Cancel holds (resume schedule) | ✅ Working |
| Precise temperature setting | ❌ Canvas slider not automatable |
| Vacation mode detection | ⚠️ Unreliable |
| Vacation mode creation/deletion | ❌ Date picker not automatable |

---

## Setup

### 1. Install dependencies

```bash
sudo apt install -y chromium-browser chromium-chromedriver
cd ~/src/pumphouse
source venv/bin/activate
pip install -r requirements.txt   # includes selenium
```

### 2. Add credentials to secrets file

```bash
nano ~/.config/pumphouse/secrets.conf
```

```ini
ECOBEE_USERNAME=your-email@example.com
ECOBEE_PASSWORD=your-password
```

A TOTP secret may also be required if 2FA is enabled (stored in `secrets.conf` as well).

### 3. Test

```bash
./ecobee_control.py status
```

---

## CLI Tool

```bash
# Get status of all thermostats
./ecobee_control.py status

# Get specific thermostat
./ecobee_control.py status --thermostat "Living Room Ecobee"

# JSON output
./ecobee_control.py status --json

# Set to Home comfort preset (indefinite hold)
./ecobee_control.py set --thermostat "Living Room Ecobee"

# Reset all thermostats to Home preset
./ecobee_control.py reset

# Show browser (for debugging)
./ecobee_control.py status --show-browser
```

### Example output

```
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

---

## Python API

```python
from monitor.ecobee import EcobeeController

with EcobeeController() as ecobee:
    # Get all thermostats
    thermostats = ecobee.get_all_thermostats()

    # Get specific thermostat
    living_room = ecobee.get_thermostat('Living Room Ecobee')
    print(f"Temperature: {living_room['temperature']}°F")
    print(f"Setpoint: {living_room['heat_setpoint']}°F")

    # Set to Home preset
    ecobee.set_temperature('Living Room Ecobee')

    # Resume schedule (cancel hold)
    ecobee.cancel_hold('Living Room Ecobee')
```

---

## Limitations of Web Scraping

- **Canvas slider**: Ecobee's temperature control uses an HTML5 canvas slider that Selenium cannot reliably interact with — precise temperature setting is not possible
- **Date picker**: Complex AngularJS component — vacation creation/deletion not automatable
- **Session**: Each operation requires a fresh login (~30–40 seconds total)
- **Not suitable for**: Real-time control, rapid polling, high-frequency adjustments

**Performance**: Login ~15–20 s, data scraping ~8–10 s, temperature setting ~10–12 s.

---

## API Approach (If Developer Access Becomes Available)

If Ecobee reopens their developer program:

1. Create app at https://www.ecobee.com/developers/
2. Add API key to `~/.config/pumphouse/secrets.conf`:
   ```ini
   ECOBEE_API_KEY=your-ecobee-api-key-here
   ```
3. On first run, the PIN-based OAuth flow will authorize the app and save tokens to `~/.config/pumphouse/ecobee_tokens.json`
4. Replace the `monitor/ecobee.py` implementation with API calls while keeping the same interface

---

## Troubleshooting

### `Thermostat 'Living Room Ecobee' not found`

Run with `--debug` to see all available thermostats. Edit `TARGET_THERMOSTAT` in the script if the name differs.

### Login failures

Check credentials in `secrets.conf`. If 2FA is enabled, verify the TOTP secret is configured.

### Slow or hanging

Ecobee's web portal is JavaScript-heavy. Use `--show-browser` to watch what Selenium is doing.

---

## Files

| File | Purpose |
|------|---------|
| `monitor/ecobee.py` | Core library (`EcobeeController` class) |
| `ecobee_control.py` | CLI control interface |
| `scrape_ecobee_selenium.py` | Standalone scraper (development) |
| `~/.config/pumphouse/ecobee_tokens.json` | OAuth tokens (API approach only) |
