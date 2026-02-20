#!/usr/bin/env python3
"""
Sunset timelapse daemon for pumphouse.

Runs continuously, waking each day to capture RTSP frames in a window
around sunset, then assembling them into an MP4 with ffmpeg.
The latest video is served by the web dashboard at /timelapse.

Location: Newport, OR  (44.6368 N, 124.0535 W)
"""
import os
import sys
import time
import logging
import subprocess
import shutil
import threading
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
LOCATION_NAME  = "Newport, OR"
LOCATION_TZ    = "America/Los_Angeles"
LOCATION_LAT   = 44.6368
LOCATION_LON   = -124.0535

CAMERA_IP      = "192.168.1.81"
CAMERA_PORT    = 554

TIMELAPSE_DIR  = Path("/home/pi/timelapses")

FRAME_INTERVAL   = 20    # base seconds between captured frames
SLOWDOWN_FACTOR  = 4     # capture this many times more frames (divide interval by this)
                         # e.g. factor=4, interval=20 → 5s/frame → 1440 frames/2hr → 60s at 24fps
WINDOW_BEFORE    = 60    # minutes before sunset to start capture
WINDOW_AFTER     = 60    # minutes after sunset to stop capture
RETENTION_DAYS   = 30    # keep every day's timelapse for this many days
WEEKLY_YEARS     = 3     # after RETENTION_DAYS, keep one per ISO week for this many years
OUTPUT_FPS       = 24    # output video frame rate
OUTPUT_CRF       = 32    # H.264 quality (lower = better; 23 = default, 35 = ~40x smaller)
PREVIEW_INTERVAL = 600   # seconds between partial preview assemblies (10 min)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(TIMELAPSE_DIR / 'timelapse.log'),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger('timelapse')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_camera_creds():
    """Load CAMERA_USER / CAMERA_PASS from ~/.config/pumphouse/secrets.conf."""
    secrets = Path.home() / '.config' / 'pumphouse' / 'secrets.conf'
    user, password = 'admin', ''
    if secrets.exists():
        with open(secrets) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k, v = k.strip(), v.strip()
                if k == 'CAMERA_USER':
                    user = v
                elif k == 'CAMERA_PASS':
                    password = v
    return user, password


def get_sunset(for_date=None):
    """Return the sunset datetime (timezone-aware) for the given date."""
    from astral import LocationInfo
    from astral.sun import sun
    tz = ZoneInfo(LOCATION_TZ)
    loc = LocationInfo(LOCATION_NAME, "Oregon", LOCATION_TZ, LOCATION_LAT, LOCATION_LON)
    if for_date is None:
        for_date = date.today()
    s = sun(loc.observer, date=for_date, tzinfo=tz)
    return s['sunset']


