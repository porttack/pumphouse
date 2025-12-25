#!/usr/bin/env python3
"""
System status checker - displays current sensor states and tank level
"""
import argparse
from datetime import datetime

from monitor.config import TANK_URL, TANK_HEIGHT_INCHES, TANK_CAPACITY_GALLONS, NTFY_TOPIC
from monitor.gpio_helpers import (
    read_pressure, read_float_sensor, init_gpio, cleanup_gpio,
    FLOAT_STATE_FULL, FLOAT_STATE_CALLING
)
from monitor.tank import get_tank_data
from monitor.relay import get_all_relay_status

# Try to import temp/humidity sensor
try:
    import board
    import adafruit_ahtx0
    TEMP_SENSOR_AVAILABLE = True
except (ImportError, NotImplementedError):
    TEMP_SENSOR_AVAILABLE = False

def format_float_state(state):
    """Format float state with explanation"""
    if state == FLOAT_STATE_CALLING:
        return f"{state} (tank needs water)"
    elif state == FLOAT_STATE_FULL:
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

def format_relay_state(name, state):
    """Format relay state with explanation"""
    descriptions = {
        'bypass': 'Emergency bypass valve (BCM 26)',
        'supply_override': 'Supply valve override (BCM 19)',
        'purge': 'Spindown filter purge (BCM 13)',
        'reserved': 'Reserved channel'
    }
    desc = descriptions.get(name, name)
    return f"{state:8s} - {desc}"

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

def test_notification():
    """Test ntfy.sh notification"""
    from monitor.ntfy import test_ping

    print("=" * 60)
    print("NTFY.SH NOTIFICATION TEST")
    print("=" * 60)
    print(f"Topic: {NTFY_TOPIC}")
    print(f"Sending test notification...\n")

    if test_ping(debug=True):
        print("\n✓ Test notification sent successfully!")
        print(f"\nCheck your ntfy.sh app for topic: {NTFY_TOPIC}")
        print("  - iOS: Download 'ntfy' from App Store")
        print("  - Android: Download 'ntfy' from Play Store")
        print("  - Web: Open https://ntfy.sh in your browser")
        print(f"\nSubscribe to topic: {NTFY_TOPIC}")
    else:
        print("\n✗ Failed to send test notification")
        print("\nPossible issues:")
        print("  1. NTFY_TOPIC is not configured (still set to 'pumphouse-CHANGE-ME')")
        print("  2. No internet connection")
        print("  3. ntfy.sh server is down")
        print("\nCheck NTFY_TOPIC and NTFY_SERVER settings in config.py")

    print("=" * 60)

