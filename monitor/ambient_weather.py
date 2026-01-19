"""
Ambient Weather API integration
"""
import requests
from datetime import datetime

def get_weather_data(api_key, application_key, mac_address=None, debug=False):
    """
    Fetch current weather data from Ambient Weather API

    Args:
        api_key: User's API key from ambientweather.net account
        application_key: Developer application key
        mac_address: Optional MAC address of specific device. If None, uses first device.
        debug: Enable debug output

    Returns:
        dict with status, outdoor_temp, indoor_temp, outdoor_humidity, indoor_humidity, data_age
    """
    base_url = "https://rt.ambientweather.net/v1"

    # First, get list of devices if no MAC address provided
    if not mac_address:
        devices_url = f"{base_url}/devices"
        params = {
            'apiKey': api_key,
            'applicationKey': application_key
        }

        try:
            if debug:
                print(f"Fetching device list from Ambient Weather...")

            response = requests.get(devices_url, params=params, timeout=10)

            if response.status_code == 429:
                if debug:
                    print("  Rate limit exceeded")
                return {
                    'status': 'rate_limit',
                    'outdoor_temp': None,
                    'indoor_temp': None,
                    'outdoor_humidity': None,
                    'indoor_humidity': None,
                    'baro_abs': None,
                    'baro_rel': None,
                    'wind_speed': None,
                    'wind_gust': None,
                    'data_age': None
                }

            if response.status_code != 200:
                if debug:
                    print(f"  HTTP error: {response.status_code}")
                return {
                    'status': 'error',
                    'outdoor_temp': None,
                    'indoor_temp': None,
                    'outdoor_humidity': None,
                    'indoor_humidity': None,
                    'data_age': None
                }

            devices = response.json()

            if not devices or len(devices) == 0:
                if debug:
                    print("  No devices found")
                return {
                    'status': 'no_devices',
                    'outdoor_temp': None,
                    'indoor_temp': None,
                    'outdoor_humidity': None,
                    'indoor_humidity': None,
                    'baro_abs': None,
                    'baro_rel': None,
                    'wind_speed': None,
                    'wind_gust': None,
                    'data_age': None
                }

            # Use first device
            device = devices[0]
            mac_address = device.get('macAddress')

            if debug:
                print(f"  Found device: {mac_address}")

        except requests.exceptions.Timeout:
            if debug:
                print("  Request timeout")
            return {
                'status': 'timeout',
                'outdoor_temp': None,
                'indoor_temp': None,
                'outdoor_humidity': None,
                'indoor_humidity': None,
                'data_age': None
            }
        except requests.exceptions.RequestException as e:
            if debug:
                print(f"  Request error: {e}")
            return {
                'status': 'error',
                'outdoor_temp': None,
                'indoor_temp': None,
                'outdoor_humidity': None,
                'indoor_humidity': None,
                'baro_abs': None,
                'baro_rel': None,
                'wind_speed': None,
                'wind_gust': None,
                'data_age': None
            }

    # Now fetch device data (most recent reading)
    device_url = f"{base_url}/devices/{mac_address}"
    params = {
        'apiKey': api_key,
        'applicationKey': application_key,
        'limit': 1  # Only get most recent reading
    }

    try:
        if debug:
            print(f"Fetching weather data for device {mac_address}...")

        response = requests.get(device_url, params=params, timeout=10)

        if response.status_code == 429:
            if debug:
                print("  Rate limit exceeded")
            return {
                'status': 'rate_limit',
                'outdoor_temp': None,
                'indoor_temp': None,
                'outdoor_humidity': None,
                'indoor_humidity': None,
                'data_age': None
            }

        if response.status_code != 200:
            if debug:
                print(f"  HTTP error: {response.status_code}")
            return {
                'status': 'error',
                'outdoor_temp': None,
                'indoor_temp': None,
                'outdoor_humidity': None,
                'indoor_humidity': None,
                'baro_abs': None,
                'baro_rel': None,
                'wind_speed': None,
                'wind_gust': None,
                'data_age': None
            }

        data = response.json()

        if not data or len(data) == 0:
            if debug:
                print("  No data available")
            return {
                'status': 'no_data',
                'outdoor_temp': None,
                'indoor_temp': None,
                'outdoor_humidity': None,
                'indoor_humidity': None,
                'baro_abs': None,
                'baro_rel': None,
                'wind_speed': None,
                'wind_gust': None,
                'data_age': None
            }

        # Get most recent reading (first item in array)
        reading = data[0]

        # Extract temperature and humidity values
        outdoor_temp = reading.get('tempf')  # Outdoor temperature in Fahrenheit
        indoor_temp = reading.get('tempinf')  # Indoor temperature in Fahrenheit
        outdoor_humidity = reading.get('humidity')  # Outdoor humidity percentage
        indoor_humidity = reading.get('humidityin')  # Indoor humidity percentage

        # Extract barometric pressure
        baro_abs = reading.get('baromabsin')  # Absolute barometric pressure in inHg
        baro_rel = reading.get('baromrelin')  # Relative (sea-level adjusted) barometric pressure in inHg

        # Extract wind data
        wind_speed = reading.get('windspeedmph')  # Wind speed in MPH
        wind_gust = reading.get('windgustmph')  # Wind gust in MPH

        # Calculate data age
        data_age = None
        if 'dateutc' in reading:
            try:
                # dateutc is in milliseconds since epoch
                reading_time = reading['dateutc'] / 1000
                data_age = datetime.now().timestamp() - reading_time
            except (ValueError, TypeError):
                pass

        if debug:
            print(f"  Outdoor temp: {outdoor_temp}°F")
            print(f"  Indoor temp: {indoor_temp}°F")
            print(f"  Outdoor humidity: {outdoor_humidity}%")
            print(f"  Indoor humidity: {indoor_humidity}%")
            print(f"  Barometric pressure (abs): {baro_abs} inHg")
            print(f"  Barometric pressure (rel): {baro_rel} inHg")
            print(f"  Wind speed: {wind_speed} mph")
            print(f"  Wind gust: {wind_gust} mph")
            if data_age is not None:
                print(f"  Data age: {data_age:.0f}s")

        return {
            'status': 'success',
            'outdoor_temp': outdoor_temp,
            'indoor_temp': indoor_temp,
            'outdoor_humidity': outdoor_humidity,
            'indoor_humidity': indoor_humidity,
            'baro_abs': baro_abs,
            'baro_rel': baro_rel,
            'wind_speed': wind_speed,
            'wind_gust': wind_gust,
            'data_age': data_age
        }

    except requests.exceptions.Timeout:
        if debug:
            print("  Request timeout")
        return {
            'status': 'timeout',
            'outdoor_temp': None,
            'indoor_temp': None,
            'outdoor_humidity': None,
            'indoor_humidity': None,
            'baro_abs': None,
            'baro_rel': None,
            'wind_speed': None,
            'wind_gust': None,
            'data_age': None
        }
    except requests.exceptions.RequestException as e:
        if debug:
            print(f"  Request error: {e}")
        return {
            'status': 'error',
            'outdoor_temp': None,
            'indoor_temp': None,
            'outdoor_humidity': None,
            'indoor_humidity': None,
            'data_age': None
        }
    except Exception as e:
        if debug:
            print(f"  Unexpected error: {e}")
        return {
            'status': 'error',
            'outdoor_temp': None,
            'indoor_temp': None,
            'outdoor_humidity': None,
            'indoor_humidity': None,
            'data_age': None
        }
