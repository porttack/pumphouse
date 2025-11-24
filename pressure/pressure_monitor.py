#!/usr/bin/env python3
"""
Pressure Monitor - Log and track pressure sensor state changes
Monitors a pressure switch connected to GPIO and logs events
"""
import RPi.GPIO as GPIO
import time
from datetime import datetime
import sys
import argparse
import csv

# GPIO Configuration
PRESSURE_PIN = 17

# Water Volume Estimation Constants
RESIDUAL_PRESSURE_SECONDS = 30  # Last N seconds are residual pressure (not pumping)
SECONDS_PER_GALLON = 350 / 15   # 350 seconds = 15 gallons, so ~23.33 seconds/gallon

def estimate_gallons(duration_seconds):
    """
    Estimate gallons pumped based on pressure duration.
    
    Args:
        duration_seconds: Total time pressure was active
    
    Returns:
        Estimated gallons (float), or 0 if duration too short
    """
    # Subtract residual pressure time
    effective_pumping_time = duration_seconds - RESIDUAL_PRESSURE_SECONDS
    
    # If duration was less than residual time, no water pumped
    if effective_pumping_time <= 0:
        return 0.0
    
    # Calculate gallons
    gallons = effective_pumping_time / SECONDS_PER_GALLON
    
    return gallons

def get_status_text(state):
    return "≥10 PSI (NC OPEN)" if state else "<10 PSI (NC CLOSED)"