def test_email():
    """Test email notification"""
    from monitor.email_notifier import test_email
    from monitor.config import (
        ENABLE_EMAIL_NOTIFICATIONS,
        EMAIL_TO,
        EMAIL_FROM,
        EMAIL_SMTP_SERVER,
        EMAIL_SMTP_PORT,
        EMAIL_SMTP_USER,
        EMAIL_SMTP_PASSWORD
    )

    print("=" * 60)
    print("EMAIL NOTIFICATION TEST")
    print("=" * 60)
    print(f"Enabled:      {ENABLE_EMAIL_NOTIFICATIONS}")
    print(f"From:         {EMAIL_FROM if EMAIL_FROM else '(not configured)'}")
    print(f"To:           {EMAIL_TO if EMAIL_TO else '(not configured)'}")
    print(f"SMTP Server:  {EMAIL_SMTP_SERVER}:{EMAIL_SMTP_PORT}")
    print(f"SMTP User:    {EMAIL_SMTP_USER if EMAIL_SMTP_USER else '(not configured)'}")
    print(f"SMTP Pass:    {'(configured)' if EMAIL_SMTP_PASSWORD else '(not configured)'}")
    print()

    if not ENABLE_EMAIL_NOTIFICATIONS:
        print("⚠️  Email notifications are disabled in config.py")
        print("   Set ENABLE_EMAIL_NOTIFICATIONS = True to enable")
        print("=" * 60)
        return

    if not all([EMAIL_FROM, EMAIL_TO, EMAIL_SMTP_USER, EMAIL_SMTP_PASSWORD]):
        print("❌ Email not fully configured!")
        print("\nPlease configure in config.py:")
        if not EMAIL_FROM:
            print("  - EMAIL_FROM")
        if not EMAIL_TO:
            print("  - EMAIL_TO")
        if not EMAIL_SMTP_USER:
            print("  - EMAIL_SMTP_USER")
        if not EMAIL_SMTP_PASSWORD:
            print("  - EMAIL_SMTP_PASSWORD")
        print("\nFor Gmail, you need to create an App Password:")
        print("  1. Go to https://myaccount.google.com/security")
        print("  2. Enable 2-Step Verification (if not already enabled)")
        print("  3. Go to https://myaccount.google.com/apppasswords")
        print("  4. Create an App Password for 'Mail'")
        print("  5. Copy the 16-character password to EMAIL_SMTP_PASSWORD")
        print("=" * 60)
        return

    print("Sending test email...\n")

    if test_email(debug=True):
        print("\n✓ Test email sent successfully!")
        print(f"\nCheck your inbox at: {EMAIL_TO}")
        print("Note: Check spam folder if you don't see it")
    else:
        print("\n✗ Failed to send test email")
        print("\nPossible issues:")
        print("  1. Incorrect SMTP credentials")
        print("  2. Using regular password instead of App Password (Gmail)")
        print("  3. SMTP server or port is incorrect")
        print("  4. No internet connection")
        print("  5. Email blocked by server/firewall")
        print("\nFor Gmail troubleshooting:")
        print("  - Make sure you're using an App Password, not your Google password")
        print("  - Check https://myaccount.google.com/apppasswords")
        print("  - Verify 2-Step Verification is enabled")

    print("=" * 60)

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
    parser.add_argument('--test-notification', action='store_true',
                       help='Send a test notification to ntfy.sh')
    parser.add_argument('--test-email', action='store_true',
                       help='Send a test email notification')

    args = parser.parse_args()

    # Handle test notification command
    if args.test_notification:
        test_notification()
        return

    # Handle test email command
    if args.test_email:
        test_email()
        return

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

    # Read GPIO sensors (works even if GPIO init failed, uses gpio command fallback)
    print("SENSORS:")
    print("-" * 60)

    pressure = read_pressure()
    print(f"Pressure:  {format_pressure_state(pressure)} (BCM 17)")

    float_state = read_float_sensor()
    print(f"Float:     {format_float_state(float_state)} (BCM 21)")

    # Read temp/humidity if available (only works with GPIO init)
    if gpio_available:
        temp_f, humidity = read_temp_humidity()
        if temp_f is not None:
            print(f"Temp:      {temp_f:.1f}°F")
            print(f"Humidity:  {humidity:.1f}%")

    print()

    # Read relay status (if pins are configured as outputs)
    print("RELAYS:")
    print("-" * 60)

    relay_status = get_all_relay_status()
    print(f"Bypass:    {format_relay_state('bypass', relay_status['bypass'])}")
    print(f"Override:  {format_relay_state('supply_override', relay_status['supply_override'])}")
    print(f"Purge:     {format_relay_state('purge', relay_status['purge'])}")
    print(f"Reserved:  {format_relay_state('reserved', relay_status['reserved'])}")

    if relay_status['bypass'] == 'N/A':
        print("Note:      Relay pins not configured (monitor not running)")

    print()

    # Fetch tank data
    print("TANK LEVEL:")
    print("-" * 60)

    data = get_tank_data(args.url)

    if data['status'] == 'success':
        if data['depth'] is not None:
            depth_line = f"Depth:       {data['depth']:.2f}\" / {TANK_HEIGHT_INCHES}\" ({data['percentage']:.1f}%)"
            if data['pt_percentage']:
                depth_line += f" (PT: {data['pt_percentage']}%)"
            print(depth_line)

            print(f"Gallons:     {data['gallons']:.0f} / {TANK_CAPACITY_GALLONS} gal")

            if data['last_updated']:
                age_seconds = (datetime.now() - data['last_updated']).total_seconds()
                age_minutes = int(age_seconds / 60)
                print(f"Data Age:    {age_minutes} min (updated {data['last_updated'].strftime('%H:%M')})")
                print(f"PT URL:      {args.url}")

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
