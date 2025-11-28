"""
Main entry point for the monitoring system
"""
import argparse
from datetime import datetime

from monitor import __version__
from monitor.config import (
    POLL_INTERVAL, TANK_POLL_INTERVAL, TANK_URL, TANK_CHANGE_THRESHOLD,
    DEFAULT_LOG_FILE, DEFAULT_EVENTS_FILE, ARTIFACT_LOW_GRACE_PERIOD,
    load_config_file
)
from monitor.state import SystemState
from monitor.tank import get_tank_data
from monitor.poll import UnifiedMonitor
from monitor.logger import initialize_csv, log_startup
from monitor.gpio_helpers import init_gpio, cleanup_gpio, read_pressure

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
      Monitor pressure and tank, log events when data changes
  
  python -m monitor --changes events.csv --debug
      Debug mode with console output
  
  python -m monitor --tank-interval 5
      Check tank every 5 minutes instead of default 1 minute

Running in Background:
  nohup venv/bin/python -m monitor --changes events.csv --debug > output.txt 2>&1 &
  
  # Check if running
  ps aux | grep monitor
  
  # Stop
  pkill -f "python -m monitor"

Event Types:
  INIT             - System startup state
  NORMAL           - Pressure cycle completed
  STARTUP          - Pressure was high at startup
  SHUTDOWN         - Program stopped during pressure
  MAXTIME          - 30-minute checkpoint during long events
  TANK_CHANGE      - Tank level changed by ≥threshold gallons
  PRESSURE_ARTIFACT - Neighbor had pressure but our tank was full

CSV Columns:
  timestamp, event_type, pressure_state, duration_seconds, estimated_gallons,
  pressure_on_time, pressure_off_time, float_state, float_last_change, 
  tank_gallons, tank_depth, tank_percentage, tank_pt_percentage, gallons_changed
        ''')
    
    parser.add_argument('--log', default=file_config.get('LOG_FILE', DEFAULT_LOG_FILE),
                       help='Main log file (default: pressure_log.txt)')
    parser.add_argument('--changes', default=file_config.get('EVENTS_FILE', None),
                       help='CSV file for events')
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
    parser.add_argument('--artifact-grace-period', type=int,
                       default=file_config.get('ARTIFACT_LOW_GRACE_PERIOD', ARTIFACT_LOW_GRACE_PERIOD),
                       help='Seconds to wait before confirming artifact ended (default: 300)')
    parser.add_argument('--tank-url', 
                       default=file_config.get('TANK_URL', TANK_URL),
                       help='Tank monitoring URL')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    
    args = parser.parse_args()
    
    # Initialize GPIO once
    if not init_gpio():
        print("Warning: Could not initialize GPIO, sensor readings will not work")
    
    # Initialize CSV
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
        print(f"Artifact grace: {args.artifact_grace_period}s")
        print("\nFetching initial tank level...")
    
    startup_msg = f"=== Monitor v{__version__} Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ==="
    with open(args.log, 'a') as f:
        f.write('\n' + startup_msg + '\n')
    
    # Create shared state
    system_state = SystemState()
    
    # Fetch initial tank level
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
        
        if initial_tank_data['gallons'] is not None:
            system_state.set_last_logged_gallons(initial_tank_data['gallons'])
        
        if args.debug:
            print(f"Initial tank: {initial_tank_data['gallons']:.0f}gal ({initial_tank_data['percentage']:.1f}%), "
                  f"Float: {initial_tank_data['float_state']}")
    else:
        system_state.record_tank_error(initial_tank_data.get('error_message', 'Unknown'))
        if args.debug:
            print(f"Warning: Could not fetch initial tank data")
    
    # Get initial pressure state
    initial_pressure = read_pressure()
    
    # Log startup event
    if args.changes:
        log_startup(args.changes, system_state, initial_pressure, args.debug)
    
    if args.debug:
        print("Press Ctrl+C to stop\n")
    
    # Clear tank changed flag
    system_state.clear_tank_changed_flag()
    
    # Run unified monitor (single-threaded)
    monitor = UnifiedMonitor(
        args.log,
        args.changes,
        system_state,
        args.tank_url,
        args.debug,
        args.debug_interval,
        args.poll_interval,
        args.tank_interval * 60,  # Convert to seconds
        args.tank_threshold,
        args.artifact_grace_period
    )
    
    # Optionally enable relay control (comment out if not using relays yet)
    # monitor.enable_relay_control()
    
    try:
        monitor.run()
    finally:
        cleanup_gpio()

if __name__ == "__main__":
    main()
