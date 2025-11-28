"""
Main entry point for the monitoring system
"""
import argparse
from datetime import datetime

from monitor import __version__
from monitor.config import (
    POLL_INTERVAL, TANK_POLL_INTERVAL, TANK_URL, TANK_CHANGE_THRESHOLD,
    DEFAULT_LOG_FILE, DEFAULT_EVENTS_FILE,
    load_config_file
)
from monitor.state import SystemState
from monitor.tank import TankMonitor, get_tank_data
from monitor.pressure import PressureMonitor
from monitor.logger import initialize_csv, log_startup
from monitor.gpio_helpers import read_pressure

def main():
    """Main entry point"""
    
    # Load config file if it exists
    file_config = load_config_file()
    
    parser = argparse.ArgumentParser(
        prog='monitor',
        description='Monitor pressure sensor and tank level',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python -m monitor --changes events.csv
      Monitor pressure and tank, log events to CSV when tank data changes
  
  python -m monitor --changes events.csv --debug
      Debug mode with console output
  
  python -m monitor --tank-interval 5
      Check tank level every 5 minutes instead of default 1 minute
  
  python -m monitor --tank-threshold 5.0
      Log tank changes when ≥5 gallons instead of default 2 gallons

Running in Background:
  nohup venv/bin/python -m monitor --changes events.csv --debug > output.txt 2>&1 &
  
  # Check if running
  ps aux | grep monitor
  
  # Stop
  pkill -f "python -m monitor"

Logging Behavior:
  - Logs startup state (INIT event)
  - Logs when pressure drops AND tank level data has been updated
  - Logs when tank changes by ≥threshold gallons (usage/filling detection)
  - Waits up to 2 minutes after pressure drop for fresh tank data
  - For long pressure events (30+ min), only logs if tank data changed
  - Fetches final tank reading on shutdown if pressure is active
  - Continues pressure monitoring even if tank monitoring fails
  - Ignores pressure when float is OPEN/FULL (prevents false logging)

Event Types:
  INIT        - System startup state
  NORMAL      - Pressure cycle completed normally
  STARTUP     - Pressure was already high at startup (logged when it drops)
  SHUTDOWN    - Program stopped while pressure was high
  MAXTIME     - Pressure checkpoint after 30 minutes (if tank changed)
  TANK_CHANGE - Tank level changed by ≥threshold gallons (usage detection)

CSV Columns:
  timestamp, event_type, pressure_state, duration_seconds, estimated_gallons,
  pressure_on_time, pressure_off_time, float_state, float_last_change, 
  tank_gallons, tank_depth, tank_percentage, tank_pt_percentage, gallons_changed
        ''')
    
    parser.add_argument('--log', default=file_config.get('LOG_FILE', DEFAULT_LOG_FILE),
                       help='Main log file (default: pressure_log.txt)')
    parser.add_argument('--changes', default=file_config.get('EVENTS_FILE', None),
                       help='CSV file for pressure events')
    parser.add_argument('--debug', action='store_true',
                       help='Enable console output')
    parser.add_argument('--debug-interval', type=int, 
                       default=file_config.get('DEBUG_INTERVAL', 60),
                       help='Console logging interval in debug mode (default: 60s)')
    parser.add_argument('--poll-interval', type=int, 
                       default=file_config.get('POLL_INTERVAL', POLL_INTERVAL),
                       help='Pressure sensor poll interval (default: 5s)')
    parser.add_argument('--tank-interval', type=int, 
                       default=file_config.get('TANK_POLL_INTERVAL', TANK_POLL_INTERVAL // 60),
                       help='Tank check interval in minutes (default: 1 minute)')
    parser.add_argument('--tank-threshold', type=float,
                       default=file_config.get('TANK_CHANGE_THRESHOLD', TANK_CHANGE_THRESHOLD),
                       help='Log tank changes ≥ this many gallons (default: 2.0)')
    parser.add_argument('--tank-url', 
                       default=file_config.get('TANK_URL', TANK_URL),
                       help='Tank monitoring URL')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    
    args = parser.parse_args()
    
    # Initialize CSV with headers if needed
    if args.changes:
        if initialize_csv(args.changes):
            if args.debug:
                print(f"Created new CSV file: {args.changes}")
    
    # Startup message
    if args.debug:
        print(f"=== Monitor v{__version__} Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
        print(f"Pressure poll: {args.poll_interval}s")
        print(f"Tank poll: {args.tank_interval}m")
        print(f"Tank threshold: {args.tank_threshold}gal")
        print("\nFetching initial tank level...")
    
    startup_msg = f"=== Monitor v{__version__} Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ==="
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
        system_state.record_tank_success()
        
        # Set initial logged gallons so first event can calculate delta
        if initial_tank_data['gallons'] is not None:
            system_state.set_last_logged_gallons(initial_tank_data['gallons'])
        
        if args.debug:
            print(f"Initial tank: {initial_tank_data['gallons']:.0f}gal ({initial_tank_data['percentage']:.1f}%), "
                  f"Float: {initial_tank_data['float_state']}")
    else:
        system_state.record_tank_error(initial_tank_data.get('error_message', 'Unknown'))
        if args.debug:
            print(f"Warning: Could not fetch initial tank data: {initial_tank_data.get('error_message')}")
            print("Continuing with pressure monitoring...\n")
    
    # Get initial pressure state
    initial_pressure = read_pressure()
    
    # Log startup event
    if args.changes:
        log_startup(args.changes, system_state, initial_pressure, args.debug)
    
    if args.debug:
        print("Press Ctrl+C to stop\n")
    
    # Clear the changed flag since we just initialized
    system_state.clear_tank_changed_flag()
    
    # Start tank monitoring thread
    tank_monitor = TankMonitor(
        system_state, 
        args.tank_url, 
        args.tank_interval * 60,  # Convert minutes to seconds
        change_log_file=args.changes,
        debug=args.debug,
        change_threshold=args.tank_threshold
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