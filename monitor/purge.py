#!/usr/bin/env python3
"""
Standalone spindown filter purge script
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
    parser = argparse.ArgumentParser(description='Purge spindown sediment filter')
    parser.add_argument('--duration', type=int, default=DEFAULT_PURGE_DURATION,
                       help=f'Purge duration in seconds (default: {DEFAULT_PURGE_DURATION})')
    parser.add_argument('--debug', action='store_true',
                       help='Print debug messages')
    
    args = parser.parse_args()
    
    if args.duration < 1 or args.duration > 300:
        print("Error: Duration must be between 1 and 300 seconds", file=sys.stderr)
        sys.exit(1)
    
    if not init_relays():
        print("Error: Could not initialize relay control", file=sys.stderr)
        sys.exit(1)
    
    try:
        print(f"Starting spindown filter purge ({args.duration} seconds)...")
        
        if purge_spindown_filter(duration=args.duration, debug=args.debug):
            print("✓ Purge completed successfully")
            return 0
        else:
            print("✗ Purge failed", file=sys.stderr)
            return 1
            
    except KeyboardInterrupt:
        print("\n\nInterrupted - closing valve...")
        cleanup_relays()
        return 130
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        cleanup_relays()
        return 1
    finally:
        cleanup_relays()

if __name__ == '__main__':
    sys.exit(main())
