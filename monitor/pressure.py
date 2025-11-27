"""
Pressure monitoring logic and event detection
"""
import time
from datetime import datetime
import signal

from monitor.config import RESIDUAL_PRESSURE_SECONDS, SECONDS_PER_GALLON, MAX_PRESSURE_LOG_INTERVAL
from monitor.gpio_helpers import read_pressure
from monitor.tank import get_tank_data
from monitor.logger import log_status, log_pressure_event

def estimate_gallons(duration_seconds):
    """
    Estimate gallons pumped based on pressure duration.
    
    Args:
        duration_seconds: Total time pressure was active
    
    Returns:
        Estimated gallons (float), or 0 if duration too short
    """
    effective_pumping_time = duration_seconds - RESIDUAL_PRESSURE_SECONDS
    
    if effective_pumping_time <= 0:
        return 0.0
    
    gallons = effective_pumping_time / SECONDS_PER_GALLON
    return gallons

class PressureMonitor:
    """Main pressure monitoring class"""
    
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
        if current_state and self.activation_start_time:
            current_time = time.time()
            duration = current_time - self.activation_start_time
            
            if self.debug:
                shutdown_msg += f"\n  (Pressure was active for {duration:.2f}s when stopped)"
                print("\n  Fetching final tank level before shutdown...")
            
            # Fetch tank level one final time before logging shutdown event
            try:
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
            except Exception as e:
                if self.debug:
                    print(f"  Could not fetch final tank level: {e}")
            
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
            print("ERROR: Cannot read pressure sensor on startup")
            return
        
        if last_state:  # GPIO.HIGH
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
                    if current_state:  # GPIO.HIGH
                        # Pressure activated - clear any previous tank change flag and start fresh
                        self.system_state.clear_tank_changed_flag()
                        self.activation_start_time = current_time
                        self.last_maxtime_log = current_time
                        self.is_startup_activation = False
                        self.system_state.update_pressure(current_state, self.activation_start_time)
                        log_status(current_state, change=True, log_file=self.log_file, debug=self.debug)
                        
                    else:  # GPIO.LOW
                        # Pressure deactivated - wait for tank update, then log
                        log_status(current_state, change=True, log_file=self.log_file, debug=self.debug)
                        
                        if self.activation_start_time and self.change_log_file:
                            if self.debug:
                                print("  Waiting for tank level update before logging...")
                            
                            # Wait up to 2 minutes for tank data to update
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
                elif (current_state and  # GPIO.HIGH
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
