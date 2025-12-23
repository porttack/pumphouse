#!/usr/bin/env python3
"""
Ecobee Web Portal Scraper (Selenium Version)

Scrapes temperature and vacation mode data from the Ecobee consumer portal using Selenium.
This works with JavaScript-heavy SPAs that don't render with plain HTTP requests.

Requirements:
    pip install selenium
    sudo apt-get install chromium-chromedriver  # or chrome driver

Usage:
    python scrape_ecobee_selenium.py [--debug] [--headless]
"""

import argparse
import json
import sys
from pathlib import Path
import time
import re
from datetime import datetime

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options
    from selenium.common.exceptions import TimeoutException, NoSuchElementException
except ImportError:
    print("ERROR: selenium library not installed")
    print("Install with: pip install selenium")
    print("Also install chromium-chromedriver: sudo apt-get install chromium-chromedriver")
    sys.exit(1)

try:
    import pyotp
except ImportError:
    print("WARNING: pyotp library not installed. 2FA will not work.")
    print("Install with: pip install pyotp")
    pyotp = None

# Configuration
ECOBEE_BASE_URL = "https://www.ecobee.com"
# The actual login URL (state parameter will vary, but the base path is what matters)
LOGIN_URL = "https://auth.ecobee.com/u/login"
PORTAL_URL = f"{ECOBEE_BASE_URL}/consumerportal/index.html"

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

def generate_totp_code():
    """Generate TOTP code from secret."""
    if not TOTP_SECRET:
        return None

    if not pyotp:
        return None

    try:
        totp = pyotp.TOTP(TOTP_SECRET)
        code = totp.now()
        return code
    except Exception as e:
        print(f"ERROR generating TOTP code: {e}")
        return None

def create_driver(headless=True, debug=False):
    """Create a Selenium WebDriver instance."""
    from selenium.webdriver.chrome.service import Service

    options = Options()

    if headless:
        options.add_argument('--headless=new')  # Use new headless mode

    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')

    # Explicitly specify chromedriver path
    service = Service('/usr/bin/chromedriver')

    if debug:
        print("Creating Chrome driver...")
        print(f"ChromeDriver path: /usr/bin/chromedriver")

    try:
        driver = webdriver.Chrome(service=service, options=options)
        return driver
    except Exception as e:
        print(f"ERROR creating Chrome driver: {e}")
        print("Make sure chromium-driver is installed:")
        print("  sudo apt-get install chromium-driver")
        sys.exit(1)

