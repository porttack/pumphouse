#!/usr/bin/env python3
"""
Comprehensive Monitor - Pressure and Tank Level Monitoring
Monitors pressure switch and tank level, logs when tank data changes
"""
import RPi.GPIO as GPIO
import time
from datetime import datetime
import sys
import argparse
import csv
import signal
import threading
import requests
from bs4 import BeautifulSoup
import re

# GPIO Configuration
PRESSURE_PIN = 17
FLOAT_PIN = 27
POLL_INTERVAL = 5  # Seconds between pressure sensor readings
TANK_POLL_INTERVAL = 60  # Seconds between tank level checks (1 minute)

# Tank configuration
TANK_HEIGHT_INCHES = 58
TANK_CAPACITY_GALLONS = 1400
TANK_URL = "https://www.mypt.in/s/REDACTED-TANK-URL"

# Water Volume Estimation Constants
RESIDUAL_PRESSURE_SECONDS = 30
SECONDS_PER_GALLON = 10 / 0.14

# Logging Configuration
MAX_PRESSURE_LOG_INTERVAL = 1800

class SystemState:
    """
    Shared state for all monitoring threads.
    Thread-safe access to current system state for future web serving.
    """
    def __init__(self):
        self.lock = threading.Lock()
        
        # Pressure state
        self.pressure_state = None
        self.pressure_last_change = None
        self.pressure_activation_start = None
        
        # Tank state - current
        self.tank_depth = None
        self.tank_percentage = None
        self.tank_pt_percentage = None
        self.tank_gallons = None
        self.tank_last_updated = None
        self.float_state = None
        self.float_last_change = None
        
        # Tank state - previous (for detecting changes)
        self.prev_tank_depth = None
        self.prev_tank_percentage = None
        self.prev_tank_pt_percentage = None
        self.prev_tank_gallons = None
        
        # Last logged values for calculating deltas
        self.last_logged_gallons = None
        
        # Flag to indicate tank data has changed
        self.tank_data_changed = threading.Event()
    
    def update_pressure(self, state, activation_start=None):
        with self.lock:
            if state != self.pressure_state:
                self.pressure_last_change = datetime.now()
            self.pressure_state = state
            if activation_start is not None:
                self.pressure_activation_start = activation_start
    
    def update_tank(self, depth, percentage, pt_percentage, gallons, last_updated, float_state):
        """
        Update tank state and set changed flag if any values differ.
        Returns True if data changed, False otherwise.
        """
        with self.lock:
            changed = False
            
            # Check if any values changed
            if (depth != self.tank_depth or 
                percentage != self.tank_percentage or
                pt_percentage != self.tank_pt_percentage or
                gallons != self.tank_gallons):
                changed = True
            
            # Check float state change
            if float_state != self.float_state and float_state is not None:
                self.float_last_change = datetime.now()
                changed = True
            
            # Save previous values
            self.prev_tank_depth = self.tank_depth
            self.prev_tank_percentage = self.tank_percentage
            self.prev_tank_pt_percentage = self.tank_pt_percentage
            self.prev_tank_gallons = self.tank_gallons
            
            # Update current values
            self.tank_depth = depth
            self.tank_percentage = percentage
            self.tank_pt_percentage = pt_percentage
            self.tank_gallons = gallons
            self.tank_last_updated = last_updated
            self.float_state = float_state
            
            if changed:
                self.tank_data_changed.set()
            
            return changed
    
    def clear_tank_changed_flag(self):
        """Clear the tank data changed flag"""
        self.tank_data_changed.clear()
    
    def wait_for_tank_change(self, timeout=None):
        """Wait for tank data to change"""
        return self.tank_data_changed.wait(timeout)
    
    def get_snapshot(self):
        """Get a thread-safe snapshot of current state"""
        with self.lock:
            return {
                'pressure_state': self.pressure_state,
                'pressure_last_change': self.pressure_last_change,
                'pressure_activation_start': self.pressure_activation_start,
                'tank_depth': self.tank_depth,
                'tank_percentage': self.tank_percentage,
                'tank_pt_percentage': self.tank_pt_percentage,
                'tank_gallons': self.tank_gallons,
                'tank_last_updated': self.tank_last_updated,
                'float_state': self.float_state,
                'float_last_change': self.float_last_change,
                'last_logged_gallons': self.last_logged_gallons
            }
    
    def set_last_logged_gallons(self, gallons):
        with self.lock:
            self.last_logged_gallons = gallons

