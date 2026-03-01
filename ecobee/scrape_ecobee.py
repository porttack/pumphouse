#!/usr/bin/env python3
"""
Ecobee Web Portal Scraper

Scrapes temperature and vacation mode data from the Ecobee consumer portal.
This is an alternative to the API-based approach when you don't have developer access.

Usage:
    python scrape_ecobee.py [--debug] [--save-html]
"""

import argparse
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import sys
from pathlib import Path
import re
import time

try:
    import pyotp
except ImportError:
    print("WARNING: pyotp library not installed. 2FA will not work.")
    print("Install with: pip install pyotp")
    pyotp = None

# Configuration
ECOBEE_BASE_URL = "https://www.ecobee.com"
PORTAL_URL = f"{ECOBEE_BASE_URL}/consumerportal/"
# Try different login URLs
LOGIN_URLS = [
    f"{ECOBEE_BASE_URL}/consumerportal/index.html",  # Try portal login first
    f"{ECOBEE_BASE_URL}/home/login.jsf",
    f"{ECOBEE_BASE_URL}/home/secure/homeLogin.jsp",
]

# Target thermostat and location
TARGET_THERMOSTAT = "Living Room Ecobee"
TARGET_LOCATION = "Blackberry Hill"

# Load credentials from secrets file
SECRETS_PATH = Path.home() / ".config" / "pumphouse" / "secrets.conf"

def load_secrets():
    """Load Ecobee credentials from secrets.conf."""
    if not SECRETS_PATH.exists():
        print(f"ERROR: Secrets file not found: {SECRETS_PATH}")
        print("Please create secrets.conf with ECOBEE_USERNAME and ECOBEE_PASSWORD")
        sys.exit(1)

    secrets = {}
    with open(SECRETS_PATH, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                secrets[key.strip()] = value.strip()

    username = secrets.get('ECOBEE_USERNAME')
    password = secrets.get('ECOBEE_PASSWORD')
    totp_secret = secrets.get('ECOBEE_TOTP_SECRET')  # Optional

    if not username or not password:
        print(f"ERROR: ECOBEE_USERNAME and ECOBEE_PASSWORD must be set in {SECRETS_PATH}")
        sys.exit(1)

    return username, password, totp_secret

USERNAME, PASSWORD, TOTP_SECRET = load_secrets()

def create_session():
    """Create a requests session with proper headers."""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
    })
    return session

def save_debug_html(content, filename):
    """Save HTML content for debugging."""
    debug_file = Path(__file__).parent / filename
    try:
        with open(debug_file, 'w', encoding='utf-8', errors='ignore') as f:
            f.write(content)
        print(f"DEBUG: Saved HTML to {debug_file}")
        # Also print first 500 chars to see what we got
        print(f"DEBUG: First 500 chars of {filename}:")
        print(content[:500])
    except Exception as e:
        print(f"WARNING: Could not save debug HTML: {e}")

def generate_totp_code():
    """Generate TOTP code from secret."""
    if not TOTP_SECRET:
        print("ERROR: ECOBEE_TOTP_SECRET not configured in secrets.conf")
        return None

    if not pyotp:
        print("ERROR: pyotp library not installed")
        return None

    try:
        totp = pyotp.TOTP(TOTP_SECRET)
        code = totp.now()
        return code
    except Exception as e:
        print(f"ERROR generating TOTP code: {e}")
        return None

