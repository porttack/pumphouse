# TrackHS Reservation Scraper

Automated system to scrape reservations from TrackHS and notify when new bookings come in.

## Overview

This system consists of:
1. **scrape_reservations.py** - Downloads reservations CSV from TrackHS
2. **check_new_reservations.py** - Detects new bookings and sends notifications
3. **update_reservations.sh** - Wrapper script that runs both
4. **Cron jobs** - Scheduled to run twice daily (8 AM and 8 PM)

## Setup

### 1. Credentials

Credentials are stored in `~/.config/pumphouse/secrets.conf`:

```bash
TRACKHS_USERNAME=your-email@example.com
TRACKHS_PASSWORD=your-password
```

The credentials have already been added during setup.

### 2. Install Cron Jobs

Run the setup script to schedule automatic scraping:

```bash
cd ~/src/pumphouse
./setup_reservation_cron.sh
```

This will:
- Create cron jobs to run at 8 AM and 8 PM daily
- Download reservations and check for new bookings
- Log to `reservation_updates.log`

### 3. Verify Cron Installation

```bash
crontab -l | grep update_reservations
```

You should see two entries for 8 AM and 8 PM.

## Manual Usage

### Download Reservations Only

```bash
python3 scrape_reservations.py
```

This creates/updates `reservations.csv` with all current reservations.

### Check for Reservation Changes

```bash
python3 check_new_reservations.py
```

This compares current reservations with the snapshot and:
- Logs new reservations to `events.csv` with type `NEW_RESERVATION`
- Logs canceled/removed reservations with type `CANCELED_RESERVATION`
- Logs modified reservations with type `CHANGED_RESERVATION` (tracks changes to
  Check-In, Checkout, Nights, Guest, Type, Income, Status)
- Sends ntfy push notifications for each type (if enabled)
- Updates the snapshot for next comparison

### Run Complete Update

```bash
./update_reservations.sh
```

This runs both the scraper and checker in sequence.

## Files

- `reservations.csv` - Current reservations downloaded from TrackHS
- `reservations_snapshot.csv` - Previous snapshot for comparison
- `reservation_updates.log` - Log of automatic updates
- `scrape_reservations.py` - Main scraper script
- `check_new_reservations.py` - New reservation detector
- `update_reservations.sh` - Wrapper script for cron
- `setup_reservation_cron.sh` - Cron installation script

## Notifications

Three types of changes are detected on each run:

### New Reservation
1. **Event Logging**: Added to `events.csv`:
   ```
   2025-12-21 14:00:00,NEW_RESERVATION,John Doe | 2026-01-15 to 2026-01-18 (3n) | Airbnb | $450.00
   ```
2. **Push Notification**: ntfy title "New Reservation - [Guest]", tags: `calendar house`
   - Details: check-in, check-out, nights, type, income, booked date

### Canceled Reservation
1. **Event Logging**: Added to `events.csv`:
   ```
   2025-12-21 14:00:00,CANCELED_RESERVATION,John Doe | 2026-01-15 to 2026-01-18 (3n) | Airbnb | $450.00
   ```
2. **Push Notification**: ntfy title "Reservation Canceled - [Guest]", tags: `calendar x`
   - Details: check-in, check-out, nights, type, income

### Changed Reservation
1. **Event Logging**: Added to `events.csv` with a compact change summary:
   ```
   2025-12-21 14:00:00,CHANGED_RESERVATION,John Doe | 2026-01-16 to 2026-01-19 (3n) | Airbnb | $450.00 | Check-In: 2026-01-15→2026-01-16; Checkout: 2026-01-18→2026-01-19
   ```
2. **Push Notification**: ntfy title "Reservation Changed - [Guest]", tags: `calendar pencil`
   - Lists each changed field as `old → new`, then full current details

Tracked fields for change detection: `Check-In`, `Checkout`, `Nights`, `Guest`, `Type`, `Income`, `Status`

## Data Fields

The scraper captures these fields from TrackHS:

- **Reservation Id** - Unique booking identifier
- **Status** - Confirmed, Checked Out, etc.
- **Type** - Airbnb, Vrbo, Regular - Renter, Owner Stay, etc.
- **Unit** - Property name
- **Guest** - Guest name
- **Booked Date** - When reservation was made
- **Check-In** - Check-in date
- **Checkout** - Check-out date
- **Nights** - Number of nights
- **Income** - Revenue amount
- **Currency** - USD, etc.

## Troubleshooting

### View Logs

```bash
# View update log
tail -f ~/src/pumphouse/reservation_updates.log

# View recent events
tail ~/src/pumphouse/events.csv | grep -E 'NEW_RESERVATION|CANCELED_RESERVATION|CHANGED_RESERVATION'
```

### Test Manual Run

```bash
cd ~/src/pumphouse
./update_reservations.sh
```

### Force Notification Test

```bash
# Remove snapshot to trigger all reservations as "new" (for testing)
rm ~/src/pumphouse/reservations_snapshot.csv
python3 check_new_reservations.py --debug
```

### Check Cron Status

```bash
# View crontab
crontab -l

# Check system log for cron execution
grep CRON /var/log/syslog | grep update_reservations | tail -20
```

## Integration with Monitor

Reservation events are logged to `events.csv` and will appear:
- In the web dashboard under "Recent Events"
- In the events.csv file with types `NEW_RESERVATION`, `CANCELED_RESERVATION`, or `CHANGED_RESERVATION`

The reservation data is separate from the tank monitoring but uses the same event logging system for consistency.

## Security

- Credentials stored in `~/.config/pumphouse/secrets.conf` (not in git)
- HTTPS connection to TrackHS
- Session cookies used for authentication
- No sensitive data stored in logs

## Future Enhancements

Possible improvements:
- Email notifications with full reservation details
- Revenue tracking and reporting
- Integration with calendar services
- Occupancy rate calculations
- Automated guest communication triggers
