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
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Separate cache for vehicle count (updated from Ring snapshots, read by epaper)
_VEHICLE_COUNT_CACHE = Path.home() / '.config' / 'pumphouse' / 'vehicle_count.json'
_VEHICLE_COUNT_TTL   = 2 * 3600  # seconds

# Directory for ML model files (lazy-downloaded on first vehicle-count call)
_MODELS_DIR = Path(__file__).parent.parent / 'models'
_YOLO_CFG    = _MODELS_DIR / 'yolov4-tiny.cfg'
_YOLO_WEIGHTS = _MODELS_DIR / 'yolov4-tiny.weights'
_COCO_NAMES  = _MODELS_DIR / 'coco.names'

# COCO class indices that count as "vehicles"
_VEHICLE_CLASSES = {2, 3, 5, 7}  # car, motorcycle, bus, truck

_MODEL_URLS = {
    _YOLO_CFG:     'https://raw.githubusercontent.com/AlexeyAB/darknet/master/cfg/yolov4-tiny.cfg',
    _YOLO_WEIGHTS: 'https://github.com/AlexeyAB/darknet/releases/download/yolov4/yolov4-tiny.weights',
    _COCO_NAMES:   'https://raw.githubusercontent.com/AlexeyAB/darknet/master/data/coco.names',
}


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
            if data is None:
                logger.warning('Ring fetch attempt 1 failed; retrying in 5s')
                time.sleep(5)
                data = _fetch_from_ring(token_file, camera_name)

            if data:
                vehicle_count = _count_vehicles(data)
                if vehicle_count is not None:
                    _write_count_cache(vehicle_count)
                data = _stamp_timestamp(data, vehicle_count)
                data = _add_exif_metadata(data, vehicle_count)
                RING_CACHE_FILE.write_bytes(data)
                logger.info('Ring snapshot fetched and cached (%d bytes, vehicles=%s)',
                            len(data), vehicle_count)
            else:
                if RING_CACHE_FILE.exists():
                    age_s = int(time.time() - RING_CACHE_FILE.stat().st_mtime)
                    logger.warning('Ring snapshot fetch failed after retry; serving stale cache (%ds old)', age_s)
                    return _stamp_stale(RING_CACHE_FILE.read_bytes(), age_s)
                logger.warning('Ring snapshot fetch returned nothing after retry; no cache to fall back on')
            return data
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def get_vehicle_count() -> Optional[int]:
    """
    Return the most recently cached vehicle count.

    Refreshes by running YOLO on the existing Ring snapshot cache file —
    no Ring API call is made here. Skips refresh between midnight and 8am.
    Returns None if no count is available at all.
    """
    now = datetime.now()
    cached_count, cached_ts = _read_count_cache()

    # Midnight–8am: stale is fine, don't wake up the camera
    if now.hour < 8:
        return cached_count

    # Still fresh?
    if cached_ts is not None and (time.time() - cached_ts) < _VEHICLE_COUNT_TTL:
        return cached_count

    # Try to refresh from the existing Ring snapshot (no new API call)
    try:
        from monitor.config import RING_CACHE_FILE
        if not RING_CACHE_FILE.exists():
            return cached_count
        count = _count_vehicles(RING_CACHE_FILE.read_bytes())
        if count is not None:
            _write_count_cache(count)
            return count
    except Exception as e:
        logger.warning('Vehicle count refresh failed (%s: %s)', type(e).__name__, e)

    return cached_count


def _read_count_cache():
    """Return (count, timestamp) from the vehicle count cache, or (None, None)."""
    try:
        data = json.loads(_VEHICLE_COUNT_CACHE.read_text())
        return data.get('count'), data.get('ts')
    except Exception:
        return None, None


def _write_count_cache(count: int) -> None:
    _VEHICLE_COUNT_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _VEHICLE_COUNT_CACHE.write_text(json.dumps({'count': count, 'ts': time.time()}))


def _fetch_from_ring(token_file: Path, camera_name: str) -> Optional[bytes]:
    """Fetch a raw JPEG from the Ring API. No caching."""
    if not token_file.exists():
        logger.warning('Ring token file not found: %s', token_file)
        return None

    try:
        token_data = json.loads(token_file.read_text())
    except Exception as e:
        logger.error('Ring token unreadable (%s: %s)', type(e).__name__, e)
        return None

    async def _fetch() -> Optional[bytes]:
        from ring_doorbell import Auth, Ring

        def _save(new_token: dict) -> None:
            token_file.write_text(json.dumps(new_token))

        auth = Auth('pumphouse/1.0', token_data, _save)
        ring = Ring(auth)
        try:
            logger.debug('Ring: creating session')
            await ring.async_create_session()
            logger.debug('Ring: session created; updating data')
            await ring.async_update_data()
            devices = ring.video_devices()
            logger.info('Ring: found %d video device(s): %s',
                        len(devices), [d.name for d in devices])
            if not devices:
                logger.warning('Ring: no video devices found — account may have no cameras')
                return None
            cam = devices[0]
            if camera_name:
                match = next((d for d in devices if d.name == camera_name), None)
                if match is None:
                    logger.warning('Ring: camera %r not found in [%s]; using first device %r',
                                   camera_name,
                                   ', '.join(d.name for d in devices),
                                   cam.name)
                else:
                    cam = match
            logger.debug('Ring: requesting snapshot from %r', cam.name)
            data = await cam.async_get_snapshot()
            if data:
                logger.debug('Ring: snapshot received (%d bytes) from %r', len(data), cam.name)
            else:
                logger.warning('Ring: async_get_snapshot returned empty/None for camera %r', cam.name)
            return data
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
        logger.error('Ring snapshot error (%s: %s)', type(e).__name__, e)
        return None


