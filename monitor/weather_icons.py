"""Simple pixel-art weather icons drawn with PIL primitives.

Public API:
    draw_weather_icon(draw, wmo_code, cx, cy, size, fill)   # single-colour (BMP/XOR)
    draw_weather_icon_color(draw, wmo_code, cx, cy, size)   # full-colour (JPG)

Works at any scale; recognisable at size ≥ 12 px.
"""
import math

# Colour palette used by draw_weather_icon_color()
_P = {
    'sun_core':   (255, 225,  55),   # bright yellow
    'sun_rays':   (255, 165,   0),   # warm orange
    'cloud':      (195, 215, 235),   # light blue-grey
    'cloud_dark': (130, 150, 175),   # darker cloud for thunder
    'rain':       ( 75, 140, 220),   # cornflower blue
    'snow':       (210, 235, 255),   # near-white icy blue
    'bolt':       (255, 220,  30),   # golden yellow
    'fog':        (170, 190, 210),   # muted steel blue
}


def draw_weather_icon(draw, wmo_code: int, cx: int, cy: int, size: int, fill) -> None:
    """Draw a weather icon centred at (cx, cy) within a size×size bounding box.

    draw     – PIL ImageDraw instance
    wmo_code – WMO weather interpretation code
    cx, cy   – centre pixel of the icon
    size     – bounding box width/height in pixels
    fill     – PIL colour (e.g. 1 for 1-bit BMP, (255,255,255) for RGB)
    """
    r = max(3, size // 2)
    _DISPATCH[_icon_type(wmo_code)](draw, cx, cy, r, fill)


# ── WMO → icon category ──────────────────────────────────────────────────────

def _icon_type(wmo: int) -> str:
    if wmo == 0:
        return 'sun'
    if wmo in (1, 2):
        return 'partly'
    if wmo == 3:
        return 'cloud'
    if wmo in (45, 48):
        return 'fog'
    if (51 <= wmo <= 67) or (80 <= wmo <= 82):
        return 'rain'
    if (71 <= wmo <= 77) or wmo in (85, 86):
        return 'snow'
    if wmo in (95, 96, 99):
        return 'thunder'
    return 'cloud'


# ── primitive helpers ────────────────────────────────────────────────────────

def _e(draw, cx, cy, r, fill):
    """Filled circle of radius r at (cx, cy)."""
    r = max(1, r)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)


