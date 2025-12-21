# Detection Reliability Fixes and Alert Improvements

**Date:** 2025-12-21
**Version:** 2.13.0
**Context:** Log analysis revealed multiple detection and alerting issues

## Issues Discovered

### Issue #1: Missing Backflush Detection
- **Observation:** 117-gallon backflush at 1:15am was not detected
- **Root Cause:** Hourly cooldown timer meant checks could happen before/after brief backflush window
- **Solution:** Removed hourly cooldown from `check_backflush_status()` - now checks every 15-minute snapshot
- **Additional Fix:** Widened detection window from 2 to 3 snapshots (30min → 45min)

### Issue #2: False Well Recovery Alerts
- **Observation:** Recovery alert at 7:15am during continuous slow fill
- **Pattern:** Tank slowly filling (1132 → 1203 gal overnight) with tenant water usage
- **Root Cause:** Slow fill (24 gal/hr) + tenant usage created false "stagnation" periods
- **Solution:** Increased `NOTIFY_WELL_RECOVERY_MAX_STAGNATION_GAIN` from 15 to 30 gallons

### Issue #3: Excessive Tank Stopped Filling Events
- **Observation:** 8 TANK_STOPPED_FILLING events in 8 hours during normal slow refill
- **Pattern:** Well refilling at ~6 gal/15min (slow recovery mode)
- **Root Cause:** 60-minute window with 10-gallon threshold too sensitive
- **Solution:**
  - Increased `TANK_FILLING_WINDOW_MINUTES` from 60 to 120 minutes
  - Increased `TANK_FILLING_THRESHOLD` from 10 to 15 gallons

### Issue #4: Tank Unreadable False Shutoff
- **Observation:** Single transient network glitch triggered immediate override shutoff
- **Root Cause:** No tolerance for transient internet failures
- **Solution:** Added consecutive failure counter requiring 3 failures (3 minutes) before safety shutdown
- **Benefit:** Dramatically reduces false shutdowns while maintaining safety

### Issue #5: Suspected High Water Usage
- **Observation:** Backflush every 2 days suggests 29 gal/hour continuous usage (1400 gal / 48 hours)
- **Concern:** Possible leak or filter misconfiguration
- **Action:** Deferred - will instrument house meter in future to verify

## Configuration Changes

```python
# config.py changes
NOTIFY_WELL_RECOVERY_MAX_STAGNATION_GAIN = 30  # was 15
TANK_FILLING_WINDOW_MINUTES = 120              # was 60
TANK_FILLING_THRESHOLD = 15                    # was 10
NOTIFY_BACKFLUSH_WINDOW_SNAPSHOTS = 3          # was 2
```

## Code Changes

### notifications.py
- Removed hourly cooldown check from `check_backflush_status()`
- Updated return value to include timestamp: `('backflush', gallons_used, timestamp)`
- Added detailed comment explaining why backflush needs frequent checking

### poll.py
- Added `tank_fetch_failures` and `max_tank_failures` tracking (default: 3)
- Enhanced tank polling with consecutive failure counting
- Reset counter on successful read
- Shows debug progress: "Tank fetch failed (2/3)"
- Alert message includes attempt count

## Message Improvements

Based on user feedback, enhanced notification messages to include timestamps:

### Backflush Messages
- **Old:** "Carbon filter backflush used ~152 gallons"
- **New:** "Carbon filter backflush at Sat 1:15 AM used ~152 gallons"

### Well Recovery Messages
- **Old:** "Tank gained 50+ gallons after stagnation period"
- **New:** "Tank gained 50+ gallons after stagnation period ended Fri 9:00 PM"

## Technical Details

### Tank Fetch Retry Logic
```python
# Track consecutive failures
if not tank_fetch_success:
    self.tank_fetch_failures += 1
    if self.debug:
        print(f"Tank fetch failed ({self.tank_fetch_failures}/{self.max_tank_failures})")
else:
    # Success - reset failure counter
    if self.tank_fetch_failures > 0:
        if self.debug:
            print(f"Tank fetch recovered (was {self.tank_fetch_failures} failures)")
    self.tank_fetch_failures = 0

# Safety shutdown only after multiple consecutive failures
if self.tank_fetch_failures >= self.max_tank_failures:
    # Turn off override and alert
```

### Backflush Detection Timing
- **Before:** Checked once per hour via shared `last_refill_check` timer
- **After:** Checks every 15-minute snapshot
- **Window:** 3 snapshots = 45 minutes (previously 30 minutes)
- **Deduplication:** Still handled by `backflush_alerted_ts` timestamp tracking

### Timestamp Formatting
```python
# Backflush timestamp
backflush_time_str = backflush_ts.strftime('%a %I:%M %p').replace(' 0', ' ')
# Example: "Sat 1:15 AM"

# Stagnation end calculation
stagnation_end_ts = value + timedelta(hours=NOTIFY_WELL_RECOVERY_STAGNATION_HOURS)
stagnation_end_str = stagnation_end_ts.strftime('%a %I:%M %p').replace(' 0', ' ')
# Example: "Fri 9:00 PM"
```

## Testing Recommendations

1. **Monitor backflush detection** - Next backflush should be caught within 45-minute window
2. **Watch for false recovery alerts** - Should see fewer during slow fill periods
3. **Track TANK_STOPPED_FILLING** - Should only trigger on true fill cessation
4. **Test network resilience** - Brief internet glitches should not trigger shutoff

## Future Improvements

1. **Water meter instrumentation** - Verify tenant usage vs filter backflush
2. **Carbon filter investigation** - Understand filter config to address frequent backflush
3. **Adaptive thresholds** - Could auto-tune based on historical fill patterns
4. **Pressure event investigation** - Address mystery LOW pressure events during fast fill

## Related Files

- `monitor/config.py` - Configuration thresholds
- `monitor/notifications.py` - Detection logic and state tracking
- `monitor/poll.py` - Main polling loop and alert messages
- `monitor/stats.py` - Core detection algorithms
- `CHANGELOG.md` - Release notes

## Commits

1. `0dd7856` - Fix detection reliability and reduce false alerts (v2.13.0)
2. (Pending) - Add timestamps to backflush and recovery notification messages
