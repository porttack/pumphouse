#!/usr/bin/env python3
"""
System status checker - displays current sensor states and tank level
"""
import argparse
from datetime import datetime

from monitor.config import TANK_URL, TANK_HEIGHT_INCHES, TANK_CAPACITY_GALLONS
from monitor.gpio_helpers import read_pressure, read_float_sensor, init_gpio, cleanup_gpio
from monitor.tank import get_tank_data

# Try to import temp/humidity sensor
try:
    import board
    import adafruit_ahtx0
    TEMP_SENSOR_AVAILABLE = True
except (ImportError, NotImplementedError):
    TEMP_SENSOR_AVAILABLE = False

def format_float_state(state):
    """Format float state with explanation"""
    if state == 'CLOSED/CALLING':
        return f"{state} (tank needs water)"
    elif state == 'OPEN/FULL':
        return f"{state} (tank is full)"
    else:
        return state

def format_pressure_state(state):
    """Format pressure state with explanation"""
    if state is None:
        return "UNKNOWN (cannot read sensor)"
    elif state:
        return "HIGH (≥10 PSI) - Water available"
    else:
        return "LOW (<10 PSI) - No water pressure"

def read_temp_humidity():
    """Read temperature and humidity from AHT20 sensor"""
    if not TEMP_SENSOR_AVAILABLE:
        return None, None

    try:
        i2c = board.I2C()
        sensor = adafruit_ahtx0.AHTx0(i2c)
        temp_c = sensor.temperature
        temp_f = (temp_c * 9/5) + 32
        humidity = sensor.relative_humidity
        return temp_f, humidity
    except Exception:
        return None, None

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        prog='monitor.check',
        description='Check current system status'
    )
    parser.add_argument('--url', default=TANK_URL,
                       help=f'Tank monitoring URL (default: config)')
    parser.add_argument('--no-gpio', action='store_true',
                       help='Skip GPIO sensor readings')

    args = parser.parse_args()

    # Initialize GPIO unless disabled
    gpio_available = False
    if not args.no_gpio:
        if init_gpio():
            gpio_available = True
        else:
            print("⚠️  Warning: Could not initialize GPIO\n")

    print("=" * 60)
    print("PUMPHOUSE SYSTEM STATUS")
    print("=" * 60)
    print(f"Checked at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Read GPIO sensors
    if gpio_available:
        print("SENSORS:")
        print("-" * 60)

        pressure = read_pressure()
        print(f"Pressure:  {format_pressure_state(pressure)}")

        float_state = read_float_sensor()
        print(f"Float:     {format_float_state(float_state)}")

        # Read temp/humidity if available
        temp_f, humidity = read_temp_humidity()
        if temp_f is not None:
            print(f"Temp:      {temp_f:.1f}°F")
            print(f"Humidity:  {humidity:.1f}%")

        print()

    # Fetch tank data
    print("TANK LEVEL:")
    print("-" * 60)

    data = get_tank_data(args.url)

    if data['status'] == 'success':
        if data['depth'] is not None:
            depth_line = f"Depth:       {data['depth']:.2f}\" / {TANK_HEIGHT_INCHES}\" ({data['percentage']:.1f}%)"
            if data['pt_percentage']:
                depth_line += f" (PT: {data['pt_percentage']}% {args.url})"
            print(depth_line)

            print(f"Gallons:     {data['gallons']:.0f} / {TANK_CAPACITY_GALLONS} gal")

            if data['last_updated']:
                age_seconds = (datetime.now() - data['last_updated']).total_seconds()
                age_minutes = int(age_seconds / 60)
                print(f"Data Age:    {age_minutes} min (updated {data['last_updated'].strftime('%H:%M')})")

            # Warnings
            if data['depth'] > TANK_HEIGHT_INCHES:
                overflow = data['depth'] - TANK_HEIGHT_INCHES
                print(f"\n⚠️  WARNING: Water level is {overflow:.2f}\" above normal tank height!")
        else:
            print("⚠️  Could not parse depth data from website")
    else:
        print(f"❌ Error: {data.get('error_message', 'Unknown error')}")

    print()
    print("=" * 60)

    # Cleanup
    if gpio_available:
        cleanup_gpio()

if __name__ == "__main__":
    main()
