#!/bin/bash
# fix_snapshot_gallons_and_simplify.sh

cat > monitor/poll.py << 'EOF_POLL'
"""
Simplified event-based polling loop
"""
import time
from datetime import datetime
import signal

from monitor.config import (
    POLL_INTERVAL, TANK_POLL_INTERVAL, SNAPSHOT_INTERVAL,
    RESIDUAL_PRESSURE_SECONDS, SECONDS_PER_GALLON
)
from monitor.gpio_helpers import read_pressure, read_float_sensor
from monitor.tank import get_tank_data
from monitor.logger import log_event, log_snapshot
from monitor.state import SystemState

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
        
        float_ever_calling = 'CLOSED/CALLING' in self.float_states
        float_always_full = (len(self.float_states) > 0 and 
                            all(s == 'OPEN/FULL' for s in self.float_states))
        
        pressure_high_percent = (self.pressure_high_time / duration * 100) if duration > 0 else 0
        
        return {
            'duration': duration,
            'tank_gallons': tank_gallons,
            'tank_data_age': tank_data_age,
            'float_state': current_float,
            'float_ever_calling': float_ever_calling,
            'float_always_full': float_always_full,
            'pressure_high_seconds': self.pressure_high_time,
            'pressure_high_percent': pressure_high_percent,
            'estimated_gallons': self.estimated_gallons,
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
        
        # Relay control
        self.relay_control_enabled = False
        
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        if self.debug:
            print(f"\nReceived signal {signum}, shutting down...")
        self.running = False
    
    def enable_relay_control(self):
        """Enable relay control"""
        from monitor.relay import init_relays
        if init_relays():
            self.relay_control_enabled = True
            if self.debug:
                print("Relay control enabled")
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
            self.tank_last_updated = time.time()
            self.snapshot_tracker.update_float(data['float_state'])
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
    
    def run(self):
        """Main monitoring loop"""
        # Initial state
        self.last_pressure_state = read_pressure()
        self.fetch_tank_data()
        
        # Log initial state
        self.log_state_event('INIT', 'System startup')
        
        # Initialize snapshot timing
        self.next_snapshot_time = get_next_snapshot_time(
            time.time(), 
            self.snapshot_interval
        )
        
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
                    else:  # Went LOW
                        if self.pressure_high_start:
                            duration = current_time - self.pressure_high_start
                            estimated = estimate_gallons(duration)
                            self.log_pressure_event('PRESSURE_LOW', estimated,
                                                    f'Duration: {duration:.1f}s')
                            if self.debug:
                                print(f"{datetime.now().strftime('%H:%M:%S')} - Pressure LOW "
                                     f"(was HIGH for {duration:.1f}s, ~{estimated:.1f} gal)")
                            
                            # Trigger purge if enabled
                            if self.relay_control_enabled and estimated > 0:
                                if self.debug:
                                    print("  → Triggering filter purge...")
                                from monitor.relay import purge_spindown_filter
                                if purge_spindown_filter(debug=self.debug):
                                    self.log_state_event('PURGE', 'Auto-purge after water delivery')
                                    self.snapshot_tracker.increment_purge()
                        
                        self.pressure_high_start = None
                    
                    self.last_pressure_state = current_pressure
                
                # TANK POLLING
                if current_time - self.last_tank_check >= self.tank_interval:
                    prev_gallons = self.state.tank_gallons
                    if self.fetch_tank_data():
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
                        
                        # Check for float state change
                        if self.state.float_state != last_float_state:
                            if self.state.float_state == 'CLOSED/CALLING':
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
                    
                    self.last_tank_check = current_time
                
                # SNAPSHOT
                if current_time >= self.next_snapshot_time:
                    tank_data_age = self.get_tank_data_age()
                    snapshot_data = self.snapshot_tracker.get_snapshot_data(
                        self.state.tank_gallons,
                        tank_data_age,
                        self.state.float_state,
                        self.get_relay_status()
                    )
                    
                    log_snapshot(
                        self.snapshots_file,
                        snapshot_data['duration'],
                        snapshot_data['tank_gallons'],
                        snapshot_data['tank_data_age'],
                        snapshot_data['float_state'],
                        snapshot_data['float_ever_calling'],
                        snapshot_data['float_always_full'],
                        snapshot_data['pressure_high_seconds'],
                        snapshot_data['pressure_high_percent'],
                        snapshot_data['estimated_gallons'],
                        snapshot_data['purge_count'],
                        snapshot_data['relay_status']
                    )
                    
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
EOF_POLL

# Update logger.py with new snapshot format
cat > monitor/logger.py << 'EOF_LOGGER'
"""
Logging functions for events and snapshots
"""
import csv
from datetime import datetime

def initialize_events_csv(filepath):
    """Initialize events CSV file with headers"""
    try:
        with open(filepath, 'x', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'timestamp', 'event_type', 'pressure_state', 'float_state',
                'tank_gallons', 'tank_depth', 'tank_percentage', 
                'estimated_gallons', 'relay_bypass', 'relay_supply_override', 'notes'
            ])
        return True
    except FileExistsError:
        return False

