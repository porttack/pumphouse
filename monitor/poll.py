"""
Unified polling loop for all sensors
"""
import time
from datetime import datetime
import signal

from monitor.config import (
    POLL_INTERVAL, TANK_POLL_INTERVAL, TANK_CHANGE_THRESHOLD,
    RESIDUAL_PRESSURE_SECONDS, SECONDS_PER_GALLON, MAX_PRESSURE_LOG_INTERVAL,
    ARTIFACT_LOW_GRACE_PERIOD
)
from monitor.gpio_helpers import read_pressure, read_float_sensor
from monitor.tank import get_tank_data
from monitor.logger import log_status, log_pressure_event, log_tank_change

def estimate_gallons(duration_seconds):
    """Estimate gallons pumped based on pressure duration"""
    effective_pumping_time = duration_seconds - RESIDUAL_PRESSURE_SECONDS
    if effective_pumping_time <= 0:
        return 0.0
    return effective_pumping_time / SECONDS_PER_GALLON

class UnifiedMonitor:
    """Single-threaded monitor polling all sensors"""
    
    def __init__(self, log_file, change_log_file, system_state, tank_url,
                 debug=False, debug_interval=60, poll_interval=5, 
                 tank_interval=60, tank_threshold=2.0, artifact_grace_period=300):
        self.log_file = log_file
        self.change_log_file = change_log_file
        self.system_state = system_state
        self.tank_url = tank_url
        self.debug = debug
        self.debug_interval = debug_interval
        self.poll_interval = poll_interval
        self.tank_interval = tank_interval
        self.tank_threshold = tank_threshold
        self.artifact_low_grace_period = artifact_grace_period
        self.running = True
        
        # Pressure tracking
        self.activation_start_time = None
        self.last_maxtime_log = None
        self.is_startup_activation = False
        self.artifact_start_time = None
        self.is_artifact = False
        
        # Track when pressure went LOW (for delayed artifact logging)
        self.pressure_low_since = None
        
        # Timing
        self.last_tank_check = 0
        self.last_log_time = 0
        self.consecutive_tank_errors = 0
        self.max_consecutive_errors = 10
        
        # Relay control
        self.relay_control_enabled = False
        
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        if self.debug:
            print(f"\n\nReceived signal {signum}, shutting down gracefully...")
        self.running = False
    
    def enable_relay_control(self):
        """Enable relay control for automatic filter purging"""
        from monitor.relay import init_relays
        if init_relays():
            self.relay_control_enabled = True
            if self.debug:
                print("Relay control enabled for automatic filter purging")
            return True
        else:
            if self.debug:
                print("Warning: Relay control not available")
            return False
    
    def fetch_tank_data(self):
        """Fetch tank data and update system state"""
        data = get_tank_data(self.tank_url)
        
        if data['status'] == 'success':
            # Reset error counter
            if self.consecutive_tank_errors > 0:
                if self.debug:
                    print(f"\n✓ Tank monitoring recovered after {self.consecutive_tank_errors} errors")
            self.consecutive_tank_errors = 0
            self.system_state.record_tank_success()
            
            # Get previous state
            prev_state = self.system_state.get_snapshot()
            prev_gallons = prev_state['last_logged_gallons']
            
            # Update tank state
            changed = self.system_state.update_tank(
                data['depth'], data['percentage'], data['pt_percentage'],
                data['gallons'], data['last_updated'], data['float_state']
            )
            
            # Check for significant tank change (NOT during artifact)
            if (self.change_log_file and prev_gallons is not None and 
                data['gallons'] is not None and not self.is_artifact):
                delta = data['gallons'] - prev_gallons
                
                if abs(delta) >= self.tank_threshold:
                    if self.debug:
                        action = "gained" if delta > 0 else "lost"
                        print(f"\n💧 Significant tank change: {action} {abs(delta):.1f}gal")
                    
                    pressure_state = read_pressure()
                    log_tank_change(self.change_log_file, self.system_state,
                                  pressure_state, self.debug)
            
            if self.debug:
                change_indicator = " [CHANGED]" if changed else ""
                gallons_str = f"{data['gallons']:.0f}gal" if data['gallons'] else "??gal"
                pct_str = f"({data['percentage']:.1f}%)" if data['percentage'] else "(??%)"
                print(f"\n[Tank Update{change_indicator}] {gallons_str} {pct_str}, "
                      f"Float: {data['float_state']}")
            
            return True
            
        else:
            self.consecutive_tank_errors += 1
            error_msg = data.get('error_message', 'Unknown error')
            self.system_state.record_tank_error(error_msg)
            
            if self.debug:
                print(f"\n[Tank Error {self.consecutive_tank_errors}/{self.max_consecutive_errors}] {error_msg}")
            
            if self.consecutive_tank_errors == self.max_consecutive_errors and self.debug:
                print(f"\n⚠️  Warning: Tank monitoring has failed {self.consecutive_tank_errors} times")
                print(f"   Pressure monitoring continues, but tank data may be stale")
            
            return False
    
    def handle_pressure_artifact_end(self, end_time):
        """Log when a pressure artifact ends"""
        if not self.artifact_start_time or not self.is_artifact:
            return
        
        duration = end_time - self.artifact_start_time
        
        if self.debug:
            print(f"\n  Artifact ended (total duration: {duration:.1f}s)")
        
        if self.change_log_file:
            if self.debug:
                print(f"\n  === LOGGING PRESSURE_ARTIFACT EVENT ===")
                state = self.system_state.get_snapshot()
                print(f"  Artifact duration: {duration:.1f}s")
                print(f"  Float state:       {state['float_state']}")
                print(f"  Trigger:           Pressure LOW for {self.artifact_low_grace_period}s")
            
            log_pressure_event(self.artifact_start_time, end_time,
                             duration, 0.0, 'PRESSURE_ARTIFACT',
                             self.change_log_file, self.system_state, self.debug)
        
        self.artifact_start_time = None
        self.is_artifact = False
    
    def handle_water_event_end(self, current_time):
        """Log when a water delivery event ends"""
        if not self.activation_start_time:
            return
        
        if self.debug:
            print(f"\n  Pressure dropped, fetching final tank update...")
        
        # Give tank one more update before logging
        self.fetch_tank_data()
        
        duration = current_time - self.activation_start_time
        gallons = estimate_gallons(duration)
        event_type = 'STARTUP' if self.is_startup_activation else 'NORMAL'
        
        if self.debug:
            print(f"\n  === LOGGING {event_type} EVENT ===")
            state = self.system_state.get_snapshot()
            print(f"  Tank before: {state['last_logged_gallons']:.0f}gal" if state['last_logged_gallons'] else "  Tank before: Unknown")
            print(f"  Tank after:  {state['tank_gallons']:.0f}gal ({state['tank_percentage']:.1f}%)" if state['tank_gallons'] else "  Tank after: Unknown")
            if state['tank_gallons'] and state['last_logged_gallons']:
                delta = state['tank_gallons'] - state['last_logged_gallons']
                print(f"  Actual change: {delta:+.1f}gal")
            print(f"  Estimated:     {gallons:.1f}gal")
            print(f"  Float:         {state['float_state']}")
        
        log_pressure_event(self.activation_start_time, current_time,
                         duration, gallons, event_type, self.change_log_file,
                         self.system_state, self.debug)
        
        # Trigger spindown filter purge after water delivery
        if self.relay_control_enabled:
            if self.debug:
                print(f"\n  🌀 Triggering spindown filter purge...")
            from monitor.relay import purge_spindown_filter
            purge_spindown_filter(debug=self.debug)
        
        self.activation_start_time = None
        self.last_maxtime_log = None
        self.is_startup_activation = False
    
    def shutdown(self):
        """Clean shutdown and final logging"""
        shutdown_msg = f"\n✓ Monitor stopped at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        current_state = read_pressure()
        current_time = time.time()
        
        # Log active artifact (even if in grace period)
        if self.is_artifact and self.artifact_start_time:
            # Use the time when pressure first dropped if available, otherwise now
            end_time = self.pressure_low_since if self.pressure_low_since else current_time
            self.handle_pressure_artifact_end(end_time)
        
        # Log active water event
        elif current_state and self.activation_start_time:
            if self.debug:
                duration = current_time - self.activation_start_time
                shutdown_msg += f"\n  (Pressure active for {duration:.2f}s when stopped)"
            
            self.fetch_tank_data()  # Final tank update
            
            duration = current_time - self.activation_start_time
            gallons = estimate_gallons(duration)
            
            if self.change_log_file:
                log_pressure_event(self.activation_start_time, current_time,
                                 duration, gallons, 'SHUTDOWN', self.change_log_file,
                                 self.system_state, self.debug)
        
        # Cleanup relays
        if self.relay_control_enabled:
            from monitor.relay import cleanup_relays
            cleanup_relays()
        
        if self.debug:
            print(shutdown_msg)
        
        with open(self.log_file, 'a') as f:
            f.write(shutdown_msg + '\n')
    
    def run(self):
        """Main polling loop"""
        # Initial pressure state
        last_state = read_pressure()
        
        if last_state is None:
            print("ERROR: Cannot read pressure sensor on startup")
            return
        
        # Check startup conditions
        if last_state:  # Pressure HIGH at startup
            current_float = self.system_state.get_snapshot()['float_state']
            
            if current_float == 'OPEN/FULL':
                if self.debug:
                    print(f"Startup: Pressure HIGH but float OPEN/FULL")
                    print(f"         Starting artifact timer (neighbor has pressure)")
                self.artifact_start_time = time.time()
                self.is_artifact = True
            else:
                if self.debug:
                    print(f"Startup: Pressure HIGH and float {current_float} - starting timer")
                self.activation_start_time = time.time()
                self.last_maxtime_log = time.time()
                self.is_startup_activation = True
        
        self.system_state.update_pressure(last_state, self.activation_start_time)
        log_status(last_state, log_file=self.log_file, debug=self.debug)
        
        self.last_log_time = time.time()
        self.last_tank_check = time.time()
        
        try:
            while self.running:
                current_time = time.time()
                current_state = read_pressure()
                
                # Handle GPIO read failure
                if current_state is None:
                    if self.debug:
                        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Cannot read pressure")
                    time.sleep(self.poll_interval)
                    continue
                
                # PRESSURE STATE CHANGE
                if current_state != last_state:
                    if current_state:  # Pressure went HIGH
                        # Cancel any pending artifact end (pressure came back)
                        if self.pressure_low_since is not None:
                            low_duration = current_time - self.pressure_low_since
                            if self.debug:
                                print(f"  Pressure returned after {low_duration:.1f}s LOW (< {self.artifact_low_grace_period}s grace period)")
                            self.pressure_low_since = None
                        
                        current_float = self.system_state.get_snapshot()['float_state']
                        
                        if current_float == 'OPEN/FULL':
                            # Start or continue artifact tracking
                            if not self.is_artifact:
                                if self.debug:
                                    print(f"  ⚠️  Pressure HIGH but float OPEN/FULL")
                                    print(f"      Starting artifact timer")
                                self.artifact_start_time = current_time
                                self.is_artifact = True
                            else:
                                if self.debug:
                                    print(f"  Pressure returned during artifact (brief LOW, ignored)")
                        else:
                            # Start water event tracking
                            if self.debug:
                                print(f"  Pressure HIGH, float {current_float} - starting timer")
                            self.activation_start_time = current_time
                            self.last_maxtime_log = current_time
                        
                        self.system_state.update_pressure(current_state, self.activation_start_time)
                        log_status(current_state, change=True, log_file=self.log_file, debug=self.debug)
                    
                    else:  # Pressure went LOW
                        log_status(current_state, change=True, log_file=self.log_file, debug=self.debug)
                        
                        # Mark when pressure went LOW, but don't log artifact yet
                        if self.is_artifact:
                            self.pressure_low_since = current_time
                            if self.debug:
                                print(f"  Pressure dropped during artifact - waiting {self.artifact_low_grace_period}s to confirm...")
                        elif self.activation_start_time:
                            # Water event ended
                            self.handle_water_event_end(current_time)
                        
                        self.system_state.update_pressure(current_state, None)
                    
                    last_state = current_state
                    self.last_log_time = current_time
                
                # CHECK if pressure has been LOW long enough to confirm artifact ended
                elif (not current_state and  # Pressure is LOW
                      self.is_artifact and 
                      self.pressure_low_since is not None and
                      current_time - self.pressure_low_since >= self.artifact_low_grace_period):
                    
                    # Pressure has been LOW for grace period - artifact really ended
                    if self.debug:
                        print(f"\n  Pressure LOW for {self.artifact_low_grace_period}s - confirming artifact end")
                    
                    self.handle_pressure_artifact_end(self.pressure_low_since)
                    self.pressure_low_since = None
                
                # MAXTIME checkpoint (30 min during water event)
                elif (current_state and self.activation_start_time and 
                      self.last_maxtime_log and
                      current_time - self.last_maxtime_log >= MAX_PRESSURE_LOG_INTERVAL):
                    
                    duration = current_time - self.activation_start_time
                    gallons = estimate_gallons(duration)
                    
                    if self.debug:
                        print(f"\n  === LOGGING MAXTIME EVENT ===")
                        print(f"  Duration so far: {duration:.1f}s")
                        print(f"  Estimated:       {gallons:.1f}gal")
                    
                    if self.change_log_file:
                        log_pressure_event(self.activation_start_time, current_time,
                                         duration, gallons, 'MAXTIME', self.change_log_file,
                                         self.system_state, self.debug)
                    
                    self.activation_start_time = current_time
                    self.last_maxtime_log = current_time
                
                # FLOAT CHANGE during artifact (tank ready to receive water)
                elif current_state and self.is_artifact:
                    current_float = self.system_state.get_snapshot()['float_state']
                    
                    if current_float == 'CLOSED/CALLING':
                        if self.debug:
                            print(f"\n  Float changed to CLOSED/CALLING during artifact")
                        
                        # End artifact (log it now, regardless of grace period)
                        end_time = self.pressure_low_since if self.pressure_low_since else current_time
                        self.handle_pressure_artifact_end(end_time)
                        self.pressure_low_since = None
                        
                        # Start water event
                        if self.debug:
                            print(f"  Starting water event timer")
                        self.activation_start_time = current_time
                        self.last_maxtime_log = current_time
                
                # TANK POLLING (every tank_interval seconds)
                if current_time - self.last_tank_check >= self.tank_interval:
                    self.fetch_tank_data()
                    self.last_tank_check = current_time
                
                # DEBUG LOGGING
                if self.debug and current_time - self.last_log_time >= self.debug_interval:
                    log_status(current_state, log_file=self.log_file, debug=self.debug)
                    
                    if self.is_artifact and self.artifact_start_time:
                        duration = current_time - self.artifact_start_time
                        if self.pressure_low_since:
                            low_duration = current_time - self.pressure_low_since
                            print(f"  📡 Artifact: {duration:.0f}s total, pressure LOW for {low_duration:.0f}s/{self.artifact_low_grace_period}s")
                        else:
                            print(f"  📡 Artifact active for {duration:.0f}s (pressure HIGH)")
                    
                    self.last_log_time = current_time
                
                time.sleep(self.poll_interval)
        
        except Exception as e:
            if self.debug:
                print(f"\nError: {e}")
                import traceback
                traceback.print_exc()
            with open(self.log_file, 'a') as f:
                f.write(f"\nERROR: {e}\n")
            self.shutdown()
            raise
        
        self.shutdown()