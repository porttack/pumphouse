# TrackHS Reservation Scraper

Automated system to scrape reservations from TrackHS and notify when bookings change.

---

## Components

1. **`scrape_reservations.py`** — Downloads reservations CSV from TrackHS
2. **`check_new_reservations.py`** — Detects new/changed/canceled bookings, sends notifications
3. **`update_reservations.sh`** — Wrapper script that runs both in sequence
4. **Cron jobs** — Scheduled to run at 8 AM and 8 PM daily

---

## Setup

### 1. Credentials

Add to `~/.config/pumphouse/secrets.conf`:

```ini
TRACKHS_USERNAME=your-email@example.com
TRACKHS_PASSWORD=your-password
```

### 2. Install cron jobs

```bash
cd ~/src/pumphouse
./setup_reservation_cron.sh
```

This creates cron jobs to run at 8 AM and 8 PM daily.

### 3. Verify

```bash
crontab -l | grep update_reservations
```

Should show two entries for 8 AM and 8 PM.

---

## Manual Usage

```bash
# Download reservations only
python3 scrape_reservations.py

# Check for changes (compare with snapshot, send notifications)
python3 check_new_reservations.py

# Run both in sequence
./update_reservations.sh

# Force all reservations to appear as "new" (for testing)
rm reservations_snapshot.csv
python3 check_new_reservations.py --debug
```

---

## Notification Events

Three types of changes are detected and logged to `events.csv`:

### New Reservation

```
2025-12-21 14:00:00,NEW_RESERVATION,John Doe | 2026-01-15 to 2026-01-18 (3n) | Airbnb | $450.00
```

ntfy notification: "New Reservation — [Guest]" with check-in, check-out, nights, type, income, booked date.

### Canceled Reservation

```
2025-12-21 14:00:00,CANCELED_RESERVATION,John Doe | 2026-01-15 to 2026-01-18 (3n) | Airbnb | $450.00
```

ntfy notification: "Reservation Canceled — [Guest]".

### Changed Reservation

```
2025-12-21 14:00:00,CHANGED_RESERVATION,John Doe | 2026-01-16 to 2026-01-19 (3n) | Airbnb | $450.00 | Check-In: 2026-01-15→2026-01-16; Checkout: 2026-01-18→2026-01-19
```

ntfy notification: "Reservation Changed — [Guest]" with each changed field shown as `old → new`.

**Tracked fields**: Check-In, Checkout, Nights, Guest, Type, Income, Status

---

## Data Fields

Fields captured from TrackHS:

| Field | Description |
|-------|-------------|
| Reservation Id | Unique booking identifier |
| Status | Confirmed, Checked Out, etc. |
| Type | Airbnb, Vrbo, Regular - Renter, Owner Stay, etc. |
| Unit | Property name |
| Guest | Guest name |
| Booked Date | When reservation was made |
| Check-In | Check-in date |
| Checkout | Check-out date |
| Nights | Number of nights |
| Income | Revenue amount |
| Currency | USD, etc. |

---

## Files

| File | Description |
|------|-------------|
| `reservations.csv` | Current reservations downloaded from TrackHS |
| `reservations_snapshot.csv` | Previous snapshot for change detection |
| `reservation_updates.log` | Log of automatic updates |
| `scrape_reservations.py` | Main scraper script |
| `check_new_reservations.py` | Change detector and notifier |
| `update_reservations.sh` | Cron wrapper script |
| `setup_reservation_cron.sh` | Cron installation script |

---

## Integration with Dashboard

Reservation events appear in the web dashboard under "Recent Events" with types `NEW_RESERVATION`, `CANCELED_RESERVATION`, or `CHANGED_RESERVATION`. The full reservations table in the dashboard reads directly from `reservations.csv`.

---

## Troubleshooting

```bash
# View update log
tail -f ~/src/pumphouse/reservation_updates.log

# View recent reservation events
tail ~/src/pumphouse/events.csv | grep -E 'NEW_RESERVATION|CANCELED_RESERVATION|CHANGED_RESERVATION'

# Check cron execution
grep CRON /var/log/syslog | grep update_reservations | tail -20
```

---

## Security

- Credentials stored in `~/.config/pumphouse/secrets.conf` (mode 600, not in git)
- HTTPS connection to TrackHS
- No sensitive data stored in logs
