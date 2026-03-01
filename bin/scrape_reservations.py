#!/usr/bin/env python3
"""
TrackHS Reservation Scraper

Logs into meredith.trackhs.com and downloads the reservations CSV export.
Saves the data to reservations.csv for tracking new bookings.

Usage:
    python scrape_reservations.py [--debug] [--output FILENAME]
"""

import argparse
import csv
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import os
import sys
from pathlib import Path

# Configuration
TRACKHS_BASE_URL = "https://meredith.trackhs.com"
OWNER_PORTAL_URL = f"{TRACKHS_BASE_URL}/owner/"
LOGIN_URL = OWNER_PORTAL_URL  # Owner Connect login
RESERVATIONS_URL = f"{TRACKHS_BASE_URL}/owner/reservations/"

# Data directory for output files
DATA_DIR = Path.home() / '.local' / 'share' / 'pumphouse'
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Load credentials from secrets file
SECRETS_PATH = Path.home() / ".config" / "pumphouse" / "secrets.conf"

def load_secrets():
    """Load TrackHS credentials from secrets.conf."""
    if not SECRETS_PATH.exists():
        print(f"ERROR: Secrets file not found: {SECRETS_PATH}")
        print("Please create secrets.conf with TRACKHS_USERNAME and TRACKHS_PASSWORD")
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
    """Create a requests session with proper headers."""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    return session

def login(session, debug=False):
    """
    Login to TrackHS and establish authenticated session.

    Args:
        session: requests.Session object
        debug: Print debug information

    Returns:
        bool: True if login successful, False otherwise
    """
    try:
        if debug:
            print(f"Fetching login page: {LOGIN_URL}")

        # Get login page to extract CSRF token or form data
        response = session.get(LOGIN_URL)
        response.raise_for_status()

        if debug:
            print(f"Login page status: {response.status_code}")

        # Parse the login form to find any hidden fields (CSRF tokens, etc.)
        soup = BeautifulSoup(response.text, 'html.parser')
        login_form = soup.find('form')

        if not login_form:
            print("ERROR: Could not find login form on page")
            return False

        # Extract form action URL
        form_action = login_form.get('action', '')

        # If no action or invalid action, POST to the same page
        if not form_action or form_action in ['/login', '/owner/login']:
            form_action = LOGIN_URL
        elif not form_action.startswith('http'):
            if form_action.startswith('/'):
                form_action = TRACKHS_BASE_URL + form_action
            else:
                form_action = LOGIN_URL.rstrip('/') + '/' + form_action

        if debug:
            print(f"Form action: {form_action}")

        # Build login payload - start with any hidden fields (CSRF tokens, etc.)
        payload = {}
        for hidden_input in login_form.find_all('input', type='hidden'):
            name = hidden_input.get('name')
            value = hidden_input.get('value', '')
            if name:
                payload[name] = value
                if debug:
                    print(f"Found hidden field: {name} = {value}")

        # Find the actual field names used by the form
        username_field = login_form.find('input', {'name': 'username'}) or \
                        login_form.find('input', {'type': 'email'}) or \
                        login_form.find('input', {'name': 'email'})

        password_field = login_form.find('input', {'type': 'password'})

        if username_field:
            username_name = username_field.get('name')
            payload[username_name] = USERNAME
            if debug:
                print(f"Using username field: {username_name}")
        else:
            # Fallback
            payload['username'] = USERNAME

        if password_field:
            password_name = password_field.get('name')
            payload[password_name] = PASSWORD
            if debug:
                print(f"Using password field: {password_name}")
        else:
            # Fallback
            payload['password'] = PASSWORD

        if debug:
            print(f"Submitting login with payload keys: {list(payload.keys())}")

        # Submit login form
        response = session.post(form_action, data=payload, allow_redirects=True)
        response.raise_for_status()

        if debug:
            print(f"Login response status: {response.status_code}")
            print(f"Final URL after redirects: {response.url}")

        # Check if login was successful
        # Successful login should redirect away from /login
        if '/login' in response.url and response.url != LOGIN_URL:
            # Still on login page - likely failed
            if debug:
                print("WARNING: Still on login page after POST")
                # Check for error messages
                soup = BeautifulSoup(response.text, 'html.parser')
                errors = soup.find_all(class_=['error', 'alert', 'danger'])
                if errors:
                    print("Found error messages:")
                    for error in errors:
                        print(f"  - {error.get_text(strip=True)}")
            return False

        # Verify we can access the reservations page
        if debug:
            print(f"Verifying access to: {RESERVATIONS_URL}")
            print(f"Session cookies: {session.cookies.get_dict()}")

        response = session.get(RESERVATIONS_URL)
        response.raise_for_status()

        if debug:
            print(f"Reservations page response URL: {response.url}")
            print(f"Reservations page status: {response.status_code}")

        # Check if we got redirected back to login
        if 'Log in to get started' in response.text or 'Login - Owner Connect' in response.text:
            print("ERROR: Redirected to login when accessing reservations (auth failed)")
            if debug:
                print("Session cookies after failed access:", session.cookies.get_dict())
            return False

        if debug:
            print("✓ Login successful - can access reservations page")

        return True

    except requests.RequestException as e:
        print(f"ERROR during login: {e}")
        return False

