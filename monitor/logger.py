"""
Logging functions for pressure events and status
"""
import csv
import sys
from datetime import datetime

def get_status_text(state):
    """Convert GPIO state to human-readable text"""
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

def initialize_csv(filepath):
    """Initialize CSV file with headers if it doesn't exist"""
    try:
        with open(filepath, 'x', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'pressure_on_time', 'pressure_off_time', 'duration_seconds', 
                'estimated_gallons', 'event_type', 'float_state', 'float_last_change',
                'tank_gallons', 'tank_depth', 'tank_percentage', 'tank_pt_percentage',
                'gallons_changed'
            ])
        return True
    except FileExistsError:
        return False
