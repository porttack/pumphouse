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
import signal

# GPIO Configuration
PRESSURE_PIN = 17

# Water Volume Estimation Constants
RESIDUAL_PRESSURE_SECONDS = 30  # Last N seconds are residual pressure (not pumping)
SECONDS_PER_GALLON = 10 / 0.14   # 10 seconds = 0.14 gallons - represents 2 clicks of dosatron at 0.08

# Logging Configuration
MAX_PRESSURE_LOG_INTERVAL = 1800  # Log at least every 30 minutes (1800s) when pressure is high

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

def log_status(state, change=False, log_file=None, debug=False):
    """Log status to console (if debug) and main log file"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    status = get_status_text(state)
    prefix = ">>> CHANGE: " if change else ""
    
    message = f"{timestamp} - {prefix}{status}"
    
    # Print to console only if debug mode
    if debug:
        print(message)
        sys.stdout.flush()
        
        if change:
            print('\a', end='', flush=True)
    
    # Always write to main log file
    if log_file:
        with open(log_file, 'a') as f:
            f.write(message + '\n')

def log_pressure_event(start_time, end_time, duration, gallons, event_type, change_log_file, debug=False):
    """
    Log complete pressure event to CSV (one row per event)
    
    Args:
        start_time: Unix timestamp when pressure activated
        end_time: Unix timestamp when pressure deactivated (or current time for partial events)
        duration: Duration in seconds
        gallons: Estimated gallons pumped
        event_type: Type of event - 'NORMAL', 'SHUTDOWN', 'STARTUP', 'MAXTIME'
        change_log_file: Path to CSV file
        debug: Whether to print to console
    """
    start_timestamp = datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    end_timestamp = datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    
    with open(change_log_file, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([start_timestamp, end_timestamp, f'{duration:.3f}', f'{gallons:.2f}', event_type])
    
    # Print summary to console only if debug mode
    if debug:
        print(f"  → Event summary: {duration:.1f}s duration, ~{gallons:.1f} gallons [{event_type}]")

class PressureMonitor:
    def __init__(self, log_file, change_log_file, debug=False, debug_interval=60):
        self.log_file = log_file
        self.change_log_file = change_log_file
        self.debug = debug
        self.debug_interval = debug_interval
        self.running = True
        self.activation_start_time = None
        self.last_maxtime_log = None
        self.is_startup_activation = False  # Track if current activation is from startup
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        if self.debug:
            print(f"\n\nReceived signal {signum}, shutting down gracefully...")
        self.running = False
    
    def shutdown(self):
        """Perform clean shutdown"""
        shutdown_msg = f"\n✓ Pressure Monitor stopped at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        # If pressure still active, log final event as SHUTDOWN
        current_state = GPIO.input(PRESSURE_PIN)
        if current_state == GPIO.HIGH and self.activation_start_time:
            current_time = time.time()
            duration = current_time - self.activation_start_time
            
            if self.debug:
                shutdown_msg += f"\n  (Pressure was active for {duration:.2f}s when stopped)"
            
            if self.change_log_file:
                gallons = estimate_gallons(duration)
                log_pressure_event(self.activation_start_time, current_time, 
                                 duration, gallons, 'SHUTDOWN', self.change_log_file, self.debug)
        
        if self.debug:
            print(shutdown_msg)
        
        with open(self.log_file, 'a') as f:
            f.write(shutdown_msg + '\n')
        
        GPIO.cleanup()
    
    def run(self):
        """Main monitoring loop"""
        # Initial state
        last_state = GPIO.input(PRESSURE_PIN)
        
        # Handle startup with pressure already high
        if last_state == GPIO.HIGH:
            if self.debug:
                print(f"Startup: Pressure already ≥10 PSI, starting timer from 0")
            self.activation_start_time = time.time()
            self.last_maxtime_log = time.time()
            self.is_startup_activation = True  # Mark as startup activation
        
        log_status(last_state, log_file=self.log_file, debug=self.debug)
        
        last_log_time = time.time()
        
        try:
            while self.running:
                current_state = GPIO.input(PRESSURE_PIN)
                current_time = time.time()
                
                # Check for state change
                if current_state != last_state:
                    if current_state == GPIO.HIGH:
                        # Pressure activated
                        self.activation_start_time = current_time
                        self.last_maxtime_log = current_time
                        self.is_startup_activation = False  # Normal activation, not startup
                        log_status(current_state, change=True, log_file=self.log_file, debug=self.debug)
                        
                    else:
                        # Pressure deactivated - log complete event
                        log_status(current_state, change=True, log_file=self.log_file, debug=self.debug)
                        
                        if self.activation_start_time and self.change_log_file:
                            duration = current_time - self.activation_start_time
                            gallons = estimate_gallons(duration)
                            
                            # Determine event type based on startup flag
                            event_type = 'STARTUP' if self.is_startup_activation else 'NORMAL'
                            
                            log_pressure_event(self.activation_start_time, current_time, 
                                             duration, gallons, event_type, self.change_log_file, self.debug)
                            self.activation_start_time = None
                            self.last_maxtime_log = None
                            self.is_startup_activation = False  # Reset flag
                    
                    last_state = current_state
                    last_log_time = current_time
                
                # Check if pressure has been high for MAX_PRESSURE_LOG_INTERVAL
                elif (current_state == GPIO.HIGH and 
                      self.activation_start_time and 
                      self.last_maxtime_log and
                      current_time - self.last_maxtime_log >= MAX_PRESSURE_LOG_INTERVAL):
                    
                    # Log MAXTIME event
                    duration = current_time - self.activation_start_time
                    gallons = estimate_gallons(duration)
                    
                    if self.change_log_file:
                        log_pressure_event(self.activation_start_time, current_time,
                                         duration, gallons, 'MAXTIME', self.change_log_file, self.debug)
                    
                    # Reset for next interval, but preserve is_startup_activation flag
                    # (so if this was a startup activation, subsequent MAXTIME events know that)
                    self.activation_start_time = current_time
                    self.last_maxtime_log = current_time
                    
                    if self.debug:
                        print(f"  → Pressure still high, resetting timer for next interval")
                
                # Check if debug interval has passed for regular logging
                elif self.debug and current_time - last_log_time >= self.debug_interval:
                    log_status(current_state, log_file=self.log_file, debug=self.debug)
                    last_log_time = current_time
                
                time.sleep(0.1)
                
        except Exception as e:
            if self.debug:
                print(f"\nError occurred: {e}")
            # Always log errors to file
            with open(self.log_file, 'a') as f:
                f.write(f"\nERROR: {e}\n")
            self.shutdown()
            raise
        
        # Normal shutdown
        self.shutdown()

def main():
    parser = argparse.ArgumentParser(
        prog='pressure_monitor.py',
        description='Monitor pressure sensor and log state changes',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  ./pressure_monitor.py --changes events.csv
      Run quietly, log events to CSV
  
  ./pressure_monitor.py --changes events.csv --debug
      Run with console output (debug mode), log every 60 seconds
  
  ./pressure_monitor.py --changes events.csv --debug --debug-interval 10
      Debug mode with console output every 10 seconds

Running in Background:
  
  # Quiet mode (recommended) - no output redirection needed
  nohup ./pressure_monitor.py --changes events.csv &
  
  # With debug output
  nohup ./pressure_monitor.py --changes events.csv --debug > output.txt 2>&1 &
  
  # Check if running
  ps aux | grep pressure_monitor
  
  # View logs
  tail -f events.csv
  
  # Stop
  pkill -f pressure_monitor.py

CSV Format:
  pressure_on_time, pressure_off_time, duration_seconds, estimated_gallons, event_type
  2025-01-23 20:30:12.345, 2025-01-23 20:30:20.123, 7.778, 0.00, NORMAL
  2025-01-23 20:45:05.123, 2025-01-23 21:30:15.456, 2710.333, 114.86, NORMAL
  2025-01-23 21:30:15.456, 2025-01-23 22:00:15.456, 1800.000, 75.86, MAXTIME

Event Types:
  NORMAL   - Pressure cycle completed normally (pressure dropped below 10 PSI)
  SHUTDOWN - Program stopped while pressure was still high (Ctrl+C or kill signal)
  STARTUP  - Pressure was already high when program started (logs when it drops)
  MAXTIME  - Pressure has been high for 30+ minutes, logging checkpoint

Water Volume Estimation:
  - Last {sec}s of pressure assumed to be residual (not pumping)
  - Formula: gallons = (duration - {sec}s) / {spg:.2f} seconds/gallon
  - Adjust RESIDUAL_PRESSURE_SECONDS and SECONDS_PER_GALLON at top of file
  
Hardware Setup:
  - Pressure switch NC contact → GPIO17 (physical pin 11)
  - Pressure switch C contact  → Ground (physical pin 9)
  - NC closes below 10 PSI, opens at/above 10 PSI
  
Debug Mode:
  - Without --debug: No console output (quiet mode)
  - With --debug: Console output with configurable interval
  - Default debug interval: 60 seconds
  - Always logs state changes immediately in debug mode
        '''.format(sec=RESIDUAL_PRESSURE_SECONDS, spg=SECONDS_PER_GALLON))
    
    parser.add_argument('--log', default='pressure_log.txt', 
                       metavar='FILE',
                       help='Main log file with all events (default: pressure_log.txt)')
    parser.add_argument('--changes', default=None,
                       metavar='FILE',
                       help='CSV file for pressure events with water volume (e.g., pressure_events.csv)')
    parser.add_argument('--debug', action='store_true',
                       help='Enable console output (quiet mode by default)')
    parser.add_argument('--debug-interval', type=int, default=60, metavar='SECONDS',
                       help='Console logging interval in debug mode (default: 60 seconds)')
    parser.add_argument('--version', action='version', version='%(prog)s 1.2.0')
    
    args = parser.parse_args()
    
    # Setup GPIO after argument parsing
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(PRESSURE_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    
    # Initialize change log CSV with header if specified
    if args.changes:
        try:
            with open(args.changes, 'x', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['pressure_on_time', 'pressure_off_time', 'duration_seconds', 
                               'estimated_gallons', 'event_type'])
        except FileExistsError:
            # File exists, append to it
            pass
    
    # Startup message - only show if debug mode
    if args.debug:
        startup_msg = f"=== Pressure Monitor Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ==="
        print(startup_msg)
        print(f"Main log: {args.log}")
        if args.changes:
            print(f"Events log (CSV): {args.changes}")
        print(f"\nWater estimation: {SECONDS_PER_GALLON:.2f}s/gallon, {RESIDUAL_PRESSURE_SECONDS}s residual")
        print(f"Max pressure log interval: {MAX_PRESSURE_LOG_INTERVAL}s (30 minutes)")
        print(f"Debug interval: {args.debug_interval}s")
        print("Press Ctrl+C to stop\n")
    
    # Always log startup to file
    startup_msg = f"=== Pressure Monitor Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ==="
    with open(args.log, 'a') as f:
        f.write('\n' + startup_msg + '\n')
    
    # Create and run monitor
    monitor = PressureMonitor(args.log, args.changes, args.debug, args.debug_interval)
    monitor.run()

if __name__ == "__main__":
    main()
