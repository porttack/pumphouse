"""
GPIO helper functions for sensor access with thread safety
"""
import sys
import threading
import time

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    GPIO_AVAILABLE = False
    print("Warning: RPi.GPIO not available, GPIO functions will be simulated", file=sys.stderr)

from monitor.config import PRESSURE_PIN, FLOAT_PIN

# Global lock for thread-safe GPIO access
_gpio_lock = threading.Lock()
_gpio_initialized = False
_last_pressure_state = None  # Track last known good state

def init_gpio():
    """
    One-time GPIO initialization. Call this once at program startup.
    """
    global _gpio_initialized
    
    if not GPIO_AVAILABLE:
        return False
    
    if _gpio_initialized:
        return True
    
    try:
        GPIO.setmode(GPIO.BCM)
        # Set up both pins with pull-up resistors
        GPIO.setup(PRESSURE_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(FLOAT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        _gpio_initialized = True
        print("GPIO initialized successfully")
        return True
    except Exception as e:
        print(f"Error initializing GPIO: {e}", file=sys.stderr)
        return False

def read_pressure():
    """
    Read pressure sensor with retry logic when state changes.
    Returns 1 (HIGH) or 0 (LOW), or None if failed.
    
    If reading changes from previous state, retry 2 more times with 1 second pauses
    to confirm it's not a glitch.
    
    HIGH (1) = Pressure >= 10 PSI (NC switch open)
    LOW (0) = Pressure < 10 PSI (NC switch closed)
    """
    global _last_pressure_state
    
    if not GPIO_AVAILABLE or not _gpio_initialized:
        return None
    
    with _gpio_lock:
        try:
            state = GPIO.input(PRESSURE_PIN)
            
            # If state changed from last known state, verify with retries
            if _last_pressure_state is not None and state != _last_pressure_state:
                # Try 2 more times with 1 second pauses
                retry_states = [state]
                for retry in range(2):
                    time.sleep(1)
                    retry_state = GPIO.input(PRESSURE_PIN)
                    retry_states.append(retry_state)
                
                # Check if all 3 readings agree
                if all(s == state for s in retry_states):
                    # All agree - this is a real state change
                    _last_pressure_state = state
                    return state
                else:
                    # Readings don't agree - probably a glitch, return last known state
                    return _last_pressure_state
            else:
                # No state change, or first reading
                if _last_pressure_state is None:
                    _last_pressure_state = state
                return state
            
        except Exception as e:
            print(f"Error reading pressure: {e}", file=sys.stderr)
            return None

def read_float_sensor():
    """
    Read float sensor with thread-safe access.
    Returns 'OPEN/FULL', 'CLOSED/CALLING', or 'UNKNOWN'.
    
    HARDWARE: Float switch is normally CLOSED when calling for water
    HIGH (1) = Float switch CLOSED = Tank NOT full (calling for water)
    LOW (0) = Float switch OPEN = Tank is FULL
    """
    if not GPIO_AVAILABLE or not _gpio_initialized:
        return 'UNKNOWN'
    
    with _gpio_lock:
        try:
            state = GPIO.input(FLOAT_PIN)
            # FIXED: HIGH means CALLING, LOW means FULL
            return 'CLOSED/CALLING' if state else 'OPEN/FULL'
        except Exception as e:
            print(f"Error reading float sensor: {e}", file=sys.stderr)
            return 'UNKNOWN'

def cleanup_gpio():
    """Clean up GPIO on shutdown"""
    global _gpio_initialized, _last_pressure_state
    
    if GPIO_AVAILABLE and _gpio_initialized:
        try:
            GPIO.cleanup()
            _gpio_initialized = False
            _last_pressure_state = None
        except Exception:
            pass

# Legacy compatibility functions
def setup_gpio():
    """Legacy function - initialization happens once at startup"""
    return _gpio_initialized
