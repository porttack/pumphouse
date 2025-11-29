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
        
    def update_tank(self, depth, percentage, gallons, float_state):
        """Update tank state"""
        self.tank_depth = depth
        self.tank_percentage = percentage
        self.tank_gallons = gallons
        self.float_state = float_state
    
    def get_snapshot(self):
        """Get current state snapshot"""
        return {
            'tank_gallons': self.tank_gallons,
            'tank_depth': self.tank_depth,
            'tank_percentage': self.tank_percentage,
            'float_state': self.float_state
        }
