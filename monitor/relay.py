"""
Relay control for valves and pumps using RPi.GPIO
"""
import time
try:
    import RPi.GPIO as GPIO
    RELAY_AVAILABLE = True
except (ImportError, RuntimeError):
    RELAY_AVAILABLE = False
    print("Warning: RPi.GPIO not available for relay control")

# Relay pin assignments (BCM numbering)
BYPASS_VALVE_PIN = 26      # Channel 1 - Emergency bypass
SUPPLY_VALVE_PIN = 19      # Channel 2 - Primary inlet with float override
SPIN_PURGE_VALVE_PIN = 13  # Channel 3 - Spindown filter purge
RESERVED_PIN = 6           # Channel 4 - Reserved for future use

# Relay configuration
# Relays are ACTIVE LOW - writing LOW (0) activates the relay
RELAY_ON = GPIO.LOW
RELAY_OFF = GPIO.HIGH

# Purge duration (seconds)
DEFAULT_PURGE_DURATION = 10

_relays_initialized = False

def init_relays():
    """
    Initialize relay pins. Must be called before using relay functions.
    GPIO.setmode should already be called by gpio_helpers.init_gpio()
    """
    global _relays_initialized
    
    if not RELAY_AVAILABLE:
        return False
    
    if _relays_initialized:
        return True
    
    try:
        # Check if GPIO is already set up
        if GPIO.getmode() is None:
            GPIO.setmode(GPIO.BCM)
        
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

def purge_spindown_filter(duration=DEFAULT_PURGE_DURATION, debug=False):
    """
    Activate spindown filter purge valve for specified duration.
    
    Args:
        duration: How long to keep purge valve open (seconds)
        debug: Print debug messages
    
    Returns:
        True if successful, False otherwise
    """
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

def open_bypass_valve(debug=False):
    """Open bypass valve (emergency bypass - water flows without filtration)"""
    if not RELAY_AVAILABLE or not _relays_initialized:
        return False
    
    try:
        GPIO.output(BYPASS_VALVE_PIN, RELAY_ON)
        if debug:
            print("Bypass valve OPENED")
        return True
    except Exception as e:
        print(f"Error opening bypass valve: {e}")
        return False

def close_bypass_valve(debug=False):
    """Close bypass valve"""
    if not RELAY_AVAILABLE or not _relays_initialized:
        return False
    
    try:
        GPIO.output(BYPASS_VALVE_PIN, RELAY_OFF)
        if debug:
            print("Bypass valve CLOSED")
        return True
    except Exception as e:
        print(f"Error closing bypass valve: {e}")
        return False

def override_supply_valve_open(debug=False):
    """
    Override supply valve to OPEN (force water flow even if float is full).
    Note: Float switch will still close valve when tank reaches ~95%.
    """
    if not RELAY_AVAILABLE or not _relays_initialized:
        return False
    
    try:
        GPIO.output(SUPPLY_VALVE_PIN, RELAY_ON)
        if debug:
            print("Supply valve override - FORCED OPEN")
        return True
    except Exception as e:
        print(f"Error opening supply valve: {e}")
        return False

def release_supply_valve_override(debug=False):
    """
    Release supply valve override (return control to float switch).
    """
    if not RELAY_AVAILABLE or not _relays_initialized:
        return False
    
    try:
        GPIO.output(SUPPLY_VALVE_PIN, RELAY_OFF)
        if debug:
            print("Supply valve override RELEASED - float switch controls")
        return True
    except Exception as e:
        print(f"Error releasing supply valve: {e}")
        return False

def get_relay_status():
    """
    Get current status of all relays.
    Returns dict with valve states.
    """
    if not RELAY_AVAILABLE or not _relays_initialized:
        return {
            'bypass': 'UNKNOWN',
            'supply': 'UNKNOWN',
            'spin_purge': 'UNKNOWN',
            'reserved': 'UNKNOWN'
        }
    
    try:
        return {
            'bypass': 'OPEN' if GPIO.input(BYPASS_VALVE_PIN) == RELAY_ON else 'CLOSED',
            'supply': 'OVERRIDE' if GPIO.input(SUPPLY_VALVE_PIN) == RELAY_ON else 'FLOAT_CONTROL',
            'spin_purge': 'OPEN' if GPIO.input(SPIN_PURGE_VALVE_PIN) == RELAY_ON else 'CLOSED',
            'reserved': 'ON' if GPIO.input(RESERVED_PIN) == RELAY_ON else 'OFF'
        }
    except Exception as e:
        print(f"Error reading relay status: {e}")
        return None