# GPIO Helper Functions
def setup_gpio():
    """Setup GPIO for reading. Returns True on success, False on failure."""
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
    try:
        GPIO.cleanup()
    except Exception:
        pass

def read_pressure():
    """Read pressure sensor with GPIO management"""
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
    """Read float sensor with GPIO management"""
    if not setup_gpio():
        return 'UNKNOWN'
    
    try:
        GPIO.setup(FLOAT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        state = GPIO.input(FLOAT_PIN)
        
        if state == GPIO.HIGH:
            return 'OPEN/FULL'
        else:
            return 'CLOSED/CALLING'
    except Exception:
        return 'UNKNOWN'
    finally:
        cleanup_gpio()

# Tank monitoring functions (from scrape_tank_level.py)
def calculate_gallons(depth_inches):
    """Calculate gallons based on linear relationship"""
    if depth_inches is None:
        return None
    gallons = (depth_inches / TANK_HEIGHT_INCHES) * TANK_CAPACITY_GALLONS
    return gallons

def parse_last_updated(last_updated_text):
    """Parse 'X minutes, Y seconds ago' text"""
    current_time = datetime.now()
    minutes = 0
    
    minutes_match = re.search(r'(\d+)\s+minute', last_updated_text)
    if minutes_match:
        minutes = int(minutes_match.group(1))
    
    from datetime import timedelta
    time_delta = timedelta(minutes=minutes)
    last_updated = current_time - time_delta
    last_updated = last_updated.replace(second=0, microsecond=0)
    
    return last_updated

def get_tank_data(url):
    """Scrape tank data from PT website"""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        current_timestamp = datetime.now()
        
        # Find PT percentage
        pt_percentage = None
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string and 'ptlevel' in script.string:
                match = re.search(r"level:\s*(\d+)", script.string)
                if match:
                    pt_percentage = int(match.group(1))
                    break
        
        # Find depth
        depth_inches = None
        inch_level_span = soup.find('span', class_='inchLevel')
        if inch_level_span:
            depth_inches = float(inch_level_span.get_text(strip=True))
        
        # Find last updated time
        last_updated_timestamp = None
        updated_on_span = soup.find('span', class_='updated_on')
        if updated_on_span:
            last_updated_text = updated_on_span.get_text(strip=True)
            last_updated_timestamp = parse_last_updated(last_updated_text)
        
        # Calculate percentage and gallons
        percentage = None
        gallons = None
        if depth_inches is not None:
            percentage = (depth_inches / TANK_HEIGHT_INCHES) * 100
            percentage = round(percentage, 1)
            gallons = calculate_gallons(depth_inches)
        
        # Read float sensor
        float_state = read_float_sensor()
        
        return {
            'percentage': percentage,
            'pt_percentage': pt_percentage,
            'depth': depth_inches,
            'gallons': gallons,
            'last_updated': last_updated_timestamp,
            'float_state': float_state,
            'status': 'success'
        }
        
    except Exception as e:
        return {
            'percentage': None,
            'pt_percentage': None,
            'depth': None,
            'gallons': None,
            'last_updated': None,
            'float_state': read_float_sensor(),
            'status': 'error',
            'error_message': str(e)
        }

# Pressure monitoring functions
def estimate_gallons(duration_seconds):
    """Estimate gallons pumped based on pressure duration"""
    effective_pumping_time = duration_seconds - RESIDUAL_PRESSURE_SECONDS
    
    if effective_pumping_time <= 0:
        return 0.0
    
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
    
    if debug:
        print(message)
        sys.stdout.flush()
        
        if change:
            print('\a', end='', flush=True)
    
    if log_file:
        with open(log_file, 'a') as f:
            f.write(message + '\n')

def log_pressure_event(start_time, end_time, duration, gallons_pumped, event_type, 
                       change_log_file, system_state, debug=False):
    """
    Log complete pressure event to CSV with tank data
    
    CSV columns: pressure_on_time, pressure_off_time, duration_seconds, estimated_gallons, 
                 event_type, float_state, float_last_change, tank_gallons, tank_depth, 
                 tank_percentage, tank_pt_percentage, gallons_changed
    """
    start_timestamp = datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    end_timestamp = datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    
    # Get current system state
    state = system_state.get_snapshot()
    
    # Calculate gallons changed
    gallons_changed = None
    if state['tank_gallons'] is not None and state['last_logged_gallons'] is not None:
        gallons_changed = state['tank_gallons'] - state['last_logged_gallons']
    
    # Format float last change
    float_last_change_str = ''
    if state['float_last_change']:
        float_last_change_str = state['float_last_change'].strftime('%Y-%m-%d %H:%M:%S')
    
    with open(change_log_file, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            start_timestamp,
            end_timestamp,
            f'{duration:.3f}',
            f'{gallons_pumped:.2f}',
            event_type,
            state['float_state'] or '',
            float_last_change_str,
            f'{state["tank_gallons"]:.0f}' if state['tank_gallons'] else '',
            f'{state["tank_depth"]:.2f}' if state['tank_depth'] else '',
            f'{state["tank_percentage"]:.1f}' if state['tank_percentage'] else '',
            f'{state["tank_pt_percentage"]}' if state['tank_pt_percentage'] else '',
            f'{gallons_changed:.1f}' if gallons_changed is not None else ''
        ])
    
    # Update last logged gallons
    if state['tank_gallons'] is not None:
        system_state.set_last_logged_gallons(state['tank_gallons'])
    
    if debug:
        print(f"  → Event: {duration:.1f}s, ~{gallons_pumped:.1f}gal pumped [{event_type}]")
        if state['tank_gallons']:
            change_str = f", Δ{gallons_changed:+.1f}gal" if gallons_changed is not None else ""
            print(f"     Tank: {state['tank_gallons']:.0f}gal ({state['tank_percentage']:.1f}%){change_str}, "
                  f"Float: {state['float_state']}")

