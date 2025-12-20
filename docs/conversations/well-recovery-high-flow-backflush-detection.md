# Well Recovery, High Flow, and Backflush Detection Improvements

**Date:** 2025-12-20
**Version:** 2.11.0

## Problem Statement

The user reported receiving "Well Recovery Detected" alerts every 15-30 minutes instead of the expected frequency after version 2.10.0 attempted to fix duplicate alerts. The original intent was to alert when the tank hasn't received much water for 6+ hours but then gains 50+ gallons, indicating well recovery.

## Root Cause Analysis

### Investigation Process

The user opened the TODO.md file and explained:
> "We just tried to fix the frequency of 'Well Recovery Detected' alerts earlier today in version 2.10.0. But now instead of getting these alerts every 60 to 90 minutes, I'm getting them every 15 to 30 minutes."

The user suspected the logic was sending alerts for every sequence of tank level changes representing 50+ gallons without checking for a reasonable period of flat or decreasing tank levels first.

### Code Analysis

I examined three key files:

1. **monitor/stats.py:8-69** - `find_last_refill()` function
2. **monitor/notifications.py:105-146** - `check_refill_status()` method
3. **monitor/poll.py:523-533** - Where well recovery check is called

**The Bug:**

The `find_last_refill()` function iterated backwards through snapshots and returned the timestamp of ANY snapshot where the tank gained 50+ gallons in the previous 24 hours:

```python
# Iterate backwards from the most recent snapshot
for i in range(len(parsed_rows) - 1, 0, -1):
    current_snapshot = parsed_rows[i]
    # Find snapshot from 24 hours before this one
    past_snapshot = ...

    # If it gained 50+ gallons, return THIS timestamp
    if (current_snapshot['gallons'] - past_snapshot['gallons']) >= threshold_gallons:
        return current_snapshot['ts'], days_ago
```

**Why This Caused Repeated Alerts:**

During a gradual tank fill (e.g., 10 gallons every few hours), EVERY snapshot showed a 50+ gallon gain when looking back 24 hours:

- 10:00 AM - Tank at 1200 gal (was 1150 24hrs ago) → 50+ gain ✓ Alert! (timestamp: 10:00 AM)
- 10:15 AM - Tank at 1210 gal (was 1160 24hrs ago) → 50+ gain ✓ Alert! (timestamp: 10:15 AM)
- 10:30 AM - Tank at 1220 gal (was 1170 24hrs ago) → 50+ gain ✓ Alert! (timestamp: 10:30 AM)

Version 2.10.0 added persistent state tracking to prevent duplicate alerts:

```python
if self.well_recovery_alerted_ts != refill_ts_str:
    # This is a NEW refill we haven't alerted about yet
    self.well_recovery_alerted_ts = refill_ts_str
```

But since `find_last_refill()` returned a **different timestamp every 15 minutes**, the state tracking saw each as a "new" event!

### User Clarification

The user asked:
> "You say 'The algorithm needs to look for a period of stagnation or decline (6+ hours as you mentioned) followed by a significant gain (50+ gallons).' But it is more than this. We also have to know if we've already sent the alert? (Or am I missing a subtlety in your argument?)"

I clarified that we need **BOTH**:
1. Better detection logic to find stagnation followed by recovery
2. State tracking (which already existed but was ineffective)

The key insight: The existing state tracking **would work fine** if the detection logic returned a **consistent timestamp** for each recovery event instead of a constantly-changing one.

The user asked where state would be stored, and I explained it was already being stored in `notification_state.json` at monitor/notifications.py:28.

## Solution Design

### 1. Fix Well Recovery Detection

Modify `find_last_refill()` to:
1. Look for the most recent snapshot where tank was at a local minimum (flat or declining for 6+ hours)
2. Check if tank subsequently gained 50+ gallons from that low point
3. Return the timestamp of the **LOW POINT** (end of stagnation), not the current snapshot

This ensures the same timestamp is returned throughout the entire recovery event.

**Configuration Added:**
```python
NOTIFY_WELL_RECOVERY_THRESHOLD = 50  # Gallons gained to count as recovery
NOTIFY_WELL_RECOVERY_STAGNATION_HOURS = 6  # Hours of flat/declining before recovery
```

### 2. Add High Flow Detection

The user explained:
> "The well appears to have two modes -- slow fill (which is really just purging air from the lines but I get some water) -- and fast fill (where the shared well's float has told the well to fill). I would like a second alert triggered the first time we see > 60 GPH (configurable) in the last 6-hours."

**Purpose:** Help decide whether to manually reconfigure the bypass relay depending on expected occupancy.

**Implementation:**
- Create `find_high_flow_event()` in stats.py
- Calculate GPH between snapshots using `tank_gallons_delta`
- Average over 2 snapshots (30 minutes) to filter noise
- Return first snapshot timestamp where threshold exceeded

**Configuration Added:**
```python
NOTIFY_HIGH_FLOW_ENABLED = True  # Enable high flow alerts
NOTIFY_HIGH_FLOW_GPH = 60  # GPH threshold for fast fill detection
NOTIFY_HIGH_FLOW_WINDOW_HOURS = 6  # How far back to look
NOTIFY_HIGH_FLOW_AVERAGING = 2  # Average over N snapshots (1=no averaging)
```

### 3. Add Backflush Detection