def login(driver, debug=False):
    """
    Login to Ecobee consumer portal.

    Args:
        driver: Selenium WebDriver
        debug: Print debug information

    Returns:
        bool: True if login successful, False otherwise
    """
    try:
        if debug:
            print(f"Navigating directly to login: {LOGIN_URL}")

        # Navigate directly to the login page
        driver.get(LOGIN_URL)

        # Wait for page to load
        time.sleep(3)

        # Try to dismiss cookie consent banner
        try:
            # Common cookie banner dismiss buttons
            cookie_dismiss_selectors = [
                (By.ID, 'onetrust-accept-btn-handler'),
                (By.CSS_SELECTOR, '.onetrust-close-btn-handler'),
                (By.XPATH, '//button[contains(text(), "Accept")]'),
                (By.XPATH, '//button[contains(text(), "Agree")]'),
            ]

            for by, selector in cookie_dismiss_selectors:
                try:
                    cookie_button = WebDriverWait(driver, 3).until(
                        EC.element_to_be_clickable((by, selector))
                    )
                    cookie_button.click()
                    if debug:
                        print(f"✓ Dismissed cookie banner: {by}={selector}")
                    time.sleep(1)
                    break
                except (TimeoutException, NoSuchElementException):
                    continue
        except Exception:
            pass  # Cookie banner not found or already dismissed

        if debug:
            print(f"Current URL: {driver.current_url}")
            print(f"Page title: {driver.title}")

        # Look for and click "Sign In" / "Login" link
        if debug:
            print("Looking for Sign In link...")

        sign_in_selectors = [
            (By.LINK_TEXT, 'Sign In'),
            (By.LINK_TEXT, 'Log In'),
            (By.LINK_TEXT, 'Login'),
            (By.PARTIAL_LINK_TEXT, 'Sign'),
            (By.XPATH, '//a[contains(@href, "login") or contains(@href, "signin") or contains(@href, "auth")]'),
            (By.XPATH, '//button[contains(text(), "Sign In") or contains(text(), "Login")]'),
        ]

        sign_in_link = None
        for by, selector in sign_in_selectors:
            try:
                sign_in_link = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((by, selector))
                )
                if debug:
                    print(f"✓ Found sign in link: {by}={selector}")
                break
            except TimeoutException:
                continue

        if sign_in_link:
            if debug:
                print("Clicking sign in link...")
            sign_in_link.click()
            time.sleep(3)

            if debug:
                print(f"After clicking sign in, URL: {driver.current_url}")
                driver.save_screenshot('/home/pi/src/pumphouse/ecobee_login_page.png')
                print("DEBUG: Saved screenshot to ecobee_login_page.png")
        else:
            if debug:
                print("WARNING: Could not find sign in link, will try to find login form anyway")
                driver.save_screenshot('/home/pi/src/pumphouse/ecobee_initial_page.png')
                print("DEBUG: Saved screenshot to ecobee_initial_page.png")

        # Look for login form elements
        # Try to find username/email field
        username_selectors = [
            (By.ID, 'username'),
            (By.ID, 'email'),
            (By.NAME, 'username'),
            (By.NAME, 'email'),
            (By.CSS_SELECTOR, 'input[type="email"]'),
            (By.CSS_SELECTOR, 'input[placeholder*="email" i]'),
            (By.CSS_SELECTOR, 'input[placeholder*="username" i]'),
        ]

        username_field = None
        for by, selector in username_selectors:
            try:
                username_field = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((by, selector))
                )
                if debug:
                    print(f"✓ Found username field: {by}={selector}")
                break
            except TimeoutException:
                continue

        if not username_field:
            print("ERROR: Could not find username/email field")
            if debug:
                print("Page source (first 1000 chars):")
                print(driver.page_source[:1000])
            return False

        # Enter username/email first
        if debug:
            print("Entering email...")

        username_field.clear()
        username_field.send_keys(USERNAME)

        time.sleep(1)

        # Find and click "Continue" or "Next" button to proceed to password
        next_button = None
        next_selectors = [
            (By.CSS_SELECTOR, 'button[type="submit"]'),
            (By.CSS_SELECTOR, 'input[type="submit"]'),
            (By.XPATH, '//button[contains(text(), "Continue") or contains(text(), "Next")]'),
            (By.XPATH, '//button[contains(text(), "Sign") or contains(text(), "Log")]'),
        ]

        for by, selector in next_selectors:
            try:
                next_button = driver.find_element(by, selector)
                if debug:
                    print(f"✓ Found next button: {by}={selector}")
                break
            except NoSuchElementException:
                continue

        if next_button:
            if debug:
                print("Clicking next button...")
            # Use JavaScript click if regular click is intercepted
            try:
                next_button.click()
            except Exception as e:
                if debug:
                    print(f"Regular click failed, trying JavaScript click: {e}")
                driver.execute_script("arguments[0].click();", next_button)

            time.sleep(5)  # Wait for password field to appear

            if debug:
                print(f"After clicking next, URL: {driver.current_url}")

        # Now find password field (might be on same page or new page)
        password_field = None
        try:
            password_field = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'input[type="password"]'))
            )
            if debug:
                print("✓ Found password field")
        except TimeoutException:
            print("ERROR: Could not find password field after clicking next")
            if debug:
                driver.save_screenshot('/home/pi/src/pumphouse/ecobee_after_email.png')
                print("Saved screenshot to ecobee_after_email.png")
                print(f"Current URL: {driver.current_url}")

                # Save page source
                with open('/home/pi/src/pumphouse/ecobee_after_email.html', 'w') as f:
                    f.write(driver.page_source)
                print("Saved page source to ecobee_after_email.html")

                # Check if there's an error message
                try:
                    error_msg = driver.find_element(By.CSS_SELECTOR, '.error, .alert, [role="alert"]')
                    print(f"Error message found: {error_msg.text}")
                except:
                    pass

                # Print first 1000 chars of page source
                print("\nPage source (first 1000 chars):")
                print(driver.page_source[:1000])
            return False

        # Enter password
        if debug:
            print("Entering password...")

        password_field.clear()
        password_field.send_keys(PASSWORD)

        time.sleep(1)

        # Find and click submit button
        submit_button = None
        submit_selectors = [
            (By.CSS_SELECTOR, 'button[type="submit"]'),
            (By.CSS_SELECTOR, 'input[type="submit"]'),
            (By.XPATH, '//button[contains(text(), "Log") or contains(text(), "Sign")]'),
            (By.XPATH, '//input[@value="Log" or @value="Sign"]'),
        ]

        for by, selector in submit_selectors:
            try:
                submit_button = driver.find_element(by, selector)
                if debug:
                    print(f"✓ Found submit button: {by}={selector}")
                break
            except NoSuchElementException:
                continue

        if not submit_button:
            print("ERROR: Could not find submit button")
            return False

        if debug:
            print("Clicking submit button...")

        submit_button.click()

        # Wait for page to process login
        time.sleep(5)

        if debug:
            print(f"After login URL: {driver.current_url}")
            driver.save_screenshot('/home/pi/src/pumphouse/ecobee_after_login.png')
            print("DEBUG: Saved screenshot to ecobee_after_login.png")

        # Check for 2FA prompt
        page_text = driver.page_source.lower()
        if any(keyword in page_text for keyword in ['verification', 'authenticator', 'enter code', '2fa', 'two-factor']):
            if debug:
                print("2FA detected...")

            if not handle_2fa(driver, debug):
                print("ERROR: 2FA authentication failed")
                return False

            if debug:
                print("✓ 2FA completed")

        # Check if login was successful
        # Should be on consumer portal now (even if URL has #/login hash)
        # Auth pages are at auth.ecobee.com, portal is at www.ecobee.com/consumerportal
        if 'auth.ecobee.com' in driver.current_url.lower():
            print("ERROR: Still on auth page after submit")
            if debug:
                driver.save_screenshot('/home/pi/src/pumphouse/ecobee_auth_failed.png')
            return False

        if 'consumerportal' not in driver.current_url.lower():
            print(f"ERROR: Not on consumer portal. URL: {driver.current_url}")
            if debug:
                driver.save_screenshot('/home/pi/src/pumphouse/ecobee_wrong_page.png')
            return False

        if debug:
            print("✓ Login successful")

        return True

    except Exception as e:
        print(f"ERROR during login: {e}")
        if debug:
            import traceback
            traceback.print_exc()
        return False

