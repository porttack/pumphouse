#!/usr/bin/env python3

import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime, timedelta

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except (ImportError, RuntimeError):
    GPIO_AVAILABLE = False

# Tank configuration
TANK_HEIGHT_INCHES = 58  # Height at which tank is 100% full
TANK_CAPACITY_GALLONS = 1400  # Capacity at 58 inches

# GPIO Configuration
FLOAT_PIN = 21  # BCM pin 21 for float sensor
PRESSURE_PIN = 17  # BCM pin 17 for pressure sensor

def calculate_gallons(depth_inches):
    """
    Calculate gallons based on linear relationship
    58 inches = 1400 gallons
    
    Args:
        depth_inches (float): Depth of liquid in inches
        
    Returns:
        float: Volume in gallons
    """
    gallons = (depth_inches / TANK_HEIGHT_INCHES) * TANK_CAPACITY_GALLONS
    return gallons

def parse_last_updated(last_updated_text):
    """
    Parse "X minutes, Y seconds ago" text and return the actual timestamp
    Rounds down to the nearest minute
    
    Args:
        last_updated_text (str): Text like "3 minutes, 20 seconds ago"
        
    Returns:
        datetime: The timestamp when data was last updated (rounded to minute)
    """
    current_time = datetime.now()
    
    # Extract minutes (we'll ignore seconds since updates are ~15 min intervals)
    minutes = 0
    
    # Look for minutes
    minutes_match = re.search(r'(\d+)\s+minute', last_updated_text)
    if minutes_match:
        minutes = int(minutes_match.group(1))
    
    # Calculate the actual update time and round down to nearest minute
    time_delta = timedelta(minutes=minutes)
    last_updated = current_time - time_delta
    
    # Round down to the nearest minute (zero out seconds and microseconds)
    last_updated = last_updated.replace(second=0, microsecond=0)
    
    return last_updated

