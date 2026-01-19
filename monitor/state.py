"""
Simple state tracking for monitoring
"""
from datetime import datetime

class SystemState:
    """Track current system state"""

    def __init__(self):
        self.tank_gallons = None
        self.tank_depth = None
        self.tank_percentage = None
        self.float_state = None
        self.outdoor_temp = None
        self.indoor_temp = None
        self.outdoor_humidity = None
        self.indoor_humidity = None
        self.baro_abs = None
        self.baro_rel = None
        self.wind_speed = None
        self.wind_gust = None
        
    def update_tank(self, depth, percentage, gallons, float_state):
        """Update tank state"""
        self.tank_depth = depth
        self.tank_percentage = percentage
        self.tank_gallons = gallons
        self.float_state = float_state

    def update_weather(self, outdoor_temp, indoor_temp, outdoor_humidity, indoor_humidity,
                       baro_abs, baro_rel, wind_speed, wind_gust):
        """Update weather state"""
        self.outdoor_temp = outdoor_temp
        self.indoor_temp = indoor_temp
        self.outdoor_humidity = outdoor_humidity
        self.indoor_humidity = indoor_humidity
        self.baro_abs = baro_abs
        self.baro_rel = baro_rel
        self.wind_speed = wind_speed
        self.wind_gust = wind_gust

    def get_snapshot(self):
        """Get current state snapshot"""
        return {
            'tank_gallons': self.tank_gallons,
            'tank_depth': self.tank_depth,
            'tank_percentage': self.tank_percentage,
            'float_state': self.float_state,
            'outdoor_temp': self.outdoor_temp,
            'indoor_temp': self.indoor_temp,
            'outdoor_humidity': self.outdoor_humidity,
            'indoor_humidity': self.indoor_humidity,
            'baro_abs': self.baro_abs,
            'baro_rel': self.baro_rel,
            'wind_speed': self.wind_speed,
            'wind_gust': self.wind_gust
        }
