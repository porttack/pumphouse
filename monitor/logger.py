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
                'tank_gallons', 'tank_gallons_delta', 'tank_data_age_seconds',
                'float_state', 'float_ever_calling', 'float_always_full',
                'pressure_high_seconds', 'pressure_high_percent',
                'estimated_gallons_pumped', 'purge_count',
                'relay_bypass', 'relay_supply_override', 'occupied',
                'outdoor_temp_f', 'indoor_temp_f', 'outdoor_humidity',
                'baro_abs_inhg', 'wind_gust_mph'
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
            relay_status.get('bypass', '') if relay_status else '',
            relay_status.get('supply_override', '') if relay_status else '',
            notes
        ])

def log_snapshot(filepath, duration, tank_gallons, tank_gallons_delta, tank_data_age,
                float_state, float_ever_calling, float_always_full,
                pressure_high_seconds, pressure_high_percent,
                estimated_gallons, purge_count, relay_status, occupied='',
                outdoor_temp=None, indoor_temp=None, outdoor_humidity=None,
                baro_abs=None, wind_gust=None):
    """Log a snapshot to snapshots.csv"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

    # Format tank_gallons_delta with explicit sign (+0, -N, +N)
    if tank_gallons_delta is not None:
        delta_str = f'{tank_gallons_delta:+.0f}'  # Always includes sign
    else:
        delta_str = ''

    with open(filepath, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            timestamp, f'{duration:.0f}',
            f'{tank_gallons:.0f}' if tank_gallons else '',
            delta_str,
            f'{tank_data_age:.0f}' if tank_data_age else '',
            float_state or '',
            'Yes' if float_ever_calling else 'No',
            'Yes' if float_always_full else 'No',
            f'{pressure_high_seconds:.0f}',
            f'{pressure_high_percent:.1f}',
            f'{estimated_gallons:+.2f}',  # Always includes sign
            purge_count,
            relay_status.get('bypass', '') if relay_status else '',
            relay_status.get('supply_override', '') if relay_status else '',
            occupied or '',
            f'{outdoor_temp:.1f}' if outdoor_temp is not None else '',
            f'{indoor_temp:.1f}' if indoor_temp is not None else '',
            f'{outdoor_humidity:.0f}' if outdoor_humidity is not None else '',
            f'{baro_abs:.3f}' if baro_abs is not None else '',
            f'{wind_gust:.1f}' if wind_gust is not None else ''
        ])
