#!/usr/bin/env python3
"""
Build Ring camera empty-driveway reference images for background subtraction.

Runs continuously during daylight, fetching a fresh Ring snapshot every
RING_CACHE_MINUTES and saving it as the reference for the current hour when
YOLO sees 0 vehicles.  One image per hour slot (00–23) is kept; each run
overwrites the slot with a fresher sample so lighting stays accurate.

Usage:
    nohup /home/pi/src/pumphouse/venv/bin/python3 \
        /home/pi/src/pumphouse/bin/build_ring_baseline.py >> /tmp/ring_baseline.log 2>&1 &
"""
import sys
import time
import logging
from datetime import datetime
from pathlib import Path

# Run from repo root so monitor package imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('baseline')

REF_DIR = Path.home() / '.config' / 'pumphouse' / 'ring_reference'


def daylight_window():
    """Return (sunrise_hour, sunset_hour) for today in local time."""
    try:
        import pytz
        from astral import LocationInfo
        from astral.sun import sun
        tz = pytz.timezone('America/Los_Angeles')
        now = datetime.now(tz)
        loc = LocationInfo('Newport, OR', 'Oregon', 'America/Los_Angeles', 44.6368, -124.0535)
        st = sun(loc.observer, date=now.date(), tzinfo=tz)
        return st['sunrise'].hour, st['sunset'].hour
    except Exception:
        return 6, 20  # safe fallback


def coverage_summary():
    """Return sorted list of hour slots that have a reference image."""
    if not REF_DIR.exists():
        return []
    return sorted(int(p.stem) for p in REF_DIR.glob('??.jpg') if p.stem.isdigit())


def is_daytime():
    rise, set_ = daylight_window()
    h = datetime.now().hour
    return rise <= h <= set_


def fetch_and_save():
    """
    Fetch a Ring snapshot (from cache if still fresh, Ring API if stale),
    run vehicle count, and save as reference for the current hour if count == 0.
    Returns (vehicle_count, saved: bool).
    """
    from monitor.config import RING_TOKEN_FILE, RING_CAMERA_NAME, RING_CACHE_FILE, RING_CACHE_MINUTES
    from monitor.ring_camera import get_snapshot, read_vehicle_count_from_exif, _count_vehicles, maybe_save_reference

    # Force a fresh Ring API call by removing the cache if it hasn't been
    # touched in the last RING_CACHE_MINUTES.  This is safe: the lock in
    # get_snapshot() prevents other processes from racing on the same file.
    if RING_CACHE_FILE.exists():
        age = time.time() - RING_CACHE_FILE.stat().st_mtime
        if age >= RING_CACHE_MINUTES * 60:
            try:
                RING_CACHE_FILE.unlink()
            except Exception:
                pass

    jpeg = get_snapshot(RING_TOKEN_FILE, RING_CAMERA_NAME)
    if jpeg is None:
        return None, False

    # Prefer EXIF-embedded count (already computed by get_snapshot); fall back
    # to running inference again only if the tag is absent.
    count = read_vehicle_count_from_exif(jpeg)
    if count is None:
        count = _count_vehicles(jpeg)

    if count is None:
        log.warning('YOLO inference unavailable, skipping save')
        return None, False

    if count == 0:
        saved = maybe_save_reference(jpeg)
        return count, saved
    return count, False


def main():
    from monitor.config import RING_CACHE_MINUTES

    sleep_secs = RING_CACHE_MINUTES * 60  # match cache TTL so every fetch is fresh

    rise, set_ = daylight_window()
    log.info('Daylight window: %02d:00 – %02d:00  (fetching every %d min)',
             rise, set_, RING_CACHE_MINUTES)
    log.info('Reference dir: %s', REF_DIR)
    log.info('Current coverage: hours %s', coverage_summary() or 'none')

    while True:
        now = datetime.now()
        hour = now.hour

        if not is_daytime():
            # Sleep until sunrise to avoid waking the camera at night
            rise, _ = daylight_window()
            mins_until = ((rise - hour) % 24) * 60 - now.minute
            mins_until = max(mins_until, 1)
            log.info('Night-time (%02d:00), sleeping %d min until ~sunrise', hour, mins_until)
            time.sleep(mins_until * 60)
            continue

        log.info('Hour %02d — fetching Ring snapshot…', hour)
        try:
            count, saved = fetch_and_save()
        except Exception as exc:
            log.error('Unexpected error: %s', exc, exc_info=True)
            time.sleep(60)
            continue

        if count is None:
            log.warning('Hour %02d — fetch failed, retrying in 2 min', hour)
            time.sleep(120)
            continue

        if saved:
            log.info('Hour %02d — reference saved  (0 vehicles)', hour)
        else:
            log.info('Hour %02d — skipped (%d vehicle(s) detected, not saving)', hour, count)

        covered = set(coverage_summary())
        rise, set_ = daylight_window()
        needed = set(range(rise, set_ + 1))
        missing = sorted(needed - covered)
        log.info('Coverage: %d/%d daylight slots  covered=%s  missing=%s',
                 len(covered & needed), len(needed),
                 sorted(covered), missing)

        time.sleep(sleep_secs)


if __name__ == '__main__':
    main()
