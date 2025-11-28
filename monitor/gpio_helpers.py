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
        print("DEBUG: init_gpio - SUCCESS")
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
        print(f"DEBUG: read_pressure - GPIO_AVAILABLE={GPIO_AVAILABLE}, _gpio_initialized={_gpio_initialized}", file=sys.stderr)
        return None
    
    with _gpio_lock:
        try:
            state = GPIO.input(PRESSURE_PIN)
            
            # If state changed from last known state, verify with retries
            if _last_pressure_state is not None and state != _last_pressure_state:
                print(f"DEBUG: read_pressure - STATE CHANGE detected: {_last_pressure_state} -> {state}, verifying...", file=sys.stderr)
                
                # Try 2 more times with 1 second pauses
                retry_states = [state]
                for retry in range(2):
                    time.sleep(1)
                    retry_state = GPIO.input(PRESSURE_PIN)
                    retry_states.append(retry_state)
                    print(f"DEBUG: read_pressure - retry {retry+1}/2: {retry_state}", file=sys.stderr)
                
                # Check if all 3 readings agree
                if all(s == state for s in retry_states):
                    # All agree - this is a real state change
                    print(f"DEBUG: read_pressure - CONFIRMED state change: {_last_pressure_state} -> {state}", file=sys.stderr)
                    _last_pressure_state = state
                    return state
                else:
                    # Readings don't agree - probably a glitch, return last known state
                    print(f"DEBUG: read_pressure - GLITCH detected, readings: {retry_states}, keeping state={_last_pressure_state}", file=sys.stderr)
                    return _last_pressure_state
            else:
                # No state change, or first reading - just return the state
                if _last_pressure_state is None:
                    print(f"DEBUG: read_pressure - Initial read: {state}", file=sys.stderr)
                    _last_pressure_state = state
                else:
                    print(f"DEBUG: read_pressure - pin={PRESSURE_PIN}, state={state} (unchanged)", file=sys.stderr)
                return state
            
        except Exception as e:
            print(f"Error reading pressure: {e}", file=sys.stderr)
            return None

def read_float_sensor():
    """
    Read float sensor with thread-safe access.
    Returns 'OPEN/FULL', 'CLOSED/CALLING', or 'UNKNOWN'.
    
    HIGH (1) = Float switch OPEN = Tank is FULL
    LOW (0) = Float switch CLOSED = Tank NOT full (calling for water)
    """
    if not GPIO_AVAILABLE or not _gpio_initialized:
        print(f"DEBUG: read_float_sensor - GPIO_AVAILABLE={GPIO_AVAILABLE}, _gpio_initialized={_gpio_initialized}", file=sys.stderr)
        return 'UNKNOWN'
    
    with _gpio_lock:
        try:
            state = GPIO.input(FLOAT_PIN)
            result = 'OPEN/FULL' if state else 'CLOSED/CALLING'
            print(f"DEBUG: read_float_sensor - pin={FLOAT_PIN}, raw_state={state}, result={result}", file=sys.stderr)
            return result
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
            print("DEBUG: GPIO cleaned up")
        except Exception:
            pass

# Legacy compatibility functions
def setup_gpio():
    """Legacy function - initialization happens once at startup"""
    return _gpio_initialized