def initialize_snapshots_csv(filepath):
    """Initialize snapshots CSV file with headers"""
    try:
        with open(filepath, 'x', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'timestamp', 'duration_seconds',
                'tank_gallons', 'tank_data_age_seconds',
                'float_state', 'float_ever_calling', 'float_always_full',
                'pressure_high_seconds', 'pressure_high_percent',
                'estimated_gallons_pumped', 'purge_count',
                'relay_bypass', 'relay_supply_override'
            ])
        return True
    except FileExistsError:
        return False

def log_event(filepath, event_type, pressure_state, float_state, tank_gallons,
              tank_depth, tank_percentage, estimated_gallons, relay_status, notes=''):
    """Log an event to events.csv"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    
    if pressure_state is None:
        pressure_str = 'UNKNOWN'
    elif pressure_state:
        pressure_str = 'HIGH'
    else:
        pressure_str = 'LOW'
    
    with open(filepath, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            timestamp, event_type, pressure_str, float_state or '',
            f'{tank_gallons:.0f}' if tank_gallons else '',
            f'{tank_depth:.2f}' if tank_depth else '',
            f'{tank_percentage:.1f}' if tank_percentage else '',
            f'{estimated_gallons:.2f}' if estimated_gallons is not None else '',
            relay_status.get('bypass', ''),
            relay_status.get('supply_override', ''),
            notes
        ])

def log_snapshot(filepath, duration, tank_gallons, tank_data_age,
                float_state, float_ever_calling, float_always_full,
                pressure_high_seconds, pressure_high_percent,
                estimated_gallons, purge_count, relay_status):
    """Log a snapshot to snapshots.csv"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    
    with open(filepath, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            timestamp, f'{duration:.0f}',
            f'{tank_gallons:.0f}' if tank_gallons else '',
            f'{tank_data_age:.0f}' if tank_data_age else '',
            float_state or '',
            'Yes' if float_ever_calling else 'No',
            'Yes' if float_always_full else 'No',
            f'{pressure_high_seconds:.0f}',
            f'{pressure_high_percent:.1f}',
            f'{estimated_gallons:.2f}',
            purge_count,
            relay_status.get('bypass', ''),
            relay_status.get('supply_override', '')
        ])
EOF_LOGGER

echo "✓ Fixed both issues:"
echo "  1. Estimated gallons now added to snapshot BEFORE logging (not after)"
echo "  2. Simplified tank data: single 'tank_gallons' + 'tank_data_age_seconds'"
echo ""
echo "New snapshot format:"
echo "  - tank_gallons: Current value"
echo "  - tank_data_age_seconds: How old the data is"
echo "  (Removed: tank_start/end/min/max)"