#!/usr/bin/env python3
"""
TrackHS Work Orders Scraper

Logs into meredith.trackhs.com and scrapes work orders from the owner portal
for the past N months (default 3). Saves the data to work_orders.csv.

Usage:
    python scrape_work_orders.py [--debug] [--months N] [--output FILENAME]
"""

import argparse
import csv
import requests
from bs4 import BeautifulSoup
from datetime import datetime, date
import sys
from pathlib import Path

TRACKHS_BASE_URL = "https://meredith.trackhs.com"
OWNER_PORTAL_URL = f"{TRACKHS_BASE_URL}/owner/"
LOGIN_URL = OWNER_PORTAL_URL
WORK_ORDERS_URL = f"{TRACKHS_BASE_URL}/owner/work-orders/"

DATA_DIR = Path.home() / '.local' / 'share' / 'pumphouse'
DATA_DIR.mkdir(parents=True, exist_ok=True)

SECRETS_PATH = Path.home() / ".config" / "pumphouse" / "secrets.conf"

MONTH_NAMES = [
    '', 'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December'
]


def load_secrets():
    if not SECRETS_PATH.exists():
        print(f"ERROR: Secrets file not found: {SECRETS_PATH}")
        sys.exit(1)

    secrets = {}
    with open(SECRETS_PATH, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                secrets[key.strip()] = value.strip()

    username = secrets.get('TRACKHS_USERNAME')
    password = secrets.get('TRACKHS_PASSWORD')

    if not username or not password:
        print(f"ERROR: TRACKHS_USERNAME and TRACKHS_PASSWORD must be set in {SECRETS_PATH}")
        sys.exit(1)

    return username, password


USERNAME, PASSWORD = load_secrets()


def create_session():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    return session


def login(session, debug=False):
    try:
        if debug:
            print(f"Fetching login page: {LOGIN_URL}")

        response = session.get(LOGIN_URL)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        login_form = soup.find('form')

        if not login_form:
            print("ERROR: Could not find login form on page")
            return False

        form_action = login_form.get('action', '')
        if not form_action or form_action in ['/login', '/owner/login']:
            form_action = LOGIN_URL
        elif not form_action.startswith('http'):
            form_action = TRACKHS_BASE_URL + form_action if form_action.startswith('/') else LOGIN_URL.rstrip('/') + '/' + form_action

        payload = {}
        for hidden_input in login_form.find_all('input', type='hidden'):
            name = hidden_input.get('name')
            if name:
                payload[name] = hidden_input.get('value', '')

        username_field = (login_form.find('input', {'name': 'username'}) or
                          login_form.find('input', {'type': 'email'}) or
                          login_form.find('input', {'name': 'email'}))
        password_field = login_form.find('input', {'type': 'password'})

        payload[username_field.get('name') if username_field else 'username'] = USERNAME
        payload[password_field.get('name') if password_field else 'password'] = PASSWORD

        response = session.post(form_action, data=payload, allow_redirects=True)
        response.raise_for_status()

        if debug:
            print(f"Login response URL: {response.url}")

        if 'Log in to get started' in response.text or 'Login - Owner Connect' in response.text:
            print("ERROR: Login failed")
            return False

        if debug:
            print("✓ Login successful")

        return True

    except requests.RequestException as e:
        print(f"ERROR during login: {e}")
        return False


def get_unit_id(session, debug=False):
    """Discover the unit ID from the work orders page select element."""
    try:
        response = session.get(WORK_ORDERS_URL)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        unit_select = soup.find('select', {'name': 'unit'})
        if unit_select:
            first_option = unit_select.find('option', value=True)
            if first_option and first_option.get('value'):
                unit_id = first_option['value']
                if debug:
                    print(f"Discovered unit ID: {unit_id} ({first_option.get_text(strip=True)})")
                return unit_id
    except Exception as e:
        if debug:
            print(f"Could not discover unit ID: {e}")
    return None


def parse_work_orders_table(html, year, month, debug=False):
    """Parse work orders from a monthly report HTML page."""
    soup = BeautifulSoup(html, 'html.parser')

    for table in soup.find_all('table'):
        # Headers may be <th> directly inside <thead> (no wrapping <tr>)
        thead = table.find('thead')
        if thead:
            headers = [th.get_text(strip=True) for th in thead.find_all('th')]
        else:
            first_row = table.find('tr')
            headers = [th.get_text(strip=True) for th in first_row.find_all(['th', 'td'])] if first_row else []

        if not any(h in headers for h in ['WO #', 'Status', 'Summary']):
            continue

        tbody = table.find('tbody')
        data_rows = tbody.find_all('tr') if tbody else table.find_all('tr')[1:]

        # Each work order spans: a header row (WO # non-empty), one or more
        # detail rows (WO # empty, contains description + amount), and a Sub-Total row.
        # Merge them into one row per work order.
        wo_idx = headers.index('WO #') if 'WO #' in headers else 0
        summary_idx = headers.index('Summary') if 'Summary' in headers else -1
        total_idx = headers.index('Total') if 'Total' in headers else -1

        rows = []
        current = None
        for tr in data_rows:
            cells = [td.get_text(strip=True) for td in tr.find_all('td')]
            if not cells or all(c == '' for c in cells):
                continue

            first = cells[0] if cells else ''

            # Sub-total row — flush current and skip
            if first.lower().startswith('sub-total') or first.lower().startswith('total charges'):
                if current:
                    rows.append(current)
                    current = None
                continue

            entry = dict(zip(headers, cells))

            if entry.get('WO #'):
                # New work order header row
                if current:
                    rows.append(current)
                entry['Month'] = f"{MONTH_NAMES[month]} {year}"
                current = entry
            else:
                # Detail row — append description and fill in amount
                if current is None:
                    continue
                detail_summary = cells[summary_idx] if summary_idx >= 0 and summary_idx < len(cells) else ''
                detail_total = cells[total_idx] if total_idx >= 0 and total_idx < len(cells) else ''
                if detail_summary and summary_idx >= 0:
                    existing = current.get('Summary', '')
                    current['Summary'] = f"{existing} — {detail_summary}" if existing else detail_summary
                if detail_total and total_idx >= 0 and not current.get('Total'):
                    current['Total'] = detail_total

        if current:
            rows.append(current)

        if debug:
            print(f"  {year}-{month:02d}: found {len(rows)} work order(s)")
        return headers, rows

    if debug:
        print(f"  {year}-{month:02d}: no matching table found")
    return [], []


def scrape_work_orders(session, months=3, debug=False):
    """Scrape work orders for the past N months. Returns (fieldnames, all_rows)."""
    unit_id = get_unit_id(session, debug=debug)
    if not unit_id:
        print("ERROR: Could not determine unit ID")
        return None, None

    today = date.today()
    all_rows = []
    all_headers = []

    for i in range(months):
        # Walk backwards: current month, then prior months
        month = today.month - i
        year = today.year
        while month <= 0:
            month += 12
            year -= 1

        params = {'unit': unit_id, 'year': str(year), 'period': str(month)}
        if debug:
            print(f"Fetching {MONTH_NAMES[month]} {year}...")

        try:
            response = session.get(WORK_ORDERS_URL, params=params)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"ERROR fetching {year}-{month:02d}: {e}")
            continue

        headers, rows = parse_work_orders_table(response.text, year, month, debug=debug)
        if headers and not all_headers:
            all_headers = headers + ['Month']
        all_rows.extend(rows)

    return all_headers, all_rows


