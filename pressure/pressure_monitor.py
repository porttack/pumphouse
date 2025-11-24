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

PRESSURE_PIN = 17

def get_status_text(state):
    return "≥10 PSI (NC OPEN)" if state else "<10 PSI (NC CLOSED)"

def log_status(state, change=False, log_file=None, change_log_file=None, activation_start=None):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]  # Include milliseconds
    status = get_status_text(state)
    prefix = ">>> CHANGE: " if change else ""
    
    message = f"{timestamp} - {prefix}{status}"
    
    # Print to console
    print(message)
    sys.stdout.flush()
    
    # Write to main log file (text format for readability)
    if log_file:
        with open(log_file, 'a') as f:
            f.write(message + '\n')
    
    # Write to change-only log file (CSV format)
    if change and change_log_file:
        with open(change_log_file, 'a', newline='') as f:
            writer = csv.writer(f)
            
            if state == GPIO.HIGH:  # Pressure activated
                writer.writerow([timestamp, 'PRESSURE_ON', '>=10', ''])
            else:  # Pressure deactivated
                if activation_start:
                    duration = time.time() - activation_start
                    writer.writerow([timestamp, 'PRESSURE_OFF', '<10', f'{duration:.3f}'])
                else:
                    writer.writerow([timestamp, 'PRESSURE_OFF', '<10', ''])
    
    if change:
        print('\a', end='', flush=True)

def main():
    parser = argparse.ArgumentParser(
        prog='pressure_monitor.py',
        description='Monitor pressure sensor and log state changes',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python3 pressure_monitor.py
      Log every 5 seconds to pressure_log.txt
  
  python3 pressure_monitor.py --changes pressure_changes.csv
      Also log state changes with durations to CSV file
  
  python3 pressure_monitor.py --log full.txt --changes changes.csv
      Custom log filenames for both logs

Running in Background (survives SSH disconnect):
  
  Method 1 - Using nohup (simplest):
    nohup python3 pressure_monitor.py --changes changes.csv > output.txt 2>&1 &
    
    To check if running:
      ps aux | grep pressure_monitor
    
    To view logs:
      tail -f changes.csv
      tail -f output.txt
    
    To stop:
      pkill -f pressure_monitor.py
  
  Method 2 - Using screen (interactive):
    screen -S pressure
    python3 pressure_monitor.py --changes changes.csv
    <Ctrl+A, then D to detach>
    
    To reattach:
      screen -r pressure
    
    To stop:
      screen -r pressure
      <Ctrl+C>
  
  Method 3 - Using tmux (modern alternative):
    tmux new -s pressure
    python3 pressure_monitor.py --changes changes.csv
    <Ctrl+B, then D to detach>
    
    To reattach:
      tmux attach -t pressure
    
    To stop:
      tmux kill-session -t pressure

CSV Format:
  timestamp, event, pressure, duration_seconds
  2025-01-23 20:30:12.345, PRESSURE_ON, >=10, 
  2025-01-23 20:30:20.123, PRESSURE_OFF, <10, 8.153

Hardware Setup:
  - Pressure switch NC contact → GPIO17 (physical pin 11)
  - Pressure switch C contact  → Ground (physical pin 9)
  - NC closes below 10 PSI, opens at/above 10 PSI
  
Monitoring:
  - Logs every 5 seconds in main log
  - Logs immediately on state changes
  - CSV file logs only changes with durations
  - Terminal beeps on state changes
  - Press Ctrl+C to stop gracefully
        ''')
    
    parser.add_argument('--log', default='pressure_log.txt', 
                       metavar='FILE',
                       help='Main log file with all events (default: pressure_log.txt)')
    parser.add_argument('--changes', default=None,
                       metavar='FILE',
                       help='CSV file for state changes only with durations (e.g., pressure_changes.csv)')
    parser.add_argument('--version', action='version', version='%(prog)s 1.0')
    
    args = parser.parse_args()
    
    # Setup GPIO after argument parsing
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(PRESSURE_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    
    # Initialize change log CSV with header if specified
    if args.changes:
        with open(args.changes, 'a', newline='') as f:
            # Check if file is empty (new file)
            f.seek(0, 2)  # Go to end
            if f.tell() == 0:  # File is empty
                writer = csv.writer(f)
                writer.writerow(['timestamp', 'event', 'pressure', 'duration_seconds'])
    
    # Startup message
    startup_msg = f"=== Pressure Monitor Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ==="
    print(startup_msg)
    print(f"Main log: {args.log}")
    if args.changes:
        print(f"Change log (CSV): {args.changes}")
    print("Press Ctrl+C to stop\n")
    
    with open(args.log, 'a') as f:
        f.write('\n' + startup_msg + '\n')
    
    # Initial state
    last_state = GPIO.input(PRESSURE_PIN)
    log_status(last_state, log_file=args.log)
    
    last_log_time = time.time()
    activation_start = None
    
    try:
        while True:
            current_state = GPIO.input(PRESSURE_PIN)
            current_time = time.time()
            
            # Check for state change
            if current_state != last_state:
                # Track activation time for duration calculation
                if current_state == GPIO.HIGH:
                    activation_start = current_time
                
                log_status(current_state, change=True, 
                          log_file=args.log, 
                          change_log_file=args.changes,
                          activation_start=activation_start)
                
                # Reset activation_start after logging deactivation
                if current_state == GPIO.LOW:
                    activation_start = None
                
                last_state = current_state
                last_log_time = current_time
            
            # Check if 5 seconds have passed
            elif current_time - last_log_time >= 5.0:
                log_status(current_state, log_file=args.log)
                last_log_time = current_time
            
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        shutdown_msg = f"\n✓ Pressure Monitor stopped at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        # If pressure still active, log final duration
        if activation_start:
            duration = time.time() - activation_start
            shutdown_msg += f"\n  (Pressure was active for {duration:.2f}s when stopped)"
        
        print(shutdown_msg)
        with open(args.log, 'a') as f:
            f.write(shutdown_msg + '\n')
    
    finally:
        GPIO.cleanup()

if __name__ == "__main__":
    main()