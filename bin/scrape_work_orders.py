#!/usr/bin/env python3
"""
TrackHS Work Orders Scraper

Logs into meredith.trackhs.com and scrapes work orders from the owner portal.
Saves the data to work_orders.csv.

Usage:
    python scrape_work_orders.py [--debug] [--output FILENAME]
"""

import argparse
import csv
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import sys
from pathlib import Path

TRACKHS_BASE_URL = "https://meredith.trackhs.com"
OWNER_PORTAL_URL = f"{TRACKHS_BASE_URL}/owner/"
LOGIN_URL = OWNER_PORTAL_URL
WORK_ORDERS_URL = f"{TRACKHS_BASE_URL}/owner/work-orders/"

DATA_DIR = Path.home() / '.local' / 'share' / 'pumphouse'
DATA_DIR.mkdir(parents=True, exist_ok=True)

SECRETS_PATH = Path.home() / ".config" / "pumphouse" / "secrets.conf"


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
            if form_action.startswith('/'):
                form_action = TRACKHS_BASE_URL + form_action
            else:
                form_action = LOGIN_URL.rstrip('/') + '/' + form_action

        payload = {}
        for hidden_input in login_form.find_all('input', type='hidden'):
            name = hidden_input.get('name')
            value = hidden_input.get('value', '')
            if name:
                payload[name] = value

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


def scrape_work_orders(session, debug=False):
    """
    Fetch and parse work orders from the TrackHS owner portal.
    Returns a list of dicts with work order data.
    """
    try:
        if debug:
            print(f"Fetching work orders page: {WORK_ORDERS_URL}")

        response = session.get(WORK_ORDERS_URL)
        response.raise_for_status()

        if debug:
            print(f"Work orders page status: {response.status_code}")
            print(f"Final URL: {response.url}")

        if 'Log in to get started' in response.text or 'Login - Owner Connect' in response.text:
            print("ERROR: Redirected to login when accessing work orders (auth failed)")
            return None

        if debug:
            # Save HTML for inspection
            debug_file = DATA_DIR / 'work_orders_debug.html'
            with open(debug_file, 'w') as f:
                f.write(response.text)
            print(f"Saved HTML to: {debug_file}")

        soup = BeautifulSoup(response.text, 'html.parser')

        # Find the work orders table - try multiple strategies
        work_orders = []

        # Strategy 1: Look for a table with work order-like columns
        tables = soup.find_all('table')
        if debug:
            print(f"Found {len(tables)} table(s) on page")

        best_table = None
        best_headers = []
        for table in tables:
            headers = []
            header_row = table.find('tr')
            if header_row:
                headers = [th.get_text(strip=True) for th in header_row.find_all(['th', 'td'])]
            if debug:
                print(f"  Table headers: {headers}")
            # Prefer tables that look like work orders
            if any(kw in ' '.join(headers).lower() for kw in ['date', 'status', 'description', 'work', 'order', 'request']):
                best_table = table
                best_headers = headers
                break

        if best_table is None and tables:
            # Fall back to the largest table
            best_table = max(tables, key=lambda t: len(t.find_all('tr')))
            header_row = best_table.find('tr')
            if header_row:
                best_headers = [th.get_text(strip=True) for th in header_row.find_all(['th', 'td'])]

        if best_table:
            rows = best_table.find_all('tr')
            if debug:
                print(f"Parsing table with {len(rows)} rows, headers: {best_headers}")

            for row in rows[1:]:  # Skip header row
                cells = [td.get_text(strip=True) for td in row.find_all(['td', 'th'])]
                if not cells or all(c == '' for c in cells):
                    continue

                # Map cells to headers, padding if needed
                entry = {}
                for i, header in enumerate(best_headers):
                    entry[header] = cells[i] if i < len(cells) else ''

                # Also store raw cells under generic keys if no headers
                if not best_headers:
                    for i, cell in enumerate(cells):
                        entry[f'col_{i}'] = cell

                # Try to find a date field for filtering
                entry['_scraped_at'] = datetime.now().isoformat(timespec='seconds')
                work_orders.append(entry)

        else:
            # Strategy 2: Look for work order cards/list items (non-table layout)
            if debug:
                print("No table found, looking for list/card layout...")

            # Common TrackHS patterns for work order listings
            items = (soup.find_all(class_=lambda c: c and any(
                kw in c.lower() for kw in ['work-order', 'workorder', 'maintenance', 'ticket', 'request']
            )) or soup.find_all('li', class_=True))

            for item in items:
                text = item.get_text(separator='|', strip=True)
                if text:
                    work_orders.append({'Description': text, '_scraped_at': datetime.now().isoformat(timespec='seconds')})

        if debug:
            print(f"Found {len(work_orders)} work order(s)")
            if work_orders:
                print(f"Sample: {work_orders[0]}")

        return work_orders

    except requests.RequestException as e:
        print(f"ERROR fetching work orders: {e}")
        return None


def save_work_orders(work_orders, output_file, debug=False):
    if not work_orders:
        print("No work orders to save")
        # Write empty file with minimal header so dashboard doesn't break
        with open(output_file, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Date', 'Description', 'Status', 'Priority', 'Unit', '_scraped_at'])
        return True

    # Collect all fieldnames across all rows
    fieldnames = []
    seen = set()
    for wo in work_orders:
        for key in wo:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)

    with open(output_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(work_orders)

    if debug:
        print(f"✓ Saved {len(work_orders)} work orders to {output_file}")

    return True


def main():
    parser = argparse.ArgumentParser(description='Download TrackHS work orders')
    parser.add_argument('--debug', action='store_true', help='Enable debug output')
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

    print("\n[2/3] Fetching work orders...")
    work_orders = scrape_work_orders(session, debug=args.debug)
    if work_orders is None:
        print("✗ Failed to fetch work orders")
        return 1
    print(f"✓ Found {len(work_orders)} work orders")

    print("\n[3/3] Saving work orders...")
    save_work_orders(work_orders, args.output, debug=args.debug)
    print(f"✓ Saved to: {args.output}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
