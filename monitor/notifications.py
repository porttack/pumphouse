"""
Notification rule engine with state tracking
Evaluates notification rules without directly sending (separation of concerns)
"""
import time
import json
import os
from pathlib import Path
from datetime import datetime
from monitor.config import (
    ENABLE_NOTIFICATIONS,
    NOTIFY_TANK_DECREASING, NOTIFY_TANK_INCREASING,
    NOTIFY_WELL_RECOVERY_THRESHOLD, NOTIFY_FLOAT_CONFIRMATIONS,
    NOTIFY_WELL_DRY_DAYS, NOTIFY_OVERRIDE_SHUTOFF,
    MIN_NOTIFICATION_INTERVAL
)
from monitor.stats import find_last_refill

class NotificationManager:
    """
    Manages notification state and rule evaluation with persistent state storage
    """

    def __init__(self, snapshots_file='snapshots.csv', debug=False):
        self.enabled = ENABLE_NOTIFICATIONS
        self.debug = debug
        self.snapshots_file = snapshots_file
        self.state_file = Path('notification_state.json')

        # State tracking
        self.last_notification_time = {}  # event_type -> timestamp
        self.last_alerted_levels = {
            'decreasing': set(),  # Set of levels we've alerted for going down
            'increasing': set(),  # Set of levels we've alerted for going up
        }
        self.float_state_history = []  # Last N float states for confirmation
        self.last_refill_check = 0
        self.well_dry_alerted = False
        self.well_recovery_alerted_ts = None  # Timestamp of last recovery we alerted for

        # Load persistent state
        self._load_state()

    def check_tank_threshold_crossing(self, current_gallons, previous_gallons):
        """
        Check if tank crossed any notification thresholds.
        Returns list of (direction, level) tuples to notify about.
        """
        if current_gallons is None or previous_gallons is None:
            return []

        notifications = []
        delta = current_gallons - previous_gallons

        # Check decreasing thresholds
        if delta < 0:  # Tank is going down
            for threshold in NOTIFY_TANK_DECREASING:
                # Crossed this threshold going down?
                if previous_gallons >= threshold > current_gallons:
                    if threshold not in self.last_alerted_levels['decreasing']:
                        notifications.append(('decreasing', threshold))
                        self.last_alerted_levels['decreasing'].add(threshold)
                # Tank went back up above this threshold - reset alert
                elif current_gallons > threshold:
                    self.last_alerted_levels['decreasing'].discard(threshold)

        # Check increasing thresholds
        elif delta > 0:  # Tank is going up
            for threshold in NOTIFY_TANK_INCREASING:
                # Crossed this threshold going up?
                if previous_gallons <= threshold < current_gallons:
                    if threshold not in self.last_alerted_levels['increasing']:
                        notifications.append(('increasing', threshold))
                        self.last_alerted_levels['increasing'].add(threshold)
                # Tank went back down below this threshold - reset alert
                elif current_gallons < threshold:
                    self.last_alerted_levels['increasing'].discard(threshold)

        return notifications

    def check_float_confirmation(self, current_float_state):
        """
        Track float state changes with confirmation.
        Returns True if should alert (CLOSED→OPEN confirmed N times).
        """
        self.float_state_history.append(current_float_state)

        # Keep only last N+1 states
        if len(self.float_state_history) > NOTIFY_FLOAT_CONFIRMATIONS + 1:
            self.float_state_history.pop(0)

        # Check if we have enough history
        if len(self.float_state_history) < NOTIFY_FLOAT_CONFIRMATIONS + 1:
            return False

        # Check pattern: was CLOSED, now OPEN for N consecutive times
        if (self.float_state_history[0] == 'CLOSED/CALLING' and
            all(s == 'OPEN/FULL' for s in self.float_state_history[1:])):
            # Clear history so we don't alert again
            self.float_state_history = []
            return True

        return False

    def check_refill_status(self):
        """
        Check for well recovery and well dry conditions.
        Returns ('recovery', timestamp) or ('dry', days) or None.
        """
        current_time = time.time()

        # Don't check too frequently (once per hour)
        if current_time - self.last_refill_check < 3600:
            return None

        self.last_refill_check = current_time

        # Find last refill
        refill_ts, days_ago = find_last_refill(
            self.snapshots_file,
            threshold_gallons=NOTIFY_WELL_RECOVERY_THRESHOLD
        )

        if refill_ts and days_ago is not None:
            # Only alert if we haven't already alerted for this specific refill event
            # Convert timestamp to string for comparison (datetime objects aren't JSON serializable)
            refill_ts_str = refill_ts.isoformat() if isinstance(refill_ts, datetime) else str(refill_ts)

            if self.well_recovery_alerted_ts != refill_ts_str:
                # This is a NEW refill we haven't alerted about yet
                self.well_recovery_alerted_ts = refill_ts_str
                self.well_dry_alerted = False  # Reset dry flag on recovery
                self._save_state()
                return ('recovery', refill_ts)

            # Check if well has been dry too long
            if days_ago >= NOTIFY_WELL_DRY_DAYS and not self.well_dry_alerted:
                self.well_dry_alerted = True
                self._save_state()
                return ('dry', days_ago)
            elif days_ago < NOTIFY_WELL_DRY_DAYS and self.well_dry_alerted:
                # Got water again, reset dry flag
                self.well_dry_alerted = False
                self._save_state()

        return None

    def _load_state(self):
        """Load persistent notification state from disk"""
        try:
            if self.state_file.exists():
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    self.well_recovery_alerted_ts = state.get('well_recovery_alerted_ts')
                    self.well_dry_alerted = state.get('well_dry_alerted', False)
                    if self.debug:
                        print(f"Loaded notification state: recovery_ts={self.well_recovery_alerted_ts}, dry={self.well_dry_alerted}")
        except Exception as e:
            if self.debug:
                print(f"Warning: Could not load notification state: {e}")

    def _save_state(self):
        """Save persistent notification state to disk"""
        try:
            state = {
                'well_recovery_alerted_ts': self.well_recovery_alerted_ts,
                'well_dry_alerted': self.well_dry_alerted,
                'saved_at': datetime.now().isoformat()
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            if self.debug:
                print(f"Warning: Could not save notification state: {e}")

    def can_notify(self, event_type):
        """
        Check if enough time has passed since last notification of this type.
        """
        if not self.enabled:
            return False

        current_time = time.time()
        last_time = self.last_notification_time.get(event_type, 0)

        if current_time - last_time >= MIN_NOTIFICATION_INTERVAL:
            self.last_notification_time[event_type] = current_time
            return True

        return False
