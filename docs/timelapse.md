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
| `/timelapse/YYYY-MM-DD/snapshot` | Snapshot JPEG (~35 min after sunset) used as list thumbnail |
| `/timelapse/YYYY-MM-DD/set-snapshot` | `POST` — save a JPEG as the key snapshot for that date (direct access only) |
| `/timelapse/YYYY-MM-DD/frame-view` | `POST` — display a POSTed frame JPEG in a full viewer page (direct access only) |
| `/timelapse/YYYY-MM-DD/frame-view-client` | `GET` — static frame viewer that reads image from `localStorage` (Cloudflare-safe) |
| `/api/ratings/YYYY-MM-DD` | JSON aggregate rating `{count, avg}` (served by Cloudflare Worker) |
| `/snapshot` | Live camera frame with weather panel and date; `?info=0` for raw JPEG |
| `/frame` | Alias for `/snapshot` (backwards compat) |

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

## Snapshot Button (Viewer Page)

The **Snapshot** button in the video controls extracts whatever frame is currently showing in the player and displays it in a new page at full resolution (scaled by HTML). The page has **Timelapse** and **Download** buttons; it does not show the weather panel.

### Two paths depending on access method

| Access | Mechanism | Data sent to Pi |
|--------|-----------|-----------------|
| Direct Pi (`192.168.x.x`, `tplinkdns`, etc.) | `POST` image to `/timelapse/DATE/frame-view` | Full JPEG (LAN only, fine) |
| Cloudflare (`onblackberryhill.com`) | Store in `localStorage`, open `/timelapse/DATE/frame-view-client` | **Zero** — fully client-side |

The Cloudflare path avoids a DoS vector: without it, every public Snapshot click would bypass the CDN and POST a large JPEG directly to the Pi.

### Set key snapshot (direct access only)

On direct access, the frame-view page also shows a **Set key snapshot** button that `POST`s the displayed frame to `/timelapse/DATE/set-snapshot`, saving it as `timelapses/snapshots/YYYY-MM-DD.jpg`. This replaces whatever image the timelapse daemon saved automatically and updates the thumbnail in the "All timelapses" list.

### Download button

Both the frame-view page and the `/snapshot` live-camera page include a **⬇ Download** button that uses the HTML5 `download` attribute on an `<a>` pointing at the embedded data URL — works on desktop and mobile without a round-trip to the server.

---

## Live Snapshot Page (`/snapshot`)

Shows the most recent camera image with current weather (wind, cloud %, humidity, weather description).

**Frame source priority:**
1. Most recent frame from `/tmp/timelapse-frames/YYYY-MM-DD/` if a timelapse is currently recording (avoids competing RTSP connections with the daemon)
2. Live RTSP grab via ffmpeg otherwise

**Weather description** comes from Open-Meteo's real-time `weather_code` (mapped via WMO codes) so it matches the current cloud % shown next to it. The daily NWS description is used only as a fallback if the real-time call fails.

---

## Viewer Page Header (Direct Access)

When visiting the timelapse viewer directly (not via Cloudflare), the site header shows a **(public site)** link that navigates to the same date on `https://onblackberryhill.com/timelapse/YYYY-MM-DD`. This makes it easy to check what the public/cached version looks like. The link is hidden on Cloudflare.

---

## Weather Data

Weather is shown per day using two sources:

1. **NWS KONP** (Newport Municipal Airport) — actual station observations: temperature hi/lo, precipitation, wind avg/max, humidity, conditions text, and cloud cover derived from `cloudLayers` (CLR/FEW/SCT/BKN/OVC → 0/13/38/63/100 %)
2. **Open-Meteo ERA5** — solar radiation and sunrise/sunset times; used as full fallback for older dates. ERA5 `cloud_cover_mean` is only used when NWS `cloudLayers` data is absent — ERA5 models high-altitude cloud layers that can show 100 % on days the ground observer reports "Clear".

**Observation timing:** Both the weather description and NWS-derived cloud cover are taken from the observation **closest to sunset time** (computed via `astral`), so they reflect timelapse conditions rather than midday.

**Cache policy:** Past days' data is cached permanently in `timelapses/weather/YYYY-MM-DD.json`. Today's data is re-fetched if the cache is older than 30 minutes, so conditions stay current while the timelapse is recording.

---

## Ratings

- Visitors rate each day 3–5 stars (1–2 star ratings not allowed)
- One rating per day per browser (cookie stored for 1 year)
- Aggregate data stored in **Cloudflare KV** (`RATINGS` namespace); `ratings.json` kept in sync for direct Pi access
- Rating widget is fully client-side — cookie read via JS, count/avg fetched from `/api/ratings/DATE` — so HTML pages are cacheable by Cloudflare

---

## Caching (Cloudflare CDN)

| Content | Cache-Control | Rationale |
|---------|--------------|-----------|
| Today's HTML page | `public, max-age=300, must-revalidate` | Preview MP4 and "All Timelapses" chevron change every ~10 min during recording |
| Yesterday's HTML page | `public, max-age=300, must-revalidate` | "Next →" nav button must appear within minutes of today's first MP4 (~20 min before sunset) |
| HTML pages 2+ days old | `public, max-age=3600` | Content is stable; 1-hour TTL lets the chevron slowly propagate to cached pages |
| Past MP4 files | `max-age=31536000` | Immutable once finalized |
| Today's MP4 (preview) | `max-age=600` | Updated in-place every ~10 min during recording |
| Snapshot JPEGs | `max-age=31536000` | Immutable once written by daemon |
| `/timelapse/DATE/frame-view-client` | `public, max-age=3600` | Static JS template; image lives in browser `localStorage`, never reaches Pi |
| `/api/ratings/DATE` | `public, max-age=60` | Served by Worker from KV |

### Why short TTLs on HTML pages are safe

Cloudflare caches **per URL per edge node**. If one visitor browses through 30 old pages, all 30 URLs are primed for everyone else at that edge. The Pi sees at most one re-fetch per URL per TTL period per edge node — not one per visitor. With 10 edge nodes and 60 past pages, `max-age=3600` means at most 600 Pi requests per hour for old HTML, all trivially fast (no DB or external API calls for past dates).

### The `immutable` trap

The previous code used `immutable` on all past pages (`date < today`), which caused a correctness bug: **yesterday's "Next →" button never activated** after today's timelapse was generated, because Cloudflare would serve the stale HTML for a full year. The fix uses a three-tier TTL:

1. Today + yesterday → 5 min (active/changing content)
2. 2+ days old → 1 hour (stable but eventually consistent)
3. MP4s and JPEGs → 1 year (truly immutable binary assets)

**Note:** HTML page caching requires a Cloudflare Cache Rule set to "Cache Everything" for URI path `/timelapse/20*`. Binary assets (MP4, JPEG) are cached automatically based on `Cache-Control` headers. See the [Cloudflare CDN setup notes](conversations/cloudflare-cdn-setup.md).

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