class TankMonitor(threading.Thread):
    """Thread for monitoring tank level periodically"""
    
    def __init__(self, system_state, url, interval, debug=False):
        super().__init__(daemon=True)
        self.system_state = system_state
        self.url = url
        self.interval = interval
        self.debug = debug
        self.running = True
    
    def stop(self):
        self.running = False
    
    def run(self):
        """Periodically fetch tank data and update system state"""
        while self.running:
            try:
                data = get_tank_data(self.url)
                
                if data['status'] == 'success':
                    changed = self.system_state.update_tank(
                        data['depth'],
                        data['percentage'],
                        data['pt_percentage'],
                        data['gallons'],
                        data['last_updated'],
                        data['float_state']
                    )
                    
                    if self.debug:
                        change_indicator = " [CHANGED]" if changed else ""
                        print(f"\n[Tank Update{change_indicator}] {data['gallons']:.0f}gal ({data['percentage']:.1f}%), "
                              f"Float: {data['float_state']}")
                else:
                    if self.debug:
                        print(f"\n[Tank Error] {data.get('error_message', 'Unknown error')}")
                
            except Exception as e:
                if self.debug:
                    print(f"\n[Tank Monitor Error] {e}")
            
            # Sleep in small increments to allow faster shutdown
            for _ in range(self.interval):
                if not self.running:
                    break
                time.sleep(1)

