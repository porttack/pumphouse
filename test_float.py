#!/usr/bin/env python3
"""
Test script for float sensor on GPIO 21
The float sensor is pulled high by the internal pull-up resistor.
When the float activates, it grounds the pin (reads LOW).
"""

import RPi.GPIO as GPIO
import time
import sys

# Configuration
FLOAT_PIN = 21  # BCM pin 21 for float sensor

def setup_gpio():
    """Initialize GPIO with BCM numbering and pull-up resistor"""
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(FLOAT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    print(f"GPIO {FLOAT_PIN} configured as input with pull-up resistor enabled")

def test_float_continuous():
    """Continuously monitor the float sensor state"""
    print("\n=== Float Sensor Test ===")
    print("GPIO 21 with internal pull-up resistor")
    print("Expected behavior:")
    print("  - HIGH (1) = Float switch OPEN/FULL")
    print("  - LOW  (0) = Float switch CLOSED/CALLING (tank NOT full OR override enabled)")
    print("\nMonitoring float sensor... Press Ctrl+C to exit\n")
    
    last_state = None
    
    try:
        while True:
            # Read the current state
            current_state = GPIO.input(FLOAT_PIN)
            
            # Only print when state changes
            if current_state != last_state:
                timestamp = time.strftime("%H:%M:%S")
                if current_state == GPIO.HIGH:
                    print(f"[{timestamp}] Float: OPEN (HIGH) - Tank IS FULL")
                else:
                    print(f"[{timestamp}] Float: CLOSED (LOW) - Tank NOT full (or override)")
                
                last_state = current_state
            
            time.sleep(0.1)  # Check every 100ms
            
    except KeyboardInterrupt:
        print("\n\nTest stopped by user")
    finally:
        GPIO.cleanup()
        print("GPIO cleaned up")

def test_float_single():
    """Read float sensor state once and display"""
    state = GPIO.input(FLOAT_PIN)
    timestamp = time.strftime("%H:%M:%S")
    
    print(f"\n[{timestamp}] Float sensor state:")
    if state == GPIO.HIGH:
        print("  Status: OPEN (HIGH)")
        print("  Meaning: Tank IS FULL - Float switch opened")
    else:
        print("  Status: CLOSED (LOW)")
        print("  Meaning: Tank NOT full - Float switch closed (or override enabled)")
    
    print(f"  Raw value: {state}")

def main():
    """Main test function"""
    print("Float Sensor Test Script")
    print("=" * 50)
    
    # Setup GPIO
    setup_gpio()
    
    # Check if user wants continuous or single read
    if len(sys.argv) > 1 and sys.argv[1] == '--once':
        test_float_single()
        GPIO.cleanup()
    else:
        test_float_continuous()

if __name__ == "__main__":
    main()
