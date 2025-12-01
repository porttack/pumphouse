#!/usr/bin/env python3
"""
Wyze camera snapshot functionality
"""
import os
from wyze_sdk import Client
from wyze_sdk.errors import WyzeApiError

# Wyze credentials from environment
WYZE_EMAIL = os.environ.get('WYZE_EMAIL', 'cathyanderichome@gmail.com')
WYZE_PASSWORD = os.environ.get('WYZE_PASSWORD', 'Pumphouse123!')
# CamPlus subscription API credentials
WYZE_API_KEY = os.environ.get('WYZE_API_KEY', 'VuWM76VoJCmtcJIcs4xpRHyUaR8Fp7f5vJ2lgXisHkFtXEkf0wLNv7n2txVt')
WYZE_API_ID = os.environ.get('WYZE_API_ID', '72402a29-8a35-463d-8a16-79377ed96797')

# Camera name to monitor
CAMERA_NAME = os.environ.get('WYZE_CAMERA_NAME', 'Filter spycam')

def get_camera_snapshot(camera_name=None):
    """
    Get a snapshot from a Wyze camera via wyze-bridge

    Args:
        camera_name: Name of the camera (default: from WYZE_CAMERA_NAME env var)

    Returns:
        tuple: (success: bool, data: bytes or error_message: str)
    """
    if camera_name is None:
        camera_name = CAMERA_NAME

    try:
        import requests

        # Try to get snapshot from wyze-bridge HTTP server
        # The wyze-bridge provides snapshots at /api/<camera-name>/snapshot.jpg
        # Camera names are normalized (lowercased, spaces/special chars removed)

        # Normalize camera name for wyze-bridge
        normalized_name = camera_name.lower().strip().replace(' ', '-')

        # Try the wyze-bridge snapshot endpoint
        snapshot_url = f"http://localhost:8888/img/{normalized_name}.jpg"

        try:
            response = requests.get(snapshot_url, timeout=5)
            if response.status_code == 200:
                return True, response.content
        except:
            pass

        # If wyze-bridge fails, fall back to Wyze API events
        try:
            # Initialize Wyze client
            client = Client(email=WYZE_EMAIL, password=WYZE_PASSWORD,
                           key_id=WYZE_API_ID, api_key=WYZE_API_KEY)

            # Get list of cameras
            cameras = client.devices_list()

            # Find the target camera
            target_camera = None
            for device in cameras:
                if hasattr(device, 'nickname') and device.nickname == camera_name:
                    target_camera = device
                    break

            if not target_camera:
                available = [d.nickname for d in cameras if hasattr(d, 'nickname')]
                return False, f"Camera '{camera_name}' not found. Available cameras: {available}"

            # Try to get recent events (within last 24 hours)
            events = client.events.list(device_ids=[target_camera.mac], limit=10)

            if not events or len(events) == 0:
                return False, "No recent camera events and wyze-bridge not available"

            # Get the most recent event
            latest_event = events[0]

            # Get the thumbnail URL from the event
            if hasattr(latest_event, 'file_list') and latest_event.file_list and len(latest_event.file_list) > 0:
                thumbnail_url = latest_event.file_list[0].url
                response = requests.get(thumbnail_url, timeout=10)
                response.raise_for_status()
                return True, response.content
            else:
                return False, "No thumbnail available"

        except Exception as e:
            return False, f"Error: {str(e)}"

    except Exception as e:
        return False, f"Error getting camera snapshot: {str(e)}"

def list_cameras():
    """
    List all available Wyze cameras

    Returns:
        tuple: (success: bool, cameras: list or error_message: str)
    """
    try:
        client = Client(email=WYZE_EMAIL, password=WYZE_PASSWORD,
                       key_id=WYZE_API_ID, api_key=WYZE_API_KEY)
        cameras = client.devices_list()

        camera_list = []
        for device in cameras:
            if hasattr(device, 'nickname'):
                camera_list.append({
                    'name': device.nickname,
                    'mac': device.mac,
                    'product_model': getattr(device, 'product_model', 'Unknown')
                })

        return True, camera_list

    except WyzeApiError as e:
        return False, f"Wyze API error: {str(e)}"
    except Exception as e:
        return False, f"Error listing cameras: {str(e)}"