class PressureMonitor:
    def __init__(self, log_file, change_log_file, system_state, tank_monitor, debug=False, 
                 debug_interval=60, poll_interval=5):
        self.log_file = log_file
        self.change_log_file = change_log_file
        self.system_state = system_state
        self.tank_monitor = tank_monitor
        self.debug = debug
        self.debug_interval = debug_interval
        self.poll_interval = poll_interval
        self.running = True
        self.activation_start_time = None
        self.last_maxtime_log = None
        self.is_startup_activation = False
        
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
        
        current_state = read_pressure()
        if current_state == GPIO.HIGH and self.activation_start_time:
            current_time = time.time()
            duration = current_time - self.activation_start_time
            
            if self.debug:
                shutdown_msg += f"\n  (Pressure was active for {duration:.2f}s when stopped)"
                print("\n  Fetching final tank level before shutdown...")
            
            # Fetch tank level one final time before logging shutdown event
            tank_data = get_tank_data(self.tank_monitor.url)
            if tank_data['status'] == 'success':
                self.system_state.update_tank(
                    tank_data['depth'],
                    tank_data['percentage'],
                    tank_data['pt_percentage'],
                    tank_data['gallons'],
                    tank_data['last_updated'],
                    tank_data['float_state']
                )
            
            if self.change_log_file:
                gallons = estimate_gallons(duration)
                log_pressure_event(self.activation_start_time, current_time, 
                                 duration, gallons, 'SHUTDOWN', self.change_log_file,
                                 self.system_state, self.debug)
        
        if self.debug:
            print(shutdown_msg)
        
        with open(self.log_file, 'a') as f:
            f.write(shutdown_msg + '\n')
    
    def wait_for_tank_update_or_timeout(self, timeout):
        """
        Wait for tank data to change or timeout.
        Returns True if tank changed, False if timeout.
        """
        return self.system_state.wait_for_tank_change(timeout)
    
    def run(self):
        """Main monitoring loop"""
        last_state = read_pressure()
        
        if last_state is None:
            print("ERROR: Cannot read pressure sensor on startup", file=sys.stderr)
            return
        
        if last_state == GPIO.HIGH:
            if self.debug:
                print(f"Startup: Pressure already ≥10 PSI, starting timer from 0")
            self.activation_start_time = time.time()
            self.last_maxtime_log = time.time()
            self.is_startup_activation = True
        
        self.system_state.update_pressure(last_state, self.activation_start_time)
        log_status(last_state, log_file=self.log_file, debug=self.debug)
        
        last_log_time = time.time()
        
        try:
            while self.running:
                current_state = read_pressure()
                current_time = time.time()
                
                if current_state is None:
                    if self.debug:
                        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Waiting for GPIO access...")
                    time.sleep(self.poll_interval)
                    continue
                
                # Check for state change
                if current_state != last_state:
                    if current_state == GPIO.HIGH:
                        # Pressure activated - clear any previous tank change flag and start fresh
                        self.system_state.clear_tank_changed_flag()
                        self.activation_start_time = current_time
                        self.last_maxtime_log = current_time
                        self.is_startup_activation = False
                        self.system_state.update_pressure(current_state, self.activation_start_time)
                        log_status(current_state, change=True, log_file=self.log_file, debug=self.debug)
                        
                    else:
                        # Pressure deactivated - wait for tank update, then log
                        log_status(current_state, change=True, log_file=self.log_file, debug=self.debug)
                        
                        if self.activation_start_time and self.change_log_file:
                            if self.debug:
                                print("  Waiting for tank level update before logging...")
                            
                            # Wait up to 2 minutes for tank data to update
                            # (tank updates every 1 minute, so this gives it time)
                            tank_updated = self.wait_for_tank_update_or_timeout(120)
                            
                            if tank_updated:
                                if self.debug:
                                    print("  Tank level updated, logging event...")
                                self.system_state.clear_tank_changed_flag()
                            else:
                                if self.debug:
                                    print("  Timeout waiting for tank update, logging with current data...")
                            
                            duration = current_time - self.activation_start_time
                            gallons = estimate_gallons(duration)
                            
                            event_type = 'STARTUP' if self.is_startup_activation else 'NORMAL'
                            
                            log_pressure_event(self.activation_start_time, current_time, 
                                             duration, gallons, event_type, self.change_log_file,
                                             self.system_state, self.debug)
                            self.activation_start_time = None
                            self.last_maxtime_log = None
                            self.is_startup_activation = False
                        
                        self.system_state.update_pressure(current_state, None)
                    
                    last_state = current_state
                    last_log_time = current_time
                
                # Check for MAXTIME event (only log if tank data changed)
                elif (current_state == GPIO.HIGH and 
                      self.activation_start_time and 
                      self.last_maxtime_log and
                      current_time - self.last_maxtime_log >= MAX_PRESSURE_LOG_INTERVAL):
                    
                    # Only log MAXTIME if tank data has changed since last log
                    if self.system_state.wait_for_tank_change(0):  # Non-blocking check
                        if self.debug:
                            print("  Tank data changed during long pressure event, logging MAXTIME...")
                        
                        self.system_state.clear_tank_changed_flag()
                        duration = current_time - self.activation_start_time
                        gallons = estimate_gallons(duration)
                        
                        if self.change_log_file:
                            log_pressure_event(self.activation_start_time, current_time,
                                             duration, gallons, 'MAXTIME', self.change_log_file,
                                             self.system_state, self.debug)
                        
                        self.activation_start_time = current_time
                        self.last_maxtime_log = current_time
                    else:
                        # Reset timer but don't log since tank data hasn't changed
                        self.last_maxtime_log = current_time
                        if self.debug:
                            print(f"  → 30 min checkpoint (no tank change, not logging)")
                
                # Regular debug logging
                elif self.debug and current_time - last_log_time >= self.debug_interval:
                    log_status(current_state, log_file=self.log_file, debug=self.debug)
                    last_log_time = current_time
                
                time.sleep(self.poll_interval)
                
        except Exception as e:
            if self.debug:
                print(f"\nError occurred: {e}")
            with open(self.log_file, 'a') as f:
                f.write(f"\nERROR: {e}\n")
            self.shutdown()
            raise
        
        self.shutdown()

