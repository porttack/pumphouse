# Occupancy & Reservation Tracking

Occupancy detection from `reservations.csv` integrated throughout the monitoring system.

---

## How Occupancy is Determined

The `monitor/occupancy.py` module reads `reservations.csv` and determines:

- **Current occupancy**: Is the property occupied RIGHT NOW?
  - Check-in time: 4:00 PM
  - Check-out time: 10:00 AM
  - Returns: occupied status, current guest, checkout date
- **Next reservation**: Upcoming guest + check-in date
- **Upcoming reservations**: All reservations in the next 6 weeks

### Manual check

```bash
cd ~/src/pumphouse
source venv/bin/activate
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

---

## Where Occupancy Appears

### Web dashboard
- **Occupancy sensor box**: "OCCUPIED until MM-DD" or "UNOCCUPIED" (orange / green)
- **Next guest**: Check-in date and name
- **Reservations table**: Current & upcoming reservations (next 6 weeks) with check-in/out, guest, nights, type, income
  - Income and repeat columns hidden unless authenticated

### Snapshots CSV
The `occupied` column (YES/NO) is logged with every 15-minute snapshot for historical water usage correlation.

### E-paper display
The occupancy bar at the bottom of the tank graph shows:
- `occupied until M/DD` — with checkout date
- `next checkin M/DD` — with check-in date
- `unoccupied` — when no upcoming reservations

The display also switches to "Save Water" mode for tenants when tank is low.

---

## Automatic CHECK-IN / CHECK-OUT Events

The reservation cron job (`update_reservations.sh`, running twice daily) automatically logs events to `events.csv`:

| Event | When | Details |
|-------|------|---------|
| `CHECK-IN` | Detected within 4 hours of 4:00 PM check-in | Guest name, dates, reservation type |
| `CHECK-OUT` | Detected within 4 hours of 10:00 AM check-out | Guest name, dates |

Events appear in the web dashboard under "Recent Events" and in `events.csv`.

---

## Email Integration

- Daily 6 AM status email changes subject to **"Turn on heat!"** when a tenant checks in today
- **Checkout reminder** at 11 AM when tenant checks out today: "Turn down thermostat"

---

## Configuration

Check-in/check-out times are set in `monitor/occupancy.py`:

```python
CHECKIN_HOUR = 16   # 4:00 PM
CHECKOUT_HOUR = 10  # 10:00 AM
```

Owner vs. tenant reservation types are set in `monitor/config.py`:

```python
EPAPER_OWNER_STAY_TYPES = ['Owner Stay', 'Owner Stay, Full Clean']
```

---

## Troubleshooting

### Occupancy showing "NO" when property is occupied

**Causes:**
1. `reservations.csv` doesn't exist or is empty
2. Reservation status isn't "Confirmed" or "Checked In"
3. Current time is before check-in (4 PM) or after check-out (10 AM)

**Fix:**
```bash
python3 scrape_reservations.py    # Re-download reservations
python3 monitor/occupancy.py      # Check current status
```

### CHECK-IN / CHECK-OUT events not appearing

**Causes:**
1. Cron job not installed
2. Event already logged (only logs once per guest within 24 hours)
3. Current time not within 4 hours of check-in/out time

**Check:**
```bash
crontab -l | grep update_reservations
python3 check_new_reservations.py --debug
```

### `occupied` column missing from snapshots

```bash
head -1 snapshots.csv
# Should show: timestamp,duration_seconds,...,occupied
```

If missing, add manually:
```bash
python3 << 'EOF'
import csv
with open('snapshots.csv', 'r') as f:
    reader = csv.reader(f)
    rows = list(reader)
if rows and 'occupied' not in rows[0]:
    rows[0].append('occupied')
    with open('snapshots.csv', 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    print("Fixed")
EOF
```

---

## Data Flow

```
scrape_reservations.py
  (TrackHS scraper)
        │
        ▼
  reservations.csv ◄──────────────┐
  (All bookings)                  │
        │                         │
        ▼                         │
check_new_reservations.py         │
  - CHECK-IN/OUT events           │
  - New booking alerts            │
        │                         │
        ▼                         │
    events.csv                    │
  - CHECK-IN                      │
  - CHECK-OUT                     │
  - NEW_RESERVATION               │
                                  │
  monitor/poll.py ────────────────┘
  (every 15 min)
        │
        ▼
   snapshots.csv
   (occupied: YES/NO)
        │
        ▼
   Web Dashboard
  - Occupancy box
  - Reservations table
  - Events log
```
