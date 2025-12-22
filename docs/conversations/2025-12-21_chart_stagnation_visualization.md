# Chart Stagnation Visualization & Privacy Protection

**Date:** 2025-12-21
**Focus:** Visual stagnation detection on tank chart, consolidating stagnation logic, privacy protection for reservations

## Summary

Added visual stagnation detection to the tank level chart using color-coded dots (orange = stagnant, green = filling). Consolidated all "stagnant" definitions to a single 6-hour standard by removing the TANK_STOPPED_FILLING event. Added privacy protection to the reservations table to hide sensitive columns from public view.

## Changes Made

### 1. Chart Visual Stagnation Detection

**Problem:** User wanted to visualize stagnant periods on the tank level chart to better understand when the well is not producing water.

**Initial Request:** "In the tank level section of the web page, is the Last Refill the same as that computed by the stagnant period (notified as TANK_STOPPED_FILLING?)? And how hard would it be to draw stagnant periods on the tank level history on the web and emails in a different color?"

**Investigation:**
- Read `stats.py` to understand `find_last_refill()` function
- Found two different stagnation definitions in the codebase:
  1. **TANK_STOPPED_FILLING**: 120 minutes, ≤15 gallons (real-time event)
  2. **Well Recovery Stagnation**: 6 hours, ≤30 gallons (notification logic)
- This created confusion and alert spam

**Solution Approach:**
1. Initially tried event-based coloring (marking TANK_STOPPED_FILLING timestamps)
2. Tried per-snapshot delta (≤5 gallons per 15 min) - too sensitive
3. Final: 6-hour lookback window with ≤30 gallon threshold - matches well recovery