def read_float_sensor():
    """
    Read the float sensor state
    
    Returns:
        str: 'OPEN/FULL', 'CLOSED/CALLING', or 'UNKNOWN'
    """
    if not GPIO_AVAILABLE:
        return 'UNKNOWN'
    
    try:
        # Setup GPIO if not already done
        if GPIO.getmode() is None:
            GPIO.setmode(GPIO.BCM)
        
        # Setup the pin with pull-up resistor
        GPIO.setup(FLOAT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        
        # Read the state
        state = GPIO.input(FLOAT_PIN)
        
        # HIGH = Float switch OPEN = Tank is FULL
        # LOW = Float switch CLOSED = Tank NOT full (calling for water)
        if state == GPIO.HIGH:
            return 'OPEN/FULL (no water needed)'
        else:
            return 'CLOSED/CALLING'
            
    except Exception as e:
        # GPIO conflict or error
        return 'UNKNOWN'

def read_pressure_sensor():
    """
    Read the pressure sensor state
    
    Returns:
        str: 'HIGH' (>10 PSI), 'LOW' (<10 PSI), or 'UNKNOWN'
    """
    if not GPIO_AVAILABLE:
        return 'UNKNOWN'
    
    try:
        # Setup GPIO if not already done
        if GPIO.getmode() is None:
            GPIO.setmode(GPIO.BCM)
        
        # Setup the pin with pull-up resistor
        GPIO.setup(PRESSURE_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        
        # Read the state
        state = GPIO.input(PRESSURE_PIN)
        
        # NC pressure switch:
        # HIGH = Switch OPEN = Pressure >= 10 PSI
        # LOW = Switch CLOSED = Pressure < 10 PSI
        if state == GPIO.HIGH:
            return 'HIGH (> 10 PSI) - WATER AVAILABLE'
        else:
            return 'LOW (< 10 PSI) - NO WATER PRESSURE'
            
    except Exception as e:
        # GPIO conflict or error
        return 'UNKNOWN'

def get_tank_data(url):
    """
    Scrape tank depth and calculate actual percentage based on tank height
    
    Args:
        url (str): The URL to scrape
        
    Returns:
        dict: Dictionary containing percentage and depth values as numbers
    """
    try:
        # Send GET request
        response = requests.get(url)
        response.raise_for_status()
        
        # Parse HTML
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Get current timestamp
        current_timestamp = datetime.now()
        
        # Find the PT percentage from JavaScript
        pt_percentage = None
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string and 'ptlevel' in script.string:
                # Look for pattern like: level:84
                match = re.search(r"level:\s*(\d+)", script.string)
                if match:
                    pt_percentage = int(match.group(1))
                    break
        
        # Find the depth of liquid (in inches)
        depth_inches = None
        inch_level_span = soup.find('span', class_='inchLevel')
        if inch_level_span:
            depth_inches = float(inch_level_span.get_text(strip=True))
        
        # Find the last updated time
        last_updated_timestamp = None
        updated_on_span = soup.find('span', class_='updated_on')
        if updated_on_span:
            last_updated_text = updated_on_span.get_text(strip=True)
            last_updated_timestamp = parse_last_updated(last_updated_text)
        
        # Calculate actual percentage based on tank height
        percentage = None
        gallons = None
        if depth_inches is not None:
            percentage = (depth_inches / TANK_HEIGHT_INCHES) * 100
            percentage = round(percentage, 1)
            gallons = calculate_gallons(depth_inches)
        
        # Read sensors
        float_state = read_float_sensor()
        pressure_state = read_pressure_sensor()
        
        return {
            'percentage': percentage,
            'pt_percentage': pt_percentage,
            'depth': depth_inches,
            'gallons': gallons,
            'current_timestamp': current_timestamp,
            'last_updated': last_updated_timestamp,
            'float_state': float_state,
            'pressure_state': pressure_state,
            'status': 'success'
        }
        
    except requests.RequestException as e:
        return {
            'percentage': None,
            'pt_percentage': None,
            'depth': None,
            'gallons': None,
            'current_timestamp': datetime.now(),
            'last_updated': None,
            'float_state': read_float_sensor(),
            'pressure_state': read_pressure_sensor(),
            'status': 'error',
            'error_message': str(e)
        }
    except ValueError as e:
        return {
            'percentage': None,
            'pt_percentage': None,
            'depth': None,
            'gallons': None,
            'current_timestamp': datetime.now(),
            'last_updated': None,
            'float_state': read_float_sensor(),
            'pressure_state': read_pressure_sensor(),
            'status': 'error',
            'error_message': f"Could not parse value: {e}"
        }

if __name__ == "__main__":
    url = "https://www.mypt.in/s/REDACTED-TANK-URL"
    
    print("Fetching tank data...")
    data = get_tank_data(url)
    
    if data['status'] == 'success':
        print(f"\nTank Status:")
        print(f"Depth: {data['depth']:.2f} in")
        print(f"Percentage: {data['percentage']}%")
        print(f"PT Percentage: {data['pt_percentage']}%")
        print(f"Gallons: {data['gallons']:.0f} gal")
        print(f"Float State: {data['float_state']}")
        print(f"Pressure State: {data['pressure_state']}")
        if data['last_updated']:
            print(f"Last Updated: {data['last_updated'].isoformat()}")
        
        # Show warning if over 100%
        if data['depth'] and data['depth'] > TANK_HEIGHT_INCHES:
            print(f"⚠️  Warning: Water level is {data['depth'] - TANK_HEIGHT_INCHES:.2f} inches above normal tank height")
    else:
        print(f"\nError: {data['error_message']}")
        print(f"Float State: {data['float_state']}")
        print(f"Pressure State: {data['pressure_state']}")
    
    # Clean up GPIO if it was used
    if GPIO_AVAILABLE:
        try:
            GPIO.cleanup()
        except:
            pass