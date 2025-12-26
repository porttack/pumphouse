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

# Known thermostat names per house (used for filtering when house switch fails)
HOUSE_THERMOSTATS = {
    "Blackberry Hill": ["Living Room Ecobee", "Hallway"],
    # Adjust these as needed for your second house
    "Ashley St": ["Home", "Caboose"],
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

    def _select_house(self, house_name):
        """Ensure the specified house is selected on the devices page."""
        if self.debug:
            print(f"Selecting house: {house_name}")

        # We're already on the devices page at this point
        # Strategy: try clicking a visible element with exact text match; if not,
        # open any likely selector/dropdown, then click the desired house.
        try:
            # Direct click if the house name element is clickable
            target = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, f"//*[normalize-space(text())='{house_name}']"))
            )
            try:
                target.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", target)
            time.sleep(2)
            return True
        except TimeoutException:
            if self.debug:
                print("House name element not directly clickable; trying selector menu...")

        # Try opening a location/house selector and then clicking the house
        try:
            # Common patterns for location selectors (best-effort heuristics)
            triggers = [
                (By.CSS_SELECTOR, '[data-qa-class="location-selector"]'),
                (By.CSS_SELECTOR, '[data-qa-class*="selector"]'),
                (By.XPATH, "//button[contains(@aria-label,'Location') or contains(.,'Location') or contains(.,'Home')]")
            ]
            opened = False
            for by, sel in triggers:
                try:
                    btn = WebDriverWait(self.driver, 3).until(
                        EC.element_to_be_clickable((by, sel))
                    )
                    btn.click()
                    opened = True
                    time.sleep(1)
                    break
                except TimeoutException:
                    continue

            if opened:
                target = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, f"//*[normalize-space(text())='{house_name}']"))
                )
                try:
                    target.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", target)
                time.sleep(2)
                return True
        except TimeoutException:
            pass

        if self.debug:
            print("⚠️ Could not switch house; continuing with current selection.")
        return False

    def _get_house_thermostat_ids(self, house_name=None):
        """Return a list of thermostats with their Ecobee IDs for a house.

        Each item: {'name': <display name>, 'id': <numeric string>}
        """
        self._ensure_logged_in()
        self.driver.get(f"{PORTAL_URL}#/devices")
        time.sleep(8)
        if house_name:
            try:
                self._select_house(house_name)
            except Exception:
                pass

        results = []
        tiles = self.driver.find_elements(By.CSS_SELECTOR, '[data-qa-class="thermostat-tile"]')
        import re
        for tile in tiles:
            try:
                name_elem = tile.find_element(By.CSS_SELECTOR, '[data-qa-class="interactive-tile_title"]')
                name = name_elem.text.strip()
                data_qa_id = tile.get_attribute('data-qa-id') or ''
                m = re.search(r'(\d+)', data_qa_id)
                if m:
                    results.append({'name': name, 'id': m.group(1)})
            except Exception:
                continue
        return results

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

        # Robustly detect chromedriver path
        import shutil
        import os
        from pathlib import Path as _Path

        candidates = []
        # Allow override via environment
        env_path = os.environ.get('CHROMEDRIVER_PATH')
        if env_path:
            candidates.append(env_path)

        # Try PATH resolution
        which_path = shutil.which('chromedriver')
        if which_path:
            candidates.append(which_path)

        # Common macOS/Homebrew locations
        candidates.extend([
            '/opt/homebrew/bin/chromedriver',
            '/usr/local/bin/chromedriver',
            '/usr/bin/chromedriver'
        ])

        chromedriver_path = None
        for p in candidates:
            try:
                if p and _Path(p).exists():
                    chromedriver_path = p
                    break
            except Exception:
                continue

        if not chromedriver_path:
            raise RuntimeError(
                "chromedriver not found. Install via Homebrew: 'brew install chromedriver'. "
                "If already installed, set CHROMEDRIVER_PATH to its location (e.g., /opt/homebrew/bin/chromedriver)."
            )

        if self.debug:
            print(f"Using chromedriver at: {chromedriver_path}")

        service = Service(chromedriver_path)
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

    def get_all_thermostats(self, house_name=None):
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

        # Ensure we are on requested house if provided
        if house_name:
            try:
                self._select_house(house_name)
            except Exception:
                pass

        # Ensure we are on the correct house
        try:
            self._select_house(HOUSE_NAME)
        except Exception:
            # Non-fatal; proceed with current selection
            pass

        thermostats = []
        thermostat_ids = {}
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

                # Extract thermostat ID from tile's data-qa-id (e.g., ref521750262114)
                try:
                    data_qa_id = tile.get_attribute('data-qa-id')
                    if data_qa_id:
                        import re
                        m = re.search(r'(\d+)', data_qa_id)
                        if m:
                            thermostat_ids[name] = m.group(1)
                            if self.debug:
                                print(f"  {name}: thermostat_id={thermostat_ids[name]}")
                except Exception:
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

        # Second pass: navigate to each thermostat's vacations page to detect status
        for t in thermostats:
            name = t['name']
            t_id = thermostat_ids.get(name)
            if not t_id:
                continue
            try:
                vac_url = f"{PORTAL_URL}#/devices/thermostats/{t_id}/vacations"
                if self.debug:
                    print(f"  {name}: checking vacations at {vac_url}")
                self.driver.get(vac_url)
                time.sleep(3)
                # If the 'no vacations' message exists, vacation_mode=False; else True
                try:
                    self.driver.find_element(By.XPATH, "//*[contains(text(), 'There are no scheduled vacations')]")
                    t['vacation_mode'] = False
                    if self.debug:
                        print(f"  {name}: vacation_mode=False")
                except NoSuchElementException:
                    t['vacation_mode'] = True
                    if self.debug:
                        print(f"  {name}: vacation_mode=True")
            except Exception as e:
                if self.debug:
                    print(f"  {name}: error checking vacation: {e}")
                # Leave existing value or default to False
                t['vacation_mode'] = t.get('vacation_mode', False)

        # Optional filtering by house name if we couldn't switch houses reliably
        if house_name and house_name in HOUSE_THERMOSTATS:
            expected = set(HOUSE_THERMOSTATS[house_name])
            filtered = [t for t in thermostats if t['name'] in expected]
            if self.debug:
                print(f"Filtered thermostats for {house_name}: {[t['name'] for t in filtered]}")
            return filtered if filtered else thermostats

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

    def enable_vacation_mode(self, start_date=None, end_date=None, heat=55, cool=85, house_name=None):
        """
        Create a new vacation via the thermostat vacations page.

        If dates are not provided, starts now and ends one month later.

        Args:
            start_date: datetime for start (None = now)
            end_date: datetime for end (None = start + 30 days)
            heat: Heat setpoint during vacation (default: 55)
            cool: Cool setpoint during vacation (default: 85)
            house_name: Optional house to target; if provided, attempt to switch first

        Returns:
            bool: True if creation appears successful

        Raises:
            RuntimeError: If vacation modal cannot be opened or submission fails obviously
        """
        from datetime import datetime, timedelta
        self._ensure_logged_in()

        # Default dates
        start_dt = start_date or datetime.now()
        end_dt = end_date or (start_dt + timedelta(days=30))

        if self.debug:
            print(f"Opening vacations page to create new vacation...")
            print(f"  Start: {start_dt.isoformat(timespec='minutes')}  End: {end_dt.isoformat(timespec='minutes')}")
            print(f"  Heat: {heat}  Cool: {cool}")

        # Navigate to devices page and (optionally) switch house
        self.driver.get(f"{PORTAL_URL}#/devices")
        time.sleep(8)
        if house_name:
            try:
                self._select_house(house_name)
            except Exception:
                pass

        # Choose a thermostat and open its vacations page
        tiles = self.driver.find_elements(By.CSS_SELECTOR, '[data-qa-class="thermostat-tile"]')
        if not tiles:
            raise RuntimeError("No thermostats found")

        # Prefer the first BB Hill thermostat if mapping is available
        target_url = None
        try:
            for tile in tiles:
                name_elem = tile.find_element(By.CSS_SELECTOR, '[data-qa-class="interactive-tile_title"]')
                name = name_elem.text.strip()
                data_qa_id = tile.get_attribute('data-qa-id')
                import re
                m = re.search(r'(\d+)', data_qa_id or '')
                if m:
                    t_id = m.group(1)
                    target_url = f"{PORTAL_URL}#/devices/thermostats/{t_id}/vacations"
                    # If house_name is set, prefer thermostats from that house via known mapping
                    if house_name and house_name in HOUSE_THERMOSTATS:
                        if name in HOUSE_THERMOSTATS[house_name]:
                            break
            if not target_url:
                # Fallback: click VACATION tile from detail view
                try:
                    tiles[0].click()
                except:
                    self.driver.execute_script("arguments[0].click();", tiles[0])
                time.sleep(5)
                vacation_tile = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, '//div[contains(text(), "VACATION")]'))
                )
                vacation_tile.click()
                time.sleep(5)
            else:
                self.driver.get(target_url)
                time.sleep(5)
        except TimeoutException:
            raise RuntimeError("Could not open vacations page")

        # Click "New Vacation"
        try:
            new_btn = WebDriverWait(self.driver, 8).until(
                EC.element_to_be_clickable((By.XPATH, '//button[contains(text(), "New Vacation")]'))
            )
            try:
                new_btn.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", new_btn)
            time.sleep(2)
        except TimeoutException:
            raise RuntimeError("New Vacation button not found")

        # Fill the form: attempt multiple selector patterns for dates and setpoints
        # Date inputs
        date_selectors = [
            (By.CSS_SELECTOR, 'input[name*="start" i]'),
            (By.CSS_SELECTOR, 'input[aria-label*="Start" i]'),
            (By.XPATH, "//input[contains(@placeholder,'Start') or contains(@aria-label,'Start') or contains(@name,'start')]")
        ]
        end_selectors = [
            (By.CSS_SELECTOR, 'input[name*="end" i]'),
            (By.CSS_SELECTOR, 'input[aria-label*="End" i]'),
            (By.XPATH, "//input[contains(@placeholder,'End') or contains(@aria-label,'End') or contains(@name,'end')]")
        ]

        def _fill_input(selectors, value_str):
            for by, sel in selectors:
                try:
                    elem = WebDriverWait(self.driver, 3).until(
                        EC.presence_of_element_located((by, sel))
                    )
                    elem.clear()
                    elem.send_keys(value_str)
                    time.sleep(0.5)
                    return True
                except TimeoutException:
                    continue
                except Exception:
                    continue
            return False

        # Compose date strings; many date pickers accept YYYY-MM-DD
        start_str = start_dt.strftime('%Y-%m-%d')
        end_str = end_dt.strftime('%Y-%m-%d')
        _fill_input(date_selectors, start_str)
        _fill_input(end_selectors, end_str)

        # Heat/Cool inputs
        heat_selectors = [
            (By.XPATH, "//input[contains(@name,'heat') or contains(@aria-label,'Heat') or contains(@placeholder,'Heat')]")
        ]
        cool_selectors = [
            (By.XPATH, "//input[contains(@name,'cool') or contains(@aria-label,'Cool') or contains(@placeholder,'Cool')]")
        ]
        _fill_input(heat_selectors, str(int(heat)))
        _fill_input(cool_selectors, str(int(cool)))

        # Submit: try common buttons
        submit_selectors = [
            (By.XPATH, "//button[contains(text(),'Save') or contains(text(),'Create') or contains(text(),'Done') or contains(text(),'Add')]")
        ]
        submitted = False
        for by, sel in submit_selectors:
            try:
                btn = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((by, sel))
                )
                try:
                    btn.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", btn)
                submitted = True
                time.sleep(3)
                break
            except TimeoutException:
                continue

        if not submitted:
            raise RuntimeError("Could not submit vacation form")

        # Basic verification: no 'no vacations' message present now, or a new item appears
        page_text = self.driver.page_source
        created = 'There are no scheduled vacations' not in page_text
        if self.debug:
            print(f"  Creation status: {created}")
        return created

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

    def delete_vacations_for_house(self, house_name, first_only=False):
        """Delete vacation(s) for each thermostat in the specified house.

        Returns the total number of vacations deleted across thermostats.
        Assumes at most one vacation per thermostat.
        """
        self._ensure_logged_in()

        total_deleted = 0
        for item in self._get_house_thermostat_ids(house_name):
            t_id = item['id']
            name = item['name']
            try:
                vac_url = f"{PORTAL_URL}#/devices/thermostats/{t_id}/vacations"
                if self.debug:
                    print(f"Deleting vacations for {name} at {vac_url}")
                self.driver.get(vac_url)
                time.sleep(5)

                # If no vacations, continue
                if 'no scheduled vacations' in self.driver.page_source.lower():
                    if self.debug:
                        print(f"  No vacations for {name}")
                    continue

                # Open first/only vacation item
                items = self.driver.find_elements(By.CSS_SELECTOR, '.vacation-menu-item .menu-list__item')
                if not items:
                    items = self.driver.find_elements(By.XPATH, '//div[contains(@class, "menu-list__item")]')
                if not items:
                    if self.debug:
                        print(f"  Could not find vacation items for {name}")
                    continue
                target = items[0]
                try:
                    target.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", target)
                time.sleep(2)

                # Find a delete button and confirm
                delete_button = None
                for sel in [
                    '//button[contains(text(), "Delete")]',
                    '//button[contains(text(), "Remove")]',
                    '//button[contains(@aria-label, "Delete")]',
                    '//button[contains(@aria-label, "delete")]',
                    '.delete-button',
                    '[data-qa-class*="delete"]']:
                    try:
                        delete_button = self.driver.find_element(By.XPATH, sel) if sel.startswith('//') else self.driver.find_element(By.CSS_SELECTOR, sel)
                        break
                    except NoSuchElementException:
                        continue
                if not delete_button:
                    if self.debug:
                        print(f"  Delete button not found for {name}")
                    continue
                try:
                    delete_button.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", delete_button)
                time.sleep(2)

                # Confirm
                try:
                    confirm = WebDriverWait(self.driver, 3).until(
                        EC.presence_of_element_located((By.XPATH, '//button[contains(text(), "Yes") or contains(text(), "Confirm") or contains(text(), "Delete")]'))
                    )
                    try:
                        confirm.click()
                    except Exception:
                        self.driver.execute_script("arguments[0].click();", confirm)
                    time.sleep(2)
                except TimeoutException:
                    pass

                total_deleted += 1
                if self.debug:
                    print(f"  ✓ Deleted vacation for {name}")

            except Exception as e:
                if self.debug:
                    print(f"  ✗ Error deleting for {name}: {e}")
                continue

        return total_deleted

    def create_vacations_for_house(self, house_name, start_date=None, end_date=None, heat=55, cool=85):
        """Create a one-month (default) vacation for each thermostat without an active vacation."""
        from datetime import datetime, timedelta
        self._ensure_logged_in()
        start_dt = start_date or datetime.now()
        end_dt = end_date or (start_dt + timedelta(days=30))

        created_count = 0
        for item in self._get_house_thermostat_ids(house_name):
            t_id = item['id']
            name = item['name']
            try:
                vac_url = f"{PORTAL_URL}#/devices/thermostats/{t_id}/vacations"
                if self.debug:
                    print(f"Creating vacation for {name} at {vac_url}")
                self.driver.get(vac_url)
                time.sleep(5)

                # Skip if a vacation already exists
                if 'no scheduled vacations' not in self.driver.page_source.lower():
                    if self.debug:
                        print(f"  Existing vacation detected for {name}; skipping")
                    continue

                # New Vacation
                try:
                    new_btn = WebDriverWait(self.driver, 6).until(
                        EC.element_to_be_clickable((By.XPATH, '//button[contains(text(), "New Vacation")]'))
                    )
                    try:
                        new_btn.click()
                    except Exception:
                        self.driver.execute_script("arguments[0].click();", new_btn)
                    time.sleep(2)
                except TimeoutException:
                    if self.debug:
                        print(f"  New Vacation button not found for {name}")
                    continue

                # Fill dates and setpoints (best-effort)
                def _fill(by, sel, value):
                    try:
                        elem = WebDriverWait(self.driver, 3).until(EC.presence_of_element_located((by, sel)))
                        elem.clear()
                        elem.send_keys(value)
                        time.sleep(0.5)
                        return True
                    except Exception:
                        return False

                start_str = start_dt.strftime('%Y-%m-%d')
                end_str = end_dt.strftime('%Y-%m-%d')
                _fill(By.CSS_SELECTOR, 'input[name*="start" i]', start_str) or \
                    _fill(By.XPATH, "//input[contains(@placeholder,'Start') or contains(@aria-label,'Start') or contains(@name,'start')]", start_str)
                _fill(By.CSS_SELECTOR, 'input[name*="end" i]', end_str) or \
                    _fill(By.XPATH, "//input[contains(@placeholder,'End') or contains(@aria-label,'End') or contains(@name,'end')]", end_str)

                _fill(By.XPATH, "//input[contains(@name,'heat') or contains(@aria-label,'Heat') or contains(@placeholder,'Heat')]", str(int(heat)))
                _fill(By.XPATH, "//input[contains(@name,'cool') or contains(@aria-label,'Cool') or contains(@placeholder,'Cool')]", str(int(cool)))

                # Submit
                submitted = False
                for by, sel in [(By.XPATH, "//button[contains(text(),'Save') or contains(text(),'Create') or contains(text(),'Done') or contains(text(),'Add')]")]:
                    try:
                        btn = WebDriverWait(self.driver, 5).until(EC.element_to_be_clickable((by, sel)))
                        try:
                            btn.click()
                        except Exception:
                            self.driver.execute_script("arguments[0].click();", btn)
                        submitted = True
                        time.sleep(3)
                        break
                    except TimeoutException:
                        continue

                if not submitted:
                    if self.debug:
                        print(f"  Could not submit form for {name}")
                    continue

                # Verify success
                if 'no scheduled vacations' in self.driver.page_source.lower():
                    if self.debug:
                        print(f"  Creation may have failed for {name}")
                    continue

                created_count += 1
                if self.debug:
                    print(f"  ✓ Created vacation for {name}")

            except Exception as e:
                if self.debug:
                    print(f"  ✗ Error creating for {name}: {e}")
                continue

        return created_count

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
