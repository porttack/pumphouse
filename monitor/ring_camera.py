"""
Shared Ring camera snapshot fetcher with file-based cache.

The JPEG is cached to RING_CACHE_FILE on disk, shared across all processes
(gunicorn workers, monitor daemon, email notifier). A lock file prevents
multiple processes from hitting the Ring API simultaneously.

Cache TTL is controlled by RING_CACHE_MINUTES in config.py.
"""
import asyncio
import fcntl
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def get_snapshot(token_file: Path, camera_name: str = '') -> Optional[bytes]:
    """
    Return a Ring camera JPEG as bytes, using a file-based shared cache.
    All processes (gunicorn workers, monitor daemon) share the same cache file
    so Ring is only called once per RING_CACHE_MINUTES regardless of load.
    Returns None if the token file is missing or Ring is unreachable.
    """
    from monitor.config import RING_CACHE_FILE, RING_CACHE_MINUTES

    cache_ttl = RING_CACHE_MINUTES * 60
    lock_file = RING_CACHE_FILE.with_suffix('.lock')

    # Fast path: cache file is fresh — no lock needed
    if RING_CACHE_FILE.exists():
        age = time.time() - RING_CACHE_FILE.stat().st_mtime
        if age < cache_ttl:
            return RING_CACHE_FILE.read_bytes()

    # Cache is stale or missing — acquire lock and fetch
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_file, 'w') as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Another process is fetching — wait for it then return whatever exists
            fcntl.flock(lf, fcntl.LOCK_EX)
            fcntl.flock(lf, fcntl.LOCK_UN)
            return RING_CACHE_FILE.read_bytes() if RING_CACHE_FILE.exists() else None

        try:
            # Re-check after acquiring lock (another process may have just fetched)
            if RING_CACHE_FILE.exists():
                age = time.time() - RING_CACHE_FILE.stat().st_mtime
                if age < cache_ttl:
                    return RING_CACHE_FILE.read_bytes()

            data = _fetch_from_ring(token_file, camera_name)
            if data:
                data = _stamp_timestamp(data)
                RING_CACHE_FILE.write_bytes(data)
                logger.info('Ring snapshot fetched and cached (%d bytes)', len(data))
            else:
                logger.warning('Ring snapshot fetch returned nothing; endpoint will return unavailable')
            return data
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _fetch_from_ring(token_file: Path, camera_name: str) -> Optional[bytes]:
    """Fetch a raw JPEG from the Ring API. No caching."""
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
            return loop.run_until_complete(_fetch())
        finally:
            loop.close()
            asyncio.set_event_loop(None)
    except Exception as e:
        logger.error('Ring snapshot error: %s', e)
        return None


def _stamp_timestamp(jpeg_bytes: bytes) -> bytes:
    """
    Overlay the current time on the bottom-left of the JPEG image.
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
    label = datetime.now().strftime('%-I:%M %p')
    font  = cv2.FONT_HERSHEY_SIMPLEX
    scale = max(0.65, w / 2200)
    thick = max(1, round(scale * 2))

    (tw, th), baseline = cv2.getTextSize(label, font, scale, thick)
    margin = max(8, round(w * 0.006))
    x = margin
    y = h - margin

    pad = margin // 2
    overlay = img.copy()
    cv2.rectangle(overlay, (x - pad, y - th - pad), (x + tw + pad, y + baseline + pad),
                  (0, 0, 0), cv2.FILLED)
    cv2.addWeighted(overlay, 0.45, img, 0.55, 0, img)
    cv2.putText(img, label, (x, y), font, scale, (255, 255, 255), thick, cv2.LINE_AA)

    _, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return buf.tobytes()


def get_cache_mtime() -> Optional[float]:
    """Return the mtime of the cache file, or None if it doesn't exist."""
    from monitor.config import RING_CACHE_FILE
    return RING_CACHE_FILE.stat().st_mtime if RING_CACHE_FILE.exists() else None
