# Occupancy & Reservation Tracking Features

This document describes the new occupancy tracking and reservation features added to the pumphouse monitoring system.

## Features Added

### 1. Occupancy Detection Module ([monitor/occupancy.py](monitor/occupancy.py))

Tracks property occupancy based on reservation data:

- **Current Occupancy**: Determines if property is occupied RIGHT NOW
  - Check-in time: 4:00 PM
  - Check-out time: 10:00 AM
  - Returns: occupied status, current guest, checkout date

- **Next Reservation**: Finds upcoming reservation
  - Returns: guest name, check-in date

- **Upcoming Reservations**: Lists reservations for next 6 weeks
  - Sorted by check-in date
  - Includes current reservation if occupied

### 2. Web Dashboard Updates

#### Occupancy Sensor Box
Added 5th sensor box in the SENSORS section showing:
- **Status**: "OCCUPIED until MM-DD" or "UNOCCUPIED"
- **Next Guest**: Shows next check-in date and guest name
- **Color coding**: Orange when occupied, green when unoccupied

#### Reservations Table
New table showing "CURRENT & UPCOMING RESERVATIONS (Next 6 Weeks)":
- Check-In date (e.g., "Fri 12-19")
- Check-Out date (e.g., "Tue 12-23")
- Guest name
- Number of nights
- Reservation type (Airbnb, Vrbo, Owner Stay, etc.)
- Income

Table appears between the chart and snapshots sections.

### 3. Snapshots Tracking

Added "occupied" column to snapshots.csv:
- **YES**: Property is currently occupied
- **NO**: Property is unoccupied
- Logged every snapshot interval (15 minutes by default)
- Helps correlate water usage with occupancy

### 4. CHECK-IN and CHECK-OUT Events

Automatic event logging when guests arrive/depart:

- **CHECK-IN events**: Logged when check-in time occurs (4 PM)
  - Detected within 4 hours of check-in time
  - Only logged once per guest
  - Prevents duplicate entries

- **CHECK-OUT events**: Logged when check-out time occurs (10 AM)
  - Detected within 4 hours of check-out time
  - Only logged once per guest

Events appear in events.csv and the web dashboard with:
- Event type: CHECK-IN or CHECK-OUT
- Guest name
- Stay dates
- Reservation type

### 5. Integration with Reservation Scraper

The [check_new_reservations.py](check_new_reservations.py) script now:
1. Checks for CHECK-IN/CHECK-OUT events (runs twice daily)
2. Detects new reservations
3. Logs all events to events.csv
4. Sends notifications for new bookings

## Files Modified/Created

### New Files
- `monitor/occupancy.py` - Occupancy detection logic
- `OCCUPANCY_FEATURES.md` - This documentation

### Modified Files
- `monitor/web.py` - Added occupancy status and reservations to dashboard
- `monitor/templates/status.html` - Added occupancy box and reservations table
- `monitor/logger.py` - Added `occupied` parameter to log_snapshot()
- `monitor/poll.py` - Checks occupancy on every snapshot
- `check_new_reservations.py` - Added CHECK-IN/CHECK-OUT event detection
- `snapshots.csv` - Added `occupied` column header

## Usage

### View Occupancy in Dashboard

Simply access the web dashboard at `https://your-pi:6443/`:
- See occupancy status in the SENSORS section
- View current and upcoming reservations below the chart
- Check snapshots table for historical occupancy data
- Monitor CHECK-IN/CHECK-OUT events in Recent Events

### Manual Occupancy Check

```bash
cd ~/src/pumphouse
python3 monitor/occupancy.py
```

Output:
```
Occupancy Status Test
==================================================
Occupied: True
Status: OCCUPIED until 12-23
Current Guest: Anastasia Irwin
Next Check-in: 01-16
Next Guest: Emily Buchanan

Current & Upcoming Reservations (6 weeks):
--------------------------------------------------
Fri 12-19 → Tue 12-23  Anastasia Irwin  (HomeToGo)
Fri 01-16 → Mon 01-19  Emily Buchanan  (Airbnb)
```

