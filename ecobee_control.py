#!/usr/bin/env python3
"""
Manual Ecobee Control Script

Provides a simple interface for manual thermostat control.

IMPORTANT LIMITATIONS:
    - set_temperature() currently uses "Home and hold" preset (ignores heat/cool args)
    - Vacation mode enable/disable opens the UI but doesn't complete the form
    - Precise temperature control requires Ecobee API access (not available)

Usage:
    # Get all thermostat data
    ./ecobee_control.py status

    # Get specific thermostat
    ./ecobee_control.py status --thermostat "Living Room Ecobee"

    # Set temperature to "Home" preset (indefinite hold)
    ./ecobee_control.py set --thermostat "Living Room Ecobee"

    # Reset all thermostats to "Home" preset
    ./ecobee_control.py reset

    # Open vacation mode interface (manual completion required)
    ./ecobee_control.py vacation --enable --end "2024-12-30"

    # Delete all vacations
    ./ecobee_control.py vacation --disable

    # Delete only the first vacation
    ./ecobee_control.py vacation --disable --first-only

    # Delete a specific vacation by name
    ./ecobee_control.py vacation --disable --name "Holiday Trip"
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from monitor.ecobee import EcobeeController, DEFAULT_TEMPERATURES


def cmd_status(args):
    """Show thermostat status."""
    with EcobeeController(headless=not args.show_browser, debug=args.debug) as ecobee:
        if args.thermostat:
            tstat = ecobee.get_thermostat(args.thermostat)
            if not tstat:
                print(f"ERROR: Thermostat '{args.thermostat}' not found")
                return 1

            thermostats = [tstat]
        else:
            thermostats = ecobee.get_all_thermostats()

        if args.json:
            print(json.dumps(thermostats, indent=2))
        else:
            print(f"\nFound {len(thermostats)} thermostat(s):")
            print("=" * 60)

            for tstat in thermostats:
                print(f"\n{tstat['name']}:")
                print(f"  Current Temperature: {tstat['temperature']}°F")
                print(f"  Heat Setpoint: {tstat['heat_setpoint']}°F" if tstat['heat_setpoint'] else "  Heat Setpoint: N/A")
                print(f"  Cool Setpoint: {tstat['cool_setpoint']}°F" if tstat['cool_setpoint'] else "  Cool Setpoint: N/A")
                print(f"  System Mode: {tstat['system_mode']}")
                print(f"  Hold: {tstat['hold_text'] or 'None'}")
                print(f"  Vacation Mode: {'Yes' if tstat['vacation_mode'] else 'No'}")

    return 0


def cmd_set(args):
    """Set thermostat temperature."""
    if not args.thermostat:
        print("ERROR: --thermostat required")
        return 1

    if args.heat is None and args.cool is None:
        print("ERROR: At least one of --heat or --cool required")
        return 1

    with EcobeeController(headless=not args.show_browser, debug=args.debug) as ecobee:
        print(f"\nSetting temperature for '{args.thermostat}'...")
        if args.heat:
            print(f"  Heat: {args.heat}°F")
        if args.cool:
            print(f"  Cool: {args.cool}°F")
        print(f"  Hold Type: {args.hold_type}")

        try:
            ecobee.set_temperature(
                args.thermostat,
                heat=args.heat,
                cool=args.cool,
                hold_type=args.hold_type
            )
            print("\n✓ Temperature set successfully")
            return 0
        except Exception as e:
            print(f"\n✗ Error: {e}")
            return 1


def cmd_reset(args):
    """Reset all thermostats to default temperatures."""
    with EcobeeController(headless=not args.show_browser, debug=args.debug) as ecobee:
        print("\nResetting thermostats to defaults:")
        print("=" * 60)

        for name, temps in DEFAULT_TEMPERATURES.items():
            print(f"\n{name}:")
            print(f"  Heat: {temps['heat']}°F")
            print(f"  Cool: {temps['cool']}°F")

            try:
                ecobee.set_temperature(name, heat=temps['heat'], cool=temps['cool'])
                print(f"  ✓ Set successfully")
            except Exception as e:
                print(f"  ✗ Error: {e}")

    return 0


def cmd_vacation(args):
    """Enable or disable vacation mode."""
    if args.enable and args.disable:
        print("ERROR: Cannot use --enable and --disable together")
        return 1

    if not args.enable and not args.disable:
        print("ERROR: Must specify --enable or --disable")
        return 1

    with EcobeeController(headless=not args.show_browser, debug=args.debug) as ecobee:
        if args.enable:
            # Default: one-month vacation if --end not provided
            start_date = datetime.fromisoformat(args.start) if args.start else None
            end_date = datetime.fromisoformat(args.end) if args.end else None

            print(f"\nEnabling vacation mode:")
            print(f"  Start: {start_date or 'Now'}")
            print(f"  End: {end_date}")
            print(f"  Heat: {args.heat}°F")
            print(f"  Cool: {args.cool}°F")

            try:
                ecobee.enable_vacation_mode(
                    start_date=start_date,
                    end_date=end_date,
                    heat=args.heat,
                    cool=args.cool
                )
                print("\n✓ Vacation mode enabled")
                return 0
            except Exception as e:
                print(f"\n✗ Error: {e}")
                return 1

        else:  # disable
            print("\nDisabling vacation mode...")

            try:
                deleted = ecobee.disable_vacation_mode(
                    vacation_name=args.name,
                    delete_all=not args.first_only
                )

                if deleted > 0:
                    print(f"✓ Deleted {deleted} vacation(s)")
                else:
                    print("No vacations found to delete")

                return 0
            except Exception as e:
                print(f"✗ Error: {e}")
                return 1


def main():
    parser = argparse.ArgumentParser(description='Manual Ecobee Control')
    parser.add_argument('--debug', action='store_true', help='Enable debug output')
    parser.add_argument('--show-browser', action='store_true', help='Show browser window')

    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # Status command
    status_parser = subparsers.add_parser('status', help='Show thermostat status')
    status_parser.add_argument('--thermostat', help='Show specific thermostat only')
    status_parser.add_argument('--json', action='store_true', help='Output as JSON')

    # Set command
    set_parser = subparsers.add_parser('set', help='Set thermostat temperature')
    set_parser.add_argument('--thermostat', required=True, help='Thermostat name')
    set_parser.add_argument('--heat', type=float, help='Heat setpoint')
    set_parser.add_argument('--cool', type=float, help='Cool setpoint')
    set_parser.add_argument('--hold-type', default='indefinite', help='Hold type (default: indefinite)')

    # Reset command
    reset_parser = subparsers.add_parser('reset', help='Reset all thermostats to defaults')

    # Vacation command
    vacation_parser = subparsers.add_parser('vacation', help='Control vacation mode')
    vacation_parser.add_argument('--enable', action='store_true', help='Enable vacation mode')
    vacation_parser.add_argument('--disable', action='store_true', help='Disable vacation mode (delete vacations)')
    vacation_parser.add_argument('--start', help='Start date (YYYY-MM-DD)')
    vacation_parser.add_argument('--end', help='End date (YYYY-MM-DD)')
    vacation_parser.add_argument('--heat', type=float, default=55, help='Heat setpoint (default: 55)')
    vacation_parser.add_argument('--cool', type=float, default=85, help='Cool setpoint (default: 85)')
    vacation_parser.add_argument('--name', help='Specific vacation name to delete (only with --disable)')
    vacation_parser.add_argument('--first-only', action='store_true', help='Delete only first vacation (only with --disable)')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Route to command handler
    if args.command == 'status':
        return cmd_status(args)
    elif args.command == 'set':
        return cmd_set(args)
    elif args.command == 'reset':
        return cmd_reset(args)
    elif args.command == 'vacation':
        return cmd_vacation(args)
    else:
        parser.print_help()
        return 1


if __name__ == '__main__':
    sys.exit(main())
