"""
GPIO helper functions for sensor access with thread safety
"""
import sys
import threading
import time
import subprocess

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    GPIO_AVAILABLE = False
    print("Warning: RPi.GPIO not available, GPIO functions will be simulated", file=sys.stderr)

from monitor.config import PRESSURE_PIN, FLOAT_PIN

# Float sensor state constants
# When GPIO reads HIGH (1): Float switch is OPEN, meaning tank is FULL
# When GPIO reads LOW (0): Float switch is CLOSED, meaning tank is CALLING for water
FLOAT_STATE_FULL = 'FULL'        # Tank is full (float switch OPEN/HIGH)
FLOAT_STATE_CALLING = 'CALLING'  # Tank needs water (float switch CLOSED/LOW)
FLOAT_STATE_UNKNOWN = 'UNKNOWN'  # Cannot read sensor

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

def _read_pin_via_gpio_command(pin):
    """Read pin value using gpio command-line tool (works with multiple processes)"""
    try:
        result = subprocess.run(['gpio', '-g', 'read', str(pin)],
                              capture_output=True, text=True, check=False, timeout=1)
        if result.returncode != 0:
            return None
        value = int(result.stdout.strip())
        return value
    except Exception as e:
        return None

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

    if not GPIO_AVAILABLE:
        return None

    # Try RPi.GPIO first
    if _gpio_initialized:
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
                # Fall through to gpio command

    # Fallback to gpio command (no retry logic for now)
    return _read_pin_via_gpio_command(PRESSURE_PIN)

def read_float_sensor():
    """
    Read float sensor with thread-safe access.
    Returns FLOAT_STATE_FULL, FLOAT_STATE_CALLING, or FLOAT_STATE_UNKNOWN.

    HIGH (1) = Float switch OPEN = Tank is FULL
    LOW (0) = Float switch CLOSED = Tank NOT full (calling for water)
    """
    if not GPIO_AVAILABLE:
        return FLOAT_STATE_UNKNOWN

    # Try RPi.GPIO first
    if _gpio_initialized:
        with _gpio_lock:
            try:
                state = GPIO.input(FLOAT_PIN)
                # HIGH = FULL, LOW = CALLING
                return FLOAT_STATE_FULL if state else FLOAT_STATE_CALLING
            except Exception as e:
                print(f"Error reading float sensor: {e}", file=sys.stderr)
                # Fall through to gpio command

    # Fallback to gpio command
    state = _read_pin_via_gpio_command(FLOAT_PIN)
    if state is None:
        return FLOAT_STATE_UNKNOWN
    return FLOAT_STATE_FULL if state else FLOAT_STATE_CALLING

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
