#!/usr/bin/env python3
"""
Color JPEG version of the e-paper display image.

Generates a 1000x488 JPEG (4× the 250×122 e-paper resolution) with:
  - Camera background in the graph area (timelapse frame, snapshot, or RTSP)
  - White line chart (no fill)
  - White text overlays directly on the image (no backing boxes)
  - Light header area with navy gallons/percent text

Camera background priority:
  1. Today's timelapse snapshot JPEG — or yesterday's if the clock is before 05:00
     (/home/pi/timelapses/snapshots/YYYY-MM-DD.jpg)
  2. Latest frame from today's active timelapse capture directory
     (/tmp/timelapse-frames/YYYY-MM-DD/)
  3. Single RTSP frame — only if timelapse is NOT currently running,
     to avoid competing with an active capture
  4. Most recent snapshot of any date (last resort)

Run standalone to write a test image:
    python3 -m monitor.epaper_jpg [output.jpg]
    python3 -m monitor.epaper_jpg           # defaults to /tmp/epaper_test.jpg
"""
import csv
import io
import os
import pathlib
import subprocess
import time
from datetime import datetime, timedelta

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from monitor.config import (
    CAMERA_PASS,
    CAMERA_USER,
    EPAPER_CONSERVE_WATER_THRESHOLD,
    EPAPER_DEFAULT_HOURS_OTHER,
    EPAPER_DEFAULT_HOURS_TENANT,
    EPAPER_LOW_WATER_HOURS,
    EPAPER_LOW_WATER_HOURS_THRESHOLD,
    EPAPER_OWNER_STAY_TYPES,
    TANK_CAPACITY_GALLONS,
    TANK_URL,
)
from monitor.occupancy import (
    get_checkin_datetime,
    get_next_reservation,
    is_occupied,
    load_reservations,
)
from monitor.tank import get_tank_data

# ── Camera / timelapse paths ───────────────────────────────────────────────
_FRAME_BASE    = pathlib.Path('/tmp/timelapse-frames')
_SNAPSHOT_DIR  = pathlib.Path('/home/pi/timelapses/snapshots')
_CAMERA_IP     = '192.168.1.81'
_CAMERA_PORT   = 554
_CROP_BOTTOM   = 120  # keep in sync with sunset_timelapse.py
# A frame directory is considered "active" if its newest frame is younger than this
_ACTIVE_THRESHOLD_SECONDS = 600  # 10 minutes

# ── Simple weather-description cache (independent of web.py) ──────────────
_weather_cache: dict = {'desc': None, 'ts': 0.0}

_WMO = {
    0: 'Clear', 1: 'Mostly Clear', 2: 'Partly Cloudy', 3: 'Overcast',
    45: 'Foggy', 48: 'Icy Fog',
    51: 'Light Drizzle', 53: 'Drizzle', 55: 'Heavy Drizzle',
    61: 'Light Rain', 63: 'Rain', 65: 'Heavy Rain',
    71: 'Light Snow', 73: 'Snow', 75: 'Heavy Snow',
    80: 'Light Showers', 81: 'Showers', 82: 'Heavy Showers',
    95: 'Thunderstorm',
}


def _current_weather_desc() -> str | None:
    """Return current weather description, cached for 30 minutes."""
    import json
    import urllib.request

    now = time.time()
    if _weather_cache['desc'] is not None and now - _weather_cache['ts'] < 1800:
        return _weather_cache['desc']

    desc = None
    try:
        url = 'https://api.weather.gov/stations/KONP/observations/latest'
        req = urllib.request.Request(url, headers={'User-Agent': 'pumphouse/1.0'})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        desc = data['properties'].get('textDescription') or None
    except Exception:
        pass

    if not desc:
        try:
            url = ('https://api.open-meteo.com/v1/forecast'
                   '?latitude=44.6368&longitude=-124.0535&current=weather_code')
            with urllib.request.urlopen(url, timeout=5) as r:
                data = json.loads(r.read())
            code = data['current']['weather_code']
            desc = _WMO.get(code)
        except Exception:
            pass

    _weather_cache['desc'] = desc
    _weather_cache['ts']   = now
    return desc


# ── Camera background helpers ──────────────────────────────────────────────

def _fit_image(img: Image.Image, w: int, h: int) -> Image.Image:
    """Center-crop and resize img to exactly (w, h)."""
    cam_aspect   = img.width / img.height
    graph_aspect = w / h
    if cam_aspect > graph_aspect:
        new_w = int(img.height * graph_aspect)
        x_off = (img.width - new_w) // 2
        img = img.crop((x_off, 0, x_off + new_w, img.height))
    else:
        new_h = int(img.width / graph_aspect)
        y_off = img.height - new_h   # anchor to bottom of frame
        img = img.crop((0, y_off, img.width, img.height))
    return img.resize((w, h), Image.LANCZOS)