The user requested:
> "The carbon filter in the house runs some days between 12:45am and 4am. It uses 50 to 150 gallons in about 10 minutes. Can we add logic to detect this 'backflush' over 2 to 3 snapshots and log the event with an estimate of how many gallons of water were used?"

**Implementation:**
- Create `find_backflush_event()` in stats.py
- Detect large decline (50+ gallons) during configured time window
- Calculate total decline over 2-3 snapshots
- Return timestamp and gallons used estimate

**Configuration Added:**
```python
NOTIFY_BACKFLUSH_ENABLED = True  # Enable backflush detection
NOTIFY_BACKFLUSH_THRESHOLD = 50  # Gallons lost to trigger detection
NOTIFY_BACKFLUSH_WINDOW_SNAPSHOTS = 2  # Look back N snapshots (2=30min, 3=45min)
NOTIFY_BACKFLUSH_TIME_START = "00:00"  # Start of backflush window (HH:MM)
NOTIFY_BACKFLUSH_TIME_END = "04:30"  # End of backflush window (HH:MM)
```

### 4. Add Tank Stopped Filling Event

The user noted:
> "On the stopped filling, let's ensure that this is looked at for the past 60 minutes (configurable). The physics behind measuring the tank depth isn't perfect and we can sometimes see fluctuations. I usually see +6 and -6 gallon changes between readings for these reasons."

**Implementation:**
- Track rolling 60-minute window of tank levels
- Calculate net change over window to determine if filling
- Log event when transitions from filling to not filling
- No alerts, just event logging

**Configuration Added:**
```python
TANK_FILLING_WINDOW_MINUTES = 60  # Look back this long to determine if filling
TANK_FILLING_THRESHOLD = 10  # Gallons gained over window to be considered "filling"
```

## Implementation

### Files Modified

1. **monitor/config.py**
   - Added 13 new configuration parameters for all features

2. **monitor/stats.py**
   - Fixed `find_last_refill()` to use stagnation detection
   - Added `find_high_flow_event()` - High flow rate detection
   - Added `find_backflush_event()` - Backflush detection

3. **monitor/notifications.py**
   - Added `high_flow_alerted_ts` and `backflush_alerted_ts` state variables
   - Added `check_high_flow_status()` and `check_backflush_status()` methods
   - Updated `_load_state()` and `_save_state()` for new state persistence

4. **monitor/poll.py**
   - Added imports for new config parameters
   - Added `tank_was_filling` and `tank_gallons_history` instance variables
   - Added high flow and backflush checks in snapshot section
   - Added tank stopped filling detection logic in snapshot section
   - Updated well recovery message text

5. **monitor/__init__.py**
   - Updated version from 2.10.0 to 2.11.0

6. **README.md**
   - Updated version number
   - Added high flow, backflush, and tank stopped filling to notification events
   - Updated configuration examples with new parameters
   - Added event type documentation

7. **CHANGELOG.md**
   - Added comprehensive 2.11.0 entry documenting all changes

## Testing Considerations

The user noted testing concerns:
> "I'm not sure how we could test this, but it has been a repeating problem."

The well recovery fix will be validated by:
- Monitoring for duplicate alerts over the next few days
- Checking that alerts only occur after genuine stagnation periods
- Verifying state persistence across service restarts

For high flow and backflush:
- Monitor logs for detection events
- Verify GPH calculations match expected fill rates
- Confirm backflush detection during configured time window

## Key Insights

1. **State persistence alone isn't enough** - The algorithm must return consistent identifiers for events
2. **Sensor noise matters** - 60-minute rolling windows smooth out ±6 gallon fluctuations
3. **Different fill modes need different alerts** - Slow fill (air purging) vs fast fill (shared well activated)
4. **Time-of-day filtering** - Backflush detection benefits from time window constraints
5. **Averaging reduces false positives** - Multi-snapshot averaging for flow rate calculations

## Configuration Recommendations

For typical deployment:

```python
# Well Recovery
NOTIFY_WELL_RECOVERY_THRESHOLD = 50
NOTIFY_WELL_RECOVERY_STAGNATION_HOURS = 6

# High Flow (Fast Fill Mode)
NOTIFY_HIGH_FLOW_ENABLED = True
NOTIFY_HIGH_FLOW_GPH = 60
NOTIFY_HIGH_FLOW_AVERAGING = 2  # 30 minutes average

# Backflush
NOTIFY_BACKFLUSH_ENABLED = True
NOTIFY_BACKFLUSH_THRESHOLD = 50
NOTIFY_BACKFLUSH_TIME_START = "00:00"  # Add margin before 12:45am
NOTIFY_BACKFLUSH_TIME_END = "04:30"    # Add margin after 4:00am

# Tank Stopped Filling
TANK_FILLING_WINDOW_MINUTES = 60  # Smooth sensor noise
TANK_FILLING_THRESHOLD = 10       # Above sensor noise floor
```

## Related Conversations

- [Persistent State and Safety Shutoff](persistent-state-safety-shutoff.md) - Version 2.10.0 added persistent notification state
- Previous well recovery attempts that led to this fix

## Future Enhancements

Potential improvements discussed:
- Make well recovery stagnation threshold configurable
- Add GPH rate to well recovery alert message
- Consider historical backflush patterns for anomaly detection
- Track backflush efficiency over time
