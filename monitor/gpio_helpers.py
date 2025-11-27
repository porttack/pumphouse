"""
GPIO helper functions for sensor access with proper cleanup
"""
import sys

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    GPIO_AVAILABLE = False
    print("Warning: RPi.GPIO not available, GPIO functions will be simulated", file=sys.stderr)

from monitor.config import PRESSURE_PIN, FLOAT_PIN

def setup_gpio():
    """
    Setup GPIO for reading. Returns True on success, False on failure.
    """
    if not GPIO_AVAILABLE:
        return False
    
    try:
        GPIO.setmode(GPIO.BCM)
        return True
    except RuntimeError:
        return False
    except Exception as e:
        print(f"Error setting up GPIO: {e}", file=sys.stderr)
        return False

def cleanup_gpio():
    """Release GPIO resources"""
    if not GPIO_AVAILABLE:
        return
    
    try:
        GPIO.cleanup()
    except Exception:
        pass

def read_pressure():
    """
    Read pressure sensor with GPIO management.
    Returns GPIO.HIGH/GPIO.LOW or None if failed.
    """
    if not GPIO_AVAILABLE:
        return None
    
    if not setup_gpio():
        return None
    
    try:
        GPIO.setup(PRESSURE_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        state = GPIO.input(PRESSURE_PIN)
        return state
    except Exception as e:
        print(f"Error reading pressure: {e}", file=sys.stderr)
        return None
    finally:
        cleanup_gpio()

def read_float_sensor():
    """
    Read float sensor with GPIO management.
    Returns 'OPEN/FULL', 'CLOSED/CALLING', or 'UNKNOWN'.
    """
    if not GPIO_AVAILABLE:
        return 'UNKNOWN'
    
    if not setup_gpio():
        return 'UNKNOWN'
    
    try:
        GPIO.setup(FLOAT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        state = GPIO.input(FLOAT_PIN)
        
        # HIGH = Float switch OPEN = Tank is FULL
        # LOW = Float switch CLOSED = Tank NOT full (calling for water)
        if state == GPIO.HIGH:
            return 'OPEN/FULL'
        else:
            return 'CLOSED/CALLING'
    except Exception:
        return 'UNKNOWN'
    finally:
        cleanup_gpio()
