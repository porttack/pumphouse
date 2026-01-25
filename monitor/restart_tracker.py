"""
Track service restarts and alert on excessive restart loops.
Sends at most one email per day if restarts exceed threshold.
"""
import json
import os
from datetime import datetime, timedelta

from monitor.config import DASHBOARD_URL
from monitor.email_notifier import send_email_notification
from monitor.logger import log_event

# Default settings
DEFAULT_TRACKER_FILE = 'restart_tracker.json'
RESTART_THRESHOLD = 4  # Alert if more than this many restarts in 24h
ALERT_COOLDOWN_HOURS = 24  # Only send one alert per day


def load_tracker_data(tracker_file):
    """Load tracker data from JSON file"""
    if not os.path.exists(tracker_file):
        return {'restarts': [], 'last_alert': None}

    try:
        with open(tracker_file, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {'restarts': [], 'last_alert': None}


def save_tracker_data(tracker_file, data):
    """Save tracker data to JSON file"""
    try:
        with open(tracker_file, 'w') as f:
            json.dump(data, f, indent=2)
    except IOError as e:
        print(f"Warning: Could not save restart tracker: {e}")


def check_and_record_restart(events_file, tracker_file=DEFAULT_TRACKER_FILE, debug=False):
    """
    Record a restart and check if we're in a crash loop.

    Returns:
        dict with 'restart_count' (in last 24h) and 'alerted' (bool if alert sent)
    """
    now = datetime.now()
    cutoff = now - timedelta(hours=24)
    alert_cutoff = now - timedelta(hours=ALERT_COOLDOWN_HOURS)

    # Load existing data
    data = load_tracker_data(tracker_file)

    # Parse restart timestamps and filter to last 24h
    recent_restarts = []
    for ts_str in data.get('restarts', []):
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts > cutoff:
                recent_restarts.append(ts_str)
        except ValueError:
            continue

    # Add current restart
    recent_restarts.append(now.isoformat())
    restart_count = len(recent_restarts)

    # Check if we should alert
    alerted = False
    last_alert = data.get('last_alert')
    can_alert = True

    if last_alert:
        try:
            last_alert_dt = datetime.fromisoformat(last_alert)
            if last_alert_dt > alert_cutoff:
                can_alert = False
        except ValueError:
            pass

    if restart_count > RESTART_THRESHOLD and can_alert:
        # Log the event
        log_event(
            events_file,
            'EXCESSIVE_RESTARTS',
            None,  # pressure_state
            None,  # float_state
            None,  # tank_gallons
            None,  # tank_depth
            None,  # tank_percentage
            None,  # estimated_gallons
            None,  # relay_status
            f"{restart_count} restarts in last 24 hours"
        )

        # Send email alert
        success = send_email_notification(
            subject=f"Pumphouse Monitor - Excessive Restarts ({restart_count}x)",
            message=f"The pumphouse monitor has restarted {restart_count} times in the last 24 hours. "
                    f"This may indicate a crash loop or system instability. "
                    f"Check the system logs for errors: journalctl -u pumphouse-monitor -n 100",
            priority='high',
            dashboard_url=DASHBOARD_URL,
            chart_url=None,
            debug=debug,
            include_status=True
        )

        if success:
            data['last_alert'] = now.isoformat()
            alerted = True
            if debug:
                print(f"Sent excessive restarts alert ({restart_count} restarts in 24h)")
        elif debug:
            print(f"Failed to send excessive restarts alert")

    # Save updated data
    data['restarts'] = recent_restarts
    save_tracker_data(tracker_file, data)

    if debug:
        print(f"Restart #{restart_count} in last 24h (threshold: >{RESTART_THRESHOLD})")

    return {
        'restart_count': restart_count,
        'alerted': alerted
    }