def find_export_form(session, debug=False):
    """
    Find the CSV export form from the reservations page.

    Args:
        session: Authenticated requests.Session
        debug: Print debug information

    Returns:
        tuple: (form_action_url, form_data_dict) or (None, None) if not found
    """
    try:
        if debug:
            print(f"Fetching reservations page to find export form")

        response = session.get(RESERVATIONS_URL)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        # Look for export form - TrackHS uses POST form with action="/owner/reservations/generate-csv/"
        export_form = None

        # Find form with "export" or "csv" in the action or that has Export button
        for form in soup.find_all('form'):
            action = form.get('action', '').lower()
            if 'export' in action or 'csv' in action:
                export_form = form
                if debug:
                    print(f"Found export form by action: {form.get('action')}")
                break

            # Check if form contains Export button
            submit_button = form.find('input', {'type': 'submit', 'value': lambda x: x and 'export' in x.lower()})
            if submit_button:
                export_form = form
                if debug:
                    print(f"Found export form by submit button")
                break

        if not export_form:
            if debug:
                print("Could not find export form, trying known URL")
            # Default to known path
            return f"{TRACKHS_BASE_URL}/owner/reservations/generate-csv/", {}

        # Extract form action
        form_action = export_form.get('action', '')
        if not form_action.startswith('http'):
            if form_action.startswith('/'):
                form_action = TRACKHS_BASE_URL + form_action
            else:
                form_action = RESERVATIONS_URL.rstrip('/') + '/' + form_action

        # Extract form data (hidden fields, etc.)
        form_data = {}
        for input_field in export_form.find_all('input', {'type': 'hidden'}):
            name = input_field.get('name')
            value = input_field.get('value', '')
            if name:
                form_data[name] = value
                if debug:
                    print(f"Found form field: {name} = {value}")

        if debug:
            print(f"Export form action: {form_action}")

        return form_action, form_data

    except requests.RequestException as e:
        print(f"ERROR finding export form: {e}")
        return None, None

def download_reservations_csv(session, output_file='reservations.csv', debug=False):
    """
    Download the reservations CSV export.

    Args:
        session: Authenticated requests.Session
        output_file: Path to save CSV
        debug: Print debug information

    Returns:
        bool: True if download successful
    """
    try:
        # Find the export form
        export_url, form_data = find_export_form(session, debug=debug)

        if not export_url:
            print("ERROR: Could not determine export URL")
            return False

        if debug:
            print(f"Attempting to download from: {export_url}")
            if form_data:
                print(f"Form data: {form_data}")

        # Download the CSV using POST (as the form does)
        response = session.post(export_url, data=form_data)
        response.raise_for_status()

        # Check if response is CSV
        content_type = response.headers.get('content-type', '').lower()
        if debug:
            print(f"Response content-type: {content_type}")
            print(f"Response size: {len(response.content)} bytes")

        if 'text/html' in content_type and len(response.content) > 1000:
            # Probably got HTML page instead of CSV - export URL might be wrong
            print("WARNING: Received HTML instead of CSV")
            if debug:
                print("First 500 chars of response:")
                print(response.text[:500])

            # Save HTML for debugging
            debug_file = output_file.replace('.csv', '_debug.html')
            with open(debug_file, 'w') as f:
                f.write(response.text)
            print(f"Saved HTML response to {debug_file} for debugging")
            return False

        # Save the CSV
        with open(output_file, 'wb') as f:
            f.write(response.content)

        if debug:
            print(f"✓ Downloaded CSV to: {output_file}")

        # Verify it's valid CSV
        try:
            with open(output_file, 'r') as f:
                csv_reader = csv.reader(f)
                headers = next(csv_reader)
                row_count = sum(1 for _ in csv_reader)

                if debug:
                    print(f"CSV headers: {headers}")
                    print(f"CSV row count: {row_count}")

                print(f"✓ Successfully downloaded {row_count} reservations")
                return True

        except Exception as e:
            print(f"WARNING: CSV validation failed: {e}")
            return False

    except requests.RequestException as e:
        print(f"ERROR downloading CSV: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='Download TrackHS reservations CSV')
    parser.add_argument('--debug', action='store_true', help='Enable debug output')
    parser.add_argument('--output', default=str(DATA_DIR / 'reservations.csv'), help='Output CSV filename')
    args = parser.parse_args()

    print("TrackHS Reservation Scraper")
    print("=" * 50)

    # Create session
    session = create_session()

    # Login
    print("\n[1/3] Logging in...")
    if not login(session, debug=args.debug):
        print("✗ Login failed")
        return 1
    print("✓ Login successful")

    # Download CSV
    print("\n[2/3] Downloading reservations CSV...")
    if not download_reservations_csv(session, output_file=args.output, debug=args.debug):
        print("✗ Download failed")
        return 1

    print("\n[3/3] Complete!")
    print(f"Reservations saved to: {os.path.abspath(args.output)}")

    return 0

if __name__ == '__main__':
    sys.exit(main())