def _grab_rtsp_frame() -> Image.Image | None:
    """Grab one JPEG frame from the RTSP stream, return as PIL Image or None."""
    rtsp = (f'rtsp://{CAMERA_USER}:{CAMERA_PASS}@{_CAMERA_IP}:{_CAMERA_PORT}'
            f'/cam/realmonitor?channel=1&subtype=0')
    cmd = [
        'ffmpeg', '-y', '-rtsp_transport', 'tcp',
        '-i', rtsp, '-vframes', '1',
        '-vf', f'crop=iw:ih-{_CROP_BOTTOM}:0:0',
        '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1',
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        if result.returncode == 0 and result.stdout:
            return Image.open(io.BytesIO(result.stdout)).convert('RGB')
    except Exception:
        pass
    return None


def _get_camera_background(graph_w: int, graph_h: int) -> Image.Image | None:
    """
    Return an RGB PIL Image (graph_w × graph_h) for the graph background, or None.

    Priority:
      1. Today's snapshot (or yesterday's between midnight–05:00)
      2. Latest frame from an active timelapse capture
      3. Single RTSP frame (only when timelapse is NOT currently running)
      4. Most recent snapshot of any date
    """
    now = datetime.now()
    # Before 5 am use yesterday's snapshot — today's won't exist yet
    snap_date = (now - timedelta(days=1)).date() if now.hour < 5 else now.date()
    snap_path = _SNAPSHOT_DIR / f'{snap_date}.jpg'
    if snap_path.exists():
        try:
            return _fit_image(Image.open(snap_path).convert('RGB'), graph_w, graph_h)
        except Exception:
            pass

    # Check for an active timelapse capture
    frame_dir = _FRAME_BASE / now.strftime('%Y-%m-%d')
    timelapse_running = False
    latest_frame: pathlib.Path | None = None
    if frame_dir.exists():
        frames = sorted(frame_dir.glob('frame_*.jpg'))
        if frames:
            newest = frames[-1]
            if time.time() - newest.stat().st_mtime < _ACTIVE_THRESHOLD_SECONDS:
                timelapse_running = True
                latest_frame = newest
    if latest_frame:
        try:
            return _fit_image(Image.open(latest_frame).convert('RGB'), graph_w, graph_h)
        except Exception:
            pass

    # Live RTSP only when safe to do so
    if not timelapse_running:
        frame = _grab_rtsp_frame()
        if frame:
            return _fit_image(frame, graph_w, graph_h)

    # Last resort: any snapshot on disk
    if _SNAPSHOT_DIR.exists():
        snaps = sorted(_SNAPSHOT_DIR.glob('????-??-??.jpg'))
        if snaps:
            try:
                return _fit_image(Image.open(snaps[-1]).convert('RGB'), graph_w, graph_h)
            except Exception:
                pass

    return None


# ── Main render function ───────────────────────────────────────────────────

def render_epaper_jpg(
    *,
    hours_explicit: int | None = None,
    tenant_override: str | None = None,   # "yes" | "no" | None
    occupied_override: str | None = None, # "yes" | "no" | None
    threshold_override: int | None = None,
    scale: int = 4,
    snapshots_csv: str = 'snapshots.csv',
    reservations_csv: str = 'reservations.csv',
) -> io.BytesIO:
    """
    Render a color JPEG of the e-paper display and return it as a BytesIO buffer.

    All parameters mirror the /api/epaper.bmp query params.  Default scale=4
    produces a 1000×488 image.
    """
    scale = max(1, min(8, scale))

    # ── Cache (8 minutes, same policy as epaper_bmp) ─────────────────────
    _cache_max_age  = 8 * 60
    _is_cacheable   = (hours_explicit is None and occupied_override is None
                       and threshold_override is None)
    if _is_cacheable:
        _parts = []
        if tenant_override:
            _parts.append(f'tenant-{tenant_override}')
        if scale != 4:
            _parts.append(f's{scale}')
        _cache_file = 'epaper_jpg_cache' + ('_' + '_'.join(_parts) if _parts else '') + '.jpg'
    else:
        _cache_file = None
    if _cache_file:
        try:
            _cache_mtime = os.path.getmtime(_cache_file)
            if time.time() - _cache_mtime < _cache_max_age:
                # Also invalidate if today's snapshot is newer than the cache —
                # this fires once each day right after the timelapse is assembled.
                _now = datetime.now()
                _snap_date = (_now - timedelta(days=1)).date() if _now.hour < 5 else _now.date()
                _snap = _SNAPSHOT_DIR / f'{_snap_date}.jpg'
                if not _snap.exists() or _snap.stat().st_mtime <= _cache_mtime:
                    with open(_cache_file, 'rb') as _cf:
                        return io.BytesIO(_cf.read())
                # snapshot is newer — fall through and regenerate
        except OSError:
            pass

    def s(v: float) -> int:
        return int(v * scale)

    WIDTH, HEIGHT = 250 * scale, 122 * scale

    # ── Fonts ────────────────────────────────────────────────────────────
    try:
        _bold = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
        _reg  = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
        font_large  = ImageFont.truetype(_bold, s(22))
        font_small  = ImageFont.truetype(_reg,  s(11))
    except (IOError, OSError):
        font_large = font_small = ImageFont.load_default()

    # ── Snapshot history ─────────────────────────────────────────────────
    rows: list[dict] = []
    try:
        with open(snapshots_csv) as f:
            rows = list(csv.DictReader(f))
    except Exception:
        pass

    # ── Live tank data (fall back to latest snapshot) ────────────────────
    tank_gallons: float | None = None
    tank_pct:     float | None = None
    live_reading_ts = None
    try:
        live = get_tank_data(TANK_URL, timeout=30)
        if live['status'] == 'success' and live['gallons'] is not None:
            tank_gallons    = live['gallons']
            tank_pct        = (tank_gallons / TANK_CAPACITY_GALLONS) * 100
            live_reading_ts = live.get('last_updated')
    except Exception:
        pass

    if tank_gallons is None and rows:
        try:
            tank_gallons = float(rows[-1]['tank_gallons'])
            tank_pct     = (tank_gallons / TANK_CAPACITY_GALLONS) * 100
        except Exception:
            pass

    # ── Occupancy ────────────────────────────────────────────────────────
    reservations   = load_reservations(reservations_csv)
    occupancy      = is_occupied(reservations)
    next_res       = get_next_reservation(reservations)
    is_occupied_now = occupancy['occupied']
    is_owner = False
    if is_occupied_now and occupancy.get('current_reservation'):
        res_type = occupancy['current_reservation'].get('Type', '')
        is_owner = any(ot in res_type for ot in EPAPER_OWNER_STAY_TYPES)
    is_tenant = is_occupied_now and not is_owner

    if occupied_override == 'yes':
        is_occupied_now = True
    elif occupied_override == 'no':
        is_occupied_now = False
        is_tenant       = False
    if tenant_override == 'yes':
        is_tenant       = True
        is_occupied_now = True
    elif tenant_override == 'no':
        is_tenant = False

    # ── Hours window ─────────────────────────────────────────────────────
    if hours_explicit is not None:
        hours = hours_explicit
    elif is_tenant:
        hours = EPAPER_DEFAULT_HOURS_TENANT
    else:
        hours = EPAPER_DEFAULT_HOURS_OTHER
        if (EPAPER_LOW_WATER_HOURS_THRESHOLD is not None
                and tank_pct is not None
                and tank_pct <= EPAPER_LOW_WATER_HOURS_THRESHOLD):
            hours = EPAPER_LOW_WATER_HOURS

    low_threshold = (threshold_override
                     if threshold_override is not None
                     else EPAPER_CONSERVE_WATER_THRESHOLD)
    tank_is_low   = (low_threshold is not None
                     and tank_pct is not None
                     and tank_pct <= low_threshold)

    # ── Graph layout (identical to epaper_bmp) ───────────────────────────
    sep_y = s(28)

    # Measure y-axis label width with a placeholder draw
    _probe = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    y_label_w = max(
        _probe.textbbox((0, 0), '100%', font=font_small)[2],
        _probe.textbbox((0, 0), '0%',   font=font_small)[2],
    )

    graph_left   = y_label_w + s(6)
    graph_right  = WIDTH     - s(4)
    graph_top    = s(32)
    graph_bottom = HEIGHT    - s(14)
    graph_w      = graph_right  - graph_left
    graph_h      = graph_bottom - graph_top

    # ── Base image ───────────────────────────────────────────────────────
    BG      = (245, 246, 250)
    img     = Image.new('RGB', (WIDTH, HEIGHT), BG)
    draw    = ImageDraw.Draw(img)

    # ── Header ───────────────────────────────────────────────────────────
    TEXT_DARK = (18, 38, 80)
    TEXT_SUB  = (70, 95, 140)
    SEPARATOR = (110, 130, 170)

    if tank_gallons is not None:
        gal_text = f'{int(tank_gallons):,} gal'
        pct_text = f'{tank_pct:.0f}%'
        draw.text((s(4), s(2)), gal_text, font=font_large, fill=TEXT_DARK)
        pct_bbox = draw.textbbox((0, 0), pct_text, font=font_large)
        pct_w    = pct_bbox[2] - pct_bbox[0]
        draw.text((WIDTH - pct_w - s(4), s(2)), pct_text, font=font_large, fill=TEXT_DARK)
        gal_bbox = draw.textbbox((s(4), s(2)), gal_text, font=font_large)
        gap_cx   = (gal_bbox[2] + (WIDTH - pct_w - s(4))) // 2
        for li, line in enumerate(['available', 'water']):
            lb = draw.textbbox((0, 0), line, font=font_small)
            draw.text((gap_cx - (lb[2] - lb[0]) // 2, s(1) + li * s(12)),
                      line, font=font_small, fill=TEXT_SUB)
    else:
        draw.text((s(4), s(2)), 'No data', font=font_large, fill=TEXT_DARK)

    draw.line([(0, sep_y), (WIDTH - 1, sep_y)], fill=SEPARATOR, width=scale)

    # ── Camera background ────────────────────────────────────────────────
    cam = _get_camera_background(graph_w, graph_h)
    if cam is not None:
        cam = ImageEnhance.Brightness(cam).enhance(0.72)
        img.paste(cam, (graph_left, graph_top))
    else:
        # Plain dark fallback so text is still readable
        draw.rectangle([graph_left, graph_top, graph_right, graph_bottom],
                       fill=(30, 45, 80))

    # ── Graph border ─────────────────────────────────────────────────────
    draw.rectangle([graph_left, graph_top, graph_right, graph_bottom],
                   outline=(80, 105, 160), width=scale)

    # ── Snapshot data for graph ──────────────────────────────────────────
    graph_gallons: list[float] = []
    try:
        cutoff = datetime.now() - timedelta(hours=hours)
        for row in rows:
            try:
                if datetime.fromisoformat(row['timestamp']) >= cutoff:
                    graph_gallons.append(float(row['tank_gallons']))
            except Exception:
                continue
    except Exception:
        pass

    # ── Y-axis range ─────────────────────────────────────────────────────
    g_min_raw = min(graph_gallons) if len(graph_gallons) >= 2 else 0.0
    g_max_raw = max(graph_gallons) if len(graph_gallons) >= 2 else 0.0
    min_range = TANK_CAPACITY_GALLONS * 0.05
    if g_max_raw - g_min_raw < min_range:
        mid       = (g_min_raw + g_max_raw) / 2
        g_min_raw = mid - min_range / 2
        g_max_raw = mid + min_range / 2
    g_min  = g_min_raw - (g_max_raw - g_min_raw) * 0.05
    g_max  = g_max_raw + (g_max_raw - g_min_raw) * 0.05
    g_range = g_max - g_min

    # ── Y-axis labels ────────────────────────────────────────────────────
    AXIS_COLOR  = (25, 45, 95)
    y_max_label = f'{int(round(g_max_raw / TANK_CAPACITY_GALLONS * 100))}%'
    y_min_label = f'{int(round(g_min_raw / TANK_CAPACITY_GALLONS * 100))}%'
    draw.text((graph_left - y_label_w - s(3), graph_top    - s(1)),  y_max_label, font=font_small, fill=AXIS_COLOR)
    draw.text((graph_left - y_label_w - s(3), graph_bottom - s(11)), y_min_label, font=font_small, fill=AXIS_COLOR)

    # ── X-axis labels ────────────────────────────────────────────────────
    hours_label = f'{hours // 24}d ago' if hours % 24 == 0 else f'{hours}h ago'
    draw.text((graph_left + s(1), graph_bottom + s(1)), hours_label, font=font_small, fill=AXIS_COLOR)
    try:
        if live_reading_ts:
            now_label = live_reading_ts.strftime('%-m/%d %H:%M')
        else:
            last_ts   = datetime.fromisoformat(rows[-1]['timestamp'])
            data_age  = float(rows[-1].get('tank_data_age_seconds', 0))
            now_label = (last_ts - timedelta(seconds=data_age)).strftime('%-m/%d %H:%M')
    except Exception:
        now_label = 'now'
    nl_bbox = draw.textbbox((0, 0), now_label, font=font_small)
    draw.text((graph_right - (nl_bbox[2] - nl_bbox[0]) - s(1), graph_bottom + s(1)),
              now_label, font=font_small, fill=AXIS_COLOR)

    # ── Adaptive smoothing: large window when flat, raw when changing ─────
    # Each snapshot is ~15 min; window=11 → ~2.5 hr when tank is quiet.
    # When the window range exceeds the noise threshold (real movement),
    # fall back to the raw value so genuine trends aren't blurred.
    _SMOOTH_WINDOW   = 11   # points (~2.5 hr)
    _NOISE_THRESHOLD = 15   # gallons — above this → real movement, use raw
    if len(graph_gallons) >= _SMOOTH_WINDOW:
        smoothed = []
        half = _SMOOTH_WINDOW // 2
        for i in range(len(graph_gallons)):
            lo = max(0, i - half)
            hi = min(len(graph_gallons), i + half + 1)
            window_vals = graph_gallons[lo:hi]
            if max(window_vals) - min(window_vals) > _NOISE_THRESHOLD:
                smoothed.append(graph_gallons[i])   # real change — use raw
            else:
                smoothed.append(sum(window_vals) / len(window_vals))
        graph_gallons = smoothed

    # ── Data line (no fill) ───────────────────────────────────────────────
    if len(graph_gallons) >= 2:
        points = []
        for i, g in enumerate(graph_gallons):
            x     = graph_left + 1 + int(i * (graph_w - 2) / (len(graph_gallons) - 1))
            y_val = graph_bottom - 1 - int((g - g_min) / g_range * (graph_h - 2))
            points.append((x, y_val))
        for i in range(len(points) - 1):
            draw.line([points[i], points[i + 1]], fill=(160, 230, 80), width=2 * scale)

    # ── Text overlays: white, no backing ────────────────────────────────
    WHITE = (255, 255, 255)
    pad   = s(2)
    py    = graph_top + pad

    outdoor_temp_f = None
    if rows:
        try:
            outdoor_temp_f = float(rows[-1].get('outdoor_temp_f', ''))
        except (ValueError, TypeError):
            pass

    if outdoor_temp_f is not None:
        text = f'Outside: {int(round(outdoor_temp_f))}\u00b0'
        tb   = draw.textbbox((0, 0), text, font=font_small)
        draw.text((graph_left + pad - tb[0], py - tb[1]), text, font=font_small, fill=WHITE)
        py += (tb[3] - tb[1]) + s(3)

    weather_desc = _current_weather_desc()
    if weather_desc:
        wb = draw.textbbox((0, 0), weather_desc, font=font_small)
        draw.text((graph_left + pad - wb[0], py - wb[1]), weather_desc, font=font_small, fill=WHITE)

    # Occupancy text centred near graph bottom (owner/unoccupied mode only)
    if not is_tenant:
        def _day_suffix(dt: datetime) -> str:
            today = datetime.now().date()
            if dt.date() == today:
                return ' (today)'
            if dt.date() == today + timedelta(days=1):
                return ' (tomorrow)'
            return ''

        if is_occupied_now and occupancy.get('checkout_date'):
            co       = occupancy['checkout_date']
            occ_text = 'occupied until ' + co.strftime('%-m/%d') + _day_suffix(co)
        elif is_occupied_now:
            occ_text = 'occupied'
        elif next_res:
            checkin_dt = get_checkin_datetime(next_res.get('Check-In'))
            if checkin_dt:
                occ_text = 'next checkin ' + checkin_dt.strftime('%-m/%d') + _day_suffix(checkin_dt)
            else:
                occ_text = 'unoccupied'
        else:
            occ_text = 'unoccupied'

        ob  = draw.textbbox((0, 0), occ_text, font=font_small)
        ow, oh = ob[2] - ob[0], ob[3] - ob[1]
        draw.text(
            (graph_left + (graph_w - ow) // 2 - ob[0],
             graph_bottom - oh - s(4) - ob[1]),
            occ_text, font=font_small, fill=WHITE,
        )

    # ── "Save Water" overlay (white text, owner/non-tenant low-water) ────
    if tank_is_low and not is_tenant:
        warn_text = 'Save Water'
        wb = draw.textbbox((0, 0), warn_text, font=font_large)
        ww, wh = wb[2] - wb[0], wb[3] - wb[1]
        draw.text(
            (graph_left + (graph_w - ww) // 2 - wb[0],
             graph_top  + (graph_h - wh) // 2 - wb[1]),
            warn_text, font=font_large, fill=WHITE,
        )

    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=88)
    if _cache_file:
        try:
            with open(_cache_file, 'wb') as _cf:
                _cf.write(buf.getvalue())
        except Exception:
            pass
    buf.seek(0)
    return buf


# ── Standalone entry point ─────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else '/tmp/epaper_test.jpg'
    buf = render_epaper_jpg()
    with open(out, 'wb') as f:
        f.write(buf.read())
    print(f'Written → {out}  (1000×488)')