def save_work_orders(headers, rows, output_file, debug=False):
    if not headers:
        headers = ['WO #', 'Status', 'Vendor', 'Name', 'Date', 'Summary', 'Total', 'Month']

    with open(output_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)

    if debug:
        print(f"✓ Saved {len(rows)} work orders to {output_file}")


def main():
    parser = argparse.ArgumentParser(description='Download TrackHS work orders')
    parser.add_argument('--debug', action='store_true', help='Enable debug output')
    parser.add_argument('--months', type=int, default=3, help='Number of months to fetch (default: 3)')
    parser.add_argument('--output', default=str(DATA_DIR / 'work_orders.csv'), help='Output CSV filename')
    args = parser.parse_args()

    print("TrackHS Work Orders Scraper")
    print("=" * 50)

    session = create_session()

    print("\n[1/3] Logging in...")
    if not login(session, debug=args.debug):
        print("✗ Login failed")
        return 1
    print("✓ Login successful")

    print(f"\n[2/3] Fetching work orders for past {args.months} months...")
    headers, rows = scrape_work_orders(session, months=args.months, debug=args.debug)
    if headers is None:
        print("✗ Failed to fetch work orders")
        return 1
    print(f"✓ Found {len(rows)} work order(s) across {args.months} months")

    print("\n[3/3] Saving work orders...")
    save_work_orders(headers, rows, args.output, debug=args.debug)
    print(f"✓ Saved to: {args.output}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
