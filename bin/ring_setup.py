#!/usr/bin/env python3
"""
One-time Ring camera authentication setup.

Run this once to authenticate with Ring and save an OAuth token to disk.
The web dashboard's /ring-snapshot route reads this token file automatically
and refreshes it as needed — no re-running required unless you change your
Ring password or the refresh token expires (~12 months).

Usage:
    cd /home/pi/src/pumphouse && source venv/bin/activate
    python3 bin/ring_setup.py
"""
import asyncio
import getpass
import json
import sys
from pathlib import Path

TOKEN_FILE = Path.home() / '.config' / 'pumphouse' / 'ring_token.json'


async def main():
    try:
        from ring_doorbell import Auth, Ring
        from ring_doorbell.exceptions import AuthenticationError, Requires2FAError
    except ImportError:
        print("ring_doorbell not installed. Run: pip install ring_doorbell")
        sys.exit(1)

    print("Ring Camera Setup")
    print("=" * 40)
    email    = input("Ring account email: ").strip()
    password = getpass.getpass("Ring account password: ")

    def save_token(token):
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(json.dumps(token))

    auth = Auth("pumphouse/1.0", None, save_token)

    try:
        token = await auth.async_fetch_token(email, password)
    except Requires2FAError:
        otp = input("Enter the 2FA code sent to your phone/email: ").strip()
        token = await auth.async_fetch_token(email, password, otp)
    except AuthenticationError as e:
        print(f"Authentication failed: {e}")
        sys.exit(1)

    save_token(token)
    print(f"\nToken saved to {TOKEN_FILE}")

    # Verify by listing devices
    auth2 = Auth("pumphouse/1.0", json.loads(TOKEN_FILE.read_text()), save_token)
    ring  = Ring(auth2)
    try:
        await ring.async_create_session()
        await ring.async_update_data()
        devices = ring.video_devices()

        print(f"\nFound {len(devices)} Ring camera(s):")
        for d in devices:
            print(f"  - {d.name!r}")

        if devices:
            print("\nIf you have more than one camera, add this to secrets.conf:")
            print("  RING_CAMERA_NAME=<name from list above>")
            print("If you only have one camera, no extra configuration needed.")
        else:
            print("\nNo cameras found. Check your Ring account.")
    finally:
        await auth.async_close()
        await auth2.async_close()


if __name__ == '__main__':
    asyncio.run(main())
