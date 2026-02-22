# Sunset Timelapse Viewer

Daily sunset timelapses captured from the pumphouse camera at Newport, OR.

## Routes

| URL | Description |
|-----|-------------|
| `/timelapse` | Redirects to the most recent date |
| `/timelapse/YYYY-MM-DD` | Viewer page for a specific date |
| `/timelapse/latest.mp4` | Redirects to the most recent MP4 file |
| `/timelapse/latest.jpg` | Redirects to the most recent snapshot JPEG |
| `/timelapse/YYYY-MM-DD/snapshot` | Snapshot JPEG (~35 min after sunset) |
| `/frame` | Live single frame from the RTSP camera (cropped) |
| `/frame?raw=1` | Live single frame, uncropped |

## Keyboard Shortcuts (Viewer Page)

| Key | Action |
|-----|--------|
| `←` | Go to previous (older) day |
| `→` | Go to next (newer) day |
| `Space` | Pause / play video |
| `↓` | Open "All timelapses" list (or move down in list) |
| `↑` | Open list (or move up; closes from top item) |
| `Escape` | Close the list |
| `Enter` | Navigate to the keyboard-focused list item |

## Touch / Mobile

- **Swipe left** → newer day
- **Swipe right** → older day
- Nav buttons show only arrows on small screens (date labels hidden)
- Thumbnails auto-resize to ~44% of viewport width on small screens

## Capture Configuration (`sunset_timelapse.py`)

| Parameter | Value | Description |
|-----------|-------|-------------|
| `FRAME_INTERVAL` | 20 s | Base seconds between frames |
| `SLOWDOWN_FACTOR` | 4 | Frames captured = interval ÷ factor (5 s/frame effective) |
| `WINDOW_BEFORE` | 60 min | Capture starts this many minutes before sunset |
| `WINDOW_AFTER` | 60 min | Capture ends this many minutes after sunset |
| `OUTPUT_FPS` | 24 | Playback frame rate of output MP4 |
| `OUTPUT_CRF` | 33 | H.264 quality (lower = better; 23 = default) |
| `CROP_BOTTOM` | 120 px | Pixels removed from bottom at capture time (privacy) |
| `SNAPSHOT_OFFSET_MINUTES` | 35 min | Snapshot taken this many minutes after sunset |
| `RETENTION_DAYS` | 30 days | Keep every daily timelapse for this period |
| `WEEKLY_YEARS` | 3 years | After daily window, keep one per ISO week for this period |

## Weather Data

Weather is shown per day using two sources:

1. **NWS KONP** (Newport Municipal Airport) — actual station observations.
   Provides: temperature hi/lo, precipitation, wind avg/max, humidity, conditions.
2. **Open-Meteo ERA5** — fills in cloud cover and solar radiation.
   Used as full fallback when NWS data is unavailable (dates older than NWS retention).

Weather is cached in `timelapses/weather/YYYY-MM-DD.json` and never re-fetched
once a day's data is complete.

## Ratings

- Visitors can rate each day 3–5 stars (1–2 star ratings not allowed).
- One rating per day per browser (stored in a cookie for 1 year).
- Aggregate data (count + sum) stored in `timelapses/ratings.json`.
- Rating is shown in the "All timelapses" list as: `4.2 ★★★★☆ (3)`

## File Layout

```
timelapses/
  YYYY-MM-DD_HHMM.mp4   # daily timelapse (sunset time in filename)
  snapshots/
    YYYY-MM-DD.jpg        # snapshot JPEG ~35 min after sunset
  weather/
    YYYY-MM-DD.json       # cached weather for that day
  ratings.json            # aggregate star ratings
  timelapse.log           # daemon log
```

## Refreshing a Cached Image in Chrome

Chrome caches snapshot JPEGs aggressively. To force a reload:
- **Hard refresh**: `Cmd+Shift+R` (Mac) or `Ctrl+Shift+R` (Windows/Linux)
- Or open DevTools → Network tab → check "Disable cache" → reload
