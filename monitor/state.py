"""
Shared system state with thread-safe access
"""
import threading
from datetime import datetime

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
        
        # Tank monitoring health
        self.tank_error_count = 0
        self.tank_last_success = None
        self.tank_last_error = None
    
    def update_pressure(self, state, activation_start=None):
        """Update pressure state"""
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
    
    def record_tank_success(self):
        """Record successful tank data fetch"""
        with self.lock:
            self.tank_error_count = 0
            self.tank_last_success = datetime.now()
    
    def record_tank_error(self, error_msg):
        """Record tank data fetch error"""
        with self.lock:
            self.tank_error_count += 1
            self.tank_last_error = (datetime.now(), error_msg)
    
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
                'last_logged_gallons': self.last_logged_gallons,
                'tank_error_count': self.tank_error_count,
                'tank_last_success': self.tank_last_success,
                'tank_last_error': self.tank_last_error
            }
    
    def set_last_logged_gallons(self, gallons):
        """Set the last logged gallons value"""
        with self.lock:
            self.last_logged_gallons = gallons
