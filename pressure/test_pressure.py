#!/usr/bin/env python3
import RPi.GPIO as GPIO
import time
from datetime import datetime

PRESSURE_PIN = 17

# Setup
GPIO.setmode(GPIO.BCM)
GPIO.setup(PRESSURE_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

print("=== Pressure Sensor Logging ===")
print("Logging every 5 seconds + immediately on changes")
print("(Terminal beep on state changes)\n")

def get_status_text(state):
    return "≥10 PSI (NC OPEN)" if state else "<10 PSI (NC CLOSED)"

def log_status(state, change=False):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    status = get_status_text(state)
    prefix = ">>> CHANGE: " if change else ""
    
    print(f"{timestamp} - {prefix}{status}")
    
    if change:
        print('\a')  # Terminal beep (BEL character)

# Initial state
last_state = GPIO.input(PRESSURE_PIN)
log_status(last_state)

last_log_time = time.time()

try:
    while True:
        current_state = GPIO.input(PRESSURE_PIN)
        current_time = time.time()
        
        # Check for state change
        if current_state != last_state:
            log_status(current_state, change=True)
            last_state = current_state
            last_log_time = current_time  # Reset timer after change
        
        # Check if 5 seconds have passed
        elif current_time - last_log_time >= 5.0:
            log_status(current_state)
            last_log_time = current_time
        
        time.sleep(0.1)  # Check every 100ms for responsiveness
        
except KeyboardInterrupt:
    print("\n\n✓ Logging stopped")

GPIO.cleanup()