def main():
    parser = argparse.ArgumentParser(
        prog='monitor.py',
        description='Monitor pressure sensor and tank level',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  ./monitor.py --changes events.csv
      Monitor pressure and tank, log events to CSV when tank data changes
  
  ./monitor.py --changes events.csv --debug
      Debug mode with console output
  
  ./monitor.py --tank-interval 5
      Check tank level every 5 minutes instead of default 1 minute

Logging Behavior:
  - Logs when pressure drops AND tank level data has been updated
  - Waits up to 2 minutes after pressure drop for fresh tank data
  - For long pressure events (30+ min), only logs if tank data changed
  - Fetches final tank reading on shutdown if pressure is active

CSV Columns:
  pressure_on_time, pressure_off_time, duration_seconds, estimated_gallons, event_type,
  float_state, float_last_change, tank_gallons, tank_depth, tank_percentage, 
  tank_pt_percentage, gallons_changed
        ''')
    
    parser.add_argument('--log', default='pressure_log.txt', 
                       help='Main log file (default: pressure_log.txt)')
    parser.add_argument('--changes', default=None,
                       help='CSV file for pressure events')
    parser.add_argument('--debug', action='store_true',
                       help='Enable console output')
    parser.add_argument('--debug-interval', type=int, default=60,
                       help='Console logging interval in debug mode (default: 60s)')
    parser.add_argument('--poll-interval', type=int, default=5,
                       help='Pressure sensor poll interval (default: 5s)')
    parser.add_argument('--tank-interval', type=int, default=1,
                       help='Tank check interval in minutes (default: 1 minute)')
    parser.add_argument('--tank-url', default=TANK_URL,
                       help='Tank monitoring URL')
    parser.add_argument('--version', action='version', version='%(prog)s 2.0.0')
    
    args = parser.parse_args()
    
    # Initialize CSV with new headers
    if args.changes:
        try:
            with open(args.changes, 'x', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'pressure_on_time', 'pressure_off_time', 'duration_seconds', 
                    'estimated_gallons', 'event_type', 'float_state', 'float_last_change',
                    'tank_gallons', 'tank_depth', 'tank_percentage', 'tank_pt_percentage',
                    'gallons_changed'
                ])
        except FileExistsError:
            pass
    
    # Startup message
    if args.debug:
        print(f"=== Monitor Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
        print(f"Pressure poll: {args.poll_interval}s")
        print(f"Tank poll: {args.tank_interval}m")
        print("\nFetching initial tank level...")
    
    startup_msg = f"=== Monitor Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ==="
    with open(args.log, 'a') as f:
        f.write('\n' + startup_msg + '\n')
    
    # Create shared state
    system_state = SystemState()
    
    # Fetch initial tank level before starting monitoring
    initial_tank_data = get_tank_data(args.tank_url)
    if initial_tank_data['status'] == 'success':
        system_state.update_tank(
            initial_tank_data['depth'],
            initial_tank_data['percentage'],
            initial_tank_data['pt_percentage'],
            initial_tank_data['gallons'],
            initial_tank_data['last_updated'],
            initial_tank_data['float_state']
        )
        # Set initial logged gallons so first event can calculate delta
        if initial_tank_data['gallons'] is not None:
            system_state.set_last_logged_gallons(initial_tank_data['gallons'])
        
        if args.debug:
            print(f"Initial tank: {initial_tank_data['gallons']:.0f}gal ({initial_tank_data['percentage']:.1f}%), "
                  f"Float: {initial_tank_data['float_state']}")
            print("Press Ctrl+C to stop\n")
    else:
        if args.debug:
            print(f"Warning: Could not fetch initial tank data: {initial_tank_data.get('error_message')}")
    
    # Clear the changed flag since we just initialized
    system_state.clear_tank_changed_flag()
    
    # Start tank monitoring thread
    tank_monitor = TankMonitor(
        system_state, 
        args.tank_url, 
        args.tank_interval * 60,  # Convert minutes to seconds
        args.debug
    )
    tank_monitor.start()
    
    # Run pressure monitor in main thread
    monitor = PressureMonitor(
        args.log, 
        args.changes, 
        system_state,
        tank_monitor,
        args.debug, 
        args.debug_interval, 
        args.poll_interval
    )
    
    try:
        monitor.run()
    finally:
        # Stop tank monitor thread
        tank_monitor.stop()
        tank_monitor.join(timeout=2)

if __name__ == "__main__":
    main()