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
RELAY_ON = GPIO.LOW if RELAY_AVAILABLE else 0
RELAY_OFF = GPIO.HIGH if RELAY_AVAILABLE else 1

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
        # Suppress warnings about pins already in use
        GPIO.setwarnings(False)
        
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

def get_relay_status():
    """
    Get current status of all relays.
    Returns dict with valve states.
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
