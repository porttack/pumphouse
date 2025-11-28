"""
Logging functions for pressure events and status
"""
import csv
import sys
from datetime import datetime

def get_status_text(state):
    """Convert GPIO state to human-readable text"""
    return "≥10 PSI (NC OPEN)" if state else "<10 PSI (NC CLOSED)"

def get_pressure_state_text(state):
    """Convert pressure state to HIGH/LOW"""
    if state is None:
        return "UNKNOWN"
    return "HIGH" if state else "LOW"

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

def log_event(timestamp, pressure_state, duration, gallons_pumped, event_type, 
              change_log_file, system_state, debug=False, 
              pressure_on_time=None, pressure_off_time=None):
    """
    Log an event to CSV - can be pressure event, tank change, or startup
    
    CSV columns: timestamp, event_type, pressure_state, duration_seconds, estimated_gallons,
                 pressure_on_time, pressure_off_time,
                 float_state, float_last_change, tank_gallons, tank_depth, 
                 tank_percentage, tank_pt_percentage, gallons_changed
    
    Args:
        timestamp: Event timestamp (datetime or unix time)
        pressure_state: Current pressure state (GPIO.HIGH/LOW or None)
        duration: Duration in seconds (or None for non-pressure events)
        gallons_pumped: Estimated gallons pumped (or None for non-pressure events)
        event_type: 'NORMAL', 'SHUTDOWN', 'STARTUP', 'MAXTIME', 'TANK_CHANGE', 'INIT', 'PRESSURE_ARTIFACT'
        change_log_file: Path to CSV file
        system_state: SystemState object
        debug: Whether to print debug info
        pressure_on_time: Unix timestamp when pressure activated (optional)
        pressure_off_time: Unix timestamp when pressure deactivated (optional)
    """
    # Convert timestamp to string
    if isinstance(timestamp, datetime):
        timestamp_str = timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    elif isinstance(timestamp, (int, float)):
        timestamp_str = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    else:
        timestamp_str = str(timestamp)
    
    # Format pressure on/off times
    pressure_on_str = ''
    pressure_off_str = ''
    if pressure_on_time is not None:
        if isinstance(pressure_on_time, (int, float)):
            pressure_on_str = datetime.fromtimestamp(pressure_on_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        else:
            pressure_on_str = str(pressure_on_time)
    if pressure_off_time is not None:
        if isinstance(pressure_off_time, (int, float)):
            pressure_off_str = datetime.fromtimestamp(pressure_off_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        else:
            pressure_off_str = str(pressure_off_time)
    
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
    
    # Get pressure state text
    pressure_state_str = get_pressure_state_text(pressure_state)
    
    with open(change_log_file, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            timestamp_str,
            event_type,
            pressure_state_str,
            f'{duration:.3f}' if duration is not None else '',
            f'{gallons_pumped:.2f}' if gallons_pumped is not None else '',
            pressure_on_str,
            pressure_off_str,
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

def log_pressure_event(start_time, end_time, duration, gallons_pumped, event_type, 
                       change_log_file, system_state, debug=False):
    """
    Log a pressure event using the unified logging format
    
    Args:
        start_time: Unix timestamp when pressure activated
        end_time: Unix timestamp when pressure deactivated  
        duration: Duration in seconds
        gallons_pumped: Estimated gallons pumped
        event_type: 'NORMAL', 'SHUTDOWN', 'STARTUP', 'MAXTIME', 'PRESSURE_ARTIFACT'
        change_log_file: Path to CSV
        system_state: SystemState object
        debug: Whether to print debug info
    """
    # When pressure event is logged, pressure has just dropped to LOW
    try:
        import RPi.GPIO as GPIO
        pressure_low = GPIO.LOW  # This is 0 or False
    except (ImportError, RuntimeError):
        pressure_low = False
    
    log_event(
        timestamp=end_time,  # Use end_time as the event timestamp
        pressure_state=pressure_low,  # Pressure just dropped to LOW
        duration=duration,
        gallons_pumped=gallons_pumped,
        event_type=event_type,
        change_log_file=change_log_file,
        system_state=system_state,
        debug=debug,
        pressure_on_time=start_time,
        pressure_off_time=end_time
    )

def log_tank_change(change_log_file, system_state, pressure_state, debug=False):
    """
    Log a tank level change (usage detection)
    """
    if debug:
        state = system_state.get_snapshot()
        print(f"\n  === LOGGING TANK_CHANGE EVENT ===")
        print(f"  Tank before event: {state['last_logged_gallons']:.0f}gal" if state['last_logged_gallons'] else "  Tank before event: Unknown")
        print(f"  Tank after event:  {state['tank_gallons']:.0f}gal ({state['tank_percentage']:.1f}%)" if state['tank_gallons'] else "  Tank after event: Unknown")
        if state['tank_gallons'] and state['last_logged_gallons']:
            delta = state['tank_gallons'] - state['last_logged_gallons']
            print(f"  Actual change:     {delta:+.1f}gal")
        print(f"  Float state:       {state['float_state']}")
        print(f"  Pressure state:    {get_pressure_state_text(pressure_state)}")
        print(f"  Trigger:           Tank level changed")
    
    log_event(
        timestamp=datetime.now(),
        pressure_state=pressure_state,
        duration=None,
        gallons_pumped=None,
        event_type='TANK_CHANGE',
        change_log_file=change_log_file,
        system_state=system_state,
        debug=debug
    )

def log_startup(change_log_file, system_state, pressure_state, debug=False):
    """
    Log initial startup state
    """
    if debug:
        state = system_state.get_snapshot()
        print(f"\n  === LOGGING INIT EVENT ===")
        print(f"  Initial tank:      {state['tank_gallons']:.0f}gal ({state['tank_percentage']:.1f}%)" if state['tank_gallons'] else "  Initial tank: Unknown")
        print(f"  Float state:       {state['float_state']}")
        print(f"  Pressure state:    {get_pressure_state_text(pressure_state)}")
        print(f"  Trigger:           System startup")
    
    log_event(
        timestamp=datetime.now(),
        pressure_state=pressure_state,
        duration=None,
        gallons_pumped=None,
        event_type='INIT',
        change_log_file=change_log_file,
        system_state=system_state,
        debug=debug
    )

def initialize_csv(filepath):
    """Initialize CSV file with headers if it doesn't exist"""
    try:
        with open(filepath, 'x', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'timestamp', 'event_type', 'pressure_state', 'duration_seconds', 
                'estimated_gallons', 'pressure_on_time', 'pressure_off_time',
                'float_state', 'float_last_change', 'tank_gallons', 'tank_depth', 
                'tank_percentage', 'tank_pt_percentage', 'gallons_changed'
            ])
        return True
    except FileExistsError:
        return False