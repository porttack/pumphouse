"""
Configuration constants for the monitoring system
"""
import os
from pathlib import Path

# GPIO Pin Configuration
PRESSURE_PIN = 17
FLOAT_PIN = 27

# Polling Intervals
POLL_INTERVAL = 5  # Seconds between pressure sensor readings
TANK_POLL_INTERVAL = 60  # Seconds between tank level checks (1 minute)

# Tank Configuration
TANK_HEIGHT_INCHES = 58
TANK_CAPACITY_GALLONS = 1400
TANK_URL = "https://www.mypt.in/s/oyd95OEj/qbbBE9Loxo"

# Water Volume Estimation Constants
RESIDUAL_PRESSURE_SECONDS = 30  # Last N seconds are residual pressure (not pumping)
SECONDS_PER_GALLON = 10 / 0.14   # 10 seconds = 0.14 gallons

# Logging Configuration
MAX_PRESSURE_LOG_INTERVAL = 1800  # Log at least every 30 minutes when pressure is high
TANK_CHANGE_THRESHOLD = 8.0  # Log when tank changes by this many gallons (usage detection)

# Default file paths
DEFAULT_LOG_FILE = 'pressure_log.txt'
DEFAULT_EVENTS_FILE = 'pressure_events.csv'

# Config file path (optional)
CONFIG_FILE = Path.home() / '.config' / 'pumphouse' / 'monitor.conf'

def load_config_file():
    """
    Load configuration from file if it exists.
    Returns dict of config values or empty dict if file doesn't exist.
    """
    if not CONFIG_FILE.exists():
        return {}
    
    config = {}
    try:
        with open(CONFIG_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    # Convert to appropriate type
                    if value.lower() in ('true', 'false'):
                        config[key] = value.lower() == 'true'
                    elif value.isdigit():
                        config[key] = int(value)
                    else:
                        try:
                            config[key] = float(value)
                        except ValueError:
                            config[key] = value
        
        return config
    except Exception as e:
        print(f"Warning: Could not load config file {CONFIG_FILE}: {e}")
        return {}

def save_config_template():
    """Create a template config file if it doesn't exist"""
    if CONFIG_FILE.exists():
        return
    
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    template = """# Pumphouse Monitor Configuration
# Lines starting with # are comments

# GPIO Pins
PRESSURE_PIN=17
FLOAT_PIN=27

# Polling intervals (seconds)
POLL_INTERVAL=5
TANK_POLL_INTERVAL=60

# Tank configuration
TANK_HEIGHT_INCHES=58
TANK_CAPACITY_GALLONS=1400
TANK_URL=https://www.mypt.in/s/oyd95OEj/qbbBE9Loxo

# Water estimation
RESIDUAL_PRESSURE_SECONDS=30
SECONDS_PER_GALLON=71.43

# Logging
MAX_PRESSURE_LOG_INTERVAL=1800
TANK_CHANGE_THRESHOLD=2.0
"""
    
    try:
        with open(CONFIG_FILE, 'w') as f:
            f.write(template)
        print(f"Created config template at {CONFIG_FILE}")
    except Exception as e:
        print(f"Could not create config template: {e}")