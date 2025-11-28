"""
Tank level monitoring via web scraping and float sensor
"""
import re
import threading
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup

from monitor.config import TANK_HEIGHT_INCHES, TANK_CAPACITY_GALLONS, TANK_CHANGE_THRESHOLD
from monitor.gpio_helpers import read_float_sensor, read_pressure

def calculate_gallons(depth_inches):
    """
    Calculate gallons based on linear relationship.
    58 inches = 1400 gallons
    """
    if depth_inches is None:
        return None
    gallons = (depth_inches / TANK_HEIGHT_INCHES) * TANK_CAPACITY_GALLONS
    return gallons

def parse_last_updated(last_updated_text):
    """
    Parse 'X minutes, Y seconds ago' text and return the actual timestamp.
    Rounds down to the nearest minute.
    """
    current_time = datetime.now()
    minutes = 0
    
    # Look for minutes
    minutes_match = re.search(r'(\d+)\s+minute', last_updated_text)
    if minutes_match:
        minutes = int(minutes_match.group(1))
    
    # Calculate the actual update time and round down to nearest minute
    time_delta = timedelta(minutes=minutes)
    last_updated = current_time - time_delta
    last_updated = last_updated.replace(second=0, microsecond=0)
    
    return last_updated

def get_tank_data(url, timeout=10):
    """
    Scrape tank data from PT website.
    
    Returns dict with:
        status: 'success' or 'error'
        depth, percentage, pt_percentage, gallons, last_updated, float_state
        error_message (if status='error')
    """
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        current_timestamp = datetime.now()
        
        # Find PT percentage from JavaScript
        pt_percentage = None
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string and 'ptlevel' in script.string:
                match = re.search(r"level:\s*(\d+)", script.string)
                if match:
                    pt_percentage = int(match.group(1))
                    break
        
        # Find depth of liquid (in inches)
        depth_inches = None
        inch_level_span = soup.find('span', class_='inchLevel')
        if inch_level_span:
            depth_inches = float(inch_level_span.get_text(strip=True))
        
        # Find last updated time
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
        
    except requests.Timeout:
        return {
            'percentage': None,
            'pt_percentage': None,
            'depth': None,
            'gallons': None,
            'last_updated': None,
            'float_state': read_float_sensor(),
            'status': 'error',
            'error_message': f"Timeout after {timeout}s"
        }
    except requests.ConnectionError as e:
        return {
            'percentage': None,
            'pt_percentage': None,
            'depth': None,
            'gallons': None,
            'last_updated': None,
            'float_state': read_float_sensor(),
            'status': 'error',
            'error_message': f"Connection error: {str(e)}"
        }
    except requests.HTTPError as e:
        return {
            'percentage': None,
            'pt_percentage': None,
            'depth': None,
            'gallons': None,
            'last_updated': None,
            'float_state': read_float_sensor(),
            'status': 'error',
            'error_message': f"HTTP {e.response.status_code}: {str(e)}"
        }
    except ValueError as e:
        return {
            'percentage': None,
            'pt_percentage': None,
            'depth': None,
            'gallons': None,
            'last_updated': None,
            'float_state': read_float_sensor(),
            'status': 'error',
            'error_message': f"Parse error: {str(e)}"
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
            'error_message': f"Unexpected error: {str(e)}"
        }

class TankMonitor(threading.Thread):
    """Thread for monitoring tank level periodically"""
    
    def __init__(self, system_state, url, interval, change_log_file=None, 
                 debug=False, max_consecutive_errors=10, change_threshold=TANK_CHANGE_THRESHOLD):
        super().__init__(daemon=True)
        self.system_state = system_state
        self.url = url
        self.interval = interval
        self.change_log_file = change_log_file
        self.debug = debug
        self.max_consecutive_errors = max_consecutive_errors
        self.change_threshold = change_threshold
        self.running = True
    
    def stop(self):
        """Stop the monitoring thread"""
        self.running = False
    
    def run(self):
        """Periodically fetch tank data and update system state"""
        from monitor.logger import log_tank_change
        
        consecutive_errors = 0
        
        while self.running:
            try:
                data = get_tank_data(self.url)
                
                if data['status'] == 'success':
                    # Reset error counter on success
                    if consecutive_errors > 0:
                        if self.debug:
                            print(f"\n✓ Tank monitoring recovered after {consecutive_errors} errors")
                    consecutive_errors = 0
                    self.system_state.record_tank_success()
                    
                    # Get previous gallons before update
                    prev_state = self.system_state.get_snapshot()
                    prev_gallons = prev_state['last_logged_gallons']
                    
                    # Update tank state
                    changed = self.system_state.update_tank(
                        data['depth'],
                        data['percentage'],
                        data['pt_percentage'],
                        data['gallons'],
                        data['last_updated'],
                        data['float_state']
                    )
                    
                    # Check for significant change (usage detection)
                    if (self.change_log_file and 
                        prev_gallons is not None and 
                        data['gallons'] is not None):
                        delta = data['gallons'] - prev_gallons
                        
                        # Log if change exceeds threshold (positive or negative)
                        if abs(delta) >= self.change_threshold:
                            if self.debug:
                                action = "gained" if delta > 0 else "lost"
                                print(f"\n💧 Significant tank change detected: {action} {abs(delta):.1f}gal")
                            
                            # Get current pressure state for logging
                            pressure_state = read_pressure()
                            log_tank_change(self.change_log_file, self.system_state, 
                                          pressure_state, self.debug)
                    
                    if self.debug:
                        change_indicator = " [CHANGED]" if changed else ""
                        gallons_str = f"{data['gallons']:.0f}gal" if data['gallons'] else "??gal"
                        pct_str = f"({data['percentage']:.1f}%)" if data['percentage'] else "(??%)"
                        print(f"\n[Tank Update{change_indicator}] {gallons_str} {pct_str}, "
                              f"Float: {data['float_state']}")
                else:
                    consecutive_errors += 1
                    error_msg = data.get('error_message', 'Unknown error')
                    self.system_state.record_tank_error(error_msg)
                    
                    if self.debug:
                        print(f"\n[Tank Error {consecutive_errors}/{self.max_consecutive_errors}] {error_msg}")
                    
                    # If too many consecutive errors, log a warning but continue
                    if consecutive_errors == self.max_consecutive_errors and self.debug:
                        print(f"\n⚠️  Warning: Tank monitoring has failed {consecutive_errors} times consecutively")
                        print(f"   Pressure monitoring will continue, but tank data may be stale")
                        print(f"   Last successful fetch: {self.system_state.get_snapshot()['tank_last_success']}")
                
            except Exception as e:
                consecutive_errors += 1
                if self.debug:
                    print(f"\n[Tank Monitor Exception {consecutive_errors}/{self.max_consecutive_errors}] {e}")
                    import traceback
                    traceback.print_exc()
            
            # Sleep in small increments to allow faster shutdown
            for _ in range(self.interval):
                if not self.running:
                    break
                threading.Event().wait(1)