# Sunset Timelapse

Daily sunset timelapses captured from the pumphouse Amcrest camera at Newport, OR. Served publicly at **https://onblackberryhill.com/timelapse** via Cloudflare CDN.

---

## Routes

| URL | Description |
|-----|-------------|
| `/timelapse` | Redirects to most recent ≥4.5★ sunset (else latest) |
| `/timelapse?today` | Redirects to today's timelapse (or yesterday if not yet generated) |
| `/timelapse/YYYY-MM-DD` | Viewer page for a specific date |
| `/timelapse/latest.mp4` | Redirects to the most recent MP4 file |
| `/timelapse/latest.jpg` | Redirects to the most recent snapshot JPEG |
| `/timelapse/YYYY-MM-DD/snapshot` | Snapshot JPEG (~35 min after sunset) |
| `/api/ratings/YYYY-MM-DD` | JSON aggregate rating `{count, avg}` (served by Cloudflare Worker) |
| `/frame` | Live single frame from the RTSP camera (cropped) |
| `/frame?raw=1` | Live single frame, uncropped |

---

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

---

## Touch / Mobile

- **Swipe left** → newer day
- **Swipe right** → older day
- **Swipe up** → newer day (disabled when list is open)
- **Swipe down** → older day (disabled when list is open)
- Nav buttons show only arrows on small screens (date labels hidden)
- Thumbnails auto-resize to ~44% of viewport width on small screens

---

## Playback Speed

Buttons on the viewer page: **¼x · ½x · 1x · 2x · 4x · 8x** and **Pause**.

---

## Capture Configuration (`sunset_timelapse.py`)

| Parameter | Value | Description |
|-----------|-------|-------------|
| `FRAME_INTERVAL` | 20 s | Base seconds between captured frames |
| `SLOWDOWN_FACTOR` | 4 | Frames captured = interval ÷ factor (5 s/frame effective) |
| `WINDOW_BEFORE` | 60 min | Capture starts this many minutes before sunset |
| `WINDOW_AFTER` | 60 min | Capture ends this many minutes after sunset |
| `OUTPUT_FPS` | 24 | Playback frame rate of output MP4 |
| `OUTPUT_CRF` | 33 | H.264 quality (lower = better; 23 = default) |
| `CROP_BOTTOM` | 120 px | Pixels removed from bottom at capture time (privacy) |
| `SNAPSHOT_OFFSET_MINUTES` | 35 min | Snapshot taken this many minutes after sunset |
| `RETENTION_DAYS` | 30 days | Keep every daily timelapse for this period |
| `WEEKLY_YEARS` | 3 years | After daily window, keep one per ISO week for this period |
| `PREVIEW_INTERVAL` | 600 s | Seconds between partial preview assemblies (10 min) |

Adjust `SLOWDOWN_FACTOR` and `WINDOW_BEFORE`/`WINDOW_AFTER` to taste, then `sudo systemctl restart pumphouse-timelapse`.

Filenames embed the sunset time for easy browsing: `2026-02-19_1750.mp4`.

---

## How It Works

1. Calculates sunset time for Newport, OR using the `astral` library
2. Wakes up `WINDOW_BEFORE` minutes before sunset, opens the RTSP stream via ffmpeg
3. Grabs one frame every `FRAME_INTERVAL / SLOWDOWN_FACTOR` seconds for the full window
4. Every `PREVIEW_INTERVAL` seconds, assembles a partial MP4 in a background thread (watchable mid-capture)
5. After ffmpeg finishes, assembles the final MP4 and deletes the frames
6. **Frames are written to `/tmp/timelapse-frames/` (tmpfs / RAM)** — SD card only sees the assembled MP4s

---

## SD Card Impact

Frames are written to `/tmp/timelapse-frames/` (tmpfs) and deleted after assembly — **they never touch the SD card**. Only assembled MP4s are written to disk.

### SD writes vs. `SLOWDOWN_FACTOR`

| `SLOWDOWN_FACTOR` | Effective interval | Frames/2hr | SD writes/day |
|---|---|---|---|
| 4 (default) | 5s | 1,440 | ~30 MB |
| 2 | 10s | 720 | ~15 MB |
| 1 | 20s | 360 | ~8 MB |

Preview assemblies (10-min interval, ~12/night) add ~40 MB/day during capture. Total is well under 100 MB/day.

### Video quality vs. file size (`OUTPUT_CRF`)

| `OUTPUT_CRF` | Quality | Approx. size (60 s video) |
|---|---|---|
| 23 | near-lossless | ~200 MB |
| 28 | high | ~20 MB |
| 32 | good | ~5 MB |
| 33 (default) | good | ~4 MB |
| 35 | acceptable | ~2 MB |

### Long-term storage (tiered retention)

- `RETENTION_DAYS=30`: every day for 30 days
- `WEEKLY_YEARS=3`: one per ISO week for 3 years
- At ~5 MB/file: 30 daily + 156 weekly ≈ **~1 GB total** on a 57 GB card

---

## Weather Data

Weather is shown per day using two sources:

1. **NWS KONP** (Newport Municipal Airport) — actual observations: temperature hi/lo, precipitation, wind avg/max, humidity, conditions
2. **Open-Meteo ERA5** — cloud cover and solar radiation; used as full fallback for older dates

Weather is cached in `timelapses/weather/YYYY-MM-DD.json` and never re-fetched once a day's data is complete.

---

## Ratings

- Visitors rate each day 3–5 stars (1–2 star ratings not allowed)
- One rating per day per browser (cookie stored for 1 year)
- Aggregate data stored in **Cloudflare KV** (`RATINGS` namespace); `ratings.json` kept in sync for direct Pi access
- Rating widget is fully client-side — cookie read via JS, count/avg fetched from `/api/ratings/DATE` — so HTML pages are cacheable by Cloudflare

---

## Caching (Cloudflare CDN)

| Content | Cache-Control |
|---------|--------------|
| Past HTML pages (`date < today`) | `public, max-age=31536000, immutable` |
| Today's HTML page | `public, max-age=600, must-revalidate` |
| Past MP4 files | `max-age=31536000` |
| Today's MP4 (preview) | `max-age=600` |
| Snapshot JPEGs | `max-age=31536000` |
| `/api/ratings/DATE` | `public, max-age=60` |

**Note:** Cloudflare caches binary assets (MP4, JPEG) based on headers. HTML pages require a Cache Rule: "Cache Everything" for `/timelapse/20*` — see [docs/cloudflare.md](cloudflare.md).

---

## File Layout

```
timelapses/
  YYYY-MM-DD_HHMM.mp4       # Daily timelapse (sunset time in filename)
  snapshots/
    YYYY-MM-DD.jpg           # Snapshot JPEG ~35 min after sunset
  weather/
    YYYY-MM-DD.json          # Cached weather for that day
  ratings.json               # Local ratings mirror (canonical store is Cloudflare KV)
  timelapse.log              # Daemon log
cloudflare/
  ratings-worker.js          # Cloudflare Worker source
  wrangler.toml              # Wrangler deploy config
```

---

## Installation

```bash
# Install and enable the timelapse service
sudo cp pumphouse-timelapse.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pumphouse-timelapse

# Check status and logs
sudo systemctl status pumphouse-timelapse
sudo journalctl -u pumphouse-timelapse -f
tail -f ~/timelapses/timelapse.log
```

---

## Refreshing a Cached Image in Chrome

Chrome caches snapshot JPEGs aggressively. To force a reload:
- **Hard refresh**: `Cmd+Shift+R` (Mac) or `Ctrl+Shift+R` (Windows/Linux)
- Or: DevTools → Network tab → check "Disable cache" → reload
