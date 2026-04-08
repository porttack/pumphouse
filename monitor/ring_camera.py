"""
Shared Ring camera snapshot fetcher with in-memory cache.

Used by both the web dashboard (/ring-snapshot route) and the email notifier
so each can fetch Ring snapshots without duplicating async/auth logic.
Each OS process keeps its own 60-second cache independently.
"""
import asyncio
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_TTL = 60  # seconds

_lock             = threading.Lock()
_cached_bytes:    Optional[bytes] = None
_cached_at_mono:  float = 0.0   # time.monotonic() when last fetched
_cached_at_epoch: float = 0.0   # time.time() when last fetched (for X-Ring-Time header)


def get_snapshot(token_file: Path, camera_name: str = '') -> Optional[bytes]:
    """
    Return a Ring camera JPEG as bytes, using a 60-second in-memory cache.
    Returns None if the token file is missing, Ring is unreachable, or no
    cameras are found.
    """
    global _cached_bytes, _cached_at_mono, _cached_at_epoch

    now_mono = time.monotonic()
    with _lock:
        if _cached_bytes is not None and (now_mono - _cached_at_mono) < _CACHE_TTL:
            return _cached_bytes

    if not token_file.exists():
        logger.warning('Ring token file not found: %s', token_file)
        return None

    try:
        token_data = json.loads(token_file.read_text())
    except Exception as e:
        logger.error('Ring token unreadable: %s', e)
        return None

    async def _fetch() -> Optional[bytes]:
        from ring_doorbell import Auth, Ring

        def _save(new_token: dict) -> None:
            token_file.write_text(json.dumps(new_token))

        auth = Auth('pumphouse/1.0', token_data, _save)
        ring = Ring(auth)
        try:
            await ring.async_create_session()
            await ring.async_update_data()
            devices = ring.video_devices()
            if not devices:
                logger.warning('No Ring video devices found')
                return None
            cam = devices[0]
            if camera_name:
                match = next((d for d in devices if d.name == camera_name), None)
                if match is None:
                    logger.warning('Ring camera %r not found; using first device', camera_name)
                else:
                    cam = match
            return await cam.async_get_snapshot()
        finally:
            await auth.async_close()

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            data = loop.run_until_complete(_fetch())
        finally:
            loop.close()
            asyncio.set_event_loop(None)
    except Exception as e:
        logger.error('Ring snapshot error: %s', e)
        return None

    if data:
        data = _stamp_timestamp(data)
        with _lock:
            _cached_bytes    = data
            _cached_at_mono  = time.monotonic()
            _cached_at_epoch = time.time()

    return data


def _stamp_timestamp(jpeg_bytes: bytes) -> bytes:
    """
    Overlay the current time on the bottom-left of the JPEG image.
    Uses a black drop-shadow behind white text for readability on any background.
    Falls back to the original bytes if cv2 is unavailable or decoding fails.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return jpeg_bytes

    data = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    img  = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        return jpeg_bytes

    h, w  = img.shape[:2]
    label = datetime.now().strftime('%-I:%M %p')   # e.g. "2:34 PM"
    font  = cv2.FONT_HERSHEY_SIMPLEX
    scale = max(0.65, w / 2200)                     # scales with image width
    thick = max(1, round(scale * 2))

    (tw, th), baseline = cv2.getTextSize(label, font, scale, thick)
    margin = max(8, round(w * 0.006))
    x = margin
    y = h - margin

    # Semi-transparent dark background rectangle for contrast
    pad = margin // 2
    overlay = img.copy()
    cv2.rectangle(overlay, (x - pad, y - th - pad), (x + tw + pad, y + baseline + pad),
                  (0, 0, 0), cv2.FILLED)
    cv2.addWeighted(overlay, 0.45, img, 0.55, 0, img)

    # White text
    cv2.putText(img, label, (x, y), font, scale, (255, 255, 255), thick, cv2.LINE_AA)

    _, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return buf.tobytes()


def get_fetched_epoch() -> Optional[float]:
    """Return the Unix timestamp when the cached snapshot was last fetched, or None."""
    with _lock:
        return _cached_at_epoch if _cached_bytes is not None else None
