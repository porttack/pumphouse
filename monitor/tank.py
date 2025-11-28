"""
Tank level monitoring via web scraping
"""
import re
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup

from monitor.config import TANK_HEIGHT_INCHES, TANK_CAPACITY_GALLONS
from monitor.gpio_helpers import read_float_sensor

def calculate_gallons(depth_inches):
    """Calculate gallons based on linear relationship"""
    if depth_inches is None:
        return None
    gallons = (depth_inches / TANK_HEIGHT_INCHES) * TANK_CAPACITY_GALLONS
    return gallons

def parse_last_updated(last_updated_text):
    """Parse 'X minutes, Y seconds ago' text"""
    current_time = datetime.now()
    minutes = 0
    
    minutes_match = re.search(r'(\d+)\s+minute', last_updated_text)
    if minutes_match:
        minutes = int(minutes_match.group(1))
    
    time_delta = timedelta(minutes=minutes)
    last_updated = current_time - time_delta
    last_updated = last_updated.replace(second=0, microsecond=0)
    
    return last_updated

def get_tank_data(url, timeout=10):
    """Scrape tank data from PT website"""
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')

        # Find PT percentage
        pt_percentage = None
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string and 'ptlevel' in script.string:
                match = re.search(r"level:\s*(\d+)", script.string)
                if match:
                    pt_percentage = int(match.group(1))
                    break

        # Find depth
        depth_inches = None
        inch_level_span = soup.find('span', class_='inchLevel')
        if inch_level_span:
            depth_inches = float(inch_level_span.get_text(strip=True))

        # Find last updated timestamp
        last_updated_timestamp = None
        updated_on_span = soup.find('span', class_='updated_on')
        if updated_on_span:
            last_updated_text = updated_on_span.get_text(strip=True)
            last_updated_timestamp = parse_last_updated(last_updated_text)

        # Calculate percentage and gallons
        percentage = None
        gallons = None
        if depth_inches is not None:
            percentage = (depth_inches / TANK_HEIGHT_INCHES) * 100
            percentage = round(percentage, 1)
            gallons = calculate_gallons(depth_inches)

        # Read float sensor
        float_state = read_float_sensor()

        return {
            'percentage': percentage,
            'pt_percentage': pt_percentage,
            'depth': depth_inches,
            'gallons': gallons,
            'last_updated': last_updated_timestamp,
            'float_state': float_state,
            'status': 'success'
        }
        
    except Exception as e:
        return {
            'percentage': None,
            'pt_percentage': None,
            'depth': None,
            'gallons': None,
            'last_updated': None,
            'float_state': read_float_sensor(),
            'status': 'error',
            'error_message': str(e)
        }