### Automatic CHECK-IN/CHECK-OUT Logging

The cron job runs twice daily (8 AM and 8 PM):
```bash
0 8 * * * /home/pi/src/pumphouse/update_reservations.sh >> reservation_updates.log 2>&1
0 20 * * * /home/pi/src/pumphouse/update_reservations.sh >> reservation_updates.log 2>&1
```

This automatically:
1. Downloads latest reservations
2. Detects CHECK-IN/CHECK-OUT events
3. Logs new reservations
4. Updates the snapshot for next comparison

## Data Flow

```
┌─────────────────────────┐
│ scrape_reservations.py  │
│  (TrackHS scraper)      │
└──────────┬──────────────┘
           │
           v
┌─────────────────────────┐
│  reservations.csv       │ ←──┐
│  (All bookings)         │    │
└──────────┬──────────────┘    │
           │                   │
           v                   │
┌─────────────────────────┐    │
│check_new_reservations.py│    │
│  - CHECK-IN/OUT events  │    │
│  - New booking alerts   │    │
└──────────┬──────────────┘    │
           │                   │
           v                   │
┌─────────────────────────┐    │
│     events.csv          │    │
│  - CHECK-IN             │    │
│  - CHECK-OUT            │    │
│  - NEW_RESERVATION      │    │
└─────────────────────────┘    │
                               │
┌─────────────────────────┐    │
│  monitor/poll.py        │    │
│  (Every 15 min)         │────┘
└──────────┬──────────────┘
           │
           v
┌─────────────────────────┐
│    snapshots.csv        │
│  (occupied: YES/NO)     │
└──────────┬──────────────┘
           │
           v
┌─────────────────────────┐
│   Web Dashboard         │
│  - Occupancy box        │
│  - Reservations table   │
│  - Events log           │
└─────────────────────────┘
```

## Configuration

All times are hardcoded in `monitor/occupancy.py`:

```python
CHECKIN_HOUR = 16  # 4:00 PM
CHECKOUT_HOUR = 10  # 10:00 AM
```

To change check-in/check-out times, edit these constants.

## Troubleshooting

### Occupancy showing "NO" when property is occupied

**Causes:**
1. `reservations.csv` doesn't exist or is empty
2. Reservation status isn't "Confirmed" or "Checked In"
3. Current time is before check-in time (4 PM) or after check-out time (10 AM)

**Fix:**
```bash
# Run scraper manually
python3 scrape_reservations.py

# Check occupancy
python3 monitor/occupancy.py
```

### CHECK-IN/CHECK-OUT events not appearing

**Causes:**
1. Cron job not installed
2. Event was already logged (only logs once per guest within 24 hours)
3. Current time is not within 4 hours of check-in/check-out time

**Check:**
```bash
# View cron jobs
crontab -l | grep update_reservations

# Test manually
python3 check_new_reservations.py --debug
```

### "occupied" column missing from snapshots

**Fix:**
```bash
# The column was automatically added
head -1 snapshots.csv
# Should show: timestamp,duration_seconds,...,occupied
```

If missing, run:
```bash
python3 << 'EOF'
import csv
with open('snapshots.csv', 'r') as f:
    reader = csv.reader(f)
    rows = list(reader)
if len(rows) > 0:
    header = rows[0]
    if 'occupied' not in header:
        header.append('occupied')
        rows[0] = header
        with open('snapshots.csv', 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(rows)
        print("✓ Fixed")
EOF
```

## Future Enhancements

Possible improvements:
- Configurable check-in/check-out times per reservation
- Water usage analysis by guest
- Automatic turnover notifications (cleaning reminders)
- Integration with smart home systems (lights, thermostat)
- Revenue tracking and reporting
- Occupancy rate calculations