def handle_2fa(session, response, soup, debug=False):
    """
    Handle 2FA authentication.

    Args:
        session: requests.Session object
        response: Response from login attempt
        soup: BeautifulSoup object of the 2FA page
        debug: Print debug information

    Returns:
        bool: True if 2FA successful, False otherwise
    """
    try:
        # Generate TOTP code
        code = generate_totp_code()

        if not code:
            print("Could not generate 2FA code")
            return False

        if debug:
            print(f"Generated TOTP code: {code}")

        # Find the 2FA form
        form = soup.find('form')

        if not form:
            print("ERROR: Could not find 2FA form")
            return False

        # Extract form action
        form_action = form.get('action', '')

        if not form_action.startswith('http'):
            if form_action.startswith('/'):
                form_action = ECOBEE_BASE_URL + form_action
            else:
                base_url = response.url.rsplit('/', 1)[0]
                form_action = base_url + '/' + form_action

        if debug:
            print(f"2FA form action: {form_action}")

        # Build payload with hidden fields
        payload = {}
        for hidden_input in form.find_all('input', type='hidden'):
            name = hidden_input.get('name')
            value = hidden_input.get('value', '')
            if name:
                payload[name] = value
                if debug:
                    print(f"Hidden field: {name} = {value}")

        # Find the code input field
        code_field = form.find('input', {'type': 'text'}) or \
                     form.find('input', {'type': 'number'}) or \
                     form.find('input', {'name': re.compile(r'code|token|otp', re.I)}) or \
                     form.find('input', {'id': re.compile(r'code|token|otp', re.I)})

        if code_field:
            code_field_name = code_field.get('name')
            payload[code_field_name] = code
            if debug:
                print(f"Using code field: {code_field_name}")
        else:
            # Try common field names
            for field_name in ['code', 'token', 'otp', 'verificationCode', 'mfaCode']:
                payload[field_name] = code
                if debug:
                    print(f"Trying fallback field: {field_name}")
                break

        # Look for "Remember this device" checkbox and check it
        remember_checkbox = form.find('input', {'type': 'checkbox', 'name': re.compile(r'remember|trust', re.I)})
        if remember_checkbox:
            remember_name = remember_checkbox.get('name')
            payload[remember_name] = 'on'
            if debug:
                print(f"Checking 'remember device' checkbox: {remember_name}")

        if debug:
            print(f"Submitting 2FA with payload keys: {list(payload.keys())}")

        # Submit 2FA form
        response = session.post(form_action, data=payload, allow_redirects=True)
        response.raise_for_status()

        if debug:
            print(f"2FA response status: {response.status_code}")
            print(f"Final URL: {response.url}")
            save_debug_html(response.text, 'ecobee_after_2fa.html')

        # Check if 2FA was successful
        if 'verification' in response.url.lower() or 'code' in response.text.lower():
            # Still on verification page - likely failed
            soup_check = BeautifulSoup(response.text, 'html.parser')
            errors = soup_check.find_all(class_=re.compile(r'error|alert|danger', re.I))
            if errors and debug:
                print("2FA errors found:")
                for error in errors:
                    print(f"  - {error.get_text(strip=True)}")
            return False

        return True

    except Exception as e:
        print(f"ERROR during 2FA: {e}")
        if debug:
            import traceback
            traceback.print_exc()
        return False

