#!/usr/bin/env python3
"""
Check for new work orders and alert via notification system.

Compares current work_orders.csv with previous snapshot to detect new entries.
Logs new work orders to events.csv and sends ntfy notifications.

Usage:
    python check_new_work_orders.py [--debug]
"""

import argparse
import csv
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from monitor.config import EVENTS_FILE, WORK_ORDERS_FILE

SNAPSHOT_FILE = WORK_ORDERS_FILE.parent / 'work_orders_snapshot.csv'


def load_work_orders_csv(filepath):
    """Load work orders keyed by WO #. Skips rows with no WO #."""
    orders = {}
    if not filepath.exists():
        return orders
    try:
        with open(filepath, 'r') as f:
            for row in csv.DictReader(f):
                wo_id = row.get('WO #', '').strip()
                if wo_id:
                    orders[wo_id] = row
    except Exception as e:
        print(f"ERROR reading {filepath}: {e}")
    return orders


def log_event(wo):
    """Append a NEW_WORK_ORDER entry to events.csv."""
    try:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        summary = wo.get('Summary', '').strip()
        total = wo.get('Total', '').strip()
        date = wo.get('Date', '').strip()
        status = wo.get('Status', '').strip()
        wo_id = wo.get('WO #', '').strip()

        note = f"WO#{wo_id} | {date} | {summary}"
        if total:
            note += f" | {total}"

        event_data = {
            'timestamp': timestamp,
            'event_type': 'NEW_WORK_ORDER',
            'pressure_state': '',
            'float_state': '',
            'tank_gallons': '',
            'tank_depth': '',
            'tank_percentage': '',
            'estimated_gallons': '',
            'relay_bypass': '',
            'relay_supply_override': '',
            'notes': note,
        }

        file_exists = EVENTS_FILE.exists()
        with open(EVENTS_FILE, 'a', newline='') as f:
            if file_exists:
                with open(EVENTS_FILE, 'r') as rf:
                    fieldnames = csv.DictReader(rf).fieldnames
            else:
                fieldnames = list(event_data.keys())

            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            if not file_exists:
                writer.writeheader()
            writer.writerow(event_data)

        return True
    except Exception as e:
        print(f"ERROR logging event: {e}")
        return False


def send_alert(wo, debug=False):
    """Send ntfy notification for a new work order."""
    try:
        from monitor.config import ENABLE_NOTIFICATIONS, DASHBOARD_URL
        from monitor.ntfy import send_notification

        if not ENABLE_NOTIFICATIONS:
            if debug:
                print("Notifications disabled in config")
            return

        wo_id = wo.get('WO #', '?')
        summary = wo.get('Summary', 'No description').strip()
        date = wo.get('Date', '').strip()
        status = wo.get('Status', '').strip()
        total = wo.get('Total', '').strip()
        vendor = wo.get('Vendor', '').strip()

        title = f"New Work Order — {summary[:50]}"
        message = f"WO #{wo_id}\nDate: {date}\nStatus: {status}\nVendor: {vendor}"
        if total:
            message += f"\nTotal: {total}"

        send_notification(
            title=title,
            message=message,
            priority='default',
            tags=['wrench', 'house'],
            click_url=DASHBOARD_URL,
            debug=debug,
        )
        if debug:
            print(f"✓ Sent ntfy notification for WO#{wo_id}")

    except Exception as e:
        print(f"ERROR sending notification: {e}")


def main():
    parser = argparse.ArgumentParser(description='Check for new TrackHS work orders')
    parser.add_argument('--debug', action='store_true', help='Enable debug output')
    args = parser.parse_args()

    current = load_work_orders_csv(WORK_ORDERS_FILE)
    previous = load_work_orders_csv(SNAPSHOT_FILE)

    if args.debug:
        print(f"Current work orders: {len(current)}")
        print(f"Previous work orders: {len(previous)}")

    new_ids = set(current.keys()) - set(previous.keys())

    if not new_ids:
        if args.debug:
            print("No new work orders found")
    else:
        print(f"Found {len(new_ids)} new work order(s)")
        for wo_id in sorted(new_ids):
            wo = current[wo_id]
            summary = wo.get('Summary', '')[:60]
            print(f"  NEW: WO#{wo_id} — {summary}")
            log_event(wo)
            send_alert(wo, debug=args.debug)

    # Save current as new snapshot
    try:
        shutil.copy2(WORK_ORDERS_FILE, SNAPSHOT_FILE)
        if args.debug:
            print(f"✓ Snapshot updated")
    except Exception as e:
        print(f"ERROR saving snapshot: {e}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
