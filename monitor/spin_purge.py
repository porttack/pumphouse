#!/usr/bin/env python3
"""
Standalone script to purge the spindown filter.
Can be run manually or called from monitoring system.

Usage:
    python -m monitor.spin_purge [--duration SECONDS] [--debug]
    
Examples:
    python -m monitor.spin_purge
    python -m monitor.spin_purge --duration 45 --debug
"""
import argparse
import sys

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("Error: RPi.GPIO not available", file=sys.stderr)
    sys.exit(1)

from monitor.relay import init_relays, purge_spindown_filter, cleanup_relays, DEFAULT_PURGE_DURATION

def main():
    parser = argparse.ArgumentParser(
        description='Purge spindown sediment filter',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
This script opens the spindown filter purge valve for a specified duration
to flush accumulated sediment.

Hardware:
  - Purge valve on BCM pin 13 (physical pin 33)
  - Relay is ACTIVE LOW (writing LOW/0 opens valve)
  
Safety:
  - Valve automatically closes after specified duration
  - Valve closes on any error or interrupt
        '''
    )
    
    parser.add_argument(
        '--duration',
        type=int,
        default=DEFAULT_PURGE_DURATION,
        help=f'Purge duration in seconds (default: {DEFAULT_PURGE_DURATION})'
    )
    
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Print debug messages'
    )
    
    args = parser.parse_args()
    
    # Validate duration
    if args.duration < 1 or args.duration > 300:
        print("Error: Duration must be between 1 and 300 seconds", file=sys.stderr)
        sys.exit(1)
    
    # Initialize relays
    if not init_relays():
        print("Error: Could not initialize relay control", file=sys.stderr)
        sys.exit(1)
    
    try:
        print(f"Starting spindown filter purge ({args.duration} seconds)...")
        
        success = purge_spindown_filter(duration=args.duration, debug=args.debug)
        
        if success:
            print("✓ Purge completed successfully")
            return 0
        else:
            print("✗ Purge failed", file=sys.stderr)
            return 1
            
    except KeyboardInterrupt:
        print("\n\nInterrupted by user - closing valve...")
        cleanup_relays()
        return 130
        
    except Exception as e:
        print(f"\nError during purge: {e}", file=sys.stderr)
        cleanup_relays()
        return 1
        
    finally:
        cleanup_relays()

if __name__ == '__main__':
    sys.exit(main())