"""
Simplified event-based polling loop
"""
import time
from datetime import datetime, timedelta
import signal

from monitor.config import (
    POLL_INTERVAL, TANK_POLL_INTERVAL, SNAPSHOT_INTERVAL,
    RESIDUAL_PRESSURE_SECONDS, SECONDS_PER_GALLON,
    ENABLE_PURGE, MIN_PURGE_INTERVAL,
    ENABLE_OVERRIDE_SHUTOFF, OVERRIDE_SHUTOFF_THRESHOLD,
    OVERRIDE_ON_THRESHOLD,
    NOTIFY_OVERRIDE_SHUTOFF, NOTIFY_WELL_RECOVERY_THRESHOLD,
    NOTIFY_WELL_RECOVERY_STAGNATION_HOURS,
    NOTIFY_HIGH_PRESSURE_ENABLED, NOTIFY_HIGH_PRESSURE_USE_EMAIL,
    NOTIFY_PRESSURE_LOW_ENABLED,
    NOTIFY_TANK_OUTAGE_ENABLED, NOTIFY_TANK_OUTAGE_THRESHOLD_MINUTES,
    DASHBOARD_URL,
    ENABLE_DAILY_STATUS_EMAIL, DAILY_STATUS_EMAIL_TIME, DAILY_STATUS_EMAIL_CHART_HOURS,
    ENABLE_CHECKOUT_REMINDER, CHECKOUT_REMINDER_TIME,
    MAX_TANK_FETCH_FAILURES,
    ENABLE_AMBIENT_WEATHER, AMBIENT_WEATHER_POLL_INTERVAL,
    AMBIENT_WEATHER_API_KEY, AMBIENT_WEATHER_APPLICATION_KEY, AMBIENT_WEATHER_MAC_ADDRESS
)
from monitor.gpio_helpers import (
    read_pressure, read_float_sensor,
    FLOAT_STATE_FULL, FLOAT_STATE_CALLING
)
from monitor.tank import get_tank_data
from monitor.ambient_weather import get_weather_data
from monitor.logger import log_event, log_snapshot
from monitor.state import SystemState
from monitor.notifications import NotificationManager
from monitor.ntfy import send_notification
from monitor.email_notifier import send_email_notification
from monitor.occupancy import is_occupied, load_reservations, get_checkout_datetime, get_checkin_datetime, parse_date

def estimate_gallons(duration_seconds):
    """Estimate gallons pumped based on pressure duration"""
    effective_pumping_time = duration_seconds - RESIDUAL_PRESSURE_SECONDS
    if effective_pumping_time <= 0:
        return 0.0
    return effective_pumping_time / SECONDS_PER_GALLON

