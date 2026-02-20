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

FRAME_INTERVAL   = 20    # seconds between frames (20s → 360 frames/2hr → 15s at 24fps)
WINDOW_BEFORE    = 60    # minutes before sunset to start capture
WINDOW_AFTER     = 60    # minutes after sunset to stop capture
RETENTION_DAYS   = 30    # days of MP4s to keep
OUTPUT_FPS       = 24    # output video frame rate
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
    Writes to a temp file then atomically renames so the web server
    never serves a partially-written video. Returns True on success.
    """
    frames = sorted(frames_dir.glob('frame_*.jpg'))
    n = len(frames) - 1   # exclude the frame currently being written
    if n < 2:
        return False

    pattern = str(frames_dir / 'frame_%04d.jpg')
    tmp = output_path.with_suffix('.tmp.mp4')
    cmd = [
        'ffmpeg', '-y',
        '-framerate', str(fps),
        '-i', pattern,
        '-frames:v', str(n),   # only the completed frames
        '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p',
        '-crf', '23',
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


def cleanup_old(retention_days):
    """Delete MP4s older than retention_days."""
    cutoff = datetime.now().timestamp() - retention_days * 86400
    for f in TIMELAPSE_DIR.glob('*.mp4'):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            log.info(f"Removed old timelapse: {f.name}")


def run_todays_timelapse():
    """
    Capture frames via RTSP for the sunset window, assembling a preview
    MP4 every PREVIEW_INTERVAL seconds so it's watchable during capture.
    Final assembly runs after ffmpeg exits.
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
    frames_dir = TIMELAPSE_DIR / 'frames' / date_str
    output     = TIMELAPSE_DIR / f'{date_str}.mp4'
    frames_dir.mkdir(parents=True, exist_ok=True)

    expected = duration // FRAME_INTERVAL
    log.info(f"Capturing ~{expected} frames (1/{FRAME_INTERVAL}s) for {duration//60} min")

    cmd = [
        'ffmpeg', '-y',
        '-rtsp_transport', 'tcp',
        '-i', rtsp_url,
        '-vf', f'fps=1/{FRAME_INTERVAL}',
        '-t', str(duration),
        '-q:v', '2',
        str(frames_dir / 'frame_%04d.jpg'),
    ]

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        next_preview = time.time() + PREVIEW_INTERVAL

        # Poll until ffmpeg finishes, assembling previews along the way
        while proc.poll() is None:
            time.sleep(5)
            if time.time() >= next_preview:
                frames = sorted(frames_dir.glob('frame_*.jpg'))
                log.info(f"Preview assembly ({len(frames)} frames so far) …")
                assemble_timelapse(frames_dir, output)
                next_preview = time.time() + PREVIEW_INTERVAL

        exit_code = proc.returncode
        frames = sorted(frames_dir.glob('frame_*.jpg'))
        log.info(f"Capture done: {len(frames)} frames (ffmpeg exit={exit_code})")

        if len(frames) < 5:
            log.error("Too few frames — skipping final assembly")
            return

        # Final assembly includes all frames
        log.info("Final assembly …")
        assemble_timelapse(frames_dir, output)
        cleanup_old(RETENTION_DAYS)

    finally:
        if frames_dir.exists():
            shutil.rmtree(frames_dir)
            log.info("Cleaned up frames")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    TIMELAPSE_DIR.mkdir(parents=True, exist_ok=True)
    (TIMELAPSE_DIR / 'frames').mkdir(exist_ok=True)

    log.info(f"Sunset timelapse daemon starting")
    log.info(f"Location : {LOCATION_NAME}  ({LOCATION_LAT}N, {abs(LOCATION_LON)}W)")
    log.info(f"Interval : {FRAME_INTERVAL}s  |  window ±{WINDOW_BEFORE} min  |  keep {RETENTION_DAYS} days")
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