def assemble_timelapse(frames_dir, output_path, fps=OUTPUT_FPS):
    """
    Assemble all JPEG frames in frames_dir into an H.264 MP4.
    Skips the last frame (may still be mid-write by ffmpeg).
    Uses a thread-unique temp file so concurrent calls don't collide;
    atomically renames on success so the web server never serves a
    partially-written video. Returns True on success.
    """
    frames = sorted(frames_dir.glob('frame_*.jpg'))
    n = len(frames) - 1   # exclude the frame currently being written
    if n < 2:
        return False

    pattern = str(frames_dir / 'frame_%04d.jpg')
    tmp = output_path.with_suffix(f'.tmp{threading.get_ident()}.mp4')
    cmd = [
        'ffmpeg', '-y',
        '-framerate', str(fps),
        '-i', pattern,
        '-frames:v', str(n),   # only the completed frames
        '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p',
        '-crf', str(OUTPUT_CRF),
        '-movflags', '+faststart',
        str(tmp),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error(f"Assembly failed: {result.stderr[-400:]}")
        tmp.unlink(missing_ok=True)
        return False
    tmp.rename(output_path)
    mb = output_path.stat().st_size / 1024 / 1024
    log.info(f"{'Preview' if n < len(frames) else 'Final'}: "
             f"{output_path.name} ({n} frames, {mb:.1f} MB)")
    return True


def cleanup_old(retention_days=RETENTION_DAYS, weekly_years=WEEKLY_YEARS):
    """
    Tiered retention:
      - ≤ retention_days old        → keep all
      - retention_days..weekly_years → keep oldest file of each ISO week
      - older than weekly_years      → delete
    Handles both YYYY-MM-DD_HHMM.mp4 and legacy YYYY-MM-DD.mp4 names.
    """
    import re as _re
    from datetime import date as _date

    today = _date.today()
    cutoff_daily  = today - timedelta(days=retention_days)
    cutoff_weekly = today - timedelta(days=weekly_years * 365)

    # Collect all dated MP4s → {date: Path}
    dated = {}
    for pattern in ('????-??-??_????.mp4', '????-??-??.mp4'):
        for f in TIMELAPSE_DIR.glob(pattern):
            m = _re.match(r'(\d{4}-\d{2}-\d{2})', f.name)
            if not m:
                continue
            try:
                d = _date.fromisoformat(m.group(1))
            except ValueError:
                continue
            # Prefer new-style name if both exist for same date
            if d not in dated or '_' in f.name:
                dated[d] = f

    # For weekly zone: find the oldest file in each ISO week to keep
    keep_per_week = {}  # (iso_year, iso_week) → oldest date
    for d in sorted(dated):
        if cutoff_weekly <= d < cutoff_daily:
            key = d.isocalendar()[:2]
            if key not in keep_per_week:
                keep_per_week[key] = d

    for d, f in dated.items():
        if d >= cutoff_daily:
            continue  # within daily window — keep
        if d < cutoff_weekly:
            f.unlink()
            log.info(f"Removed (>{weekly_years}yr): {f.name}")
            continue
        # Weekly zone: keep only the oldest of each ISO week
        key = d.isocalendar()[:2]
        if keep_per_week.get(key) != d:
            f.unlink()
            log.info(f"Removed (weekly dedup): {f.name}")


def run_todays_timelapse():
    """
    Capture frames via RTSP for the sunset window, assembling a preview
    MP4 every PREVIEW_INTERVAL seconds (in a background thread so frame
    capture is never paused). Final assembly runs after ffmpeg exits.
    """
    tz = ZoneInfo(LOCATION_TZ)
    now = datetime.now(tz)
    sunset = get_sunset(now.date())
    start_time = sunset - timedelta(minutes=WINDOW_BEFORE)
    end_time   = sunset + timedelta(minutes=WINDOW_AFTER)
    duration   = int((end_time - start_time).total_seconds())

    log.info(f"Sunset: {sunset.strftime('%H:%M %Z')}  "
             f"Window: {start_time.strftime('%H:%M')}–{end_time.strftime('%H:%M')}")

    # Sleep until the window opens
    wait = (start_time - datetime.now(tz)).total_seconds()
    if wait > 0:
        log.info(f"Sleeping {wait/60:.0f} min until capture starts …")
        time.sleep(wait)

    user, password = load_camera_creds()
    rtsp_url = (
        f"rtsp://{user}:{password}@{CAMERA_IP}:{CAMERA_PORT}"
        f"/cam/realmonitor?channel=1&subtype=0"
    )

    date_str   = now.strftime('%Y-%m-%d')
    sunset_hhmm = sunset.strftime('%H%M')
    frames_dir = Path('/tmp') / 'timelapse-frames' / date_str  # tmpfs → no SD wear
    output     = TIMELAPSE_DIR / f'{date_str}_{sunset_hhmm}.mp4'
    frames_dir.mkdir(parents=True, exist_ok=True)

    effective_interval = max(1, FRAME_INTERVAL // SLOWDOWN_FACTOR)
    expected = duration // effective_interval
    log.info(f"Capturing ~{expected} frames (1/{effective_interval}s, "
             f"{SLOWDOWN_FACTOR}x slowdown) for {duration//60} min")

    cmd = [
        'ffmpeg', '-y',
        '-rtsp_transport', 'tcp',
        '-i', rtsp_url,
        '-vf', f'fps=1/{effective_interval}',
        '-t', str(duration),
        '-q:v', '2',
        str(frames_dir / 'frame_%04d.jpg'),
    ]

    def _run_preview():
        frames = sorted(frames_dir.glob('frame_*.jpg'))
        log.info(f"Preview assembly ({len(frames)} frames so far) …")
        assemble_timelapse(frames_dir, output)

    preview_threads = []

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        next_preview = time.time() + PREVIEW_INTERVAL

        # Poll until ffmpeg finishes; each preview runs in its own thread
        # using a unique temp file, so triggers are never skipped
        while proc.poll() is None:
            time.sleep(5)
            if time.time() >= next_preview:
                t = threading.Thread(target=_run_preview, daemon=True)
                t.start()
                preview_threads.append(t)
                next_preview = time.time() + PREVIEW_INTERVAL

        exit_code = proc.returncode
        frames = sorted(frames_dir.glob('frame_*.jpg'))
        log.info(f"Capture done: {len(frames)} frames (ffmpeg exit={exit_code})")

        if len(frames) < 5:
            log.error("Too few frames — skipping final assembly")
            return

        # Final assembly (main thread); join previews first so cleanup
        # doesn't delete frames out from under a still-running thread
        for t in preview_threads:
            t.join(timeout=120)
        log.info("Final assembly …")
        assemble_timelapse(frames_dir, output)
        cleanup_old(RETENTION_DAYS)

    finally:
        for t in preview_threads:
            t.join(timeout=120)
        if frames_dir.exists():
            shutil.rmtree(frames_dir)
            log.info("Cleaned up frames")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    TIMELAPSE_DIR.mkdir(parents=True, exist_ok=True)

    log.info(f"Sunset timelapse daemon starting")
    log.info(f"Location : {LOCATION_NAME}  ({LOCATION_LAT}N, {abs(LOCATION_LON)}W)")
    effective_interval = max(1, FRAME_INTERVAL // SLOWDOWN_FACTOR)
    log.info(f"Interval : {FRAME_INTERVAL}s / {SLOWDOWN_FACTOR}x = {effective_interval}s effective  |  "
             f"window ±{WINDOW_BEFORE} min  |  keep {RETENTION_DAYS} days")
    log.info(f"Output   : {TIMELAPSE_DIR}")

    tz = ZoneInfo(LOCATION_TZ)

    while True:
        try:
            now    = datetime.now(tz)
            sunset = get_sunset(now.date())
            window_end = sunset + timedelta(minutes=WINDOW_AFTER)

            if now > window_end:
                # Past today's window — schedule for tomorrow
                tomorrow_sunset = get_sunset(now.date() + timedelta(days=1))
                next_start = tomorrow_sunset - timedelta(minutes=WINDOW_BEFORE)
                wait = (next_start - datetime.now(tz)).total_seconds()
                log.info(f"Past today's window. Next start: "
                         f"{next_start.strftime('%Y-%m-%d %H:%M %Z')} "
                         f"({wait/3600:.1f} h)")
                # Wake up a minute early to avoid drift
                time.sleep(max(wait - 60, 60))
            else:
                run_todays_timelapse()
                # Short pause before recalculating
                time.sleep(300)

        except Exception as e:
            log.error(f"Unhandled error: {e}", exc_info=True)
            time.sleep(60)


if __name__ == '__main__':
    main()
