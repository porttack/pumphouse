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
    NOTIFY_WELL_RECOVERY_THRESHOLD, NOTIFY_WELL_RECOVERY_STAGNATION_HOURS,
    NOTIFY_WELL_RECOVERY_MAX_STAGNATION_GAIN,
    NOTIFY_FLOAT_CONFIRMATIONS,
    NOTIFY_WELL_DRY_DAYS, NOTIFY_OVERRIDE_SHUTOFF,
    NOTIFY_HIGH_FLOW_ENABLED, NOTIFY_HIGH_FLOW_GPH, NOTIFY_HIGH_FLOW_WINDOW_HOURS,
    NOTIFY_HIGH_FLOW_AVERAGING,
    NOTIFY_BACKFLUSH_ENABLED, NOTIFY_BACKFLUSH_THRESHOLD, NOTIFY_BACKFLUSH_WINDOW_SNAPSHOTS,
    NOTIFY_BACKFLUSH_TIME_START, NOTIFY_BACKFLUSH_TIME_END,
    MIN_NOTIFICATION_INTERVAL
)
from monitor.stats import find_last_refill, find_high_flow_event, find_backflush_event

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
        self.high_flow_alerted_ts = None  # Timestamp of last high flow we alerted for
        self.backflush_alerted_ts = None  # Timestamp of last backflush we alerted for

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
            threshold_gallons=NOTIFY_WELL_RECOVERY_THRESHOLD,
            stagnation_hours=NOTIFY_WELL_RECOVERY_STAGNATION_HOURS,
            max_stagnation_gain=NOTIFY_WELL_RECOVERY_MAX_STAGNATION_GAIN
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

    def check_high_flow_status(self):
        """
        Check for high flow rate (fast fill mode).
        Returns ('high_flow', gph) or None.
        """
        if not NOTIFY_HIGH_FLOW_ENABLED:
            return None

        current_time = time.time()

        # Don't check too frequently (once per hour)
        if current_time - self.last_refill_check < 3600:
            return None

        # Find last high flow event
        flow_ts, gph = find_high_flow_event(
            self.snapshots_file,
            gph_threshold=NOTIFY_HIGH_FLOW_GPH,
            window_hours=NOTIFY_HIGH_FLOW_WINDOW_HOURS,
            averaging_snapshots=NOTIFY_HIGH_FLOW_AVERAGING
        )

        if flow_ts and gph is not None:
            # Only alert if we haven't already alerted for this specific high flow event
            flow_ts_str = flow_ts.isoformat() if isinstance(flow_ts, datetime) else str(flow_ts)

            if self.high_flow_alerted_ts != flow_ts_str:
                # This is a NEW high flow event we haven't alerted about yet
                self.high_flow_alerted_ts = flow_ts_str
                self._save_state()
                return ('high_flow', gph)

        return None

    def check_backflush_status(self):
        """
        Check for backflush event (large water usage during specific hours).
        Returns ('backflush', gallons_used, timestamp) or None.
        """
        if not NOTIFY_BACKFLUSH_ENABLED:
            return None

        # NOTE: No cooldown timer for backflush checks (unlike recovery/high-flow)
        # Backflush events are brief (15-30 min) with a narrow detection window,
        # so we must check every snapshot to avoid missing them.
        # Duplicate alerts are already prevented by backflush_alerted_ts tracking.

        # Find last backflush event
        backflush_ts, gallons_used = find_backflush_event(
            self.snapshots_file,
            threshold_gallons=NOTIFY_BACKFLUSH_THRESHOLD,
            window_snapshots=NOTIFY_BACKFLUSH_WINDOW_SNAPSHOTS,
            time_start=NOTIFY_BACKFLUSH_TIME_START,
            time_end=NOTIFY_BACKFLUSH_TIME_END
        )

        if backflush_ts and gallons_used is not None:
            # Only alert if we haven't already alerted for this specific backflush event
            backflush_ts_str = backflush_ts.isoformat() if isinstance(backflush_ts, datetime) else str(backflush_ts)

            if self.backflush_alerted_ts != backflush_ts_str:
                # This is a NEW backflush event we haven't alerted about yet
                self.backflush_alerted_ts = backflush_ts_str
                self._save_state()
                return ('backflush', gallons_used, backflush_ts)

        return None

    def _load_state(self):
        """Load persistent notification state from disk"""
        try:
            if self.state_file.exists():
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    self.well_recovery_alerted_ts = state.get('well_recovery_alerted_ts')
                    self.well_dry_alerted = state.get('well_dry_alerted', False)
                    self.high_flow_alerted_ts = state.get('high_flow_alerted_ts')
                    self.backflush_alerted_ts = state.get('backflush_alerted_ts')
                    if self.debug:
                        print(f"Loaded notification state: recovery_ts={self.well_recovery_alerted_ts}, "
                              f"dry={self.well_dry_alerted}, high_flow_ts={self.high_flow_alerted_ts}, "
                              f"backflush_ts={self.backflush_alerted_ts}")
        except Exception as e:
            if self.debug:
                print(f"Warning: Could not load notification state: {e}")

    def _save_state(self):
        """Save persistent notification state to disk"""
        try:
            state = {
                'well_recovery_alerted_ts': self.well_recovery_alerted_ts,
                'well_dry_alerted': self.well_dry_alerted,
                'high_flow_alerted_ts': self.high_flow_alerted_ts,
                'backflush_alerted_ts': self.backflush_alerted_ts,
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