**Implementation:**
- Modified `/api/chart_data` endpoint in `web.py` (lines 220-301)
- For each data point, look back 6 hours and check if gain ≤ 30 gallons
- Return `pointColors` array with colors for each point
- Modified Chart.js configuration in `status.html` to use dynamic point colors
- Orange (#ff9800) for stagnant, green (#4CAF50) for filling

**Files Changed:**
- `monitor/web.py`: Added stagnation calculation to `/api/chart_data` endpoint
- `monitor/templates/status.html`: Added `pointBackgroundColor` and `pointBorderColor` to Chart.js config

### 2. Consolidated Stagnation Logic

**Problem:** Two different "stagnant" definitions caused confusion and alert spam.

**User Feedback:** "The 6-hours is most important for notices. I do not want notices/alerts more than once every 6 hours."

**Decision:** Remove TANK_STOPPED_FILLING entirely, use only 6-hour well stagnation definition.

**Implementation:**
- Removed TANK_STOPPED_FILLING event detection from `poll.py` (lines 676-705)
- Removed `tank_was_filling` and `tank_gallons_history` state tracking
- Removed imports of `TANK_FILLING_WINDOW_MINUTES` and `TANK_FILLING_THRESHOLD`
- Removed parameters from `config.py`:
  - `TANK_FILLING_WINDOW_MINUTES` (was 120)
  - `TANK_FILLING_THRESHOLD` (was 15)
- Added comment explaining consolidation
- Single source of truth: `NOTIFY_WELL_RECOVERY_STAGNATION_HOURS` (6) and `NOTIFY_WELL_RECOVERY_MAX_STAGNATION_GAIN` (30)

**Files Changed:**
- `monitor/poll.py`: Removed TANK_STOPPED_FILLING detection logic
- `monitor/config.py`: Removed conflicting stagnation parameters

### 3. Reservation Privacy Protection

**Problem:** Reservation table showed too much detail (Repeat guest status, Booking type) for public view.

**User Request:** "Also, unless I add totals=income, I don't want the repeat or booking columns to show up. Again, it is too much info for the public."

**Implementation:**
- Wrapped Repeat and Booking columns in `{% if show_totals %}` conditional blocks
- Public view (no token): Shows only Check-In, Check-Out, Nights, Guest Type (4 columns)
- Owner view (?totals=income): Shows all columns including Repeat, Booking, Gross, Net, Total (9 columns)
- Uses existing `SECRET_TOTALS_TOKEN` authentication mechanism

**Files Changed:**
- `monitor/templates/status.html` (lines 353-388): Wrapped columns in conditional blocks

## Technical Details

### Chart Point Coloring Algorithm

```python
# For each data point in the chart
for i, point in enumerate(data_points):
    # Look back 6 hours
    lookback_cutoff = point['timestamp'].timestamp() - stagnation_window_seconds
    lookback_gallons = None

    # Find earliest point within 6-hour window
    for j in range(i, -1, -1):
        if data_points[j]['timestamp'].timestamp() >= lookback_cutoff:
            lookback_gallons = data_points[j]['gallons']
        else:
            break

    # Color based on 6-hour gain
    if lookback_gallons is not None and i > 0:
        time_span = point['timestamp'].timestamp() - data_points[max(0, i - 25)]['timestamp'].timestamp()

        # Only consider stagnant if we have close to 6 hours of data
        if time_span >= stagnation_window_seconds * 0.9:
            gain = point['gallons'] - lookback_gallons
            if gain <= NOTIFY_WELL_RECOVERY_MAX_STAGNATION_GAIN:
                point_colors.append('#ff9800')  # Orange - stagnant
            else:
                point_colors.append('#4CAF50')  # Green - filling
```

### Single Source of Truth

All stagnation detection now uses:
- `NOTIFY_WELL_RECOVERY_STAGNATION_HOURS = 6` (hours)
- `NOTIFY_WELL_RECOVERY_MAX_STAGNATION_GAIN = 30` (gallons)

Used by:
- Chart visualization (web.py)
- Well recovery notifications (notifications.py)
- "Last Refill" statistic (stats.py)

## Testing

- Verified chart displays with orange/green dots correctly
- Confirmed 6-hour lookback calculation matches well recovery logic
- Verified monitor and web services restart successfully
- Tested reservations table privacy - Repeat/Booking columns hidden without token
- Confirmed no TANK_STOPPED_FILLING events generated after changes

## Benefits

1. **Visual Clarity**: Easy to see at a glance when well is stagnant vs. filling
2. **Reduced Alert Spam**: Single 6-hour threshold instead of multiple overlapping definitions
3. **Consistency**: All stagnation detection uses same parameters
4. **Privacy**: Sensitive reservation details protected from public view
5. **Simplified Code**: Removed duplicate/conflicting detection logic

## User Feedback

- "I was just thinking of changing the color of the dots or line, but I like your idea."
- "This is even more confusing. A stagnant period (or the most recent one is what I care about) is NOTIFY_WELL_RECOVERY_STAGNATION_HOURS with no more than NOTIFY_WELL_RECOVERY_MAX_STAGNATION_GAIN water increase."
- "The 6-hours is most important for notices. I do not want notices/alerts more than once every 6 hours."
- "Yes" (to consolidation proposal)
- "Also, unless I add totals=income, I don't want the repeat or booking columns to show up."

## Related Files

- `monitor/web.py` - Chart data API and reservations logic
- `monitor/templates/status.html` - Chart rendering and table display
- `monitor/poll.py` - Monitoring loop (removed TANK_STOPPED_FILLING)
- `monitor/config.py` - Configuration parameters (cleanup)
- `monitor/stats.py` - Well recovery detection (examined, no changes)
- `monitor/notifications.py` - Notification rules (examined, no changes)

## Configuration Impact

**Removed parameters:**
- `TANK_FILLING_WINDOW_MINUTES` (was 120)
- `TANK_FILLING_THRESHOLD` (was 15)

**Single source of truth:**
- `NOTIFY_WELL_RECOVERY_STAGNATION_HOURS = 6`
- `NOTIFY_WELL_RECOVERY_MAX_STAGNATION_GAIN = 30`

No user action required - existing config files continue to work.

## Future Considerations

- When water usage data becomes available, add second line to chart for tenant water use
- Consider adding legend to chart explaining orange vs. green dots
- Could add stagnation visualization to email charts as well (currently web-only)
