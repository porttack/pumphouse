#!/home/pi/src/pumphouse/venv/bin/python3
#!/usr/bin/env python3
"""
Ecobee Data Fetcher

Fetches temperature data from the "Living Room Ecobee" thermostat and
vacation mode status for the "Blackberry Hill" house.

This script uses the pyecobee library to interact with the Ecobee API.

Requirements:
    pip install pyecobee

First-time setup:
    1. Create an Ecobee developer account and API key at https://www.ecobee.com/developers/
       NOTE: As of March 2024, Ecobee is not accepting new developer accounts.
       If you already have an API key, add it to secrets.conf

    2. Add your API key to ~/.config/pumphouse/secrets.conf:
       ECOBEE_API_KEY=your_api_key_here

    3. Run this script for the first time. It will:
       - Display a PIN code
       - Ask you to authorize the app at ecobee.com
       - Save the OAuth tokens for future use

Usage:
    python fetch_ecobee_data.py [--debug]
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    from pyecobee import EcobeeService, Selection, SelectionType
except ImportError:
    print("ERROR: pyecobee library not installed")
    print("Install with: pip install pyecobee")
    sys.exit(1)

# Configuration
SECRETS_PATH = Path.home() / ".config" / "pumphouse" / "secrets.conf"
TOKENS_PATH = Path.home() / ".config" / "pumphouse" / "ecobee_tokens.json"

# Target thermostat and location
TARGET_THERMOSTAT = "Living Room Ecobee"
TARGET_LOCATION = "Blackberry Hill"


def load_api_key():
    """Load Ecobee API key from secrets.conf."""
    if not SECRETS_PATH.exists():
        print(f"ERROR: Secrets file not found: {SECRETS_PATH}")
        print("Please add ECOBEE_API_KEY to your secrets.conf file")
        sys.exit(1)

    api_key = None
    with open(SECRETS_PATH, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                if key.strip() == 'ECOBEE_API_KEY':
                    api_key = value.strip()
                    break

    if not api_key:
        print(f"ERROR: ECOBEE_API_KEY not found in {SECRETS_PATH}")
        print("Please add your Ecobee API key to secrets.conf")
        sys.exit(1)

    return api_key


def save_tokens(ecobee_service):
    """Save OAuth tokens to file for future use."""
    tokens = {
        'access_token': ecobee_service.access_token,
        'refresh_token': ecobee_service.refresh_token,
        'authorization_token': ecobee_service.authorization_token
    }

    TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKENS_PATH, 'w') as f:
        json.dump(tokens, f, indent=2)

    # Set restrictive permissions
    TOKENS_PATH.chmod(0o600)


def load_tokens(ecobee_service):
    """Load OAuth tokens from file."""
    if not TOKENS_PATH.exists():
        return False

    try:
        with open(TOKENS_PATH, 'r') as f:
            tokens = json.load(f)

        ecobee_service.access_token = tokens.get('access_token')
        ecobee_service.refresh_token = tokens.get('refresh_token')
        ecobee_service.authorization_token = tokens.get('authorization_token')

        return True
    except Exception as e:
        print(f"WARNING: Could not load tokens: {e}")
        return False


def authorize_app(ecobee_service, debug=False):
    """
    Perform initial app authorization (first-time setup).

    This uses the PIN-based authorization flow:
    1. Request a PIN from Ecobee
    2. User authorizes the app on ecobee.com
    3. Request access tokens
    """
    if debug:
        print("Starting PIN-based authorization...")

    # Request PIN
    authorize_response = ecobee_service.authorize()

    if debug:
        print(f"Authorization response: {authorize_response}")

    print("\n" + "=" * 60)
    print("ECOBEE AUTHORIZATION REQUIRED")
    print("=" * 60)
    print(f"\n1. Go to: https://www.ecobee.com/consumerportal/index.html")
    print(f"2. Log in to your Ecobee account")
    print(f"3. Click 'My Apps' in the menu")
    print(f"4. Click 'Add Application'")
    print(f"5. Enter this PIN: {authorize_response.ecobee_pin}")
    print(f"6. This PIN expires in {authorize_response.expires_in // 60} minutes")
    print(f"\nPress ENTER after you have authorized the app...")
    input()

    # Request tokens
    if debug:
        print("Requesting access tokens...")

    try:
        ecobee_service.request_tokens()
        save_tokens(ecobee_service)
        print("✓ Authorization successful!")
        return True
    except Exception as e:
        print(f"✗ Authorization failed: {e}")
        print("Please try again and make sure you complete the authorization on ecobee.com")
        return False


def refresh_tokens_if_needed(ecobee_service, debug=False):
    """Refresh access token if it has expired."""
    try:
        if debug:
            print("Refreshing tokens...")

        ecobee_service.refresh_tokens()
        save_tokens(ecobee_service)

        if debug:
            print("✓ Tokens refreshed successfully")

        return True
    except Exception as e:
        print(f"ERROR: Could not refresh tokens: {e}")
        print("You may need to re-authorize the app")
        return False


def find_thermostat_by_name(thermostats, name):
    """Find a thermostat by name from the list of thermostats."""
    for thermostat in thermostats:
        if thermostat['name'] == name:
            return thermostat
    return None


def get_ecobee_data(debug=False):
    """
    Fetch temperature and vacation mode data from Ecobee.

    Returns:
        dict: {
            'temperature': float,  # Current temperature in Fahrenheit
            'vacation_mode': bool,  # True if vacation mode is active
            'vacation_name': str or None,  # Name of active vacation
            'thermostat_name': str,
            'location': str,
            'timestamp': str
        }
    """
    # Load API key
    api_key = load_api_key()

    # Create Ecobee service
    ecobee_service = EcobeeService(
        thermostat_name='pumphouse_monitor',
        application_key=api_key
    )

    # Try to load existing tokens
    tokens_loaded = load_tokens(ecobee_service)

    if not tokens_loaded:
        # First-time authorization
        print("No saved tokens found. Starting authorization process...")
        if not authorize_app(ecobee_service, debug=debug):
            return None
    else:
        # Try to refresh tokens (they expire every 1 hour by default)
        if not refresh_tokens_if_needed(ecobee_service, debug=debug):
            # Refresh failed, try re-authorizing
            print("Token refresh failed. Re-authorization required.")
            if not authorize_app(ecobee_service, debug=debug):
                return None

    # Create selection to fetch thermostat data
    # Include runtime for current temperature and events for vacation holds
    selection = Selection(
        selection_type=SelectionType.REGISTERED.value,
        selection_match='',
        include_runtime=True,
        include_events=True,
        include_location=True
    )

    try:
        if debug:
            print("Requesting thermostat data...")

        # Get thermostat data
        response = ecobee_service.request_thermostats(selection)

        if debug:
            print(f"API Response status: {response.status.code}")
            print(f"Number of thermostats found: {len(response.thermostat_list)}")

        if response.status.code != 0:
            print(f"ERROR: Ecobee API returned error code {response.status.code}")
            print(f"Message: {response.status.message}")
            return None

        if not response.thermostat_list:
            print("ERROR: No thermostats found in your Ecobee account")
            return None

        # Debug: Print all thermostat names
        if debug:
            print("\nAvailable thermostats:")
            for t in response.thermostat_list:
                location = t.get('location', {})
                print(f"  - {t['name']} (Location: {location.get('name', 'Unknown')})")

        # Find the target thermostat
        thermostat = find_thermostat_by_name(response.thermostat_list, TARGET_THERMOSTAT)

        if not thermostat:
            print(f"ERROR: Thermostat '{TARGET_THERMOSTAT}' not found")
            print(f"Available thermostats: {[t['name'] for t in response.thermostat_list]}")
            return None

        # Extract temperature (in Fahrenheit * 10, so divide by 10)
        runtime = thermostat.get('runtime', {})
        temp_f = runtime.get('actualTemperature', 0) / 10.0

        # Check for active vacation holds
        events = thermostat.get('events', [])
        vacation_active = False
        vacation_name = None

        for event in events:
            if event.get('type') == 'vacation' and event.get('running', False):
                vacation_active = True
                vacation_name = event.get('name', 'Unnamed Vacation')
                if debug:
                    print(f"Found active vacation: {vacation_name}")
                break

        # Get location info
        location = thermostat.get('location', {})
        location_name = location.get('name', 'Unknown')

        # Verify this is the correct location
        if TARGET_LOCATION.lower() not in location_name.lower():
            print(f"WARNING: Thermostat location is '{location_name}', expected '{TARGET_LOCATION}'")

        result = {
            'temperature': temp_f,
            'vacation_mode': vacation_active,
            'vacation_name': vacation_name,
            'thermostat_name': thermostat['name'],
            'location': location_name,
            'timestamp': datetime.now().isoformat()
        }

        if debug:
            print(f"\nExtracted data:")
            print(json.dumps(result, indent=2))

        return result

    except Exception as e:
        print(f"ERROR: Failed to fetch thermostat data: {e}")
        if debug:
            import traceback
            traceback.print_exc()
        return None


def main():
    parser = argparse.ArgumentParser(description='Fetch Ecobee temperature and vacation mode data')
    parser.add_argument('--debug', action='store_true', help='Enable debug output')
    args = parser.parse_args()

    print("Ecobee Data Fetcher")
    print("=" * 60)
    print(f"Target Thermostat: {TARGET_THERMOSTAT}")
    print(f"Target Location: {TARGET_LOCATION}")
    print("=" * 60)

    # Fetch data
    data = get_ecobee_data(debug=args.debug)

    if data:
        print(f"\n✓ Successfully fetched data:")
        print(f"  Temperature: {data['temperature']:.1f}°F")
        print(f"  Vacation Mode: {'ACTIVE' if data['vacation_mode'] else 'INACTIVE'}")
        if data['vacation_name']:
            print(f"  Vacation Name: {data['vacation_name']}")
        print(f"  Thermostat: {data['thermostat_name']}")
        print(f"  Location: {data['location']}")
        print(f"  Timestamp: {data['timestamp']}")
        return 0
    else:
        print("\n✗ Failed to fetch data")
        return 1


if __name__ == '__main__':
    sys.exit(main())