def handle_2fa(driver, debug=False):
    """Handle 2FA authentication."""
    try:
        code = generate_totp_code()

        if not code:
            print("ERROR: Could not generate TOTP code")
            return False

        if debug:
            print(f"Generated TOTP code: {code}")

        # Find code input field
        code_field = None
        code_selectors = [
            (By.ID, 'code'),
            (By.ID, 'token'),
            (By.ID, 'otp'),
            (By.NAME, 'code'),
            (By.NAME, 'token'),
            (By.CSS_SELECTOR, 'input[type="text"]'),
            (By.CSS_SELECTOR, 'input[type="number"]'),
        ]

        for by, selector in code_selectors:
            try:
                code_field = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((by, selector))
                )
                if debug:
                    print(f"✓ Found 2FA code field: {by}={selector}")
                break
            except TimeoutException:
                continue

        if not code_field:
            print("ERROR: Could not find 2FA code field")
            return False

        # Enter code
        code_field.clear()
        code_field.send_keys(code)

        time.sleep(1)

        # Try to check "Remember this device" checkbox
        try:
            remember_checkbox = driver.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]')
            if not remember_checkbox.is_selected():
                # Use JavaScript click to avoid interception
                driver.execute_script("arguments[0].click();", remember_checkbox)
                if debug:
                    print("✓ Checked 'remember device' checkbox")
        except (NoSuchElementException, Exception) as e:
            if debug:
                print(f"Could not check remember checkbox: {e}")
            pass

        # Find and click submit
        submit_button = driver.find_element(By.CSS_SELECTOR, 'button[type="submit"], input[type="submit"]')
        submit_button.click()

        # Wait for 2FA to process
        time.sleep(5)

        if debug:
            print(f"After 2FA URL: {driver.current_url}")
            driver.save_screenshot('/home/pi/src/pumphouse/ecobee_after_2fa.png')

        return True

    except Exception as e:
        print(f"ERROR during 2FA: {e}")
        if debug:
            import traceback
            traceback.print_exc()
        return False

