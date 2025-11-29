"""
Main entry point for simplified monitoring system
"""
import argparse

from monitor import __version__
from monitor.config import (
    POLL_INTERVAL, TANK_POLL_INTERVAL, TANK_URL, SNAPSHOT_INTERVAL,
    DEFAULT_EVENTS_FILE, DEFAULT_SNAPSHOTS_FILE, load_config_file
)
from monitor.poll import SimplifiedMonitor
from monitor.logger import initialize_events_csv, initialize_snapshots_csv
from monitor.gpio_helpers import init_gpio, cleanup_gpio

def main():
    """Main entry point"""
    
    file_config = load_config_file()
    
    parser = argparse.ArgumentParser(
        prog='monitor',
        description='Simplified event-based water system monitor'
    )
    
    parser.add_argument('--events', 
                       default=file_config.get('EVENTS_FILE', DEFAULT_EVENTS_FILE),
                       help='Events CSV file (default: events.csv)')
    parser.add_argument('--snapshots',
                       default=file_config.get('SNAPSHOTS_FILE', DEFAULT_SNAPSHOTS_FILE),
                       help='Snapshots CSV file (default: snapshots.csv)')
    parser.add_argument('--debug', action='store_true',
                       help='Enable console output')
    parser.add_argument('--poll-interval', type=int,
                       default=file_config.get('POLL_INTERVAL', POLL_INTERVAL),
                       help='Pressure poll interval seconds (default: 5)')
    parser.add_argument('--tank-interval', type=int,
                       default=file_config.get('TANK_POLL_INTERVAL', TANK_POLL_INTERVAL // 60),
                       help='Tank check interval minutes (default: 1)')
    parser.add_argument('--snapshot-interval', type=int,
                       default=file_config.get('SNAPSHOT_INTERVAL', SNAPSHOT_INTERVAL),
                       help='Snapshot interval minutes: 15, 5, or 2 (default: 15)')
    parser.add_argument('--tank-url',
                       default=file_config.get('TANK_URL', TANK_URL),
                       help='Tank monitoring URL')
    parser.add_argument('--enable-purge', action='store_true',
                       help='Enable automatic filter purging')
    parser.add_argument('--version', action='version', 
                       version=f'%(prog)s {__version__}')
    
    args = parser.parse_args()
    
    # Initialize GPIO
    if not init_gpio():
        print("Warning: Could not initialize GPIO")
    
    # Initialize CSV files
    if initialize_events_csv(args.events):
        if args.debug:
            print(f"Created {args.events}")
    if initialize_snapshots_csv(args.snapshots):
        if args.debug:
            print(f"Created {args.snapshots}")
    
    if args.debug:
        print(f"\n=== Monitor v{__version__} ===")
        print(f"Events: {args.events}")
        print(f"Snapshots: {args.snapshots}")
        print(f"Snapshot interval: {args.snapshot_interval} min")
        print(f"Purge: {'Enabled' if args.enable_purge else 'Disabled'}")
        print()
    
    # Create and run monitor
    monitor = SimplifiedMonitor(
        args.events,
        args.snapshots,
        args.tank_url,
        args.debug,
        args.poll_interval,
        args.tank_interval * 60,
        args.snapshot_interval
    )
    
    if args.enable_purge:
        monitor.enable_relay_control()
    
    try:
        monitor.run()
    finally:
        cleanup_gpio()

if __name__ == "__main__":
    main()
