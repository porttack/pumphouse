#!/usr/bin/env python3
"""
Test script for Ambient Weather API integration
"""
import sys
sys.path.insert(0, '/home/pi/src/pumphouse')

from monitor.ambient_weather import get_weather_data
from monitor.config import AMBIENT_WEATHER_API_KEY, AMBIENT_WEATHER_APPLICATION_KEY, AMBIENT_WEATHER_MAC_ADDRESS

def test_ambient_weather():
    """Test fetching weather data from Ambient Weather API"""
    print("Testing Ambient Weather API integration...")
    print(f"API Key configured: {bool(AMBIENT_WEATHER_API_KEY)}")
    print(f"Application Key configured: {bool(AMBIENT_WEATHER_APPLICATION_KEY)}")
    print(f"MAC Address: {AMBIENT_WEATHER_MAC_ADDRESS or 'Auto-detect'}")
    print()

    if not AMBIENT_WEATHER_API_KEY or not AMBIENT_WEATHER_APPLICATION_KEY:
        print("ERROR: API keys not configured in secrets.conf")
        print("Please add the following to ~/.config/pumphouse/secrets.conf:")
        print("AMBIENT_WEATHER_API_KEY=your_api_key")
        print("AMBIENT_WEATHER_APPLICATION_KEY=your_application_key")
        return False

    print("Fetching weather data...")
    data = get_weather_data(
        AMBIENT_WEATHER_API_KEY,
        AMBIENT_WEATHER_APPLICATION_KEY,
        AMBIENT_WEATHER_MAC_ADDRESS,
        debug=True
    )

    print()
    print("Results:")
    print(f"  Status: {data['status']}")

    if data['status'] == 'success':
        print(f"  Outdoor Temperature: {data['outdoor_temp']}°F")
        print(f"  Indoor Temperature: {data['indoor_temp']}°F")
        print(f"  Outdoor Humidity: {data['outdoor_humidity']}%")
        print(f"  Indoor Humidity: {data['indoor_humidity']}%")
        if data['data_age']:
            print(f"  Data Age: {data['data_age']:.0f} seconds")
        print()
        print("✓ Success! Weather data fetched successfully.")
        return True
    else:
        print(f"  ✗ Failed to fetch weather data: {data['status']}")
        return False

if __name__ == "__main__":
    success = test_ambient_weather()
    sys.exit(0 if success else 1)