def extract_thermostat_data(driver, debug=False):
    """Extract thermostat data from the portal."""
    try:
        # Wait for page to fully load and React to render
        time.sleep(8)

        if debug:
            print(f"Current URL: {driver.current_url}")
            print("Looking for thermostat data...")
            driver.save_screenshot('/home/pi/src/pumphouse/ecobee_portal_page.png')
            print("DEBUG: Saved screenshot to ecobee_portal_page.png")

            # Save page source
            with open('/home/pi/src/pumphouse/ecobee_portal_source.html', 'w') as f:
                f.write(driver.page_source)
            print("DEBUG: Saved page source to ecobee_portal_source.html")

        # Find all thermostat tiles
        thermostat_tiles = driver.find_elements(By.CSS_SELECTOR, '[data-qa-class="thermostat-tile"]')

        if debug:
            print(f"Found {len(thermostat_tiles)} thermostat tiles")

        target_data = None

        for tile in thermostat_tiles:
            try:
                # Get thermostat name
                name_elem = tile.find_element(By.CSS_SELECTOR, '[data-qa-class="interactive-tile_title"]')
                name = name_elem.text.strip()

                if debug:
                    print(f"\nProcessing thermostat: {name}")

                # Check if this is our target thermostat
                if name.lower() != TARGET_THERMOSTAT.lower():
                    if debug:
                        print(f"  Skipping (not target)")
                    continue

                # Get temperature
                temp_elem = tile.find_element(By.CSS_SELECTOR, '[data-qa-class="temperature"] span')
                temperature = float(temp_elem.text.strip())

                # Check for hold/vacation mode
                try:
                    hold_elem = tile.find_element(By.CSS_SELECTOR, '[data-qa-class="hold_content"]')
                    hold_text = hold_elem.text.strip()
                    vacation_mode = 'vacation' in hold_text.lower()
                    hold_active = len(hold_text) > 0
                except NoSuchElementException:
                    vacation_mode = False
                    hold_active = False
                    hold_text = None

                # Get system mode (heat/cool)
                try:
                    mode_elem = tile.find_element(By.CSS_SELECTOR, '[data-qa-class="system_mode"]')
                    system_mode = mode_elem.get_attribute('data-qa-systemmode')
                except NoSuchElementException:
                    system_mode = None

                target_data = {
                    'thermostat_name': name,
                    'temperature': temperature,
                    'vacation_mode': vacation_mode,
                    'hold_active': hold_active,
                    'hold_text': hold_text,
                    'system_mode': system_mode,
                    'location': TARGET_LOCATION,
                    'timestamp': datetime.now().isoformat()
                }

                if debug:
                    print(f"✓ Found target thermostat!")
                    print(f"  Temperature: {temperature}°F")
                    print(f"  Hold: {hold_text}")
                    print(f"  Vacation: {vacation_mode}")
                    print(f"  System Mode: {system_mode}")

                break

            except Exception as e:
                if debug:
                    print(f"  Error processing tile: {e}")
                continue

        if not target_data:
            print(f"\nWARNING: Could not find thermostat '{TARGET_THERMOSTAT}'")
            if debug:
                print("Available thermostats:")
                for tile in thermostat_tiles:
                    try:
                        name_elem = tile.find_element(By.CSS_SELECTOR, '[data-qa-class="interactive-tile_title"]')
                        print(f"  - {name_elem.text.strip()}")
                    except:
                        pass

        return target_data

    except Exception as e:
        print(f"ERROR extracting data: {e}")
        if debug:
            import traceback
            traceback.print_exc()
        return None

def main():
    parser = argparse.ArgumentParser(description='Scrape Ecobee using Selenium')
    parser.add_argument('--debug', action='store_true', help='Enable debug output')
    parser.add_argument('--headless', action='store_true', default=True, help='Run in headless mode (default: True)')
    parser.add_argument('--show-browser', action='store_true', help='Show browser window (opposite of headless)')
    args = parser.parse_args()

    headless = args.headless and not args.show_browser

    print("Ecobee Web Portal Scraper (Selenium)")
    print("=" * 60)
    print(f"Target Thermostat: {TARGET_THERMOSTAT}")
    print(f"Target Location: {TARGET_LOCATION}")
    print(f"Headless mode: {headless}")
    print("=" * 60)

    # Create driver
    driver = create_driver(headless=headless, debug=args.debug)

    try:
        # Login
        print("\n[1/2] Logging in...")
        if not login(driver, debug=args.debug):
            print("✗ Login failed")
            return 1
        print("✓ Login successful")

        # Extract data
        print("\n[2/2] Extracting thermostat data...")
        data = extract_thermostat_data(driver, debug=args.debug)

        if data:
            print(f"\n✓ Successfully extracted data:")
            print(json.dumps(data, indent=2))
            return 0
        else:
            print("\n⚠ Data extraction needs investigation")
            print("Screenshots and HTML saved for analysis")
            return 0  # Return 0 since login worked

    finally:
        if args.debug:
            print("\nClosing browser...")
        driver.quit()

if __name__ == '__main__':
    sys.exit(main())
