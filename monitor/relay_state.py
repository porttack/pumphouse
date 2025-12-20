"""
Persistent relay state management
Saves and restores relay states across service restarts
"""
import json
from pathlib import Path
from datetime import datetime


class RelayStateManager:
    """Manages persistent relay state storage"""

    def __init__(self, state_file='relay_state.json'):
        self.state_file = Path(state_file)
        self.state = self._load_state()

    def _load_state(self):
        """Load relay state from disk"""
        try:
            if self.state_file.exists():
                with open(self.state_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Warning: Could not load relay state: {e}")

        # Default state
        return {
            'supply_override': 'OFF',
            'bypass': 'OFF',
            'last_updated': None
        }

    def _save_state(self):
        """Save relay state to disk"""
        try:
            self.state['last_updated'] = datetime.now().isoformat()
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save relay state: {e}")

    def get_supply_override(self):
        """Get saved supply override state"""
        return self.state.get('supply_override', 'OFF')

    def set_supply_override(self, state):
        """Save supply override state"""
        if state in ['ON', 'OFF']:
            self.state['supply_override'] = state
            self._save_state()

    def get_bypass(self):
        """Get saved bypass state"""
        return self.state.get('bypass', 'OFF')

    def set_bypass(self, state):
        """Save bypass state"""
        if state in ['ON', 'OFF']:
            self.state['bypass'] = state
            self._save_state()
