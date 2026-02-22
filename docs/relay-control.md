# Relay Control

Manual relay control, automatic spindown filter purging, and automatic override valve management.

---

## Manual Control (`control.sh`)

Use `control.sh` for safe relay control without Python GPIO conflicts. The script uses the `gpio` CLI command (WiringPi) rather than Python, so it's safe to run while the monitor service is active.

```bash
# Purge spindown filter (default 10 seconds)
./control.sh purge

# Purge with custom duration
./control.sh purge 15

# Enable/disable bypass valve
./control.sh bypass on
./control.sh bypass off

# Enable/disable supply override valve
./control.sh override on
./control.sh override off

# Show current relay states
./control.sh status
```

---

## Automatic Purging

Automatically purges the WHC40 spindown filter after water delivery to remove accumulated sediment.

### Enable automatic purging

Edit `monitor/config.py` (or `~/.config/pumphouse/monitor.conf`):

```python
ENABLE_PURGE = True         # Enable automatic purging
MIN_PURGE_INTERVAL = 3600   # Minimum 1 hour between purges
PURGE_DURATION = 10         # Purge for 10 seconds
```

### How it works

After a `PRESSURE_LOW` event (pump stops), the monitor triggers a brief pulse on the Spin Purge Valve (BCM 13, Channel 3) to backflush sediment from the spindown filter. The `MIN_PURGE_INTERVAL` prevents excessive purging.

**Impact**: Reduces filter maintenance visits from weekly (~$2,860/year) to 6–8 months (~$330–440/year).

---

## Automatic Override Control

The monitor can manage the supply override valve automatically to keep the tank fuller than the hardware float switch alone would allow.

### Override Auto-On (optional)

When enabled, automatically turns ON the override valve when the tank drops below a threshold — triggering a refill sooner than the physical float switch would.

```python
# In monitor/config.py or ~/.config/pumphouse/monitor.conf:
OVERRIDE_ON_THRESHOLD = 1350   # Turn ON override when tank drops below this (None = disabled)
```

To disable: set to `None` (default).

### Override Auto-Shutoff (overflow protection)

Automatically turns OFF the override valve when the tank reaches the shutoff threshold. Prevents overflow. **Enabled by default.**

```python
ENABLE_OVERRIDE_SHUTOFF = True    # Enable overflow protection (default: True)
OVERRIDE_SHUTOFF_THRESHOLD = 1410 # Turn OFF override when tank reaches this
```

To disable temporarily (e.g., to fill past the threshold):
```python
ENABLE_OVERRIDE_SHUTOFF = False
```
Then: `sudo systemctl restart pumphouse-monitor`

### How it works

- Checks tank level every 60 seconds (every tank poll cycle)
- Reloads config on each check — threshold changes take effect without restarting the service
- If override is OFF and tank < `OVERRIDE_ON_THRESHOLD` → turns override ON
- If override is ON and tank ≥ `OVERRIDE_SHUTOFF_THRESHOLD` → turns override OFF
- All automatic actions logged with `OVERRIDE_AUTO_ON` or `OVERRIDE_SHUTOFF` event types

### Example

With `OVERRIDE_ON_THRESHOLD = 1350` and `OVERRIDE_SHUTOFF_THRESHOLD = 1410`:

1. Tank drops to 1,349 gallons → override automatically turns ON
2. Tank fills to 1,410 gallons → override automatically turns OFF
3. Physical float switch still active as backup (rarely needed)

---

## Remote Control via Email

Every email alert includes one-click action buttons that control relays without command-line access.

### Setup

1. **Generate secret tokens**:
   ```bash
   python3 -c "import secrets; print('SECRET_OVERRIDE_ON_TOKEN=' + secrets.token_urlsafe(32))"
   python3 -c "import secrets; print('SECRET_OVERRIDE_OFF_TOKEN=' + secrets.token_urlsafe(32))"
   python3 -c "import secrets; print('SECRET_BYPASS_ON_TOKEN=' + secrets.token_urlsafe(32))"
   python3 -c "import secrets; print('SECRET_BYPASS_OFF_TOKEN=' + secrets.token_urlsafe(32))"
   python3 -c "import secrets; print('SECRET_PURGE_TOKEN=' + secrets.token_urlsafe(32))"
   ```

2. **Add tokens to secrets file**:
   ```bash
   nano ~/.config/pumphouse/secrets.conf
   ```

3. **Restart services**:
   ```bash
   sudo systemctl restart pumphouse-monitor pumphouse-web
   ```

### How it works

- Email buttons link to `https://your-domain.com/control/<token>`
- Each action has a unique 256-bit random token (43 characters)
- Clicking a button executes the action immediately and logs a `REMOTE_CONTROL` event
- Success page redirects to the dashboard after 2 seconds
- Feature is optional — works without tokens configured (buttons simply don't appear)

### Buttons in email

| Button | Color | Action |
|--------|-------|--------|
| Override ON | Blue | Turn on supply override valve |
| Override OFF | Orange | Turn off supply override valve |
| Bypass ON | Blue | Turn on bypass valve |
| Bypass OFF | Orange | Turn off bypass valve |
| Purge Now | Purple | Trigger one-time spindown filter purge |

---

## Safety Notes

- **Hardwired float switch**: The physical float switch provides ultimate overflow protection independently of the Pi — cannot be overridden by software
- **Never run supply and bypass simultaneously**: Could cause backflow or pressure issues
- **Override tokens**: Keep these secret; URLs are only shared via email alerts
- **Auto-shutoff re-enforcement**: Even if you manually turn override ON, auto-shutoff will turn it OFF again on the next tank poll when the threshold is reached