def _cloud_shape(draw, cx, cy, r, fill):
    """Three-bump cloud fitting roughly r×r, centred at (cx, cy).
    Recognisable at r ≥ 4."""
    _e(draw, cx,          cy + r // 6,  r * 2 // 3, fill)  # main body
    _e(draw, cx - r // 2, cy - r // 6,  r // 2,     fill)  # left bump
    _e(draw, cx + r // 5, cy - r // 3,  r * 3 // 5, fill)  # right bump (higher)


# ── icon drawers ─────────────────────────────────────────────────────────────

def _sun(draw, cx, cy, r, fill):
    cr = max(2, r * 4 // 10)
    _e(draw, cx, cy, cr, fill)
    rs = cr + max(1, r // 6)
    re = r - 1
    lw = max(1, r // 6)
    for i in range(8):
        a = math.radians(i * 45)
        draw.line(
            [(cx + int(rs * math.cos(a)), cy + int(rs * math.sin(a))),
             (cx + int(re * math.cos(a)), cy + int(re * math.sin(a)))],
            fill=fill, width=lw,
        )


def _partly_cloudy(draw, cx, cy, r, fill):
    # Small sun, upper-left quadrant
    scx = cx - r // 3
    scy = cy - r // 3
    sr  = max(2, r // 3)
    _e(draw, scx, scy, sr, fill)
    for i in range(4):
        a  = math.radians(i * 90 + 45)
        s1 = sr + 1
        s2 = sr + max(1, sr // 2)
        draw.line(
            [(scx + int(s1 * math.cos(a)), scy + int(s1 * math.sin(a))),
             (scx + int(s2 * math.cos(a)), scy + int(s2 * math.sin(a)))],
            fill=fill, width=max(1, r // 8),
        )
    # Cloud, lower-right
    _cloud_shape(draw, cx + r // 5, cy + r // 5, r * 3 // 4, fill)


def _cloud(draw, cx, cy, r, fill):
    _cloud_shape(draw, cx, cy, r, fill)


def _fog(draw, cx, cy, r, fill):
    lw  = max(1, r // 4)
    gap = max(2, r // 3)
    for dy in (-gap, 0, gap):
        draw.line([(cx - r + 1, cy + dy), (cx + r - 1, cy + dy)], fill=fill, width=lw)


def _rain(draw, cx, cy, r, fill):
    _cloud_shape(draw, cx, cy - r // 3, r * 3 // 4, fill)
    lw  = max(1, r // 7)
    top = cy + r // 5
    bot = cy + r - 1
    for dx in (-r // 3, 0, r // 3):
        draw.line([(cx + dx, top), (cx + dx, bot)], fill=fill, width=lw)


def _snow(draw, cx, cy, r, fill):
    _cloud_shape(draw, cx, cy - r // 3, r * 3 // 4, fill)
    dr = max(1, r // 7)
    fy = cy + r * 2 // 3
    for dx in (-r // 3, 0, r // 3):
        _e(draw, cx + dx, fy, dr, fill)


def _thunder(draw, cx, cy, r, fill):
    _cloud_shape(draw, cx, cy - r // 3, r * 3 // 4, fill)
    lw = max(1, r // 5)
    bx = cx + r // 8
    by = cy + r // 6
    pts = [
        (bx + r // 4,  by),
        (bx - r // 6,  by + r * 5 // 12),
        (bx + r // 8,  by + r * 5 // 12),
        (bx - r // 5,  by + r * 5 // 6),
    ]
    for i in range(len(pts) - 1):
        draw.line([pts[i], pts[i + 1]], fill=fill, width=lw)


_DISPATCH = {
    'sun':     _sun,
    'partly':  _partly_cloudy,
    'cloud':   _cloud,
    'fog':     _fog,
    'rain':    _rain,
    'snow':    _snow,
    'thunder': _thunder,
}


# ── Full-colour variants (JPG path) ──────────────────────────────────────────

def draw_weather_icon_color(draw, wmo_code: int, cx: int, cy: int, size: int) -> None:
    """Draw a full-colour weather icon for RGB (JPG) rendering.

    Uses the module-level _P palette so sun is yellow, rain is blue, etc.
    No fill argument needed — colours are chosen automatically per icon type.
    """
    r    = max(3, size // 2)
    itype = _icon_type(wmo_code)
    if itype == 'sun':
        _sun_c(draw, cx, cy, r)
    elif itype == 'partly':
        _partly_c(draw, cx, cy, r)
    elif itype == 'cloud':
        _cloud_shape(draw, cx, cy, r, _P['cloud'])
    elif itype == 'fog':
        _fog_c(draw, cx, cy, r)
    elif itype == 'rain':
        _rain_c(draw, cx, cy, r)
    elif itype == 'snow':
        _snow_c(draw, cx, cy, r)
    elif itype == 'thunder':
        _thunder_c(draw, cx, cy, r)
    else:
        _cloud_shape(draw, cx, cy, r, _P['cloud'])


def _sun_c(draw, cx, cy, r):
    cr = max(2, r * 4 // 10)
    rs = cr + max(1, r // 6)
    re = r - 1
    lw = max(1, r // 5)
    for i in range(8):
        a = math.radians(i * 45)
        draw.line(
            [(cx + int(rs * math.cos(a)), cy + int(rs * math.sin(a))),
             (cx + int(re * math.cos(a)), cy + int(re * math.sin(a)))],
            fill=_P['sun_rays'], width=lw,
        )
    _e(draw, cx, cy, cr, _P['sun_core'])


def _partly_c(draw, cx, cy, r):
    scx, scy = cx - r // 3, cy - r // 3
    sr  = max(2, r // 3)
    for i in range(4):
        a  = math.radians(i * 90 + 45)
        s1 = sr + 1
        s2 = sr + max(1, sr // 2)
        draw.line(
            [(scx + int(s1 * math.cos(a)), scy + int(s1 * math.sin(a))),
             (scx + int(s2 * math.cos(a)), scy + int(s2 * math.sin(a)))],
            fill=_P['sun_rays'], width=max(1, r // 8),
        )
    _e(draw, scx, scy, sr, _P['sun_core'])
    _cloud_shape(draw, cx + r // 5, cy + r // 5, r * 3 // 4, _P['cloud'])


def _fog_c(draw, cx, cy, r):
    lw  = max(1, r // 4)
    gap = max(2, r // 3)
    for dy in (-gap, 0, gap):
        draw.line([(cx - r + 1, cy + dy), (cx + r - 1, cy + dy)], fill=_P['fog'], width=lw)


def _rain_c(draw, cx, cy, r):
    _cloud_shape(draw, cx, cy - r // 3, r * 3 // 4, _P['cloud'])
    lw  = max(1, r // 6)
    top = cy + r // 5
    bot = cy + r - 1
    for dx in (-r // 3, 0, r // 3):
        draw.line([(cx + dx, top), (cx + dx, bot)], fill=_P['rain'], width=lw)


def _snow_c(draw, cx, cy, r):
    _cloud_shape(draw, cx, cy - r // 3, r * 3 // 4, _P['cloud'])
    dr = max(1, r // 6)
    fy = cy + r * 2 // 3
    for dx in (-r // 3, 0, r // 3):
        _e(draw, cx + dx, fy, dr, _P['snow'])


def _thunder_c(draw, cx, cy, r):
    _cloud_shape(draw, cx, cy - r // 3, r * 3 // 4, _P['cloud_dark'])
    lw = max(1, r // 5)
    bx, by = cx + r // 8, cy + r // 6
    pts = [
        (bx + r // 4,  by),
        (bx - r // 6,  by + r * 5 // 12),
        (bx + r // 8,  by + r * 5 // 12),
        (bx - r // 5,  by + r * 5 // 6),
    ]
    for i in range(len(pts) - 1):
        draw.line([pts[i], pts[i + 1]], fill=_P['bolt'], width=lw)
