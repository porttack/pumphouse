# Check-in/Checkout Thermostat Reminders

**Date**: 2025-12-24
**Version**: 2.15.0
**Conversation**: Implementation of smart check-in/checkout reminder alerts

## User Request

Add two new pumphouse alerts integrated with the reservation system:

1. **11:00 AM Checkout Reminder**: Alert when a tenant checks out to remind turning down the thermostat
2. **Daily 6 AM Check-in Enhancement**: Modify existing daily status email to include heat reminder when tenant checks in

## Implementation

### Configuration Changes

Added new configuration options to `monitor/config.py`:

```python
# Checkout Reminder Configuration
ENABLE_CHECKOUT_REMINDER = True  # Send checkout reminder to turn down thermostat
CHECKOUT_REMINDER_TIME = "11:00"  # Time to send checkout reminder (HH:MM in 24-hour format)
```

### Code Changes

Modified `monitor/poll.py`:

1. **Imports**: Added occupancy detection functions
   - `get_checkout_datetime`, `get_checkin_datetime`, `parse_date` from `monitor.occupancy`

2. **SimplifiedMonitor Class**:
   - Added `next_checkout_reminder_time` tracking variable
   - Initialized checkout reminder timing in `run()` method using existing `get_next_daily_status_time()` function

3. **Daily Status Email Enhancement** (lines 693-759):
   - Checks `reservations.csv` for same-day check-ins
   - When check-in detected:
     - Subject: `{xxx} gal - Turn on heat!`
     - Message: `⚠️ REMINDER: Tenant checking in today - turn on the heat!\n\nDaily status report...`
   - Normal days remain unchanged

4. **Checkout Reminder** (lines 761-809):
   - Daily check at 11:00 AM for same-day checkouts
   - Only sends email if checkout detected
   - Subject: `{xxx} gal - Turn down thermostat!`
   - Message: `⚠️ REMINDER: {Guest Name} checking out today - turn down the thermostat after checkout!`
   - Includes full system status, charts, and sensor readings
   - Logged as `CHECKOUT_REMINDER` event

### Behavior

Both reminders:
- Check `reservations.csv` for Check-In/Checkout dates matching today's date
- Include full system status (tank level, sensors, reservations table, events)
- Are logged to `events.csv` with event types `DAILY_STATUS_EMAIL` and `CHECKOUT_REMINDER`
- Use the same scheduler as daily status email for consistent timing
- Can be disabled via config flags

**Check-in Detection**:
- Runs every morning at configured `DAILY_STATUS_EMAIL_TIME` (default: 06:00)
- Always sends email, but customizes subject/message if check-in today

**Checkout Detection**:
- Runs every day at configured `CHECKOUT_REMINDER_TIME` (default: 11:00)
- Only sends email when someone is checking out today
- Reduces noise by not sending on days without checkouts

## Files Modified

1. **monitor/config.py**:
   - Added `ENABLE_CHECKOUT_REMINDER` configuration flag
   - Added `CHECKOUT_REMINDER_TIME` configuration value

2. **monitor/poll.py**:
   - Added occupancy imports
   - Added `next_checkout_reminder_time` instance variable
   - Modified daily status email logic to detect check-ins
   - Added checkout reminder logic
   - Both use existing `get_next_daily_status_time()` for scheduling

3. **README.md**:
   - Added checkout reminder configuration to email setup section
   - Added smart reminders to email features list

4. **CHANGELOG.md**:
   - Added version 2.15.0 entry with feature description

## Testing

Verified:
- Configuration values load correctly
- Python syntax validates without errors
- Service restarts successfully
- Occupancy detection logic works correctly with `reservations.csv`
- Monitor instantiates with new timing variables

## Technical Notes

- Reuses existing `get_next_daily_status_time()` function for both reminders
- No changes to `email_notifier.py` - uses existing `send_email_notification()`
- Check-in/checkout dates parsed using existing `parse_date()` from `monitor.occupancy`
- Scheduling happens at exact clock times (not relative to start time)
- Both reminders survive service restarts and reschedule correctly

## Use Case

Helps automate thermostat management for vacation rental property:
- **Morning (6 AM)**: Reminder to turn on heat before tenant arrives (check-in day)
- **Late morning (11 AM)**: Reminder to turn down thermostat after tenant departs (checkout day)
- Reduces energy waste and ensures tenant comfort
- Integrates seamlessly with existing reservation tracking system
