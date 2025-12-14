"""
Relay control for valves and pumps using RPi.GPIO
"""
import time
import subprocess
try:
    import RPi.GPIO as GPIO
    RELAY_AVAILABLE = True
except (ImportError, RuntimeError):
    RELAY_AVAILABLE = False
    print("Warning: RPi.GPIO not available for relay control")

from monitor.config import PURGE_DURATION

# Relay pin assignments (BCM numbering)
BYPASS_VALVE_PIN = 26      # Channel 1 - Emergency bypass
SUPPLY_VALVE_PIN = 19      # Channel 2 - Primary inlet with float override
SPIN_PURGE_VALVE_PIN = 13  # Channel 3 - Spindown filter purge
RESERVED_PIN = 6           # Channel 4 - Reserved for future use

# Relay configuration
# Relays are ACTIVE LOW - writing LOW (0) activates the relay
RELAY_ON = GPIO.LOW if RELAY_AVAILABLE else 0
RELAY_OFF = GPIO.HIGH if RELAY_AVAILABLE else 1

_relays_initialized = False

def init_relays(preserve_state=False):
    """
    Initialize relay pins. Must be called before using relay functions.
    GPIO.setmode should already be called by gpio_helpers.init_gpio()

    Args:
        preserve_state: If True, read current pin states before setup to preserve them.
                       If False, initialize all relays to OFF state (default behavior).
    """
    global _relays_initialized

    if not RELAY_AVAILABLE:
        return False

    if _relays_initialized:
        return True

    try:
        # Suppress warnings about pins already in use
        GPIO.setwarnings(False)

        # Check if GPIO is already set up
        if GPIO.getmode() is None:
            GPIO.setmode(GPIO.BCM)

        if preserve_state:
            # Try to read current pin states before reconfiguring
            # Configure pins as inputs with pull-down resistors to read actual hardware state
            try:
                # Set as inputs with pull-down to read the actual hardware state
                # Pull-down ensures we read 0 if the relay hardware is driving the pin low
                GPIO.setup(BYPASS_VALVE_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
                GPIO.setup(SUPPLY_VALVE_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
                GPIO.setup(SPIN_PURGE_VALVE_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
                GPIO.setup(RESERVED_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

                # Small delay to let pins settle
                import time
                time.sleep(0.01)

                # Read current electrical states
                bypass_state = GPIO.input(BYPASS_VALVE_PIN)
                supply_state = GPIO.input(SUPPLY_VALVE_PIN)
                purge_state = GPIO.input(SPIN_PURGE_VALVE_PIN)
                reserved_state = GPIO.input(RESERVED_PIN)

                # Now set up as outputs preserving the states we just read
                GPIO.setup(BYPASS_VALVE_PIN, GPIO.OUT, initial=bypass_state)
                GPIO.setup(SUPPLY_VALVE_PIN, GPIO.OUT, initial=supply_state)
                GPIO.setup(SPIN_PURGE_VALVE_PIN, GPIO.OUT, initial=purge_state)
                GPIO.setup(RESERVED_PIN, GPIO.OUT, initial=reserved_state)

                _relays_initialized = True
                print("Relay control initialized - states preserved")
                return True
            except Exception as e:
                # If reading fails, fall through to default initialization
                print(f"Could not preserve states ({e}), initializing to OFF")
                pass

        # Set up relay pins as outputs with relays OFF (HIGH = off for active-low relays)
        GPIO.setup(BYPASS_VALVE_PIN, GPIO.OUT, initial=RELAY_OFF)
        GPIO.setup(SUPPLY_VALVE_PIN, GPIO.OUT, initial=RELAY_OFF)
        GPIO.setup(SPIN_PURGE_VALVE_PIN, GPIO.OUT, initial=RELAY_OFF)
        GPIO.setup(RESERVED_PIN, GPIO.OUT, initial=RELAY_OFF)

        _relays_initialized = True
        print("Relay control initialized - all valves OFF")
        return True

    except Exception as e:
        print(f"Error initializing relays: {e}")
        return False

def cleanup_relays():
    """Ensure all relays are OFF on shutdown"""
    global _relays_initialized
    
    if RELAY_AVAILABLE and _relays_initialized:
        try:
            GPIO.output(BYPASS_VALVE_PIN, RELAY_OFF)
            GPIO.output(SUPPLY_VALVE_PIN, RELAY_OFF)
            GPIO.output(SPIN_PURGE_VALVE_PIN, RELAY_OFF)
            GPIO.output(RESERVED_PIN, RELAY_OFF)
            print("All relays turned OFF")
        except Exception as e:
            print(f"Error during relay cleanup: {e}")
        finally:
            _relays_initialized = False

def purge_spindown_filter(duration=None, debug=False):
    """
    Activate spindown filter purge valve for specified duration.

    Args:
        duration: How long to keep purge valve open (seconds), defaults to config PURGE_DURATION
        debug: Print debug messages

    Returns:
        True if successful, False otherwise
    """
    if duration is None:
        duration = PURGE_DURATION

    if not RELAY_AVAILABLE or not _relays_initialized:
        if debug:
            print("Relay control not available - cannot purge filter")
        return False

    try:
        if debug:
            print(f"Opening spindown purge valve for {duration} seconds...")

        # Open purge valve (ACTIVE LOW - write LOW to turn on)
        GPIO.output(SPIN_PURGE_VALVE_PIN, RELAY_ON)

        # Wait for purge duration
        time.sleep(duration)
        
        # Close purge valve (write HIGH to turn off)
        GPIO.output(SPIN_PURGE_VALVE_PIN, RELAY_OFF)
        
        if debug:
            print(f"Spindown purge complete")
        
        return True
        
    except Exception as e:
        print(f"Error during spindown purge: {e}")
        # Ensure valve is closed on error
        try:
            GPIO.output(SPIN_PURGE_VALVE_PIN, RELAY_OFF)
        except:
            pass
        return False

def get_relay_status():
    """
    Get current status of all relays.
    Returns dict with valve states (for compatibility with existing code).
    """
    if not RELAY_AVAILABLE or not _relays_initialized:
        return {
            'bypass': 'OFF',
            'supply_override': 'OFF'
        }

    try:
        return {
            'bypass': 'ON' if GPIO.input(BYPASS_VALVE_PIN) == RELAY_ON else 'OFF',
            'supply_override': 'ON' if GPIO.input(SUPPLY_VALVE_PIN) == RELAY_ON else 'OFF'
        }
    except Exception as e:
        print(f"Error reading relay status: {e}")
        return {
            'bypass': 'UNKNOWN',
            'supply_override': 'UNKNOWN'
        }

def _read_pin_via_gpio_command(pin):
    """Read pin value using gpio command-line tool (works with multiple processes)"""
    try:
        result = subprocess.run(['gpio', '-g', 'read', str(pin)],
                              capture_output=True, text=True, check=True, timeout=1)
        value = int(result.stdout.strip())
        # Relays are ACTIVE LOW - 0 means ON, 1 means OFF
        return 'ON' if value == 0 else 'OFF'
    except:
        return 'N/A'

def get_all_relay_status():
    """
    Get current status of all relays including purge valve.
    Returns dict with all relay/valve states.

    This function first tries to read via RPi.GPIO, but if that fails (e.g., another
    process is using GPIO), it falls back to using the `gpio` command-line tool.
    """
    if not RELAY_AVAILABLE:
        return {
            'bypass': 'N/A',
            'supply_override': 'N/A',
            'purge': 'N/A',
            'reserved': 'N/A'
        }

    # Try RPi.GPIO first
    try:
        # Suppress warnings
        GPIO.setwarnings(False)

        # Try to set mode if not set
        if GPIO.getmode() is None:
            GPIO.setmode(GPIO.BCM)

        def read_pin_if_output(pin):
            """Read pin value if it's configured as output"""
            try:
                func = GPIO.gpio_function(pin)
                # GPIO.OUT = 0, GPIO.IN = 1
                if func == GPIO.OUT:
                    value = GPIO.input(pin)
                    return 'ON' if value == RELAY_ON else 'OFF'
                else:
                    return 'N/A'
            except:
                return 'N/A'

        result = {
            'bypass': read_pin_if_output(BYPASS_VALVE_PIN),
            'supply_override': read_pin_if_output(SUPPLY_VALVE_PIN),
            'purge': read_pin_if_output(SPIN_PURGE_VALVE_PIN),
            'reserved': read_pin_if_output(RESERVED_PIN)
        }

        # If we got at least one valid reading, return it
        if any(v not in ['N/A', 'UNKNOWN'] for v in result.values()):
            return result

    except:
        # RPi.GPIO failed, fall through to gpio command
        pass

    # Fallback to gpio command-line tool (works even when another process is using GPIO)
    try:
        return {
            'bypass': _read_pin_via_gpio_command(BYPASS_VALVE_PIN),
            'supply_override': _read_pin_via_gpio_command(SUPPLY_VALVE_PIN),
            'purge': _read_pin_via_gpio_command(SPIN_PURGE_VALVE_PIN),
            'reserved': _read_pin_via_gpio_command(RESERVED_PIN)
        }
    except Exception as e:
        print(f"Error reading relay status: {e}")
        return {
            'bypass': 'UNKNOWN',
            'supply_override': 'UNKNOWN',
            'purge': 'UNKNOWN',
            'reserved': 'UNKNOWN'
        }

def set_supply_override(state, debug=False):
    """
    Turn supply override valve ON or OFF using gpio command.

    This function uses the gpio command-line tool instead of RPi.GPIO to avoid
    multi-process conflicts when the monitor is running.

    Args:
        state: 'ON' or 'OFF'
        debug: Print debug messages

    Returns:
        True if successful, False otherwise
    """
    import sys

    if state not in ['ON', 'OFF']:
        if debug:
            print(f"Invalid state '{state}', must be 'ON' or 'OFF'", file=sys.stderr)
        return False

    # Active-low relay: 0=ON, 1=OFF
    value = '0' if state == 'ON' else '1'

    try:
        # Use gpio command to avoid multi-process conflicts
        result = subprocess.run(['gpio', '-g', 'write', str(SUPPLY_VALVE_PIN), value],
                              capture_output=True, text=True, check=True, timeout=2)
        if debug:
            print(f"Supply override turned {state}")
        return True
    except subprocess.TimeoutExpired:
        print(f"Timeout setting supply override to {state}", file=sys.stderr)
        return False
    except subprocess.CalledProcessError as e:
        print(f"Error setting supply override to {state}: {e.stderr}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error setting supply override to {state}: {e}", file=sys.stderr)
        return False

def set_bypass(state, debug=False):
    """
    Turn bypass valve ON or OFF using gpio command.

    This function uses the gpio command-line tool instead of RPi.GPIO to avoid
    multi-process conflicts when the monitor is running.

    Args:
        state: 'ON' or 'OFF'
        debug: Print debug messages

    Returns:
        True if successful, False otherwise
    """
    import sys

    if state not in ['ON', 'OFF']:
        if debug:
            print(f"Invalid state '{state}', must be 'ON' or 'OFF'", file=sys.stderr)
        return False

    # Active-low relay: 0=ON, 1=OFF
    value = '0' if state == 'ON' else '1'

    try:
        # Use gpio command to avoid multi-process conflicts
        result = subprocess.run(['gpio', '-g', 'write', str(BYPASS_VALVE_PIN), value],
                              capture_output=True, text=True, check=True, timeout=2)
        if debug:
            print(f"Bypass valve turned {state}")
        return True
    except subprocess.TimeoutExpired:
        print(f"Timeout setting bypass to {state}", file=sys.stderr)
        return False
    except subprocess.CalledProcessError as e:
        print(f"Error setting bypass to {state}: {e.stderr}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error setting bypass to {state}: {e}", file=sys.stderr)
        return False
