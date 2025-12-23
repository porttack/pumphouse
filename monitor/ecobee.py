#!/usr/bin/env python3
"""
Ecobee Thermostat Control Library

Provides functions to:
1. Read temperatures from all thermostats in the Blackberry Hill house
2. Set temperature holds (indefinite) for individual thermostats
3. Enable/disable vacation mode for the entire house

Uses Selenium to scrape the Ecobee consumer portal since API access is restricted.

Usage:
    from monitor.ecobee import EcobeeController

    ecobee = EcobeeController()

    # Get all thermostat data
    data = ecobee.get_all_thermostats()

    # Set temperature for a specific thermostat
    ecobee.set_temperature('Living Room Ecobee', heat=71, cool=75)

    # Enable vacation mode
    ecobee.enable_vacation_mode()

    # Disable vacation mode
    ecobee.disable_vacation_mode()
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.common.exceptions import TimeoutException, NoSuchElementException
except ImportError:
    print("ERROR: selenium library not installed")
    print("Install with: pip install selenium")
    sys.exit(1)

try:
    import pyotp
except ImportError:
    print("WARNING: pyotp library not installed. 2FA will not work.")
    pyotp = None

# Configuration
ECOBEE_BASE_URL = "https://www.ecobee.com"
LOGIN_URL = "https://auth.ecobee.com/u/login"
PORTAL_URL = f"{ECOBEE_BASE_URL}/consumerportal/index.html"
SECRETS_PATH = Path.home() / ".config" / "pumphouse" / "secrets.conf"

# House configuration
HOUSE_NAME = "Blackberry Hill"
DEFAULT_TEMPERATURES = {
    "Living Room Ecobee": {"heat": 71, "cool": 75},
    "Hallway": {"heat": 68, "cool": 75},
}


class EcobeeController:
    """Controller for Ecobee thermostats via web scraping."""

    def __init__(self, headless=True, debug=False):
        """
        Initialize the Ecobee controller.

        Args:
            headless: Run browser in headless mode
            debug: Enable debug output
        """
        self.headless = headless
        self.debug = debug
        self.driver = None
        self.username = None
        self.password = None
        self.totp_secret = None

        # Load credentials
        self._load_credentials()

    def _load_credentials(self):
        """Load Ecobee credentials from secrets.conf."""
        if not SECRETS_PATH.exists():
            raise RuntimeError(f"Secrets file not found: {SECRETS_PATH}")

        secrets = {}
        with open(SECRETS_PATH, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    secrets[key.strip()] = value.strip()

        self.username = secrets.get('ECOBEE_USERNAME')
        self.password = secrets.get('ECOBEE_PASSWORD')
        self.totp_secret = secrets.get('ECOBEE_TOTP_SECRET')

        if not self.username or not self.password:
            raise RuntimeError("ECOBEE_USERNAME and ECOBEE_PASSWORD must be set in secrets.conf")

    def _generate_totp(self):
        """Generate TOTP code for 2FA."""
        if not self.totp_secret:
            raise RuntimeError("ECOBEE_TOTP_SECRET not configured")

        if not pyotp:
            raise RuntimeError("pyotp library not installed")

        totp = pyotp.TOTP(self.totp_secret)
        return totp.now()

    def _create_driver(self):
        """Create and return a Selenium WebDriver instance."""
        options = Options()

        if self.headless:
            options.add_argument('--headless=new')

        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)')

        service = Service('/usr/bin/chromedriver')
        return webdriver.Chrome(service=service, options=options)

    def _login(self):
        """Login to Ecobee portal."""
        if self.debug:
            print("Logging in to Ecobee...")

        self.driver.get(LOGIN_URL)
        time.sleep(3)

        # Dismiss cookie banner
        try:
            cookie_button = WebDriverWait(self.driver, 3).until(
                EC.element_to_be_clickable((By.XPATH, '//button[contains(text(), "Accept")]'))
            )
            cookie_button.click()
            time.sleep(1)
        except TimeoutException:
            pass

        # Enter email
        username_field = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.ID, 'username'))
        )
        username_field.clear()
        username_field.send_keys(self.username)
        time.sleep(1)

        # Click next
        next_button = self.driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
        try:
            next_button.click()
        except Exception:
            self.driver.execute_script("arguments[0].click();", next_button)

        time.sleep(3)

        # Enter password
        password_field = WebDriverWait(self.driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'input[type="password"]'))
        )
        password_field.clear()
        password_field.send_keys(self.password)
        time.sleep(1)

        # Submit login
        submit_button = self.driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
        submit_button.click()
        time.sleep(5)

        # Handle 2FA
        try:
            code_field = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, 'code'))
            )

            if self.debug:
                print("2FA detected, generating code...")

            totp_code = self._generate_totp()
            code_field.clear()
            code_field.send_keys(totp_code)
            time.sleep(1)

            # Check remember device
            try:
                remember_checkbox = self.driver.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]')
                if not remember_checkbox.is_selected():
                    self.driver.execute_script("arguments[0].click();", remember_checkbox)
            except:
                pass

            # Submit 2FA
            submit_2fa = self.driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
            submit_2fa.click()
            time.sleep(5)
        except TimeoutException:
            # No 2FA required (already remembered)
            pass

        # Wait for portal to load
        time.sleep(3)

        # Verify we're on the portal
        if 'consumerportal' not in self.driver.current_url.lower():
            raise RuntimeError(f"Login failed. Current URL: {self.driver.current_url}")

        if self.debug:
            print("✓ Logged in successfully")

    def _ensure_logged_in(self):
        """Ensure we have an active browser session and are logged in."""
        if self.driver is None:
            self.driver = self._create_driver()
            self._login()

    def get_all_thermostats(self):
        """
        Get data for all thermostats in the house.

        Returns:
            list: List of thermostat data dictionaries with keys:
                - name: Thermostat name
                - temperature: Current temperature (float)
                - heat_setpoint: Heat setpoint (float or None)
                - cool_setpoint: Cool setpoint (float or None)
                - system_mode: 'heat', 'cool', 'auto', or 'off'
                - hold_active: Boolean
                - hold_text: Hold description or None
                - vacation_mode: Boolean
        """
        self._ensure_logged_in()

        # Navigate to devices page
        self.driver.get(f"{PORTAL_URL}#/devices")
        time.sleep(8)  # Wait for React to render

        thermostats = []
        tiles = self.driver.find_elements(By.CSS_SELECTOR, '[data-qa-class="thermostat-tile"]')

        for tile in tiles:
            try:
                # Get name
                name_elem = tile.find_element(By.CSS_SELECTOR, '[data-qa-class="interactive-tile_title"]')
                name = name_elem.text.strip()

                # Get temperature
                temp_elem = tile.find_element(By.CSS_SELECTOR, '[data-qa-class="temperature"] span')
                temperature = float(temp_elem.text.strip())

                # Get system mode
                try:
                    mode_elem = tile.find_element(By.CSS_SELECTOR, '[data-qa-class="system_mode"]')
                    system_mode = mode_elem.get_attribute('data-qa-systemmode').replace('mode', '')
                except NoSuchElementException:
                    system_mode = None

                # Get setpoints
                heat_setpoint = None
                cool_setpoint = None

                try:
                    heat_elem = tile.find_element(By.CSS_SELECTOR, '[data-qa-class="heat_setpoint"]')
                    heat_setpoint = float(heat_elem.text.strip())
                except NoSuchElementException:
                    pass

                try:
                    cool_elem = tile.find_element(By.CSS_SELECTOR, '[data-qa-class="cool_setpoint"]')
                    cool_setpoint = float(cool_elem.text.strip())
                except NoSuchElementException:
                    pass

                # Get hold info
                hold_active = False
                hold_text = None
                vacation_mode = False

                try:
                    hold_elem = tile.find_element(By.CSS_SELECTOR, '[data-qa-class="hold_content"]')
                    hold_text = hold_elem.text.strip()
                    hold_active = len(hold_text) > 0
                    vacation_mode = 'vacation' in hold_text.lower()
                except NoSuchElementException:
                    pass

                thermostats.append({
                    'name': name,
                    'temperature': temperature,
                    'heat_setpoint': heat_setpoint,
                    'cool_setpoint': cool_setpoint,
                    'system_mode': system_mode,
                    'hold_active': hold_active,
                    'hold_text': hold_text,
                    'vacation_mode': vacation_mode,
                    'timestamp': datetime.now().isoformat()
                })

                if self.debug:
                    print(f"Found thermostat: {name} - {temperature}°F")

            except Exception as e:
                if self.debug:
                    print(f"Error parsing thermostat tile: {e}")
                continue

        return thermostats

    def get_thermostat(self, name):
        """
        Get data for a specific thermostat by name.

        Args:
            name: Thermostat name (case-insensitive)

        Returns:
            dict: Thermostat data or None if not found
        """
        thermostats = self.get_all_thermostats()

        for tstat in thermostats:
            if tstat['name'].lower() == name.lower():
                return tstat

        return None

    def set_temperature(self, name, heat=None, cool=None, hold_type='indefinite'):
        """
        Set temperature hold for a specific thermostat.

        NOTE: The Ecobee web portal uses a canvas-based temperature slider that is
        difficult to interact with programmatically. This implementation uses the
        "Home and hold" preset button as a workaround. For precise temperature control,
        consider using the official Ecobee API if you have developer access.

        Args:
            name: Thermostat name
            heat: Heat setpoint (currently ignored - uses Home preset)
            cool: Cool setpoint (currently ignored - uses Home preset)
            hold_type: Hold type (currently only 'indefinite' supported)

        Returns:
            bool: True if successful

        Raises:
            ValueError: If thermostat not found
            RuntimeError: If operation fails
        """
        self._ensure_logged_in()

        if self.debug:
            print(f"Setting temperature for {name} (using Home preset)")

        # Navigate to devices page
        self.driver.get(f"{PORTAL_URL}#/devices")
        time.sleep(8)

        # Find the thermostat tile
        tiles = self.driver.find_elements(By.CSS_SELECTOR, '[data-qa-class="thermostat-tile"]')

        target_tile = None
        for tile in tiles:
            try:
                name_elem = tile.find_element(By.CSS_SELECTOR, '[data-qa-class="interactive-tile_title"]')
                if name_elem.text.strip().lower() == name.lower():
                    target_tile = tile
                    break
            except:
                continue

        if not target_tile:
            raise ValueError(f"Thermostat '{name}' not found")

        # Click the tile to open details
        try:
            target_tile.click()
        except:
            self.driver.execute_script("arguments[0].click();", target_tile)

        time.sleep(5)

        # Click "Home and hold" button to set to comfort temperature
        try:
            home_button = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, '//button[contains(text(), "Home and hold")]'))
            )

            if self.debug:
                print(f"  Clicking 'Home and hold' button")

            try:
                home_button.click()
            except:
                self.driver.execute_script("arguments[0].click();", home_button)

            time.sleep(3)

            if self.debug:
                print(f"  ✓ Temperature hold set for {name}")

            return True

        except TimeoutException:
            raise RuntimeError(f"Could not find temperature controls for {name}")

    def cancel_hold(self, name):
        """
        Cancel any active hold on a thermostat (resume schedule).

        Args:
            name: Thermostat name

        Returns:
            bool: True if successful
        """
        self._ensure_logged_in()

        if self.debug:
            print(f"Canceling hold for {name}")

        # Navigate to devices page
        self.driver.get(f"{PORTAL_URL}#/devices")
        time.sleep(8)

        # Find and click the thermostat tile
        tiles = self.driver.find_elements(By.CSS_SELECTOR, '[data-qa-class="thermostat-tile"]')

        for tile in tiles:
            try:
                name_elem = tile.find_element(By.CSS_SELECTOR, '[data-qa-class="interactive-tile_title"]')
                if name_elem.text.strip().lower() == name.lower():
                    try:
                        tile.click()
                    except:
                        self.driver.execute_script("arguments[0].click();", tile)
                    time.sleep(5)
                    break
            except:
                continue
        else:
            raise ValueError(f"Thermostat '{name}' not found")

        # Look for the hold close icon (X button)
        try:
            close_icon = self.driver.find_element(By.ID, 'holdCloseIcon')
            if self.debug:
                print(f"  Clicking hold close icon")

            try:
                close_icon.click()
            except:
                self.driver.execute_script("arguments[0].click();", close_icon)

            time.sleep(2)

            if self.debug:
                print(f"  ✓ Hold canceled for {name}")

            return True

        except NoSuchElementException:
            if self.debug:
                print(f"  No active hold found for {name}")
            return True

    def enable_vacation_mode(self, start_date=None, end_date=None, heat=55, cool=85):
        """
        Enable vacation mode for the house.

        NOTE: This function opens the vacation creation dialog. The actual form filling
        for start/end dates is complex and requires further investigation of the date
        picker widget. Current implementation clicks through to the vacation page.

        Args:
            start_date: Start date (datetime or None for now) - NOT YET IMPLEMENTED
            end_date: End date (datetime, required) - NOT YET IMPLEMENTED
            heat: Heat setpoint during vacation (default: 55) - NOT YET IMPLEMENTED
            cool: Cool setpoint during vacation (default: 85) - NOT YET IMPLEMENTED

        Returns:
            bool: True if vacation page is reached

        Raises:
            RuntimeError: If vacation modal cannot be opened
        """
        self._ensure_logged_in()

        if self.debug:
            print(f"Opening vacation mode interface...")

        # Navigate to devices page
        self.driver.get(f"{PORTAL_URL}#/devices")
        time.sleep(8)

        # Click on first thermostat to get to detail view
        tiles = self.driver.find_elements(By.CSS_SELECTOR, '[data-qa-class="thermostat-tile"]')
        if tiles:
            try:
                tiles[0].click()
            except:
                self.driver.execute_script("arguments[0].click();", tiles[0])
            time.sleep(5)

        # Click on VACATION tile
        try:
            vacation_tile = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, '//div[contains(text(), "VACATION")]'))
            )

            if self.debug:
                print(f"  Clicking vacation tile")

            try:
                vacation_tile.click()
            except:
                self.driver.execute_script("arguments[0].click();", vacation_tile)

            time.sleep(5)

            # Check if we're on vacation page
            if 'vacations' in self.driver.current_url:
                if self.debug:
                    print(f"  ✓ Vacation page opened")
                    print(f"  URL: {self.driver.current_url}")

                # Look for "New Vacation" button
                try:
                    new_vacation_btn = self.driver.find_element(By.XPATH, '//button[contains(text(), "New Vacation")]')

                    if self.debug:
                        print(f"  Found 'New Vacation' button")
                        print(f"  NOTE: Date picker implementation not yet complete")
                        print(f"  Vacation creation must be completed manually for now")

                    # TODO: Click "New Vacation" and fill out form
                    # This requires implementing date picker interactions

                    return True

                except NoSuchElementException:
                    if self.debug:
                        print(f"  Could not find 'New Vacation' button")
                    return False
            else:
                raise RuntimeError(f"Not on vacation page. URL: {self.driver.current_url}")

        except TimeoutException:
            raise RuntimeError("Could not find vacation controls")

    def disable_vacation_mode(self, vacation_name=None, delete_all=True):
        """
        Disable vacation mode by deleting active vacations.

        Args:
            vacation_name: Name of specific vacation to delete (None = delete all if delete_all=True)
            delete_all: If True, delete all vacations. If False and vacation_name is None, delete first vacation.

        Returns:
            int: Number of vacations deleted

        Raises:
            RuntimeError: If vacation page cannot be accessed
            ValueError: If specified vacation_name not found
        """
        self._ensure_logged_in()

        if self.debug:
            if vacation_name:
                print(f"Deleting vacation: {vacation_name}")
            elif delete_all:
                print("Deleting all vacations...")
            else:
                print("Deleting first vacation...")

        # Navigate directly to vacations page
        # Get thermostat ID from first thermostat
        self.driver.get(f"{PORTAL_URL}#/devices")
        time.sleep(8)

        # Click first thermostat to get to detail view
        tiles = self.driver.find_elements(By.CSS_SELECTOR, '[data-qa-class="thermostat-tile"]')
        if not tiles:
            raise RuntimeError("No thermostats found")

        try:
            tiles[0].click()
        except:
            self.driver.execute_script("arguments[0].click();", tiles[0])
        time.sleep(5)

        # Extract thermostat ID from URL
        current_url = self.driver.current_url
        # URL format: .../thermostats/THERMOSTAT_ID
        thermostat_id = current_url.split('/thermostats/')[-1].split('/')[0] if '/thermostats/' in current_url else None

        if thermostat_id:
            # Navigate directly to vacations page
            vacations_url = f"{PORTAL_URL}#/devices/thermostats/{thermostat_id}/vacations"
            self.driver.get(vacations_url)
            time.sleep(5)
        else:
            # Fall back to clicking VACATION tile
            try:
                vacation_tile = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, '//div[contains(text(), "VACATION")]'))
                )
                try:
                    vacation_tile.click()
                except:
                    self.driver.execute_script("arguments[0].click();", vacation_tile)
                time.sleep(5)
            except TimeoutException:
                raise RuntimeError("Could not find vacation controls")

        # Check if there are any vacations
        page_text = self.driver.page_source.lower()

        if 'no scheduled vacations' in page_text:
            if self.debug:
                print("  No active vacations found")
            return 0

        # Find vacation items using the ng-repeat structure
        # Vacation items appear as clickable menu items in the list
        vacation_items = self.driver.find_elements(By.CSS_SELECTOR, '.vacation-menu-item .menu-list__item')

        if not vacation_items:
            # Try alternative selectors
            vacation_items = self.driver.find_elements(By.XPATH, '//div[contains(@class, "menu-list__item")]')

        if not vacation_items:
            if self.debug:
                print("  Could not find vacation items in list")
            return 0

        if self.debug:
            print(f"  Found {len(vacation_items)} vacation(s)")

        deleted_count = 0

        # Determine which vacations to delete
        items_to_delete = []

        if vacation_name:
            # Find specific vacation by name
            for item in vacation_items:
                try:
                    item_text = item.text.strip()
                    if vacation_name.lower() in item_text.lower():
                        items_to_delete.append(item)
                        break
                except:
                    continue

            if not items_to_delete:
                raise ValueError(f"Vacation '{vacation_name}' not found")

        elif delete_all:
            # Delete all vacations
            items_to_delete = vacation_items
        else:
            # Delete only first vacation
            if vacation_items:
                items_to_delete = [vacation_items[0]]

        # Delete each vacation
        for item in items_to_delete:
            try:
                if self.debug:
                    try:
                        vacation_name_text = item.text.strip()
                        print(f"  Deleting: {vacation_name_text}")
                    except:
                        print(f"  Deleting vacation...")

                # Click the vacation item to open detail view
                try:
                    item.click()
                except:
                    self.driver.execute_script("arguments[0].click();", item)

                time.sleep(3)

                # Look for delete button in detail view
                delete_button = None
                delete_selectors = [
                    '//button[contains(text(), "Delete")]',
                    '//button[contains(text(), "Remove")]',
                    '//button[contains(@aria-label, "Delete")]',
                    '//button[contains(@aria-label, "delete")]',
                    '.delete-button',
                    '[data-qa-class*="delete"]',
                ]

                for selector in delete_selectors:
                    try:
                        if selector.startswith('//'):
                            delete_button = self.driver.find_element(By.XPATH, selector)
                        else:
                            delete_button = self.driver.find_element(By.CSS_SELECTOR, selector)

                        if delete_button:
                            break
                    except NoSuchElementException:
                        continue

                if delete_button:
                    if self.debug:
                        print(f"    Clicking delete button")

                    try:
                        delete_button.click()
                    except:
                        self.driver.execute_script("arguments[0].click();", delete_button)

                    time.sleep(2)

                    # Look for confirmation dialog and confirm
                    try:
                        confirm_button = WebDriverWait(self.driver, 3).until(
                            EC.presence_of_element_located((By.XPATH, '//button[contains(text(), "Yes") or contains(text(), "Confirm") or contains(text(), "Delete")]'))
                        )
                        try:
                            confirm_button.click()
                        except:
                            self.driver.execute_script("arguments[0].click();", confirm_button)

                        time.sleep(2)

                        if self.debug:
                            print(f"    ✓ Vacation deleted")

                        deleted_count += 1

                    except TimeoutException:
                        # No confirmation needed, already deleted
                        if self.debug:
                            print(f"    ✓ Vacation deleted (no confirmation)")
                        deleted_count += 1

                else:
                    if self.debug:
                        print(f"    ✗ Could not find delete button")

                # Navigate back to vacation list for next item
                if delete_all and deleted_count < len(items_to_delete):
                    if thermostat_id:
                        self.driver.get(f"{PORTAL_URL}#/devices/thermostats/{thermostat_id}/vacations")
                    else:
                        self.driver.back()
                    time.sleep(3)

            except Exception as e:
                if self.debug:
                    print(f"    ✗ Error deleting vacation: {e}")
                continue

        if self.debug:
            print(f"\n  Deleted {deleted_count} vacation(s)")

        return deleted_count

    def close(self):
        """Close the browser session."""
        if self.driver:
            self.driver.quit()
            self.driver = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()


if __name__ == '__main__':
    # Test the library
    print("Testing Ecobee Controller")
    print("=" * 60)

    with EcobeeController(headless=True, debug=True) as ecobee:
        print("\nGetting all thermostats...")
        thermostats = ecobee.get_all_thermostats()

        print(f"\nFound {len(thermostats)} thermostats:")
        for tstat in thermostats:
            print(f"\n{tstat['name']}:")
            print(f"  Temperature: {tstat['temperature']}°F")
            print(f"  Heat Setpoint: {tstat['heat_setpoint']}")
            print(f"  Cool Setpoint: {tstat['cool_setpoint']}")
            print(f"  System Mode: {tstat['system_mode']}")
            print(f"  Hold: {tstat['hold_text'] or 'None'}")
            print(f"  Vacation: {tstat['vacation_mode']}")
