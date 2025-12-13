#!/usr/bin/env python3
"""
Test script to grab a camera snapshot and save it to a file
"""
from monitor.camera import get_camera_snapshot

print("Attempting to get camera snapshot...")
success, result = get_camera_snapshot('Filter spycam ')

if success:
    # Save the snapshot to a file
    with open('/home/pi/camera_snapshot.jpg', 'wb') as f:
        f.write(result)
    print(f"✓ Success! Snapshot saved to /home/pi/camera_snapshot.jpg")
    print(f"  Image size: {len(result)} bytes")
else:
    print(f"✗ Failed: {result}")
    print("\nTroubleshooting steps:")
    print("1. Make sure there's been recent motion in front of the camera")
    print("2. Wave your hand in front of the 'Filter spycam ' camera")
    print("3. Wait a few seconds for Wyze to process the event")
    print("4. Try running this script again")