def get_next_snapshot_time(current_time, interval_minutes):
    """Return next exact interval boundary (e.g., :00, :15, :30, :45)"""
    dt = datetime.fromtimestamp(current_time)
    # Round up to next interval
    minutes = ((dt.minute // interval_minutes) + 1) * interval_minutes
    if minutes >= 60:
        next_dt = dt.replace(hour=dt.hour + 1 if dt.hour < 23 else 0,
                            minute=0, second=0, microsecond=0)
        if dt.hour == 23:
            next_dt = next_dt.replace(day=dt.day + 1)
    else:
        next_dt = dt.replace(minute=minutes, second=0, microsecond=0)
    return next_dt.timestamp()

def get_next_daily_status_time(current_time, target_time_str):
    """
    Return next daily status email time based on target_time_str (HH:MM format).
    If current time is past target time today, returns target time tomorrow.
    """
    dt = datetime.fromtimestamp(current_time)
    try:
        target_hour, target_minute = map(int, target_time_str.split(':'))
        # Set to today's target time
        target_dt = dt.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)

        # If we're past today's target time, move to tomorrow
        if current_time >= target_dt.timestamp():
            target_dt = target_dt + timedelta(days=1)

        return target_dt.timestamp()
    except (ValueError, AttributeError):
        # If parsing fails, default to 6am tomorrow
        return (dt.replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()

class SnapshotTracker:
    """Track data for snapshot intervals"""
    
    def __init__(self):
        self.reset()
        
    def reset(self):
        """Reset for new snapshot interval"""
        self.start_time = time.time()
        self.float_states = []
        self.pressure_high_time = 0.0
        self.last_pressure_check = time.time()
        self.last_pressure_state = None
        self.estimated_gallons = 0.0
        self.purge_count = 0
        
    def update_float(self, float_state):
        """Track float state"""
        if float_state:
            self.float_states.append(float_state)
    
    def update_pressure(self, is_high):
        """Update pressure tracking"""
        current_time = time.time()
        if self.last_pressure_state and is_high:
            # Was high, still high - accumulate time
            self.pressure_high_time += current_time - self.last_pressure_check
        self.last_pressure_state = is_high
        self.last_pressure_check = current_time
    
    def add_estimated_gallons(self, gallons):
        """Add estimated gallons from pressure event"""
        if gallons and gallons > 0:
            self.estimated_gallons += gallons
    
    def increment_purge(self):
        """Increment purge counter"""
        self.purge_count += 1
    
    def get_snapshot_data(self, tank_gallons, tank_data_age, current_float, relay_status):
        """Get snapshot data for logging"""
        duration = time.time() - self.start_time

        float_ever_calling = FLOAT_STATE_CALLING in self.float_states
        float_always_full = (len(self.float_states) > 0 and
                            all(s == FLOAT_STATE_FULL for s in self.float_states))

        pressure_high_percent = (self.pressure_high_time / duration * 100) if duration > 0 else 0

        # If we have pressure HIGH time but no estimated gallons from completed cycles,
        # estimate based on the accumulated HIGH time (handles in-progress pump cycles)
        estimated_gallons = self.estimated_gallons
        if self.pressure_high_time > 0 and estimated_gallons == 0:
            estimated_gallons = estimate_gallons(self.pressure_high_time)

        return {
            'duration': duration,
            'tank_gallons': tank_gallons,
            'tank_data_age': tank_data_age,
            'float_state': current_float,
            'float_ever_calling': float_ever_calling,
            'float_always_full': float_always_full,
            'pressure_high_seconds': self.pressure_high_time,
            'pressure_high_percent': pressure_high_percent,
            'estimated_gallons': estimated_gallons,
            'purge_count': self.purge_count,
            'relay_status': relay_status
        }

class SimplifiedMonitor:
    """Simplified event-based monitor"""

    def __init__(self, events_file, snapshots_file, tank_url,
                 debug=False, poll_interval=5, tank_interval=60,
                 snapshot_interval=15):
        self.events_file = events_file
        self.snapshots_file = snapshots_file
        self.tank_url = tank_url
        self.debug = debug
        self.poll_interval = poll_interval
        self.tank_interval = tank_interval
        self.snapshot_interval = snapshot_interval
        
        self.running = True
        self.state = SystemState()
        self.snapshot_tracker = SnapshotTracker()
        
        # Pressure tracking
        self.last_pressure_state = None
        self.pressure_high_start = None
        
        # Timing
        self.last_tank_check = 0
        self.tank_last_updated = None
        self.next_snapshot_time = None
        self.next_daily_status_time = None
        self.next_checkout_reminder_time = None

        # Tank tracking for delta
        self.last_snapshot_tank_gallons = None

        # Tank fetch failure tracking
        self.tank_fetch_failures = 0
        self.max_tank_failures = MAX_TANK_FETCH_FAILURES
        self.tank_outage_start = None  # Track when tank data became unavailable

        # Weather tracking
        self.last_weather_check = 0
        self.weather_interval = AMBIENT_WEATHER_POLL_INTERVAL

        # Relay control
        self.relay_control_enabled = False

        # Purge control
        self.enable_purge = ENABLE_PURGE
        self.min_purge_interval = MIN_PURGE_INTERVAL
        self.last_purge_time = 0

        # Override shutoff control
        self.enable_override_shutoff = ENABLE_OVERRIDE_SHUTOFF
        self.override_shutoff_threshold = OVERRIDE_SHUTOFF_THRESHOLD

        # Override auto-on control
        self.override_on_threshold = OVERRIDE_ON_THRESHOLD

        # Notification system
        self.notification_manager = NotificationManager(
            snapshots_file=self.snapshots_file,
            debug=self.debug
        )

        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        if self.debug:
            print(f"\nReceived signal {signum}, shutting down...")
        self.running = False
    
    def enable_relay_control(self):
        """Enable relay control and restore saved states"""
        from monitor.relay import init_relays, restore_relay_states
        if init_relays():
            self.relay_control_enabled = True
            if self.debug:
                print("Relay control enabled")

            # Restore saved relay states
            restored = restore_relay_states(debug=self.debug)
            if self.debug and restored:
                print(f"Restored relay states: {restored}")

            # Log relay state restoration if any were changed
            if restored:
                restore_notes = []
                if 'supply_override' in restored:
                    restore_notes.append(f"Override: {restored['supply_override']}")
                if 'bypass' in restored:
                    restore_notes.append(f"Bypass: {restored['bypass']}")
                if restore_notes:
                    self.log_state_event('INIT', f"Restored relay states - {', '.join(restore_notes)}")

            return True
        return False
    
    def get_relay_status(self):
        """Get current relay status"""
        if self.relay_control_enabled:
            from monitor.relay import get_relay_status
            return get_relay_status()
        return {'bypass': 'OFF', 'supply_override': 'OFF'}
    
    def fetch_tank_data(self):
        """Fetch and update tank data"""
        data = get_tank_data(self.tank_url)
        if data['status'] == 'success':
            self.state.update_tank(
                data['depth'],
                data['percentage'],
                data['gallons'],
                data['float_state']
            )
            # Use the actual timestamp from PT website, not current time
            if data['last_updated']:
                self.tank_last_updated = data['last_updated'].timestamp()
            else:
                # Fallback if parsing failed
                self.tank_last_updated = time.time()
            self.snapshot_tracker.update_float(data['float_state'])
            return True
        return False

    def fetch_weather_data(self):
        """Fetch and update weather data from Ambient Weather"""
        if not ENABLE_AMBIENT_WEATHER:
            return False

        if not AMBIENT_WEATHER_API_KEY or not AMBIENT_WEATHER_APPLICATION_KEY:
            if self.debug:
                print("  Ambient Weather API keys not configured")
            return False

        data = get_weather_data(
            AMBIENT_WEATHER_API_KEY,
            AMBIENT_WEATHER_APPLICATION_KEY,
            AMBIENT_WEATHER_MAC_ADDRESS,
            debug=self.debug
        )

        if data['status'] == 'success':
            self.state.update_weather(
                data['outdoor_temp'],
                data['indoor_temp'],
                data['outdoor_humidity'],
                data['indoor_humidity'],
                data['baro_abs'],
                data['baro_rel'],
                data['wind_speed'],
                data['wind_gust']
            )
            return True
        return False
    
    def get_tank_data_age(self):
        """Get age of tank data in seconds"""
        if self.tank_last_updated:
            return time.time() - self.tank_last_updated
        return None
    
    def log_pressure_event(self, event_type, estimated_gallons, notes=''):
        """Log a pressure event and add to snapshot tracker"""
        # Add to snapshot FIRST before logging
        self.snapshot_tracker.add_estimated_gallons(estimated_gallons)
        
        snapshot = self.state.get_snapshot()
        relay_status = self.get_relay_status()
        
        log_event(
            self.events_file,
            event_type,
            self.last_pressure_state,
            snapshot['float_state'],
            snapshot['tank_gallons'],
            snapshot['tank_depth'],
            snapshot['tank_percentage'],
            estimated_gallons,
            relay_status,
            notes
        )
    
    def log_state_event(self, event_type, notes=''):
        """Log a state change event"""
        snapshot = self.state.get_snapshot()
        relay_status = self.get_relay_status()

        log_event(
            self.events_file,
            event_type,
            self.last_pressure_state,
            snapshot['float_state'],
            snapshot['tank_gallons'],
            snapshot['tank_depth'],
            snapshot['tank_percentage'],
            None,
            relay_status,
            notes
        )

    def send_alert(self, event_type, title, message, priority='default', chart_hours=24):
        """Send notification via both ntfy and email, and log to events.csv"""
        # Log the notification event
        self.log_state_event(event_type, f'ALERT: {title}')

        # Send ntfy notification
        send_notification(
            title=title,
            message=message,
            priority=priority,
            tags=['droplet'],
            click_url=DASHBOARD_URL,
            attach_url=f"{DASHBOARD_URL}api/chart.png?hours={chart_hours}",
            debug=self.debug
        )

        # Send email notification with full status
        send_email_notification(
            subject=title,
            message=message,
            priority=priority,
            dashboard_url=DASHBOARD_URL,
            chart_url=f"{DASHBOARD_URL}api/chart.png?hours={chart_hours}",
            debug=self.debug,
            include_status=True  # Always include full status
        )

    def log_tank_outage_recovery(self, outage_duration_seconds):
        """Log tank data outage recovery event with duration"""
        outage_duration_minutes = outage_duration_seconds / 60

        # Format duration for display
        if outage_duration_minutes < 60:
            duration_str = f"{outage_duration_minutes:.1f} minutes"
        elif outage_duration_minutes < 1440:  # Less than 24 hours
            hours = outage_duration_minutes / 60
            duration_str = f"{hours:.1f} hours"
        else:
            days = outage_duration_minutes / 1440
            duration_str = f"{days:.1f} days"

        self.log_state_event('TANK_OUTAGE_RECOVERY',
                            f'Tank data restored after {duration_str} outage')

        if self.debug:
            print(f"  → Tank outage recovery: {duration_str}")

    def send_tank_outage_notification(self, outage_duration_seconds):
        """Send notification about significant tank data outage"""
        outage_duration_minutes = outage_duration_seconds / 60

        # Format duration for display
        if outage_duration_minutes < 60:
            duration_str = f"{outage_duration_minutes:.0f} minutes"
        elif outage_duration_minutes < 1440:  # Less than 24 hours
            hours = outage_duration_minutes / 60
            duration_str = f"{hours:.1f} hours"
        else:
            days = outage_duration_minutes / 1440
            duration_str = f"{days:.1f} days"

        # Determine priority based on duration
        if outage_duration_minutes >= 1440:  # 24+ hours
            priority = 'urgent'
        elif outage_duration_minutes >= 360:  # 6+ hours
            priority = 'high'
        else:
            priority = 'default'

        title = f"📡 Tank Data Restored - {duration_str} Outage"
        message = (f"Tank level monitoring has been restored after {duration_str}. "
                  f"The system was unable to read tank data during this period, likely due to "
                  f"an internet or network connectivity issue.")

        if self.notification_manager.can_notify('tank_outage_recovery'):
            self.send_alert(
                'NOTIFY_TANK_OUTAGE',
                title,
                message,
                priority=priority,
                chart_hours=168  # Show 7 days to visualize the outage period
            )

    def run(self):
        """Main monitoring loop"""
        # Enable relay control for status monitoring
        self.enable_relay_control()

        # Initial state
        self.last_pressure_state = read_pressure()
        self.fetch_tank_data()
        self.fetch_weather_data()

        # Log initial state
        self.log_state_event('INIT', 'System startup')
        
        # Initialize snapshot timing
        self.next_snapshot_time = get_next_snapshot_time(
            time.time(),
            self.snapshot_interval
        )

        # Initialize daily status email timing
        if ENABLE_DAILY_STATUS_EMAIL:
            self.next_daily_status_time = get_next_daily_status_time(
                time.time(),
                DAILY_STATUS_EMAIL_TIME
            )
            if self.debug:
                next_daily_dt = datetime.fromtimestamp(self.next_daily_status_time)
                print(f"Next daily status email at: {next_daily_dt.strftime('%Y-%m-%d %H:%M:%S')}")

        # Initialize checkout reminder timing
        if ENABLE_CHECKOUT_REMINDER:
            self.next_checkout_reminder_time = get_next_daily_status_time(
                time.time(),
                CHECKOUT_REMINDER_TIME
            )
            if self.debug:
                next_checkout_dt = datetime.fromtimestamp(self.next_checkout_reminder_time)
                print(f"Next checkout reminder at: {next_checkout_dt.strftime('%Y-%m-%d %H:%M:%S')}")

        if self.debug:
            next_dt = datetime.fromtimestamp(self.next_snapshot_time)
            print(f"First snapshot at: {next_dt.strftime('%H:%M:%S')}")
            print("Monitoring started...\n")
        
        self.last_tank_check = time.time()
        last_float_state = self.state.float_state
        
        try:
            while self.running:
                current_time = time.time()
                current_pressure = read_pressure()
                
                if current_pressure is None:
                    time.sleep(self.poll_interval)
                    continue
                
                # Update snapshot pressure tracking
                self.snapshot_tracker.update_pressure(current_pressure)
                
                # PRESSURE STATE CHANGE
                if current_pressure != self.last_pressure_state:
                    if current_pressure:  # Went HIGH
                        self.pressure_high_start = current_time
                        self.log_state_event('PRESSURE_HIGH')
                        if self.debug:
                            print(f"{datetime.now().strftime('%H:%M:%S')} - Pressure HIGH")

                        # Send high pressure alert if enabled
                        if NOTIFY_HIGH_PRESSURE_ENABLED and self.notification_manager.can_notify('high_pressure'):
                            current_gal = self.state.tank_gallons if self.state.tank_gallons else 0

                            # Send ntfy notification
                            send_notification(
                                title=f"{current_gal:.0f} gal - Pressure HIGH",
                                message=f"Water pressure is HIGH (\u226510 PSI) - someone may be using water",
                                priority='high',
                                tags=['droplet', 'warning'],
                                click_url=DASHBOARD_URL,
                                attach_url=f"{DASHBOARD_URL}api/chart.png?hours=24",
                                debug=self.debug
                            )

                            # Optionally send email notification
                            if NOTIFY_HIGH_PRESSURE_USE_EMAIL:
                                send_email_notification(
                                    subject=f"{current_gal:.0f} gal - Pressure HIGH",
                                    message=f"Water pressure is HIGH (\u226510 PSI) - someone may be using water",
                                    priority='high',
                                    dashboard_url=DASHBOARD_URL,
                                    chart_url=f"{DASHBOARD_URL}api/chart.png?hours=24",
                                    debug=self.debug,
                                    include_status=True
                                )
                    else:  # Went LOW
                        if self.pressure_high_start:
                            duration = current_time - self.pressure_high_start
                            estimated = estimate_gallons(duration)
                            self.log_pressure_event('PRESSURE_LOW', estimated,
                                                    f'Duration: {duration:.1f}s')
                            if self.debug:
                                print(f"{datetime.now().strftime('%H:%M:%S')} - Pressure LOW "
                                     f"(was HIGH for {duration:.1f}s, ~{estimated:.1f} gal)")

                            # Send pressure LOW alert with duration info
                            if NOTIFY_PRESSURE_LOW_ENABLED and self.notification_manager.can_notify('pressure_low'):
                                current_gal = self.state.tank_gallons if self.state.tank_gallons else 0
                                duration_minutes = duration / 60

                                # Format duration nicely
                                if duration_minutes >= 1:
                                    duration_str = f"{duration_minutes:.1f} minutes"
                                else:
                                    duration_str = f"{duration:.0f} seconds"

                                # Send ntfy notification
                                send_notification(
                                    title=f"{current_gal:.0f} gal - Pressure LOW",
                                    message=f"Water usage ended. Pressure was HIGH for {duration_str} (~{estimated:.1f} gal)",
                                    priority='default',
                                    tags=['droplet'],
                                    click_url=DASHBOARD_URL,
                                    attach_url=f"{DASHBOARD_URL}api/chart.png?hours=24",
                                    debug=self.debug
                                )

                                # Send email notification with full details
                                if NOTIFY_HIGH_PRESSURE_USE_EMAIL:
                                    send_email_notification(
                                        subject=f"{current_gal:.0f} gal - Water Usage Ended",
                                        message=f"Water pressure returned to LOW (<10 PSI).\n\n"
                                               f"Duration of HIGH pressure: {duration_str}\n"
                                               f"Estimated water pumped: ~{estimated:.1f} gallons",
                                        priority='default',
                                        dashboard_url=DASHBOARD_URL,
                                        chart_url=f"{DASHBOARD_URL}api/chart.png?hours=24",
                                        debug=self.debug,
                                        include_status=True
                                    )

                            # Trigger purge if enabled AND enough time has passed
                            if self.enable_purge and self.relay_control_enabled and estimated > 0:
                                time_since_last_purge = current_time - self.last_purge_time
                                if time_since_last_purge >= self.min_purge_interval:
                                    if self.debug:
                                        print("  → Triggering filter purge...")
                                    from monitor.relay import purge_spindown_filter
                                    if purge_spindown_filter(debug=self.debug):
                                        self.log_state_event('PURGE', 'Auto-purge after water delivery')
                                        self.snapshot_tracker.increment_purge()
                                        self.last_purge_time = current_time
                                elif self.debug:
                                    mins_to_wait = int((self.min_purge_interval - time_since_last_purge) / 60)
                                    print(f"  → Skipping purge (min interval not met, wait {mins_to_wait} more min)")

                        self.pressure_high_start = None
                    
                    self.last_pressure_state = current_pressure
                
                # TANK POLLING
                if current_time - self.last_tank_check >= self.tank_interval:
                    prev_gallons = self.state.tank_gallons
                    tank_fetch_success = self.fetch_tank_data()

                    # Track consecutive failures
                    if not tank_fetch_success:
                        self.tank_fetch_failures += 1

                        # Mark outage start time on first failure
                        if self.tank_fetch_failures == 1 and self.tank_outage_start is None:
                            self.tank_outage_start = current_time
                            if self.debug:
                                print(f"  Tank outage started at {datetime.fromtimestamp(current_time).strftime('%Y-%m-%d %H:%M:%S')}")

                        if self.debug:
                            print(f"  Tank fetch failed ({self.tank_fetch_failures}/{self.max_tank_failures})")
                    else:
                        # Success - check if recovering from outage
                        if self.tank_fetch_failures > 0:
                            if self.debug:
                                print(f"  Tank fetch recovered (was {self.tank_fetch_failures} failures)")

                            # Calculate outage duration
                            if self.tank_outage_start is not None:
                                outage_duration_seconds = current_time - self.tank_outage_start
                                outage_duration_minutes = outage_duration_seconds / 60

                                # Log outage recovery event
                                self.log_tank_outage_recovery(outage_duration_seconds)

                                # Send notification if outage was significant
                                if (NOTIFY_TANK_OUTAGE_ENABLED and
                                    outage_duration_minutes >= NOTIFY_TANK_OUTAGE_THRESHOLD_MINUTES):
                                    self.send_tank_outage_notification(outage_duration_seconds)

                                # Reset outage tracking
                                self.tank_outage_start = None

                        self.tank_fetch_failures = 0

                    # SAFETY: Turn off override only after multiple consecutive failures
                    if self.tank_fetch_failures >= self.max_tank_failures and self.relay_control_enabled:
                        relay_status = self.get_relay_status()
                        if relay_status['supply_override'] == 'ON':
                            if self.debug:
                                print(f"  → SAFETY: Cannot read tank level after {self.tank_fetch_failures} attempts, turning off override to prevent overflow")

                            from monitor.relay import set_supply_override
                            if set_supply_override('OFF', debug=self.debug):
                                self.log_state_event('OVERRIDE_SHUTOFF',
                                    f'Safety shutoff: cannot read tank level after {self.tank_fetch_failures} attempts (possible internet outage)')

                                # Send urgent notification
                                if NOTIFY_OVERRIDE_SHUTOFF and self.notification_manager.can_notify('override_shutoff_safety'):
                                    self.send_alert(
                                        'NOTIFY_OVERRIDE_OFF',
                                        f"⚠️ Override OFF - Tank Unreadable",
                                        f"Override turned off because tank level cannot be read after {self.tank_fetch_failures} attempts (possible internet outage). This prevents overflow.",
                                        priority='urgent'
                                    )

                                # Reset counter after taking action
                                self.tank_fetch_failures = 0

                    if tank_fetch_success:
                        # Log tank level change
                        if prev_gallons and self.state.tank_gallons:
                            if abs(self.state.tank_gallons - prev_gallons) > 0.1:
                                delta = self.state.tank_gallons - prev_gallons
                                self.log_state_event('TANK_LEVEL',
                                                    f'Changed by {delta:+.1f} gal')
                                if self.debug:
                                    print(f"{datetime.now().strftime('%H:%M:%S')} - "
                                         f"Tank: {self.state.tank_gallons:.0f} gal "
                                         f"({delta:+.1f})")

                                # Check for threshold crossings
                                crossings = self.notification_manager.check_tank_threshold_crossing(
                                    self.state.tank_gallons, prev_gallons
                                )
                                for direction, level in crossings:
                                    if self.notification_manager.can_notify(f'tank_{direction}_{level}'):
                                        current_gal = self.state.tank_gallons
                                        if direction == 'decreasing':
                                            title = f"{current_gal:.0f} gal - Tank < {level}"
                                            msg = f"Tank is now < {level} gallons (currently at {current_gal:.0f} gal)"
                                            priority = 'high'
                                        else:
                                            title = f"{current_gal:.0f} gal - Tank > {level}"
                                            msg = f"Tank is now > {level} gallons (currently at {current_gal:.0f} gal)"
                                            priority = 'default'
                                        self.send_alert(
                                            f'NOTIFY_TANK_{direction.upper()}_{level}',
                                            title,
                                            msg,
                                            priority
                                        )
                        
                        # Check for float state change
                        if self.state.float_state != last_float_state:
                            if self.state.float_state == FLOAT_STATE_CALLING:
                                self.log_state_event('FLOAT_CALLING', '⚠️ Tank calling for water!')
                                if self.debug:
                                    print(f"{datetime.now().strftime('%H:%M:%S')} - "
                                         f"⚠️  FLOAT CALLING FOR WATER!")
                            else:
                                self.log_state_event('FLOAT_FULL', 'Tank full')
                                if self.debug:
                                    print(f"{datetime.now().strftime('%H:%M:%S')} - "
                                         f"Float: Tank full")
                            last_float_state = self.state.float_state

                        # Check for float confirmation (CLOSED→OPEN for N consecutive times)
                        # This serves as backup if override is disabled or already off
                        if self.notification_manager.check_float_confirmation(self.state.float_state):
                            if self.notification_manager.can_notify('tank_full'):
                                current_gal = self.state.tank_gallons if self.state.tank_gallons else 0
                                self.send_alert(
                                    'NOTIFY_TANK_FULL',
                                    f"{current_gal:.0f} gal - Tank Full",
                                    "Float sensor confirmed FULL for 3+ readings"
                                )

                        # Check for override auto-on (continuous enforcement)
                        if self.override_on_threshold is not None and self.relay_control_enabled:
                            # Re-read config to allow runtime threshold changes
                            from monitor import config
                            import importlib
                            importlib.reload(config)
                            self.override_on_threshold = config.OVERRIDE_ON_THRESHOLD

                            if self.override_on_threshold is not None:
                                relay_status = self.get_relay_status()
                                if (relay_status['supply_override'] == 'OFF' and
                                    self.state.tank_gallons is not None and
                                    self.state.tank_gallons < self.override_on_threshold):

                                    if self.debug:
                                        print(f"  → Tank at {self.state.tank_gallons} gal (< {self.override_on_threshold}), turning on override...")

                                    from monitor.relay import set_supply_override
                                    if set_supply_override('ON', debug=self.debug):
                                        self.log_state_event('OVERRIDE_AUTO_ON',
                                            f'Auto-on: tank at {self.state.tank_gallons:.0f} gal (threshold: {self.override_on_threshold})')

                                        # Send notification
                                        if NOTIFY_OVERRIDE_SHUTOFF and self.notification_manager.can_notify('override_auto_on'):
                                            self.send_alert(
                                                'NOTIFY_OVERRIDE_ON',
                                                f"{self.state.tank_gallons:.0f} gal - Override ON",
                                                f"Tank dropped to {self.state.tank_gallons:.0f} gal (threshold: {self.override_on_threshold}), override turned on",
                                                priority='default'
                                            )

                        # Check for override shutoff (continuous enforcement)
                        if self.enable_override_shutoff and self.relay_control_enabled:
                            # Re-read config to allow runtime threshold changes
                            from monitor import config
                            import importlib
                            importlib.reload(config)
                            self.override_shutoff_threshold = config.OVERRIDE_SHUTOFF_THRESHOLD

                            relay_status = self.get_relay_status()
                            if (relay_status['supply_override'] == 'ON' and
                                self.state.tank_gallons is not None and
                                self.state.tank_gallons >= self.override_shutoff_threshold):

                                if self.debug:
                                    print(f"  → Tank at {self.state.tank_gallons} gal (>= {self.override_shutoff_threshold}), turning off override...")

                                from monitor.relay import set_supply_override
                                if set_supply_override('OFF', debug=self.debug):
                                    self.log_state_event('OVERRIDE_SHUTOFF',
                                        f'Auto-shutoff: tank at {self.state.tank_gallons:.0f} gal (threshold: {self.override_shutoff_threshold})')

                                    # Send consolidated tank full notification (primary method)
                                    if NOTIFY_OVERRIDE_SHUTOFF and self.notification_manager.can_notify('tank_full'):
                                        self.send_alert(
                                            'NOTIFY_TANK_FULL',
                                            f"{self.state.tank_gallons:.0f} gal - Tank Full",
                                            f"Tank reached {self.state.tank_gallons:.0f} gal (threshold: {self.override_shutoff_threshold}), override turned off",
                                            priority='high'
                                        )

                    self.last_tank_check = current_time

                # WEATHER POLLING
                if ENABLE_AMBIENT_WEATHER and current_time - self.last_weather_check >= self.weather_interval:
                    weather_fetch_success = self.fetch_weather_data()
                    if weather_fetch_success and self.debug:
                        print(f"{datetime.now().strftime('%H:%M:%S')} - Weather: "
                             f"Outdoor {self.state.outdoor_temp}°F, "
                             f"Indoor {self.state.indoor_temp}°F")
                    self.last_weather_check = current_time

                # SNAPSHOT
                if current_time >= self.next_snapshot_time:
                    # Check for well status (recovery or dry)
                    refill_status = self.notification_manager.check_refill_status()
                    if refill_status:
                        status_type, value = refill_status
                        if status_type == 'recovery' and self.notification_manager.can_notify('well_recovery'):
                            current_gal = self.state.tank_gallons if self.state.tank_gallons else 0
                            # value is the stagnation start timestamp
                            stagnation_end_ts = value + timedelta(hours=NOTIFY_WELL_RECOVERY_STAGNATION_HOURS)
                            stagnation_end_str = stagnation_end_ts.strftime('%a %I:%M %p').replace(' 0', ' ')
                            self.send_alert(
                                'NOTIFY_WELL_RECOVERY',
                                f"{current_gal:.0f} gal - Well Recovery",
                                f"Tank gained {NOTIFY_WELL_RECOVERY_THRESHOLD}+ gallons after stagnation period ended {stagnation_end_str}"
                            )

                        elif status_type == 'dry' and self.notification_manager.can_notify('well_dry'):
                            current_gal = self.state.tank_gallons if self.state.tank_gallons else 0
                            self.send_alert(
                                'NOTIFY_WELL_DRY',
                                f"{current_gal:.0f} gal - Well May Be Dry",
                                f"No {NOTIFY_WELL_RECOVERY_THRESHOLD}+ gallon refill in {value:.1f} days",
                                priority='urgent',
                                chart_hours=168
                            )

                    # Check for high flow rate (fast fill mode)
                    high_flow_status = self.notification_manager.check_high_flow_status()
                    if high_flow_status:
                        status_type, gph = high_flow_status
                        if status_type == 'high_flow' and self.notification_manager.can_notify('high_flow'):
                            current_gal = self.state.tank_gallons if self.state.tank_gallons else 0
                            self.send_alert(
                                'NOTIFY_HIGH_FLOW',
                                f"{current_gal:.0f} gal - High Flow {gph:.0f} GPH",
                                f"Tank filling at {gph:.0f} GPH (fast fill mode active)",
                                priority='default'
                            )

                    # Check for backflush event
                    backflush_status = self.notification_manager.check_backflush_status()
                    if backflush_status:
                        status_type, gallons_used, backflush_ts = backflush_status
                        if status_type == 'backflush' and self.notification_manager.can_notify('backflush'):
                            current_gal = self.state.tank_gallons if self.state.tank_gallons else 0
                            backflush_time_str = backflush_ts.strftime('%a %I:%M %p').replace(' 0', ' ')
                            self.send_alert(
                                'NOTIFY_BACKFLUSH',
                                f"{current_gal:.0f} gal - Backflush",
                                f"Carbon filter backflush at {backflush_time_str} used ~{gallons_used:.0f} gallons",
                                priority='default'
                            )

                    # Check for full-flow event (pressure ~100% continuously)
                    full_flow_status = self.notification_manager.check_full_flow_status()
                    if full_flow_status and full_flow_status.get('type') == 'full_flow':
                        if self.notification_manager.can_notify('full_flow'):
                            current_gal = self.state.tank_gallons if self.state.tank_gallons else 0
                            duration_hours = full_flow_status['duration_minutes'] / 60
                            start_time_str = full_flow_status['start_ts'].strftime('%a %I:%M %p').replace(' 0', ' ')

                            # Log full-flow event
                            self.log_event(
                                'FULL_FLOW',
                                notes=f"Full-flow period detected. Started: {start_time_str}, "
                                      f"Duration: {duration_hours:.1f}h, "
                                      f"Pumped: {full_flow_status['total_gallons_pumped']:.0f} gal, "
                                      f"Tank gain: {full_flow_status['tank_gain']:+.0f} gal, "
                                      f"Est. GPH: {full_flow_status['estimated_gph']:.1f}"
                            )

                            # Send notification
                            self.send_alert(
                                'NOTIFY_FULL_FLOW',
                                f"{current_gal:.0f} gal - Full Flow Active",
                                f"System running at full capacity since {start_time_str} ({duration_hours:.1f}h). "
                                f"Pumped {full_flow_status['total_gallons_pumped']:.0f} gal "
                                f"(tank {full_flow_status['tank_gain']:+.0f} gal, ~{full_flow_status['estimated_gph']:.0f} GPH)",
                                priority='default'
                            )

                    tank_data_age = self.get_tank_data_age()
                    snapshot_data = self.snapshot_tracker.get_snapshot_data(
                        self.state.tank_gallons,
                        tank_data_age,
                        self.state.float_state,
                        self.get_relay_status()
                    )

                    # Calculate tank gallons delta
                    tank_gallons_delta = None
                    if self.state.tank_gallons is not None and self.last_snapshot_tank_gallons is not None:
                        tank_gallons_delta = self.state.tank_gallons - self.last_snapshot_tank_gallons

                    # Check occupancy status
                    occupied_status = 'NO'
                    try:
                        reservations = load_reservations('reservations.csv')
                        occupancy = is_occupied(reservations)
                        if occupancy['occupied']:
                            occupied_status = 'YES'
                    except Exception as e:
                        if self.debug:
                            print(f"Could not check occupancy: {e}")

                    log_snapshot(
                        self.snapshots_file,
                        snapshot_data['duration'],
                        snapshot_data['tank_gallons'],
                        tank_gallons_delta,
                        snapshot_data['tank_data_age'],
                        snapshot_data['float_state'],
                        snapshot_data['float_ever_calling'],
                        snapshot_data['float_always_full'],
                        snapshot_data['pressure_high_seconds'],
                        snapshot_data['pressure_high_percent'],
                        snapshot_data['estimated_gallons'],
                        snapshot_data['purge_count'],
                        snapshot_data['relay_status'],
                        occupied_status,
                        self.state.outdoor_temp,
                        self.state.indoor_temp,
                        self.state.outdoor_humidity,
                        self.state.indoor_humidity,
                        self.state.baro_abs,
                        self.state.baro_rel,
                        self.state.wind_speed,
                        self.state.wind_gust
                    )

                    # Update last snapshot tank gallons for next delta calculation
                    self.last_snapshot_tank_gallons = self.state.tank_gallons

                    # TANK_STOPPED_FILLING detection removed - consolidated with 6-hour well stagnation logic
                    # Well recovery notifications already handle stagnant period detection

                    if self.debug:
                        print(f"\n{datetime.now().strftime('%H:%M:%S')} - SNAPSHOT")
                        print(f"  Tank: {snapshot_data['tank_gallons']:.0f} gal "
                             f"(data age: {snapshot_data['tank_data_age']:.0f}s)")
                        print(f"  Float: {snapshot_data['float_state']}")
                        print(f"  Pressure HIGH: {snapshot_data['pressure_high_percent']:.1f}%")
                        print(f"  Pumped: ~{snapshot_data['estimated_gallons']:.1f} gal")
                        if snapshot_data['purge_count'] > 0:
                            print(f"  Purges: {snapshot_data['purge_count']}")
                        print()
                    
                    # Reset for next interval
                    self.snapshot_tracker.reset()
                    self.next_snapshot_time = get_next_snapshot_time(
                        current_time,
                        self.snapshot_interval
                    )

                # DAILY STATUS EMAIL
                if ENABLE_DAILY_STATUS_EMAIL and self.next_daily_status_time and current_time >= self.next_daily_status_time:
                    current_gal = self.state.tank_gallons if self.state.tank_gallons else 0

                    # Check if there's a check-in today
                    checkin_today = False
                    try:
                        reservations = load_reservations('reservations.csv')
                        today = datetime.now().date()
                        for res in reservations:
                            checkin_date = parse_date(res.get('Check-In'))
                            if checkin_date and checkin_date.date() == today:
                                checkin_today = True
                                break
                    except Exception as e:
                        if self.debug:
                            print(f"Could not check for check-ins today: {e}")

                    if self.debug:
                        print(f"\n{datetime.now().strftime('%H:%M:%S')} - DAILY STATUS EMAIL")
                        print(f"  Sending daily status email...")
                        if checkin_today:
                            print(f"  Check-in today - adding heat reminder")

                    # Customize subject and message based on check-in
                    if checkin_today:
                        subject = f"{current_gal:.0f} gal - Turn on heat!"
                        message = f"⚠️ REMINDER: Tenant checking in today - turn on the heat!\n\nDaily status report for {datetime.now().strftime('%A, %B %d, %Y')}"
                    else:
                        subject = f"{current_gal:.0f} gal - Daily Status"
                        message = f"Daily status report for {datetime.now().strftime('%A, %B %d, %Y')}"

                    # Send daily status email
                    send_email_notification(
                        subject=subject,
                        message=message,
                        priority='default',
                        dashboard_url=DASHBOARD_URL,
                        chart_url=f"{DASHBOARD_URL}api/chart.png?hours={DAILY_STATUS_EMAIL_CHART_HOURS}",
                        debug=self.debug,
                        include_status=True
                    )

                    # Log the daily status email
                    self.log_state_event('DAILY_STATUS_EMAIL', f'Daily status email sent - {current_gal:.0f} gal' + (' - Check-in today' if checkin_today else ''))

                    # Schedule next daily status email (same time tomorrow)
                    self.next_daily_status_time = get_next_daily_status_time(
                        current_time,
                        DAILY_STATUS_EMAIL_TIME
                    )

                    if self.debug:
                        next_daily_dt = datetime.fromtimestamp(self.next_daily_status_time)
                        print(f"  Next daily status email at: {next_daily_dt.strftime('%Y-%m-%d %H:%M:%S')}\n")

                # CHECKOUT REMINDER
                if ENABLE_CHECKOUT_REMINDER and self.next_checkout_reminder_time and current_time >= self.next_checkout_reminder_time:
                    current_gal = self.state.tank_gallons if self.state.tank_gallons else 0

                    # Check if there's a checkout today
                    checkout_today = False
                    checkout_guest = None
                    try:
                        reservations = load_reservations('reservations.csv')
                        today = datetime.now().date()
                        for res in reservations:
                            checkout_date = parse_date(res.get('Checkout'))
                            if checkout_date and checkout_date.date() == today:
                                checkout_today = True
                                checkout_guest = res.get('Guest', 'Unknown')
                                break
                    except Exception as e:
                        if self.debug:
                            print(f"Could not check for checkouts today: {e}")

                    # Only send if there's a checkout today
                    if checkout_today:
                        if self.debug:
                            print(f"\n{datetime.now().strftime('%H:%M:%S')} - CHECKOUT REMINDER")
                            print(f"  Sending checkout reminder - {checkout_guest} checking out today")

                        # Send checkout reminder email
                        send_email_notification(
                            subject=f"{current_gal:.0f} gal - Turn down thermostat!",
                            message=f"⚠️ REMINDER: {checkout_guest} checking out today - turn down the thermostat after checkout!",
                            priority='default',
                            dashboard_url=DASHBOARD_URL,
                            chart_url=f"{DASHBOARD_URL}api/chart.png?hours={DAILY_STATUS_EMAIL_CHART_HOURS}",
                            debug=self.debug,
                            include_status=True
                        )

                        # Log the checkout reminder
                        self.log_state_event('CHECKOUT_REMINDER', f'Checkout reminder sent - {checkout_guest} checking out')

                    # Schedule next checkout reminder (same time tomorrow)
                    self.next_checkout_reminder_time = get_next_daily_status_time(
                        current_time,
                        CHECKOUT_REMINDER_TIME
                    )

                    if self.debug:
                        next_checkout_dt = datetime.fromtimestamp(self.next_checkout_reminder_time)
                        print(f"  Next checkout reminder at: {next_checkout_dt.strftime('%Y-%m-%d %H:%M:%S')}\n")

                time.sleep(self.poll_interval)
        
        except Exception as e:
            if self.debug:
                print(f"\nError: {e}")
                import traceback
                traceback.print_exc()
            self.shutdown()
            raise
        
        self.shutdown()
    
    def shutdown(self):
        """Clean shutdown"""
        self.log_state_event('SHUTDOWN', 'Clean shutdown')
        
        if self.relay_control_enabled:
            from monitor.relay import cleanup_relays
            cleanup_relays()
        
        if self.debug:
            print("\n✓ Monitor stopped")
