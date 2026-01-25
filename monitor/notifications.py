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
    NOTIFY_FULL_FLOW_ENABLED, NOTIFY_FULL_FLOW_PRESSURE_THRESHOLD,
    NOTIFY_FULL_FLOW_DELAY_MINUTES, NOTIFY_FULL_FLOW_LOOKBACK_HOURS,
    MIN_NOTIFICATION_INTERVAL
)
from monitor.gpio_helpers import FLOAT_STATE_FULL, FLOAT_STATE_CALLING
from monitor.stats import find_last_refill, find_high_flow_event, find_backflush_event, find_full_flow_periods

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
        self.float_full_alerted = False  # Track if we've already alerted for current tank full
        self.last_refill_check = 0
        self.well_dry_alerted = False
        self.well_recovery_alerted_ts = None  # Timestamp of last recovery we alerted for
        self.high_flow_alerted_ts = None  # Timestamp of last high flow we alerted for
        self.backflush_alerted_date = None  # Date (YYYY-MM-DD) of last backflush alert (one per day max)
        self.full_flow_alerted_ts = None  # Timestamp of last full-flow we alerted for

        # New state for suppression logic
        self.tank_full_alerted_level = None  # Tank level when last tank_full alert sent
        self.tank_full_alerted_time = None  # Timestamp when last tank_full alert sent
        self.full_flow_alerted_time = None  # Timestamp when last full_flow alert sent

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

        # If float goes back to CALLING, reset the alert flag for next fill cycle
        if current_float_state == FLOAT_STATE_CALLING:
            self.float_full_alerted = False

        # Check if we have enough history
        if len(self.float_state_history) < NOTIFY_FLOAT_CONFIRMATIONS + 1:
            return False

        # Check pattern: was CALLING, now FULL for N consecutive times
        if (self.float_state_history[0] == FLOAT_STATE_CALLING and
            all(s == FLOAT_STATE_FULL for s in self.float_state_history[1:])):
            # Only alert once per CALLING→FULL transition
            if not self.float_full_alerted:
                self.float_full_alerted = True
                # Clear history so pattern doesn't keep matching
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

        Only sends one backflush alert per day to avoid duplicate notifications
        as the sliding window shifts during an ongoing backflush.
        """
        if not NOTIFY_BACKFLUSH_ENABLED:
            return None

        # Find last backflush event
        backflush_ts, gallons_used = find_backflush_event(
            self.snapshots_file,
            threshold_gallons=NOTIFY_BACKFLUSH_THRESHOLD,
            window_snapshots=NOTIFY_BACKFLUSH_WINDOW_SNAPSHOTS,
            time_start=NOTIFY_BACKFLUSH_TIME_START,
            time_end=NOTIFY_BACKFLUSH_TIME_END
        )

        if backflush_ts and gallons_used is not None:
            # Only alert once per day (date-based dedup)
            backflush_date = backflush_ts.strftime('%Y-%m-%d') if isinstance(backflush_ts, datetime) else str(backflush_ts)[:10]

            if self.backflush_alerted_date != backflush_date:
                # This is a NEW backflush day we haven't alerted about yet
                self.backflush_alerted_date = backflush_date
                self._save_state()
                return ('backflush', gallons_used, backflush_ts)

        return None

    def check_full_flow_status(self):
        """
        Check for full-flow periods (pressure_high_percent ~100%).
        Returns dict with period details or None.

        Only notifies if period has been running for at least NOTIFY_FULL_FLOW_DELAY_MINUTES.
        This captures sustained full-flow events (neighbor receiving water at max capacity).
        """
        if not NOTIFY_FULL_FLOW_ENABLED:
            return None

        # Find all full-flow periods in lookback window
        periods = find_full_flow_periods(
            self.snapshots_file,
            pressure_threshold=NOTIFY_FULL_FLOW_PRESSURE_THRESHOLD,
            lookback_hours=NOTIFY_FULL_FLOW_LOOKBACK_HOURS
        )

        if not periods:
            return None

        # Get the most recent period (last in list)
        latest_period = periods[-1]

        # Only notify if period has been running for at least the delay threshold
        if latest_period['duration_minutes'] < NOTIFY_FULL_FLOW_DELAY_MINUTES:
            return None

        # Check if we've already alerted for this period
        period_ts_str = latest_period['start_ts'].isoformat()

        if self.full_flow_alerted_ts != period_ts_str:
            # This is a NEW full-flow period we haven't alerted about yet
            self.full_flow_alerted_ts = period_ts_str
            self.full_flow_alerted_time = time.time()  # Track when we sent this alert
            self._save_state()

            # Return full period details for notification
            return {
                'type': 'full_flow',
                'start_ts': latest_period['start_ts'],
                'end_ts': latest_period['end_ts'],
                'duration_minutes': latest_period['duration_minutes'],
                'snapshot_count': latest_period['snapshot_count'],
                'total_gallons_pumped': latest_period['total_gallons_pumped'],
                'tank_gain': latest_period['tank_gain'],
                'estimated_gph': latest_period['estimated_gph']
            }

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
                    # Support both old backflush_alerted_ts and new backflush_alerted_date
                    self.backflush_alerted_date = state.get('backflush_alerted_date') or state.get('backflush_alerted_ts', '')[:10] if state.get('backflush_alerted_ts') else None
                    self.full_flow_alerted_ts = state.get('full_flow_alerted_ts')
                    # New suppression state
                    self.tank_full_alerted_level = state.get('tank_full_alerted_level')
                    self.tank_full_alerted_time = state.get('tank_full_alerted_time')
                    self.full_flow_alerted_time = state.get('full_flow_alerted_time')
                    if self.debug:
                        print(f"Loaded notification state: recovery_ts={self.well_recovery_alerted_ts}, "
                              f"dry={self.well_dry_alerted}, high_flow_ts={self.high_flow_alerted_ts}, "
                              f"backflush_date={self.backflush_alerted_date}, full_flow_ts={self.full_flow_alerted_ts}")
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
                'backflush_alerted_date': self.backflush_alerted_date,
                'full_flow_alerted_ts': self.full_flow_alerted_ts,
                'tank_full_alerted_level': self.tank_full_alerted_level,
                'tank_full_alerted_time': self.tank_full_alerted_time,
                'full_flow_alerted_time': self.full_flow_alerted_time,
                'saved_at': datetime.now().isoformat()
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            if self.debug:
                print(f"Warning: Could not save notification state: {e}")

    def should_suppress_tank_full(self, current_gallons, reset_threshold_pct=0.90):
        """
        Check if tank_full alert should be suppressed.
        Suppresses if we already alerted and tank hasn't dropped below threshold.

        Args:
            current_gallons: Current tank level in gallons
            reset_threshold_pct: Tank must drop below this % of alerted level to reset (default 90%)

        Returns:
            True if alert should be suppressed, False if alert should be sent
        """
        if self.tank_full_alerted_level is None:
            return False  # Never alerted, don't suppress

        if current_gallons is None:
            return False  # No data, don't suppress

        # Reset threshold: tank must drop below 90% of the level when we last alerted
        reset_level = self.tank_full_alerted_level * reset_threshold_pct

        if current_gallons < reset_level:
            # Tank has dropped enough, reset and allow new alert
            self.tank_full_alerted_level = None
            self.tank_full_alerted_time = None
            self._save_state()
            return False

        # Tank still high, suppress the alert
        return True

    def record_tank_full_alert(self, tank_gallons):
        """Record that a tank_full alert was sent at this level"""
        self.tank_full_alerted_level = tank_gallons
        self.tank_full_alerted_time = time.time()
        self._save_state()

    def should_suppress_well_recovery(self, suppression_hours=2.0):
        """
        Check if well_recovery alert should be suppressed.
        Suppresses if full_flow or tank_full alert was sent recently.

        Args:
            suppression_hours: Suppress if related alert sent within this many hours (default 2)

        Returns:
            True if alert should be suppressed, False if alert should be sent
        """
        current_time = time.time()
        suppression_seconds = suppression_hours * 3600

        # Suppress if full_flow alert was sent recently
        if self.full_flow_alerted_time:
            if current_time - self.full_flow_alerted_time < suppression_seconds:
                return True

        # Suppress if tank_full alert was sent recently
        if self.tank_full_alerted_time:
            if current_time - self.tank_full_alerted_time < suppression_seconds:
                return True

        return False

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