def log_status(state, change=False, log_file=None, activation_start=None, activation_time=None):
    """Log status to console and main log file"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    status = get_status_text(state)
    prefix = ">>> CHANGE: " if change else ""
    
    message = f"{timestamp} - {prefix}{status}"
    
    # Print to console
    print(message)
    sys.stdout.flush()
    
    # Write to main log file
    if log_file:
        with open(log_file, 'a') as f:
            f.write(message + '\n')
    
    if change:
        print('\a', end='', flush=True)

def log_pressure_event(start_time, end_time, duration, gallons, change_log_file):
    """Log complete pressure event to CSV (one row per event)"""
    start_timestamp = datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    end_timestamp = datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    
    with open(change_log_file, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([start_timestamp, end_timestamp, f'{duration:.3f}', f'{gallons:.2f}'])
    
    # Also print summary to console
    print(f"  → Event summary: {duration:.1f}s duration, ~{gallons:.1f} gallons")

def main():
    parser = argparse.ArgumentParser(
        prog='pressure_monitor.py',
        description='Monitor pressure sensor and log state changes',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python3 pressure_monitor.py
      Log every 5 seconds to pressure_log.txt
  
  python3 pressure_monitor.py --changes pressure_events.csv
      Also log pressure events with water volume estimates to CSV
  
  python3 pressure_monitor.py --log full.txt --changes events.csv
      Custom log filenames for both logs

Running in Background (survives SSH disconnect):
  
  Method 1 - Using nohup (simplest):
    nohup python3 pressure_monitor.py --changes events.csv > output.txt 2>&1 &
    
    To check if running:
      ps aux | grep pressure_monitor
    
    To view logs:
      tail -f events.csv
      tail -f output.txt
    
    To stop:
      pkill -f pressure_monitor.py
  
  Method 2 - Using screen (interactive):
    screen -S pressure
    python3 pressure_monitor.py --changes events.csv
    <Ctrl+A, then D to detach>
    
    To reattach:
      screen -r pressure
    
    To stop:
      screen -r pressure
      <Ctrl+C>
  
  Method 3 - Using tmux (modern alternative):
    tmux new -s pressure
    python3 pressure_monitor.py --changes events.csv
    <Ctrl+B, then D to detach>
    
    To reattach:
      tmux attach -t pressure
    
    To stop:
      tmux kill-session -t pressure

CSV Format:
  pressure_on_time, pressure_off_time, duration_seconds, estimated_gallons
  2025-01-23 20:30:12.345, 2025-01-23 20:30:20.123, 7.778, 0.00
  2025-01-23 20:45:05.123, 2025-01-23 21:30:15.456, 2710.333, 114.86

Water Volume Estimation:
  - Last {sec}s of pressure assumed to be residual (not pumping)
  - Formula: gallons = (duration - {sec}s) / {spg:.2f} seconds/gallon
  - Adjust RESIDUAL_PRESSURE_SECONDS and SECONDS_PER_GALLON at top of file
  
Hardware Setup:
  - Pressure switch NC contact → GPIO17 (physical pin 11)
  - Pressure switch C contact  → Ground (physical pin 9)
  - NC closes below 10 PSI, opens at/above 10 PSI
  
Monitoring:
  - Logs every 5 seconds in main log
  - Logs complete events (start/end/duration/gallons) to CSV
  - Terminal beeps on state changes
  - Press Ctrl+C to stop gracefully
        '''.format(sec=RESIDUAL_PRESSURE_SECONDS, spg=SECONDS_PER_GALLON))
    
    parser.add_argument('--log', default='pressure_log.txt', 
                       metavar='FILE',
                       help='Main log file with all events (default: pressure_log.txt)')
    parser.add_argument('--changes', default=None,
                       metavar='FILE',
                       help='CSV file for pressure events with water volume (e.g., pressure_events.csv)')
    parser.add_argument('--version', action='version', version='%(prog)s 1.0')
    
    args = parser.parse_args()
    
    # Setup GPIO after argument parsing
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(PRESSURE_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    
    # Initialize change log CSV with header if specified
    if args.changes:
        try:
            with open(args.changes, 'x', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['pressure_on_time', 'pressure_off_time', 'duration_seconds', 'estimated_gallons'])
        except FileExistsError:
            # File exists, append to it
            pass
    
    # Startup message
    startup_msg = f"=== Pressure Monitor Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ==="
    print(startup_msg)
    print(f"Main log: {args.log}")
    if args.changes:
        print(f"Events log (CSV): {args.changes}")
    print(f"\nWater estimation: {SECONDS_PER_GALLON:.2f}s/gallon, {RESIDUAL_PRESSURE_SECONDS}s residual")
    print("Press Ctrl+C to stop\n")
    
    with open(args.log, 'a') as f:
        f.write('\n' + startup_msg + '\n')
    
    # Initial state
    last_state = GPIO.input(PRESSURE_PIN)
    log_status(last_state, log_file=args.log)
    
    last_log_time = time.time()
    activation_start_time = None  # Wall clock time when pressure activated
    activation_start_timestamp = None  # Formatted timestamp for logging
    
    try:
        while True:
            current_state = GPIO.input(PRESSURE_PIN)
            current_time = time.time()
            
            # Check for state change
            if current_state != last_state:
                if current_state == GPIO.HIGH:
                    # Pressure activated
                    activation_start_time = current_time
                    activation_start_timestamp = datetime.fromtimestamp(current_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                    log_status(current_state, change=True, log_file=args.log)
                    
                else:
                    # Pressure deactivated - log complete event
                    log_status(current_state, change=True, log_file=args.log)
                    
                    if activation_start_time and args.changes:
                        duration = current_time - activation_start_time
                        gallons = estimate_gallons(duration)
                        log_pressure_event(activation_start_time, current_time, 
                                         duration, gallons, args.changes)
                        activation_start_time = None
                
                last_state = current_state
                last_log_time = current_time
            
            # Check if 5 seconds have passed
            elif current_time - last_log_time >= 5.0:
                log_status(current_state, log_file=args.log)
                last_log_time = current_time
            
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        shutdown_msg = f"\n✓ Pressure Monitor stopped at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        # If pressure still active, log final event
        if activation_start_time:
            duration = time.time() - activation_start_time
            shutdown_msg += f"\n  (Pressure was active for {duration:.2f}s when stopped)"
            
            if args.changes:
                gallons = estimate_gallons(duration)
                log_pressure_event(activation_start_time, time.time(), 
                                 duration, gallons, args.changes)
        
        print(shutdown_msg)
        with open(args.log, 'a') as f:
            f.write(shutdown_msg + '\n')
    
    finally:
        GPIO.cleanup()

if __name__ == "__main__":
    main()