def login(session, debug=False):
    """
    Login to Ecobee consumer portal.

    Args:
        session: requests.Session object
        debug: Print debug information

    Returns:
        bool: True if login successful, False otherwise
    """
    # Try different login URLs
    login_response = None
    login_url = None

    for url in LOGIN_URLS:
        try:
            if debug:
                print(f"Trying login page: {url}")

            # Get login page to extract any hidden fields (CSRF tokens, etc.)
            response = session.get(url, timeout=10)

            if response.status_code == 200:
                login_url = url
                login_response = response
                if debug:
                    print(f"✓ Successfully fetched login page from {url}")
                break
            elif debug:
                print(f"  Status {response.status_code}, trying next URL...")

        except Exception as e:
            if debug:
                print(f"  Failed: {e}, trying next URL...")
            continue

    if not login_response:
        print("ERROR: Could not fetch any login page")
        return False

    try:
        response = login_response

        if debug:
            print(f"Login page status: {response.status_code}")
            print(f"Login page URL: {response.url}")

        soup = BeautifulSoup(response.text, 'html.parser')

        # Save HTML for inspection
        if debug:
            save_debug_html(response.text, 'ecobee_login_page.html')

        # Find login form
        login_form = soup.find('form')

        if not login_form:
            print("ERROR: Could not find login form on page")
            return False

        # Extract form action URL
        form_action = login_form.get('action', '')

        if debug:
            print(f"Form action: {form_action}")

        # Build full action URL
        if not form_action:
            form_action = LOGIN_URL
        elif not form_action.startswith('http'):
            if form_action.startswith('/'):
                form_action = ECOBEE_BASE_URL + form_action
            else:
                form_action = LOGIN_URL.rsplit('/', 1)[0] + '/' + form_action

        if debug:
            print(f"Full form action: {form_action}")

        # Build login payload - start with hidden fields
        payload = {}
        for hidden_input in login_form.find_all('input', type='hidden'):
            name = hidden_input.get('name')
            value = hidden_input.get('value', '')
            if name:
                payload[name] = value
                if debug:
                    print(f"Found hidden field: {name} = {value}")

        # Find username/email field
        username_field = login_form.find('input', {'name': 'username'}) or \
                        login_form.find('input', {'type': 'email'}) or \
                        login_form.find('input', {'name': 'email'}) or \
                        login_form.find('input', {'id': re.compile(r'username|email', re.I)})

        if username_field:
            username_name = username_field.get('name')
            payload[username_name] = USERNAME
            if debug:
                print(f"Using username field: {username_name}")
        else:
            # Fallback to common field names
            payload['username'] = USERNAME
            if debug:
                print("Using fallback username field")

        # Find password field
        password_field = login_form.find('input', {'type': 'password'})

        if password_field:
            password_name = password_field.get('name')
            payload[password_name] = PASSWORD
            if debug:
                print(f"Using password field: {password_name}")
        else:
            # Fallback
            payload['password'] = PASSWORD
            if debug:
                print("Using fallback password field")

        if debug:
            print(f"Submitting login with payload keys: {list(payload.keys())}")

        # Submit login form
        response = session.post(form_action, data=payload, allow_redirects=True)
        response.raise_for_status()

        if debug:
            print(f"Login response status: {response.status_code}")
            print(f"Final URL after redirects: {response.url}")
            save_debug_html(response.text, 'ecobee_after_login.html')

        # Check if we need to handle 2FA
        soup_after_login = BeautifulSoup(response.text, 'html.parser')

        # Look for 2FA/MFA prompts
        if any(keyword in response.text.lower() for keyword in ['two-factor', '2fa', 'verification code', 'authenticator', 'enter code']):
            if debug:
                print("2FA detected, attempting to handle...")
                save_debug_html(response.text, 'ecobee_2fa_page.html')

            if not handle_2fa(session, response, soup_after_login, debug):
                print("ERROR: 2FA authentication failed")
                return False

            if debug:
                print("✓ 2FA completed")

        # Check if login was successful
        # Look for common error indicators
        elif 'login' in response.url.lower() and 'error' in response.text.lower():
            if debug:
                errors = soup_after_login.find_all(class_=re.compile(r'error|alert|danger', re.I))
                if errors:
                    print("Found error messages:")
                    for error in errors:
                        print(f"  - {error.get_text(strip=True)}")
            return False

        # Verify we can access the portal
        if debug:
            print(f"Verifying access to: {PORTAL_URL}")

        response = session.get(PORTAL_URL)
        response.raise_for_status()

        if debug:
            print(f"Portal page response URL: {response.url}")
            print(f"Portal page status: {response.status_code}")
            save_debug_html(response.text, 'ecobee_portal.html')

        # Check if we got redirected back to login
        if 'login' in response.url.lower():
            print("ERROR: Redirected to login when accessing portal (auth failed)")
            return False

        if debug:
            print("✓ Login successful - can access portal")

        return True

    except requests.RequestException as e:
        print(f"ERROR during login: {e}")
        return False

