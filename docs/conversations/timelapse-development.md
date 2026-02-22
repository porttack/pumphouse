# Timelapse Viewer Development — Conversation Log

This document summarizes the development conversation for the pumphouse sunset
timelapse system. The Pi hosts a Flask HTTPS app (port 6443) serving camera
snapshots and assembled timelapse MP4s.

---

## Session 1 (prior — compacted)

### Weather Integration
- Added Open-Meteo ERA5 weather per day: precip, wind avg/max, cloud cover, radiation
- **Fixed precipitation bug**: NWS returns `wmoUnit:mm` not meters; was dividing
  wrong direction giving 212-inch readings. Fix: `prec / 25.4` (mm → inches)
- Switched primary weather source from ERA5 (25 km reanalysis) to **NWS KONP**
  (Newport Municipal Airport actual station observations) — ERA5 was showing
  "Heavy snowfall" when the user had a light dusting
- ERA5 kept as supplement for cloud cover and solar radiation only
- Local sensor hi/lo temps preferred over model data when available
- Coordinates fixed to exact 44.6368, -124.0535

### Navigation
- Added day-of-week to title: `Sunset — Thursday, February 19, 2026`
- Nav buttons show abbreviated date: `← Wed Feb 18` / `Fri Feb 20 →`
- Keyboard `←` / `→` arrow key navigation between days
- Keyboard `Space` pause/play (with `e.preventDefault()` to stop scroll hijack)

### Privacy Crop
- `CROP_BOTTOM = 120` pixels removed from bottom at capture time via ffmpeg
  `crop=iw:ih-120:0:0` filter — frames never stored uncropped even in /tmp
- Removes the fire circle area

### `/frame` Route
- Fast RTSP single-frame grab via ffmpeg to stdout
- `?raw=1` returns uncropped; default applies CROP_BOTTOM
- Faster than `/sunset` (no HTTP digest auth roundtrip)

### Snapshot System
- `SNAPSHOT_OFFSET_MINUTES = 35` — snapshot JPEG saved ~35 min after sunset
- `SNAPSHOT_DIR = timelapses/snapshots/`
- Saved after final MP4 assembly, before frame cleanup
- `/timelapse/YYYY-MM-DD/snapshot` — serves JPEG (1-year cache header)
- `/timelapse/latest.jpg` — redirects to most recent snapshot
- `/timelapse/latest.mp4` — redirects to most recent MP4
- Bootstrapped existing MP4s with snapshots at 65% through video

### Thumbnail List ("All timelapses")
- `THUMB_WIDTH = 240` px (configurable)
- Shows thumbnail + date + sunset time + rating per entry
- Sunset time from `_HHMM` filename suffix; astral fallback for legacy filenames

### Star Rating System
- 3–5 stars only (1–2 not allowed but displayed same color to avoid confusion)
- Cookie `tl_rated_YYYY-MM-DD` (1-year TTL) — one rating per day per browser
- Aggregate stored in `timelapses/ratings.json` (count + sum)
- Displayed as `4.2★ (3)` in list

### Download Button
- `⬇ Snapshot` button captures current video frame via Canvas API
- Downloads as `sunset-YYYY-MM-DD.jpg` (desktop)

### Cloudflare CDN Plan
- Created `CLOUDFLARE_CDN_PLAN.md`
- Cloudflare Tunnel (persistent outbound, no open ports, hides Pi IP)
- Cloudflare Worker + KV for edge-side ratings
- Domain: onblackberryhill.com (to be purchased)
- Deferred — waiting on domain purchase and CF account setup

---

## Session 2 (this session — February 21, 2026)

### Service Restart & Snapshot Fixes
- **Root cause**: `pumphouse-timelapse` had been running since Feb 19 (before crop
  and snapshot code was added) — never restarted
- Restarted service so future captures use CROP_BOTTOM + snapshot code
- Recreated Feb 19 snapshot from second 3 of its MP4 (recording started late)
- Created Feb 21 snapshot at 47.5s into video (= ~35 min post-sunset position)

### Rating Display — Amazon Style
- Changed from plain text `4.2★ (3)` to:
  numeric score + yellow/grey star icons + count
  e.g. `4.2 ★★★★☆ (3)` with `.ls.lit { color: #f5c518 }`
- Added weather conditions as second line per list entry (cached only, no API call)

### CRF Change
- `OUTPUT_CRF` changed from 32 → 33 (~10–15% smaller files)

### List Text Sizes
- Bumped up: date line 0.9→1.0em, rating 0.85→1.0em, stars 2em, conditions 0.8→0.9em

### Keyboard Chevron Navigation
- `↓` opens "All timelapses" list (or moves down)
- `↑` opens list (or moves up; closes from top item)
- `Escape` closes list
- `Enter` navigates to keyboard-focused item
- Focused item gets `.kbd-focus` highlight style

### Snapshot Link in List
- Each list entry gets `(snapshot)` link opening the JPEG in a new tab
- Restructured `li` as flex container; main link gets `class="list-main"`

### Mobile / iPhone
- `playsinline` added to `<video>` — prevents iOS Safari auto-fullscreen
- Nav button date labels wrapped in `.nav-label` span, hidden ≤600px via media query
  so only `←` / `→` arrows show, leaving room for the title
- Swipe left = newer day, swipe right = older day
  (ignores near-vertical swipes to avoid conflicting with scroll)
- Thumbnails auto-resize to 44vw on small screens
- Speed buttons remain on own row; Pause + Snapshot moved to second `.ctrl-btns` row
- Snapshot button: on touch devices opens `/timelapse/DATE/snapshot` in new tab
  instead of canvas download (more natural on iPhone)
- Swipe hint `swipe ← → to change days` shown below title on mobile (hidden on desktop)

### Site Branding Header
- Added `<header class="site-header">` above nav with:
  - **On Blackberry Hill** (property name)
  - Links to Meredith Lodging listing and Airbnb
  - Hidden on mobile (`.site-sub { display:none }` at ≤600px) to save space
- Rental links:
  - Meredith: https://www.meredithlodging.com/listings/1830
  - Airbnb: https://www.airbnb.com/rooms/894278114876445404

### Documentation
- Created `TIMELAPSE.md` — routes, keyboard shortcuts, mobile gestures,
  capture config table, weather sources, ratings, file layout, cache tip
- Created `docs/conversations/timelapse-development.md` (this file)

---

## Key Files

| File | Purpose |
|------|---------|
| `sunset_timelapse.py` | Capture daemon: RTSP → frames → MP4 + snapshot |
| `monitor/web.py` | Flask app: routes, HTML viewer, weather, ratings |
| `TIMELAPSE.md` | User-facing feature/shortcut reference |
| `CLOUDFLARE_CDN_PLAN.md` | Future CDN/tunnel/Worker+KV plan |
| `timelapses/*.mp4` | Daily timelapse videos |
| `timelapses/snapshots/*.jpg` | Daily snapshot JPEGs |
| `timelapses/weather/*.json` | Cached weather per day |
| `timelapses/ratings.json` | Aggregate star ratings |

## Git History (selected)
```
0919e65  Mobile improvements, ↑ opens chevron, swipe nav, playsinline, docs
2794a6e  Timelapse list: bigger text, keyboard chevron nav, snapshot link
db7e548  Timelapse list: Amazon-style star ratings, weather conditions, CRF 33
c8d67b9  Timelapse: spacebar pause/play toggle
```