def _stamp_timestamp(jpeg_bytes: bytes, vehicle_count: Optional[int] = None) -> bytes:
    """
    Overlay the current time (and vehicle count if available) on the bottom-left.
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
    time_str = datetime.now().strftime('%-I:%M %p')
    if vehicle_count is not None:
        _num_words = ['Zero','One','Two','Three','Four','Five',
                      'Six','Seven','Eight','Nine','Ten']
        _vc_word = _num_words[vehicle_count] if 0 <= vehicle_count < len(_num_words) else str(vehicle_count)
        label = f'{time_str}  {_vc_word} car{"s" if vehicle_count != 1 else ""}'
    else:
        label = time_str
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


def _stamp_stale(jpeg_bytes: bytes, age_s: int) -> bytes:
    """Overlay 'stale Xm' in the bottom-right corner of a JPEG."""
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
    age_m = age_s // 60
    label = f'stale {age_m}m' if age_m >= 1 else 'stale'
    font  = cv2.FONT_HERSHEY_SIMPLEX
    scale = max(0.5, w / 2200)
    thick = max(1, round(scale * 2))

    (tw, th), baseline = cv2.getTextSize(label, font, scale, thick)
    margin = max(8, round(w * 0.006))
    x = w - tw - margin
    y = h - margin

    pad = margin // 2
    overlay = img.copy()
    cv2.rectangle(overlay, (x - pad, y - th - pad), (x + tw + pad, y + baseline + pad),
                  (0, 0, 0), cv2.FILLED)
    cv2.addWeighted(overlay, 0.45, img, 0.55, 0, img)
    cv2.putText(img, label, (x, y), font, scale, (0, 165, 255), thick, cv2.LINE_AA)  # orange

    _, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return buf.tobytes()


def _ensure_models() -> bool:
    """Download YOLOv4-tiny model files if not already present. Returns True if ready."""
    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for path, url in _MODEL_URLS.items():
        if not path.exists():
            logger.info('Downloading %s from %s', path.name, url)
            try:
                urllib.request.urlretrieve(url, path)
                logger.info('Downloaded %s (%d bytes)', path.name, path.stat().st_size)
            except Exception as e:
                logger.error('Failed to download %s (%s: %s)', path.name, type(e).__name__, e)
                return False
    return True


def _count_vehicles(jpeg_bytes: bytes) -> Optional[int]:
    """
    Count vehicles (cars, trucks, buses, motorcycles) in a JPEG using YOLOv4-tiny.
    Returns None if the model is unavailable or inference fails.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    if not _ensure_models():
        return None

    try:
        net = cv2.dnn.readNetFromDarknet(str(_YOLO_CFG), str(_YOLO_WEIGHTS))
        net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

        data = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        img  = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is None:
            logger.warning('Vehicle count: failed to decode JPEG')
            return None

        h, w = img.shape[:2]
        blob = cv2.dnn.blobFromImage(img, 1/255.0, (416, 416), swapRB=True, crop=False)
        net.setInput(blob)

        out_layers = net.getUnconnectedOutLayersNames()
        outputs = net.forward(out_layers)

        # Collect boxes + scores, then apply NMS to eliminate duplicate detections
        boxes, confidences, class_ids = [], [], []
        for output in outputs:
            for detection in output:
                scores = detection[5:]
                class_id = int(scores.argmax())
                confidence = float(scores[class_id])
                if confidence >= 0.25 and class_id in _VEHICLE_CLASSES:
                    cx, cy, bw, bh = (detection[:4] * [w, h, w, h]).astype(int)
                    x = cx - bw // 2
                    y = cy - bh // 2
                    boxes.append([x, y, bw, bh])
                    confidences.append(confidence)
                    class_ids.append(class_id)

        indices = cv2.dnn.NMSBoxes(boxes, confidences, score_threshold=0.25, nms_threshold=0.45)
        count = len(indices) if len(indices) > 0 else 0

        logger.info('Vehicle count: %d after NMS (raw detections=%d)', count, len(boxes))
        return count
    except Exception as e:
        logger.error('Vehicle count error (%s: %s)', type(e).__name__, e)
        return None


def _add_exif_metadata(jpeg_bytes: bytes, vehicle_count: Optional[int]) -> bytes:
    """Embed vehicle count in the JPEG UserComment EXIF tag via Pillow."""
    try:
        import io
        from PIL import Image

        img = Image.open(io.BytesIO(jpeg_bytes))
        exif = img.getexif()
        # Tag 0x9286 = UserComment; prefix with ASCII charset marker
        comment = f'vehicles={vehicle_count}' if vehicle_count is not None else 'vehicles=unknown'
        exif[0x9286] = comment.encode()
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=88, exif=exif.tobytes())
        return buf.getvalue()
    except Exception as e:
        logger.warning('EXIF metadata write failed (%s: %s); returning unmodified bytes',
                       type(e).__name__, e)
        return jpeg_bytes


def read_vehicle_count_from_exif(jpeg_bytes: bytes) -> Optional[int]:
    """
    Read the vehicle count embedded by _add_exif_metadata() from a JPEG's
    EXIF UserComment tag.  Returns None if the tag is absent or unparseable.
    """
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(jpeg_bytes))
        comment = img.getexif().get(0x9286)
        if comment:
            if isinstance(comment, bytes):
                comment = comment.decode('utf-8', errors='ignore')
            if comment.startswith('vehicles='):
                val = comment.split('=', 1)[1]
                if val.isdigit():
                    return int(val)
    except Exception:
        pass
    return None


def get_cache_mtime() -> Optional[float]:
    """Return the mtime of the cache file, or None if it doesn't exist."""
    from monitor.config import RING_CACHE_FILE
    return RING_CACHE_FILE.stat().st_mtime if RING_CACHE_FILE.exists() else None