def extract_thermostat_data(session, debug=False):
    """
    Extract thermostat data from the portal page.

    Args:
        session: Authenticated requests.Session
        debug: Print debug information

    Returns:
        dict or None: Thermostat data or None if extraction fails
    """
    try:
        if debug:
            print(f"Fetching portal page: {PORTAL_URL}")

        response = session.get(PORTAL_URL)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        if debug:
            save_debug_html(response.text, 'ecobee_portal_data.html')
            print("Analyzing page structure...")

        # The Ecobee portal is likely a single-page app that loads data via JavaScript/AJAX
        # We need to find the AJAX endpoints or embedded JSON data

        # Strategy 1: Look for embedded JSON data in script tags
        script_tags = soup.find_all('script')
        for script in script_tags:
            script_content = script.string
            if script_content and 'thermostat' in script_content.lower():
                if debug:
                    print("Found script with thermostat data:")
                    print(script_content[:500])

                # Try to extract JSON data
                # Common patterns: var data = {...}, window.data = {...}, etc.
                json_matches = re.findall(r'(?:var\s+\w+\s*=\s*|window\.\w+\s*=\s*)(\{.*?\});', script_content, re.DOTALL)
                for json_str in json_matches:
                    try:
                        data = json.loads(json_str)
                        if debug:
                            print("Found JSON data:")
                            print(json.dumps(data, indent=2))
                        # Process the data here
                    except json.JSONDecodeError:
                        pass

        # Strategy 2: Look for AJAX endpoints
        # Check for XHR calls in the page source
        ajax_patterns = [
            r'url:\s*["\']([^"\']+)["\']',
            r'fetch\(["\']([^"\']+)["\']',
            r'ajax\(["\']([^"\']+)["\']'
        ]

        for script in script_tags:
            script_content = script.string or ''
            for pattern in ajax_patterns:
                matches = re.findall(pattern, script_content)
                if matches and debug:
                    print(f"Found potential AJAX endpoints: {matches}")

        # Strategy 3: Look for thermostat temperature in page elements
        # Common class names: temp, temperature, current-temp, etc.
        temp_elements = soup.find_all(class_=re.compile(r'temp|temperature', re.I))
        if debug and temp_elements:
            print(f"Found {len(temp_elements)} elements with temp-related classes:")
            for elem in temp_elements[:5]:  # Show first 5
                print(f"  - {elem.get('class')}: {elem.get_text(strip=True)[:50]}")

        # Strategy 4: Look for vacation/hold indicators
        vacation_elements = soup.find_all(text=re.compile(r'vacation|hold', re.I))
        if debug and vacation_elements:
            print(f"Found {len(vacation_elements)} elements mentioning vacation/hold:")
            for elem in vacation_elements[:5]:  # Show first 5
                print(f"  - {elem[:100]}")

        # For now, return a placeholder indicating we need more investigation
        print("\nNOTE: The Ecobee portal likely uses JavaScript to load data dynamically.")
        print("Please check the debug HTML files to see the page structure.")
        print("We may need to use Selenium or find the AJAX endpoints.")

        return None

    except requests.RequestException as e:
        print(f"ERROR fetching portal data: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description='Scrape Ecobee temperature and vacation mode data')
    parser.add_argument('--debug', action='store_true', help='Enable debug output')
    parser.add_argument('--save-html', action='store_true', help='Save HTML pages for inspection')
    args = parser.parse_args()

    print("Ecobee Web Portal Scraper")
    print("=" * 60)
    print(f"Target Thermostat: {TARGET_THERMOSTAT}")
    print(f"Target Location: {TARGET_LOCATION}")
    print("=" * 60)

    # Create session
    session = create_session()

    # Login
    print("\n[1/2] Logging in...")
    if not login(session, debug=args.debug or args.save_html):
        print("✗ Login failed")
        return 1
    print("✓ Login successful")

    # Extract data
    print("\n[2/2] Extracting thermostat data...")
    data = extract_thermostat_data(session, debug=args.debug or args.save_html)

    if data:
        print(f"\n✓ Successfully extracted data:")
        print(json.dumps(data, indent=2))
        return 0
    else:
        print("\n⚠ Data extraction needs further investigation")
        print("Run with --save-html to save page content for analysis")
        return 1

if __name__ == '__main__':
    sys.exit(main())
