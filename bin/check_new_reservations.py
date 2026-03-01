#!/usr/bin/env python3
"""
Check for new reservations and alert via notification system.

Compares current reservations.csv with previous snapshot to detect new bookings.
Logs new reservations to events.csv and sends notifications.

Usage:
    python check_new_reservations.py [--debug]
"""

import argparse
import csv
import os
import sys
from datetime import datetime
from pathlib import Path

# Add monitor module to path
sys.path.insert(0, str(Path(__file__).parent.parent))  # project root
from monitor.config import EVENTS_FILE, RESERVATIONS_FILE, RESERVATIONS_SNAPSHOT_FILE

def load_reservations_csv(filepath):
    """
    Load reservations from CSV file.

    Returns:
        dict: {reservation_id: reservation_data_dict}
    """
    reservations = {}

    if not os.path.exists(filepath):
        return reservations

    try:
        with open(filepath, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                res_id = row.get('Reservation Id')
                if res_id:
                    reservations[res_id] = row
    except Exception as e:
        print(f"ERROR reading {filepath}: {e}")

    return reservations

def save_snapshot(current_csv, snapshot_csv):
    """Save current reservations as snapshot for next comparison."""
    try:
        import shutil
        shutil.copy2(current_csv, snapshot_csv)
        return True
    except Exception as e:
        print(f"ERROR saving snapshot: {e}")
        return False

def detect_new_reservations(current_file, snapshot_file, debug=False):
    """
    Compare current reservations with snapshot to find new bookings.

    Args:
        current_file: Path to current reservations.csv
        snapshot_file: Path to previous snapshot
        debug: Print debug info

    Returns:
        list: List of new reservation dicts
    """
    current = load_reservations_csv(current_file)
    previous = load_reservations_csv(snapshot_file)

    if debug:
        print(f"Current reservations: {len(current)}")
        print(f"Previous reservations: {len(previous)}")

    # Find reservation IDs that are in current but not in previous
    new_ids = set(current.keys()) - set(previous.keys())

    new_reservations = [current[rid] for rid in new_ids]

    # Sort by Check-In date (newest first)
    new_reservations.sort(key=lambda x: x.get('Check-In', ''), reverse=True)

    return new_reservations


def detect_canceled_reservations(current_file, snapshot_file, debug=False):
    """
    Compare current reservations with snapshot to find canceled/removed bookings.

    Args:
        current_file: Path to current reservations.csv
        snapshot_file: Path to previous snapshot
        debug: Print debug info

    Returns:
        list: List of canceled reservation dicts (from snapshot)
    """
    current = load_reservations_csv(current_file)
    previous = load_reservations_csv(snapshot_file)

    # Find reservation IDs that were in previous but are no longer in current
    canceled_ids = set(previous.keys()) - set(current.keys())

    canceled_reservations = [previous[rid] for rid in canceled_ids]

    # Sort by Check-In date (soonest first - most urgent)
    canceled_reservations.sort(key=lambda x: x.get('Check-In', ''))

    if debug and canceled_reservations:
        print(f"Found {len(canceled_reservations)} canceled reservation(s)")

    return canceled_reservations


# Fields to compare when checking for changes
TRACKED_FIELDS = ['Check-In', 'Checkout', 'Nights', 'Guest', 'Type', 'Income', 'Status']


def detect_changed_reservations(current_file, snapshot_file, debug=False):
    """
    Compare current reservations with snapshot to find modified bookings.

    Args:
        current_file: Path to current reservations.csv
        snapshot_file: Path to previous snapshot
        debug: Print debug info

    Returns:
        list: List of (current_res, old_res, changed_fields) tuples
    """
    current = load_reservations_csv(current_file)
    previous = load_reservations_csv(snapshot_file)

    # Only look at IDs present in both (not new, not canceled)
    common_ids = set(current.keys()) & set(previous.keys())

    changes = []
    for rid in common_ids:
        cur = current[rid]
        prev = previous[rid]
        changed_fields = []
        for field in TRACKED_FIELDS:
            if cur.get(field, '') != prev.get(field, ''):
                changed_fields.append((field, prev.get(field, ''), cur.get(field, '')))
        if changed_fields:
            changes.append((cur, prev, changed_fields))

    # Sort by Check-In date (soonest first)
    changes.sort(key=lambda x: x[0].get('Check-In', ''))

    if debug and changes:
        print(f"Found {len(changes)} changed reservation(s)")

    return changes

def log_new_reservation(reservation):
    """
    Log new reservation to events.csv.

    Args:
        reservation: Dict with reservation data
    """
    try:
        events_file = EVENTS_FILE

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        guest = reservation.get('Guest', 'Unknown')
        checkin = reservation.get('Check-In', 'Unknown')
        checkout = reservation.get('Checkout', 'Unknown')
        nights = reservation.get('Nights', '?')
        res_type = reservation.get('Type', 'Unknown')
        income = reservation.get('Income', '0')

        event_data = {
            'timestamp': timestamp,
            'event_type': 'NEW_RESERVATION',
            'pressure_state': '',
            'float_state': '',
            'tank_gallons': '',
            'tank_depth': '',
            'tank_percentage': '',
            'estimated_gallons': '',
            'relay_bypass': '',
            'relay_supply_override': '',
            'notes': f"{guest} | {checkin} to {checkout} ({nights}n) | {res_type} | ${income}"
        }

        # Check if file exists to determine if we need headers
        file_exists = events_file.exists()

        with open(events_file, 'a', newline='') as f:
            # Read first line to get field names from existing file
            if file_exists:
                with open(events_file, 'r') as rf:
                    reader = csv.DictReader(rf)
                    fieldnames = reader.fieldnames
            else:
                # Match the existing events.csv format
                fieldnames = ['timestamp', 'event_type', 'pressure_state', 'float_state',
                             'tank_gallons', 'tank_depth', 'tank_percentage', 'estimated_gallons',
                             'relay_bypass', 'relay_supply_override', 'notes']

            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')

            if not file_exists:
                writer.writeheader()

            writer.writerow(event_data)

        return True

    except Exception as e:
        print(f"ERROR logging to events.csv: {e}")
        return False

def send_notification(reservation, debug=False):
    """
    Send notification for new reservation.

    Args:
        reservation: Dict with reservation data
        debug: Print debug info
    """
    try:
        # Import notification modules
        from monitor.config import ENABLE_NOTIFICATIONS
        from monitor.ntfy import send_notification as ntfy_send

        if not ENABLE_NOTIFICATIONS:
            if debug:
                print("Notifications disabled in config")
            return

        guest = reservation.get('Guest', 'Unknown')
        checkin = reservation.get('Check-In', 'Unknown')
        checkout = reservation.get('Checkout', 'Unknown')
        nights = reservation.get('Nights', '?')
        res_type = reservation.get('Type', 'Unknown')
        income = reservation.get('Income', '0')
        booked_date = reservation.get('Booked Date', 'Unknown')

        title = f"New Reservation - {guest}"
        message = (
            f"Check-in: {checkin}\n"
            f"Check-out: {checkout}\n"
            f"Nights: {nights}\n"
            f"Type: {res_type}\n"
            f"Income: ${income}\n"
            f"Booked: {booked_date}"
        )

        # Send ntfy notification
        try:
            ntfy_send(title, message, tags=['calendar', 'house'])
            if debug:
                print(f"✓ Sent ntfy notification")
        except Exception as e:
            if debug:
                print(f"ntfy notification failed: {e}")

    except Exception as e:
        print(f"ERROR sending notification: {e}")

def send_notification_canceled(reservation, debug=False):
    """
    Send notification for a canceled/removed reservation.

    Args:
        reservation: Dict with reservation data (from snapshot)
        debug: Print debug info
    """
    try:
        from monitor.config import ENABLE_NOTIFICATIONS
        from monitor.ntfy import send_notification as ntfy_send

        if not ENABLE_NOTIFICATIONS:
            if debug:
                print("Notifications disabled in config")
            return

        guest = reservation.get('Guest', 'Unknown')
        checkin = reservation.get('Check-In', 'Unknown')
        checkout = reservation.get('Checkout', 'Unknown')
        nights = reservation.get('Nights', '?')
        res_type = reservation.get('Type', 'Unknown')
        income = reservation.get('Income', '0')

        title = f"Reservation Canceled - {guest}"
        message = (
            f"Check-in: {checkin}\n"
            f"Check-out: {checkout}\n"
            f"Nights: {nights}\n"
            f"Type: {res_type}\n"
            f"Income: ${income}"
        )

        try:
            ntfy_send(title, message, tags=['calendar', 'x'])
            if debug:
                print(f"✓ Sent canceled ntfy notification")
        except Exception as e:
            if debug:
                print(f"ntfy notification failed: {e}")

    except Exception as e:
        print(f"ERROR sending canceled notification: {e}")


def send_notification_changed(current_res, old_res, changed_fields, debug=False):
    """
    Send notification for a changed reservation.

    Args:
        current_res: Dict with current (new) reservation data
        old_res: Dict with old reservation data
        changed_fields: List of (field, old_value, new_value) tuples
        debug: Print debug info
    """
    try:
        from monitor.config import ENABLE_NOTIFICATIONS
        from monitor.ntfy import send_notification as ntfy_send

        if not ENABLE_NOTIFICATIONS:
            if debug:
                print("Notifications disabled in config")
            return

        guest = current_res.get('Guest', 'Unknown')
        checkin = current_res.get('Check-In', 'Unknown')
        checkout = current_res.get('Checkout', 'Unknown')
        nights = current_res.get('Nights', '?')
        res_type = current_res.get('Type', 'Unknown')
        income = current_res.get('Income', '0')

        # Build a summary of what changed
        change_lines = []
        for field, old_val, new_val in changed_fields:
            change_lines.append(f"  {field}: {old_val} → {new_val}")
        changes_str = '\n'.join(change_lines)

        title = f"Reservation Changed - {guest}"
        message = (
            f"Changes:\n{changes_str}\n\n"
            f"Check-in: {checkin}\n"
            f"Check-out: {checkout}\n"
            f"Nights: {nights}\n"
            f"Type: {res_type}\n"
            f"Income: ${income}"
        )

        try:
            ntfy_send(title, message, tags=['calendar', 'pencil'])
            if debug:
                print(f"✓ Sent changed ntfy notification")
        except Exception as e:
            if debug:
                print(f"ntfy notification failed: {e}")

    except Exception as e:
        print(f"ERROR sending changed notification: {e}")


def log_canceled_reservation(reservation):
    """Log canceled reservation to events.csv."""
    try:
        events_file = EVENTS_FILE

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        guest = reservation.get('Guest', 'Unknown')
        checkin = reservation.get('Check-In', 'Unknown')
        checkout = reservation.get('Checkout', 'Unknown')
        nights = reservation.get('Nights', '?')
        res_type = reservation.get('Type', 'Unknown')
        income = reservation.get('Income', '0')

        event_data = {
            'timestamp': timestamp,
            'event_type': 'CANCELED_RESERVATION',
            'pressure_state': '',
            'float_state': '',
            'tank_gallons': '',
            'tank_depth': '',
            'tank_percentage': '',
            'estimated_gallons': '',
            'relay_bypass': '',
            'relay_supply_override': '',
            'notes': f"{guest} | {checkin} to {checkout} ({nights}n) | {res_type} | ${income}"
        }

        file_exists = events_file.exists()
        with open(events_file, 'a', newline='') as f:
            if file_exists:
                with open(events_file, 'r') as rf:
                    reader = csv.DictReader(rf)
                    fieldnames = reader.fieldnames
            else:
                fieldnames = ['timestamp', 'event_type', 'pressure_state', 'float_state',
                             'tank_gallons', 'tank_depth', 'tank_percentage', 'estimated_gallons',
                             'relay_bypass', 'relay_supply_override', 'notes']

            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            if not file_exists:
                writer.writeheader()
            writer.writerow(event_data)

        return True
    except Exception as e:
        print(f"ERROR logging to events.csv: {e}")
        return False


def log_changed_reservation(current_res, old_res, changed_fields):
    """Log changed reservation to events.csv."""
    try:
        events_file = EVENTS_FILE

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        guest = current_res.get('Guest', 'Unknown')
        checkin = current_res.get('Check-In', 'Unknown')
        checkout = current_res.get('Checkout', 'Unknown')
        nights = current_res.get('Nights', '?')
        res_type = current_res.get('Type', 'Unknown')
        income = current_res.get('Income', '0')

        change_summary = '; '.join(f"{f}: {o}→{n}" for f, o, n in changed_fields)

        event_data = {
            'timestamp': timestamp,
            'event_type': 'CHANGED_RESERVATION',
            'pressure_state': '',
            'float_state': '',
            'tank_gallons': '',
            'tank_depth': '',
            'tank_percentage': '',
            'estimated_gallons': '',
            'relay_bypass': '',
            'relay_supply_override': '',
            'notes': f"{guest} | {checkin} to {checkout} ({nights}n) | {res_type} | ${income} | {change_summary}"
        }

        file_exists = events_file.exists()
        with open(events_file, 'a', newline='') as f:
            if file_exists:
                with open(events_file, 'r') as rf:
                    reader = csv.DictReader(rf)
                    fieldnames = reader.fieldnames
            else:
                fieldnames = ['timestamp', 'event_type', 'pressure_state', 'float_state',
                             'tank_gallons', 'tank_depth', 'tank_percentage', 'estimated_gallons',
                             'relay_bypass', 'relay_supply_override', 'notes']

            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            if not file_exists:
                writer.writeheader()
            writer.writerow(event_data)

        return True
    except Exception as e:
        print(f"ERROR logging to events.csv: {e}")
        return False


def check_checkin_checkout_events(events_file, debug=False):
    """
    Check if we need to log CHECK-IN or CHECK-OUT events based on occupancy changes.

    Args:
        events_file: Path to events.csv
        debug: Print debug info

    Returns:
        tuple: (checkin_event, checkout_event) - either can be dict or None
    """
    try:
        from monitor.occupancy import is_occupied, load_reservations, get_checkin_datetime, get_checkout_datetime
        from datetime import datetime, timedelta

        reservations = load_reservations(RESERVATIONS_FILE)
        now = datetime.now()

        # Check recent CHECK-IN events (within last 4 hours)
        for res in reservations:
            checkin = get_checkin_datetime(res.get('Check-In'))
            if checkin:
                # If check-in was within the last 4 hours, log it if not already logged
                time_since_checkin = (now - checkin).total_seconds()
                if 0 < time_since_checkin < 14400:  # 4 hours = 14400 seconds
                    # Check if we already logged this check-in
                    if not was_event_logged(events_file, 'CHECK-IN', res.get('Guest'), within_hours=24):
                        if debug:
                            print(f"CHECK-IN event detected for {res.get('Guest')}")
                        return ({'type': 'CHECK-IN', 'reservation': res}, None)

        # Check recent CHECK-OUT events (within last 4 hours)
        for res in reservations:
            checkout = get_checkout_datetime(res.get('Checkout'))
            if checkout:
                # If check-out was within the last 4 hours, log it if not already logged
                time_since_checkout = (now - checkout).total_seconds()
                if 0 < time_since_checkout < 14400:  # 4 hours
                    if not was_event_logged(events_file, 'CHECK-OUT', res.get('Guest'), within_hours=24):
                        if debug:
                            print(f"CHECK-OUT event detected for {res.get('Guest')}")
                        return (None, {'type': 'CHECK-OUT', 'reservation': res})

    except Exception as e:
        if debug:
            print(f"Error checking CHECK-IN/OUT events: {e}")

    return (None, None)

def was_event_logged(events_file, event_type, guest_name, within_hours=24):
    """Check if an event was already logged recently for a guest."""
    try:
        from datetime import datetime, timedelta

        cutoff_time = datetime.now() - timedelta(hours=within_hours)

        with open(events_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get('event_type') == event_type:
                    # Check if notes contain guest name
                    if guest_name in row.get('notes', ''):
                        # Check timestamp
                        try:
                            ts = datetime.strptime(row['timestamp'], '%Y-%m-%d %H:%M:%S')
                            if ts >= cutoff_time:
                                return True
                        except:
                            pass
    except:
        pass

    return False

def log_checkin_checkout_event(event_data, events_file):
    """Log CHECK-IN or CHECK-OUT event to events.csv."""
    try:
        from datetime import datetime

        res = event_data['reservation']
        event_type = event_data['type']

        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        guest = res.get('Guest', 'Unknown')
        checkin = res.get('Check-In', '')
        checkout = res.get('Checkout', '')
        nights = res.get('Nights', '')
        res_type = res.get('Type', '')

        event_row = {
            'timestamp': timestamp,
            'event_type': event_type,
            'pressure_state': '',
            'float_state': '',
            'tank_gallons': '',
            'tank_depth': '',
            'tank_percentage': '',
            'estimated_gallons': '',
            'relay_bypass': '',
            'relay_supply_override': '',
            'notes': f"{guest} | {checkin} to {checkout} ({nights}n) | {res_type}"
        }

        # Check if file exists
        file_exists = Path(events_file).exists()

        with open(events_file, 'a', newline='') as f:
            if file_exists:
                with open(events_file, 'r') as rf:
                    reader = csv.DictReader(rf)
                    fieldnames = reader.fieldnames
            else:
                fieldnames = ['timestamp', 'event_type', 'pressure_state', 'float_state',
                             'tank_gallons', 'tank_depth', 'tank_percentage', 'estimated_gallons',
                             'relay_bypass', 'relay_supply_override', 'notes']

            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            if not file_exists:
                writer.writeheader()
            writer.writerow(event_row)

        print(f"✓ Logged {event_type} for {guest}")
        return True

    except Exception as e:
        print(f"ERROR logging {event_data['type']}: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='Check for new reservations')
    parser.add_argument('--debug', action='store_true', help='Enable debug output')
    args = parser.parse_args()

    current_file  = RESERVATIONS_FILE
    snapshot_file = RESERVATIONS_SNAPSHOT_FILE
    events_file   = EVENTS_FILE

    if not current_file.exists():
        print(f"No reservations file found at {current_file}")
        print("Run scrape_reservations.py first")
        return 1

    # Check for CHECK-IN/CHECK-OUT events
    checkin_event, checkout_event = check_checkin_checkout_events(events_file, debug=args.debug)

    if checkin_event:
        log_checkin_checkout_event(checkin_event, events_file)

    if checkout_event:
        log_checkin_checkout_event(checkout_event, events_file)

    # Detect new reservations
    new_reservations = detect_new_reservations(current_file, snapshot_file, debug=args.debug)

    if new_reservations:
        print(f"Found {len(new_reservations)} new reservation(s)!")
        print()

        for res in new_reservations:
            guest = res.get('Guest', 'Unknown')
            checkin = res.get('Check-In', 'Unknown')
            checkout = res.get('Checkout', 'Unknown')
            res_type = res.get('Type', 'Unknown')
            income = res.get('Income', '0')

            print(f"  {guest}")
            print(f"  {checkin} → {checkout}")
            print(f"  {res_type} | ${income}")
            print()

            # Log to events.csv
            log_new_reservation(res)

            # Send notification
            send_notification(res, debug=args.debug)

        print(f"✓ Logged {len(new_reservations)} new reservation(s) to events.csv")

    else:
        if args.debug:
            print("No new reservations found")

    # Detect canceled reservations
    canceled_reservations = detect_canceled_reservations(current_file, snapshot_file, debug=args.debug)

    if canceled_reservations:
        print(f"Found {len(canceled_reservations)} canceled reservation(s)!")
        print()

        for res in canceled_reservations:
            guest = res.get('Guest', 'Unknown')
            checkin = res.get('Check-In', 'Unknown')
            checkout = res.get('Checkout', 'Unknown')
            res_type = res.get('Type', 'Unknown')
            income = res.get('Income', '0')

            print(f"  CANCELED: {guest}")
            print(f"  {checkin} → {checkout}")
            print(f"  {res_type} | ${income}")
            print()

            log_canceled_reservation(res)
            send_notification_canceled(res, debug=args.debug)

        print(f"✓ Logged {len(canceled_reservations)} canceled reservation(s) to events.csv")

    else:
        if args.debug:
            print("No canceled reservations found")

    # Detect changed reservations
    changed_reservations = detect_changed_reservations(current_file, snapshot_file, debug=args.debug)

    if changed_reservations:
        print(f"Found {len(changed_reservations)} changed reservation(s)!")
        print()

        for cur_res, old_res, changed_fields in changed_reservations:
            guest = cur_res.get('Guest', 'Unknown')
            checkin = cur_res.get('Check-In', 'Unknown')
            checkout = cur_res.get('Checkout', 'Unknown')
            res_type = cur_res.get('Type', 'Unknown')
            income = cur_res.get('Income', '0')

            print(f"  CHANGED: {guest}")
            print(f"  {checkin} → {checkout}")
            print(f"  {res_type} | ${income}")
            for field, old_val, new_val in changed_fields:
                print(f"    {field}: {old_val!r} → {new_val!r}")
            print()

            log_changed_reservation(cur_res, old_res, changed_fields)
            send_notification_changed(cur_res, old_res, changed_fields, debug=args.debug)

        print(f"✓ Logged {len(changed_reservations)} changed reservation(s) to events.csv")

    else:
        if args.debug:
            print("No changed reservations found")

    # Save current as snapshot for next run
    save_snapshot(current_file, snapshot_file)

    return 0

if __name__ == '__main__':
    sys.exit(main())
