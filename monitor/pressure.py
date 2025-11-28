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
        
        # Track pressure artifacts (pressure when float is OPEN/FULL)
        self.artifact_start_time = None
        self.is_artifact = False
        
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        if self.debug:
            print(f"\n\nReceived signal {signum}, shutting down gracefully...")
        self.running = False
    
    def should_log_event(self):
        """
        Determine if we should log a pressure event based on system state.
        
        Returns True if:
        - Float is not OPEN/FULL (tank is accepting water), OR
        - Pressure has dropped to zero (regardless of float state)
        
        This prevents logging false water additions when:
        - Neighbor has pressure but our float is closed
        - We have residual pressure but aren't actually filling
        """
        state = self.system_state.get_snapshot()
        float_state = state['float_state']
        
        # If float is OPEN/FULL, we should only log when pressure drops
        # (to capture the end state even though no water was added)
        if float_state == 'OPEN/FULL':
            if self.debug:
                print("  ⚠️  Float is OPEN/FULL - tank not accepting water")
            return False  # Don't log yet, wait for pressure to drop
        
        # Float is not full (CLOSED/CALLING or UNKNOWN), normal logging
        return True
    
    def fetch_tank_with_retry(self, max_retries=3, retry_delay=5):
        """
        Fetch tank data with retry logic.
        Returns tank_data dict or None if all retries failed.
        """
        for attempt in range(max_retries):
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
                    self.system_state.record_tank_success()
                    return tank_data
                else:
                    if self.debug:
                        print(f"  Attempt {attempt + 1}/{max_retries}: {tank_data.get('error_message')}")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
            except Exception as e:
                if self.debug:
                    print(f"  Attempt {attempt + 1}/{max_retries}: Exception: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
        
        return None
    
    def shutdown(self):
        """Perform clean shutdown"""
        shutdown_msg = f"\n✓ Pressure Monitor stopped at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        current_state = read_pressure()
        
        # Check if we have an active artifact that needs logging
        if current_state and self.artifact_start_time and self.is_artifact:
            current_time = time.time()
            duration = current_time - self.artifact_start_time
            
            if self.debug:
                print(f"\n  Logging pressure artifact on shutdown ({duration:.1f}s)")
            
            if self.change_log_file:
                if self.debug:
                    print(f"\n  === LOGGING PRESSURE_ARTIFACT EVENT (SHUTDOWN) ===")
                    state = self.system_state.get_snapshot()
                    print(f"  Artifact duration: {duration:.1f}s")
                    print(f"  Float state:       {state['float_state']}")
                    print(f"  Pressure state:    HIGH")
                    print(f"  Trigger:           SHUTDOWN with active artifact")
                
                log_pressure_event(self.artifact_start_time, current_time, 
                                 duration, 0.0, 'PRESSURE_ARTIFACT', self.change_log_file,
                                 self.system_state, self.debug)
        
        # Check if we have an active water event that needs logging
        elif current_state and self.activation_start_time:
            current_time = time.time()
            duration = current_time - self.activation_start_time
            
            if self.debug:
                shutdown_msg += f"\n  (Pressure was active for {duration:.2f}s when stopped)"
                print("\n  Fetching final tank level before shutdown...")
            
            # Fetch tank level one final time with retries
            tank_data = self.fetch_tank_with_retry()
            
            if self.change_log_file:
                gallons = estimate_gallons(duration)
                
                if self.debug:
                    print(f"\n  === LOGGING SHUTDOWN EVENT ===")
                    state = self.system_state.get_snapshot()
                    print(f"  Tank before event: {state['last_logged_gallons']:.0f}gal" if state['last_logged_gallons'] else "  Tank before event: Unknown")
                    print(f"  Tank after event:  {state['tank_gallons']:.0f}gal ({state['tank_percentage']:.1f}%)" if state['tank_gallons'] else "  Tank after event: Unknown")
                    print(f"  Float state: {state['float_state']}")
                    print(f"  Trigger: SHUTDOWN signal")
                
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
        
        # Check if we should start timing based on pressure AND float state
        if last_state:  # GPIO.HIGH - Pressure detected
            # Get current float state to decide if we're actually receiving water
            current_float = self.system_state.get_snapshot()['float_state']
            
            if current_float == 'OPEN/FULL':
                # Pressure but tank is full - this is an artifact (neighbor's pressure)
                if self.debug:
                    print(f"Startup: Pressure ≥10 PSI but float is OPEN/FULL")
                    print(f"         Starting artifact timer (neighbor has pressure)")
                # Start artifact tracking instead of water timer
                self.artifact_start_time = time.time()
                self.is_artifact = True
                self.activation_start_time = None
                self.last_maxtime_log = None
                self.is_startup_activation = False
            else:
                # Pressure and tank not full - we're actually receiving water
                if self.debug:
                    print(f"Startup: Pressure ≥10 PSI and float is {current_float} - starting timer")
                self.activation_start_time = time.time()
                self.last_maxtime_log = time.time()
                self.is_startup_activation = True
                self.artifact_start_time = None
                self.is_artifact = False
        
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
                    if current_state:  # GPIO.HIGH - Pressure activated
                        # Check float state before starting timer
                        current_float = self.system_state.get_snapshot()['float_state']
                        
                        if current_float == 'OPEN/FULL':
                            # Pressure activated but tank is full - start artifact tracking
                            if self.debug:
                                print(f"  ⚠️  Pressure activated but float is OPEN/FULL")
                                print(f"      Starting artifact timer (neighbor has pressure)")
                            self.artifact_start_time = current_time
                            self.is_artifact = True
                            self.activation_start_time = None
                            self.last_maxtime_log = None
                            self.is_startup_activation = False
                        else:
                            # Normal activation - start water timer
                            if self.debug:
                                print(f"  Pressure activated, float is {current_float} - starting timer")
                            # Clear any previous tank change flag and start fresh
                            self.system_state.clear_tank_changed_flag()
                            self.activation_start_time = current_time
                            self.last_maxtime_log = current_time
                            self.is_startup_activation = False
                            self.artifact_start_time = None
                            self.is_artifact = False
                        
                        self.system_state.update_pressure(current_state, self.activation_start_time)
                        log_status(current_state, change=True, log_file=self.log_file, debug=self.debug)
                        
                    else:  # GPIO.LOW - Pressure deactivated
                        log_status(current_state, change=True, log_file=self.log_file, debug=self.debug)
                        
                        # Check if we were tracking an artifact
                        if self.artifact_start_time and self.is_artifact:
                            duration = current_time - self.artifact_start_time
                            
                            if self.debug:
                                print(f"\n  Pressure artifact ended after {duration:.1f}s")
                            
                            if self.change_log_file:
                                if self.debug:
                                    print(f"\n  === LOGGING PRESSURE_ARTIFACT EVENT ===")
                                    state = self.system_state.get_snapshot()
                                    print(f"  Artifact duration: {duration:.1f}s")
                                    print(f"  Float state:       {state['float_state']}")
                                    print(f"  Pressure state:    LOW (just dropped)")
                                    print(f"  Trigger:           Neighbor's pressure available but we couldn't use it")
                                
                                log_pressure_event(self.artifact_start_time, current_time, 
                                                 duration, 0.0, 'PRESSURE_ARTIFACT', self.change_log_file,
                                                 self.system_state, self.debug)
                            
                            self.artifact_start_time = None
                            self.is_artifact = False
                        
                        # Check if we had a real water event
                        elif self.activation_start_time and self.change_log_file:
                            # Check float state first
                            state_before_wait = self.system_state.get_snapshot()
                            
                            if self.debug:
                                print(f"\n  Pressure dropped, float: {state_before_wait['float_state']}")
                                print("  Waiting for tank level update before logging...")
                            
                            # Wait up to 2 minutes for tank data to update
                            tank_updated = self.wait_for_tank_update_or_timeout(120)
                            
                            if tank_updated:
                                if self.debug:
                                    print("  ✓ Tank level updated")
                                self.system_state.clear_tank_changed_flag()
                            else:
                                if self.debug:
                                    print("  ⏱  Timeout - logging with current data")
                            
                            duration = current_time - self.activation_start_time
                            gallons = estimate_gallons(duration)
                            event_type = 'STARTUP' if self.is_startup_activation else 'NORMAL'
                            
                            # Show detailed debug info before logging
                            if self.debug:
                                print(f"\n  === LOGGING {event_type} EVENT ===")
                                state = self.system_state.get_snapshot()
                                print(f"  Tank before event: {state['last_logged_gallons']:.0f}gal" if state['last_logged_gallons'] else "  Tank before event: Unknown")
                                print(f"  Tank after event:  {state['tank_gallons']:.0f}gal ({state['tank_percentage']:.1f}%)" if state['tank_gallons'] else "  Tank after event: Unknown")
                                if state['tank_gallons'] and state['last_logged_gallons']:
                                    delta = state['tank_gallons'] - state['last_logged_gallons']
                                    print(f"  Actual change:     {delta:+.1f}gal")
                                print(f"  Estimated pumped:  {gallons:.1f}gal")
                                print(f"  Float state:       {state['float_state']}")
                                trigger = "Tank data changed" if tank_updated else "Timeout (2 min)"
                                print(f"  Trigger:           Pressure=0, {trigger}")
                            
                            log_pressure_event(self.activation_start_time, current_time, 
                                             duration, gallons, event_type, self.change_log_file,
                                             self.system_state, self.debug)
                            self.activation_start_time = None
                            self.last_maxtime_log = None
                            self.is_startup_activation = False
                        
                        self.system_state.update_pressure(current_state, None)
                    
                    last_state = current_state
                    last_log_time = current_time
                
                # Check for MAXTIME event (only log if timer is running, tank data changed, AND float allows)
                elif (current_state and  # GPIO.HIGH
                      self.activation_start_time and  # Timer is running (means we're filling)
                      self.last_maxtime_log and
                      current_time - self.last_maxtime_log >= MAX_PRESSURE_LOG_INTERVAL):
                    
                    # Only log MAXTIME if tank data has changed since last log
                    if self.system_state.wait_for_tank_change(0):  # Non-blocking check
                        # Check if we should log based on float state
                        if self.should_log_event():
                            if self.debug:
                                print("\n  Tank data changed during long pressure event, logging MAXTIME...")
                            
                            self.system_state.clear_tank_changed_flag()
                            duration = current_time - self.activation_start_time
                            gallons = estimate_gallons(duration)
                            
                            # Show detailed debug info
                            if self.debug:
                                print(f"\n  === LOGGING MAXTIME EVENT ===")
                                state = self.system_state.get_snapshot()
                                print(f"  Tank before event: {state['last_logged_gallons']:.0f}gal" if state['last_logged_gallons'] else "  Tank before event: Unknown")
                                print(f"  Tank after event:  {state['tank_gallons']:.0f}gal ({state['tank_percentage']:.1f}%)" if state['tank_gallons'] else "  Tank after event: Unknown")
                                if state['tank_gallons'] and state['last_logged_gallons']:
                                    delta = state['tank_gallons'] - state['last_logged_gallons']
                                    print(f"  Actual change:     {delta:+.1f}gal")
                                print(f"  Estimated pumped:  {gallons:.1f}gal")
                                print(f"  Float state:       {state['float_state']}")
                                print(f"  Trigger:           Pressure still high, tank changed, 30min checkpoint")
                            
                            if self.change_log_file:
                                log_pressure_event(self.activation_start_time, current_time,
                                                 duration, gallons, 'MAXTIME', self.change_log_file,
                                                 self.system_state, self.debug)
                            
                            self.activation_start_time = current_time
                            self.last_maxtime_log = current_time
                        else:
                            # Float is full, don't log but reset timer
                            self.last_maxtime_log = current_time
                            self.system_state.clear_tank_changed_flag()
                            if self.debug:
                                print(f"  → 30 min checkpoint: Float OPEN/FULL, not logging (pressure artifact)")
                    else:
                        # Reset timer but don't log since tank data hasn't changed
                        self.last_maxtime_log = current_time
                        if self.debug:
                            print(f"  → 30 min checkpoint: No tank change, not logging")
                
                # Check if float changed while in artifact state (tank now ready to receive water)
                elif (current_state and  # Pressure still HIGH
                      self.is_artifact and 
                      self.artifact_start_time):
                    
                    # Check if float has changed to CLOSED/CALLING (tank can now accept water)
                    current_float = self.system_state.get_snapshot()['float_state']
                    
                    if current_float == 'CLOSED/CALLING':
                        # Float opened - artifact ends, convert to normal water event
                        duration = current_time - self.artifact_start_time
                        
                        if self.debug:
                            print(f"\n  Float changed to CLOSED/CALLING while pressure artifact active")
                            print(f"  Artifact lasted {duration:.1f}s, now converting to water event")
                        
                        # Log the artifact that just ended
                        if self.change_log_file:
                            if self.debug:
                                print(f"\n  === LOGGING PRESSURE_ARTIFACT EVENT ===")
                                state = self.system_state.get_snapshot()
                                print(f"  Artifact duration: {duration:.1f}s")
                                print(f"  Float state:       {current_float} (changed from OPEN/FULL)")
                                print(f"  Pressure state:    HIGH (still active)")
                                print(f"  Trigger:           Float changed, tank can now accept water")
                            
                            log_pressure_event(self.artifact_start_time, current_time, 
                                             duration, 0.0, 'PRESSURE_ARTIFACT', self.change_log_file,
                                             self.system_state, self.debug)
                        
                        # Now start normal water event tracking
                        self.system_state.clear_tank_changed_flag()
                        self.activation_start_time = current_time
                        self.last_maxtime_log = current_time
                        self.is_startup_activation = False
                        self.artifact_start_time = None
                        self.is_artifact = False
                        
                        if self.debug:
                            print(f"  Started water event timer")
                
                # Regular debug logging
                elif self.debug and current_time - last_log_time >= self.debug_interval:
                    log_status(current_state, log_file=self.log_file, debug=self.debug)
                    
                    # Show artifact status if active
                    if self.is_artifact and self.artifact_start_time:
                        artifact_duration = current_time - self.artifact_start_time
                        print(f"  📡 Pressure artifact active for {artifact_duration:.0f}s (neighbor has pressure)")
                    
                    last_log_time = current_time
                
                time.sleep(self.poll_interval)
                
        except Exception as e:
            if self.debug:
                print(f"\nError occurred: {e}")
                import traceback
                traceback.print_exc()
            with open(self.log_file, 'a') as f:
                f.write(f"\nERROR: {e}\n")
            self.shutdown()
            raise
        
        self.shutdown()