#!/usr/bin/env python3
"""
Occupancy detection based on reservation data.

Determines if property is currently occupied and tracks upcoming reservations.
"""

import csv
from datetime import datetime, timedelta
from pathlib import Path

# Check-in is 4 PM, check-out is 10 AM
CHECKIN_HOUR = 16
CHECKOUT_HOUR = 10

def load_reservations(csv_path):
    """
    Load reservations from CSV file.

    Args:
        csv_path: Path to reservations.csv

    Returns:
        list: List of reservation dicts
    """
    reservations = []

    if not Path(csv_path).exists():
        return reservations

    try:
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Only include confirmed reservations
                status = row.get('Status', '').lower()
                if 'confirmed' in status or 'checked in' in status:
                    reservations.append(row)
    except Exception as e:
        print(f"ERROR reading reservations: {e}")

    return reservations

def parse_date(date_str):
    """
    Parse date string from CSV (YYYY-MM-DD format).

    Args:
        date_str: Date string

    Returns:
        datetime or None
    """
    try:
        return datetime.strptime(date_str, '%Y-%m-%d')
    except:
        return None

def get_checkin_datetime(checkin_date_str):
    """Get check-in datetime (date at 4 PM)."""
    date = parse_date(checkin_date_str)
    if date:
        return date.replace(hour=CHECKIN_HOUR, minute=0, second=0)
    return None

def get_checkout_datetime(checkout_date_str):
    """Get check-out datetime (date at 10 AM)."""
    date = parse_date(checkout_date_str)
    if date:
        return date.replace(hour=CHECKOUT_HOUR, minute=0, second=0)
    return None

def is_occupied(reservations, current_time=None):
    """
    Check if property is currently occupied.

    Args:
        reservations: List of reservation dicts
        current_time: datetime to check (default: now)

    Returns:
        dict: {
            'occupied': bool,
            'current_reservation': dict or None,
            'checkout_date': datetime or None
        }
    """
    if current_time is None:
        current_time = datetime.now()

    for res in reservations:
        checkin = get_checkin_datetime(res.get('Check-In'))
        checkout = get_checkout_datetime(res.get('Checkout'))

        if checkin and checkout:
            # Occupied if current time is between check-in and check-out
            if checkin <= current_time < checkout:
                return {
                    'occupied': True,
                    'current_reservation': res,
                    'checkout_date': checkout
                }

    return {
        'occupied': False,
        'current_reservation': None,
        'checkout_date': None
    }

def get_next_reservation(reservations, current_time=None):
    """
    Get the next upcoming reservation.

    Args:
        reservations: List of reservation dicts
        current_time: datetime to check (default: now)

    Returns:
        dict or None: Next reservation, or None if no upcoming reservations
    """
    if current_time is None:
        current_time = datetime.now()

    upcoming = []

    for res in reservations:
        checkin = get_checkin_datetime(res.get('Check-In'))

        if checkin and checkin > current_time:
            upcoming.append({
                'reservation': res,
                'checkin_datetime': checkin
            })

    if not upcoming:
        return None

    # Sort by check-in date
    upcoming.sort(key=lambda x: x['checkin_datetime'])

    return upcoming[0]['reservation']

def get_upcoming_reservations(reservations, weeks=6, current_time=None):
    """
    Get reservations in the next N weeks.

    Args:
        reservations: List of reservation dicts
        weeks: Number of weeks to look ahead
        current_time: datetime to check (default: now)

    Returns:
        list: List of reservation dicts sorted by check-in date
    """
    if current_time is None:
        current_time = datetime.now()

    cutoff_date = current_time + timedelta(weeks=weeks)

    upcoming = []

    for res in reservations:
        checkin = get_checkin_datetime(res.get('Check-In'))

        if checkin and current_time <= checkin <= cutoff_date:
            upcoming.append(res)

    # Sort by check-in date
    upcoming.sort(key=lambda x: parse_date(x.get('Check-In')))

    return upcoming

def get_current_and_upcoming_reservations(reservations, weeks=6, current_time=None):
    """
    Get current reservation (if occupied) plus upcoming reservations.

    Args:
        reservations: List of reservation dicts
        weeks: Number of weeks to look ahead
        current_time: datetime to check (default: now)

    Returns:
        list: Combined list of current + upcoming reservations
    """
    if current_time is None:
        current_time = datetime.now()

    result = []

    # Add current reservation if occupied
    occupancy = is_occupied(reservations, current_time)
    if occupancy['occupied']:
        result.append(occupancy['current_reservation'])

    # Add upcoming reservations
    upcoming = get_upcoming_reservations(reservations, weeks, current_time)
    result.extend(upcoming)

    return result

def format_date_short(date_str):
    """
    Format date as 'DDD MM-DD' (e.g., 'Sun 12-21').

    Args:
        date_str: Date string in YYYY-MM-DD format

    Returns:
        str: Formatted date
    """
    date = parse_date(date_str)
    if date:
        return date.strftime('%a %m-%d')
    return date_str

def get_occupancy_status(csv_path, current_time=None):
    """
    Get comprehensive occupancy status.

    Args:
        csv_path: Path to reservations.csv
        current_time: datetime to check (default: now)

    Returns:
        dict: {
            'occupied': bool,
            'status_text': str,  # e.g., "OCCUPIED until 12-25" or "UNOCCUPIED"
            'next_checkin': str,  # e.g., "12-28" or None
            'next_guest': str,  # Guest name or None
            'current_guest': str,  # Current guest name or None
            'checkout_date': str  # Checkout date or None
        }
    """
    if current_time is None:
        current_time = datetime.now()

    reservations = load_reservations(csv_path)
    occupancy = is_occupied(reservations, current_time)
    next_res = get_next_reservation(reservations, current_time)

    status = {
        'occupied': occupancy['occupied'],
        'current_guest': None,
        'checkout_date': None,
        'next_checkin': None,
        'next_guest': None
    }

    if occupancy['occupied']:
        checkout = occupancy['checkout_date']
        guest = occupancy['current_reservation'].get('Guest', 'Unknown')
        status['current_guest'] = guest
        status['checkout_date'] = checkout.strftime('%m-%d') if checkout else None
        status['status_text'] = f"OCCUPIED until {status['checkout_date']}" if status['checkout_date'] else "OCCUPIED"
    else:
        status['status_text'] = "UNOCCUPIED"

    if next_res:
        checkin_date = parse_date(next_res.get('Check-In'))
        status['next_checkin'] = checkin_date.strftime('%m-%d') if checkin_date else None
        status['next_guest'] = next_res.get('Guest', 'Unknown')

    return status

if __name__ == '__main__':
    # Test
    import sys
    csv_path = Path(__file__).parent.parent / 'reservations.csv'

    print("Occupancy Status Test")
    print("=" * 50)

    status = get_occupancy_status(csv_path)
    print(f"Occupied: {status['occupied']}")
    print(f"Status: {status['status_text']}")
    print(f"Current Guest: {status['current_guest']}")
    print(f"Next Check-in: {status['next_checkin']}")
    print(f"Next Guest: {status['next_guest']}")

    print("\nCurrent & Upcoming Reservations (6 weeks):")
    print("-" * 50)

    reservations = load_reservations(csv_path)
    current_upcoming = get_current_and_upcoming_reservations(reservations, weeks=6)

    for res in current_upcoming:
        checkin = format_date_short(res.get('Check-In'))
        checkout = format_date_short(res.get('Checkout'))
        guest = res.get('Guest', 'Unknown')
        res_type = res.get('Type', 'Unknown')

        print(f"{checkin} → {checkout}  {guest}  ({res_type})")
