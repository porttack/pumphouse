# System Health Monitoring

Continuous monitoring of Pi system vitals to diagnose unexpected reboots and system issues.

---

## Overview

`system_health_monitor.sh` runs every 5 minutes via cron and logs key metrics to `system_health.log`. This was implemented after a December 2025 reboot that couldn't be diagnosed because persistent journaling wasn't enabled.

---

## Metrics Logged

- **Temperature**: CPU temperature from `vcgencmd`
- **Throttling status**: Undervoltage, thermal throttling, frequency capping
- **Memory usage**: Total, used, free, available
- **CPU load**: 1, 5, and 15-minute load averages
- **Disk usage**: Root filesystem percentage
- **Uptime**: Time since last boot

### Log format

```
[YYYY-MM-DD HH:MM:SS] Temp=XX.X'C | Throttled=throttled=0xN | Mem=Total:X.XGi Used:X.XGi Free:XMi Available:X.XGi | Load=X.XX, X.XX, X.XX | Disk=Used:XG/XG (XX%) | Uptime=up X hours, X minutes
```

### Alerts

When critical conditions are detected, the script logs a `WARNING` entry and sends to the system journal via `logger`.

---

## Throttling Status Bitmask

The `throttled` value from `vcgencmd get_throttled` is a hex bitmask:

| Value | Meaning |
|-------|---------|
| `0x0` | No issues (normal) |
| `0x1` | Under-voltage detected |
| `0x2` | Arm frequency capped |
| `0x4` | Currently throttled |
| `0x8` | Soft temperature limit active |
| `0x10000` | Under-voltage has occurred since boot |
| `0x20000` | Arm frequency capping has occurred since boot |
| `0x40000` | Throttling has occurred since boot |
| `0x80000` | Soft temperature limit has occurred since boot |

---

## Setup

The cron job should be installed as part of initial Pi setup:

```bash
crontab -e
# Add:
*/5 * * * * /home/pi/src/pumphouse/system_health_monitor.sh
```

Verify it's installed:
```bash
crontab -l
```

View recent logs:
```bash
tail -50 /home/pi/src/pumphouse/system_health.log
```

---

## Persistent Journaling

Enable persistent journaling so logs survive reboots:

```bash
sudo mkdir -p /var/log/journal
sudo systemd-tmpfiles --create --prefix /var/log/journal
sudo journalctl --flush
```

### Useful journal commands

```bash
# List all available boots (including before reboots)
journalctl --list-boots

# Logs from the previous boot
sudo journalctl --boot=-1

# Logs from current boot
sudo journalctl --boot=0

# Check journal disk usage
journalctl --disk-usage
```

---

## Monitoring Best Practices

### Regular checks

```bash
# View recent health logs
tail -100 /home/pi/src/pumphouse/system_health.log

# Check for throttling events
grep "WARNING" /home/pi/src/pumphouse/system_health.log

# Monitor boot history
journalctl --list-boots

# Check current throttling status
vcgencmd get_throttled

# Check current temperature
vcgencmd measure_temp
```

### Alert conditions to watch for

- `throttled` value ≠ `0x0` (power or thermal issues)
- Temperature consistently >70°C
- Available memory <500 MB
- Load average >4.0 (RPi 4 has 4 cores)
- Multiple reboots in a short period

---

## Responding to Issues

### Undervoltage (`throttled` != 0x0)

- Check power supply (should be official RPi 4 PSU: 5V/3A)
- Check USB-C cable quality
- Verify no voltage drop in power distribution chain

### High temperature

- Verify heatsink / fan operation
- Check enclosure ventilation
- Consider active cooling if consistently >70°C

### Memory issues

```bash
ps aux --sort=-%mem | head -20
```

Check for memory leaks in custom services; consider adding swap.

### SD card health

```bash
sudo badblocks -v /dev/mmcblk0
```

---

## Files

| File | Purpose |
|------|---------|
| `system_health_monitor.sh` | Monitoring script (run via cron) |
| `system_health.log` | Health log (excluded from git) |

---

## Investigation: December 22, 2025 Reboot

### Timeline

- Last successful snapshot: 09:01:52
- System went down: between 09:01:52 and 09:18:42
- System rebooted: 09:18:42
- First snapshot after reboot: 09:30:01
- Total downtime: ~16–28 minutes

### Findings

Ruled out: overheating (37.9°C), undervoltage (`throttled=0x0`), kernel panic, OOM (1.7 GB available), humidity spike.

Most likely causes (without prior logs): hardware watchdog timeout, power glitch/brownout, manual reboot, or filesystem check during boot.

### Lessons learned

1. ✅ Enabled persistent journaling
2. ✅ Implemented health monitoring (every 5 minutes)
3. ✅ Documented for future reference
