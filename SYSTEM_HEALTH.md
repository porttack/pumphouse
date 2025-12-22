# System Health Monitoring

This document describes the system health monitoring setup for the Raspberry Pi running the Pumphouse system.

## Overview

The system health monitor was implemented to help diagnose unexpected reboots and system issues. It logs vital system metrics every 5 minutes to help identify patterns or conditions that may lead to system instability.

## Components

### Health Monitoring Script

**File**: `system_health_monitor.sh`

The monitoring script collects and logs:
- **Temperature**: CPU temperature from vcgencmd
- **Throttling Status**: Detects undervoltage, thermal throttling, and other power issues
- **Memory Usage**: Total, used, free, and available memory
- **CPU Load**: 1, 5, and 15 minute load averages
- **Disk Usage**: Root filesystem usage
- **Uptime**: How long the system has been running

**Log File**: `system_health.log`

The script runs automatically every 5 minutes via cron.

### Log Format

Each log entry contains:
```
[YYYY-MM-DD HH:MM:SS] Temp=XX.X'C | Throttled=throttled=0xN | Mem=Total:X.XGi Used:X.XGi Free:XMi Available:X.XGi | Load=X.XX, X.XX, X.XX | Disk=Used:XG/XG (XX%) | Uptime=up X hours, X minutes
```

### Throttling Status Interpretation

The `throttled` value is a hexadecimal bitmask:
- `0x0` = No issues (normal)
- `0x1` = Under-voltage detected
- `0x2` = Arm frequency capped
- `0x4` = Currently throttled
- `0x8` = Soft temperature limit active
- `0x10000` = Under-voltage has occurred since boot
- `0x20000` = Arm frequency capping has occurred since boot
- `0x40000` = Throttling has occurred since boot
- `0x80000` = Soft temperature limit has occurred since boot

### Alerts

When critical conditions are detected (throttling/undervoltage), the script:
1. Logs a WARNING entry to the health log
2. Sends an alert to the system journal via `logger`

## Setup

The monitoring script is automatically configured via crontab:

```bash
*/5 * * * * /home/pi/src/pumphouse/system_health_monitor.sh
```

To verify it's running:
```bash
crontab -l
```

To view recent logs:
```bash
tail -50 /home/pi/src/pumphouse/system_health.log
```

## Persistent Journaling

The system is configured with persistent journaling to preserve system logs across reboots. This allows investigation of issues that occurred before a reboot.

**Journal Location**: `/var/log/journal/`

### Useful Journal Commands

View logs from previous boot:
```bash
sudo journalctl --boot=-1
```

View logs from current boot:
```bash
sudo journalctl --boot=0
```

List all available boots:
```bash
journalctl --list-boots
```

Check journal disk usage:
```bash
journalctl --disk-usage
```

## Investigation: December 22, 2025 Reboot

### Timeline

- **Last successful monitoring snapshot**: 09:01:52
- **System went down**: Between 09:01:52 and 09:18:42
- **System rebooted**: 09:18:42
- **First snapshot after reboot**: 09:30:01
- **Total downtime**: Approximately 16-28 minutes

### Findings

**Ruled Out**:
- ❌ Overheating (temp: 37.9°C - normal)
- ❌ Undervoltage (throttled=0x0 - no issues)
- ❌ Kernel panic (no crash traces in dmesg)
- ❌ Out of memory (1.7GB available)
- ❌ High humidity causing issues (91% RH stable)

**Most Likely Causes** (without prior logs):
1. Hardware watchdog timeout (1-minute timeout)
2. Power glitch or brownout
3. Manual reboot
4. Filesystem check during boot

### Environmental Conditions

- **Enclosure Temperature**: 54.1°F
- **Enclosure Humidity**: 91.0% (high but stable)
- **CPU Temperature**: 37.9°C (normal)

### Lessons Learned

Without persistent journaling enabled, logs from before the reboot were lost, making it impossible to determine the exact cause. This led to:

1. ✅ **Enabling persistent journaling** to preserve logs across reboots
2. ✅ **Implementing health monitoring** to track system vitals every 5 minutes
3. ✅ **Creating documentation** for future reference

## Monitoring Best Practices

### Regular Checks

1. **Review health logs periodically**:
   ```bash
   tail -100 /home/pi/src/pumphouse/system_health.log
   ```

2. **Check for throttling events**:
   ```bash
   grep "WARNING" /home/pi/src/pumphouse/system_health.log
   ```

3. **Monitor boot history**:
   ```bash
   journalctl --list-boots
   ```

4. **Check current throttling status**:
   ```bash
   vcgencmd get_throttled
   ```

### Alert Conditions

Watch for these warning signs:
- Throttled value != 0x0 (indicates power or thermal issues)
- Temperature consistently > 70°C
- Available memory < 500MB
- Load average > 4.0 (for RPi 4 with 4 cores)
- Multiple reboots in short time period

### Response Actions

If issues are detected:

1. **For undervoltage (throttled != 0x0)**:
   - Check power supply (should be official RPi 4 PSU: 5V/3A)
   - Check USB-C cable quality
   - Verify no voltage drop in power distribution

2. **For high temperature**:
   - Verify heatsink/fan operation
   - Check enclosure ventilation
   - Consider active cooling if consistently high

3. **For memory issues**:
   - Review running processes: `ps aux --sort=-%mem | head -20`
   - Check for memory leaks in custom services
   - Consider reducing memory usage or adding swap

4. **For repeated reboots**:
   - Review journal logs immediately after reboot
   - Check health log for patterns before reboot
   - Verify SD card health: `sudo badblocks -v /dev/mmcblk0`

## Files

- `system_health_monitor.sh` - Main monitoring script
- `system_health.log` - Health monitoring log (excluded from git)
- `SYSTEM_HEALTH.md` - This documentation file

## References

- [Raspberry Pi Documentation: Monitoring](https://www.raspberrypi.com/documentation/computers/os.html#monitoring)
- [vcgencmd Documentation](https://www.raspberrypi.com/documentation/computers/os.html#vcgencmd)
- [systemd Journal Documentation](https://www.freedesktop.org/software/systemd/man/journalctl.html)
