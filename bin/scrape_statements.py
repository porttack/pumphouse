#!/usr/bin/env python3
"""
TrackHS Statements Scraper

Logs into meredith.trackhs.com and downloads monthly owner statement summaries
from the /owner/statements/view-data/ JSON endpoint.

Saves monthly Period statements to statements.csv (Annual statements excluded).

Usage:
    python scrape_statements.py [--debug] [--output FILENAME]
"""

import argparse
import csv
import sys
from pathlib import Path

import requests

DATA_DIR = Path.home() / '.local' / 'share' / 'pumphouse'
DATA_DIR.mkdir(parents=True, exist_ok=True)

TRACKHS_BASE_URL = "https://meredith.trackhs.com"
OWNER_PORTAL_URL = f"{TRACKHS_BASE_URL}/owner/"
STATEMENTS_DATA_URL = f"{TRACKHS_BASE_URL}/owner/statements/view-data/"

SECRETS_PATH = Path.home() / ".config" / "pumphouse" / "secrets.conf"

FIELDNAMES = ['year', 'period', 'end_date', 'revenue', 'charges', 'paid', 'balance']


def load_secrets():
    if not SECRETS_PATH.exists():
        print(f"ERROR: Secrets file not found: {SECRETS_PATH}")
        sys.exit(1)
    secrets = {}
    with open(SECRETS_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, value = line.partition('=')
                secrets[key.strip()] = value.strip()
    username = secrets.get('TRACKHS_USERNAME')
    password = secrets.get('TRACKHS_PASSWORD')
    if not username or not password:
        print(f"ERROR: TRACKHS_USERNAME and TRACKHS_PASSWORD must be set in {SECRETS_PATH}")
        sys.exit(1)
    return username, password


def create_session():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    return session


def login(session, username, password, debug=False):
    from bs4 import BeautifulSoup
    try:
        response = session.get(OWNER_PORTAL_URL)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        login_form = soup.find('form')
        if not login_form:
            print("ERROR: Could not find login form")
            return False

        form_action = login_form.get('action', '') or OWNER_PORTAL_URL
        if not form_action.startswith('http'):
            form_action = TRACKHS_BASE_URL + form_action if form_action.startswith('/') else OWNER_PORTAL_URL

        payload = {}
        for inp in login_form.find_all('input', type='hidden'):
            if inp.get('name'):
                payload[inp['name']] = inp.get('value', '')

        uf = (login_form.find('input', {'name': 'username'}) or
              login_form.find('input', {'type': 'email'}) or
              login_form.find('input', {'name': 'email'}))
        pf = login_form.find('input', {'type': 'password'})
        payload[uf['name'] if uf else 'username'] = username
        payload[pf['name'] if pf else 'password'] = password

        response = session.post(form_action, data=payload, allow_redirects=True)
        response.raise_for_status()

        if 'Log in to get started' in response.text or 'Login - Owner Connect' in response.text:
            print("ERROR: Login failed")
            return False

        if debug:
            print("✓ Login successful")
        return True

    except requests.RequestException as e:
        print(f"ERROR during login: {e}")
        return False


def fetch_statements(session, debug=False):
    """Fetch all period statements from the view-data JSON endpoint."""
    try:
        response = session.get(STATEMENTS_DATA_URL)
        response.raise_for_status()
        data = response.json()
        records = data.get('data', [])
        if debug:
            print(f"Fetched {len(records)} total statement records")

        rows = []
        for rec in records:
            if rec.get('isAnnual') or rec.get('type') == 'Annual':
                continue
            rows.append({
                'year':     str(rec['year']),
                'period':   str(rec['period']),
                'end_date': rec.get('endDate', ''),
                'revenue':  rec.get('revenue', '0'),
                'charges':  rec.get('charges', '0'),
                'paid':     rec.get('paid', '0'),
                'balance':  rec.get('balance', '0'),
            })

        rows.sort(key=lambda r: r['end_date'])
        if debug:
            print(f"Kept {len(rows)} monthly (Period) statements")
        return rows

    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"ERROR fetching statements: {e}")
        return None


def save_statements(rows, output_file, debug=False):
    with open(output_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    if debug:
        print(f"✓ Saved {len(rows)} statements to {output_file}")


def main():
    parser = argparse.ArgumentParser(description='Download TrackHS owner statements')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--output', default=str(DATA_DIR / 'statements.csv'))
    args = parser.parse_args()

    print("TrackHS Statements Scraper")
    print("=" * 50)

    username, password = load_secrets()
    session = create_session()

    print("\n[1/3] Logging in...")
    if not login(session, username, password, debug=args.debug):
        print("✗ Login failed")
        return 1
    print("✓ Login successful")

    print("\n[2/3] Fetching statements...")
    rows = fetch_statements(session, debug=args.debug)
    if rows is None:
        print("✗ Failed to fetch statements")
        return 1
    print(f"✓ Found {len(rows)} monthly statements")

    print("\n[3/3] Saving...")
    save_statements(rows, args.output, debug=args.debug)
    print(f"✓ Saved to: {args.output}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
