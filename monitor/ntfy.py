"""
ntfy.sh notification sender
Simple HTTP POST client for ntfy.sh push notifications
"""
import requests
import sys
from monitor.config import NTFY_SERVER, NTFY_TOPIC

def send_notification(title, message, priority='default', tags=None, click_url=None, attach_url=None, debug=False):
    """
    Send notification via ntfy.sh

    Args:
        title: Notification title
        message: Notification body
        priority: 'min', 'low', 'default', 'high', 'urgent'
        tags: List of emoji tags (e.g., ['warning', 'droplet'])
        click_url: URL to open when notification is clicked (optional)
        attach_url: URL of image/file to attach to notification (optional)
        debug: Print debug info

    Returns:
        True if sent successfully, False otherwise
    """
    if not NTFY_TOPIC or NTFY_TOPIC == "pumphouse-CHANGE-ME":
        if debug:
            print("NTFY_TOPIC not configured, skipping notification", file=sys.stderr)
        return False

    # Send to topic-specific URL with message body and metadata in headers
    url = f"{NTFY_SERVER}/{NTFY_TOPIC}"
    headers = {}

    # Encode title as UTF-8 then decode as latin-1 for HTTP headers
    # This is a workaround for requests library's header encoding limitation
    try:
        headers["X-Title"] = title.encode('utf-8').decode('latin-1')
    except (UnicodeEncodeError, UnicodeDecodeError):
        # Fallback: strip emojis if encoding fails
        headers["X-Title"] = title.encode('ascii', 'ignore').decode('ascii')

    headers["X-Priority"] = priority
    if tags:
        headers["X-Tags"] = ",".join(tags)
    if click_url:
        headers["X-Click"] = click_url
    if attach_url:
        headers["X-Attach"] = attach_url

    try:
        # Send message as plain text body
        if debug:
            print(f"Sending to URL: {url}")
            print(f"Headers: {headers}")
        response = requests.post(url, data=message.encode('utf-8'), headers=headers, timeout=5)
        response.raise_for_status()
        if debug:
            print(f"Response: {response.text}")
            print(f"Notification sent: {title}")
        return True
    except Exception as e:
        print(f"Failed to send notification: {e}", file=sys.stderr)
        return False

def test_ping(debug=False):
    """Send a test notification to verify ntfy integration"""
    from monitor.config import DASHBOARD_URL
    return send_notification(
        title="🏠 Pumphouse Test",
        message="ntfy.sh integration is working!",
        priority="low",
        tags=["white_check_mark"],
        click_url=DASHBOARD_URL,
        attach_url=f"{DASHBOARD_URL}api/chart.png?hours=24",
        debug=debug
    )
