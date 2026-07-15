# Aurora Borealis Timelapse — Implementation Plan

## Overview

Add an aurora monitoring mode that watches NOAA space weather for geomagnetic
storm alerts and automatically triggers a 10 PM–4 AM timelapse session on
eligible nights. The west-facing ocean camera is unlikely to capture aurora
directly, but during Kp 8–9 events the red/pink glow can spread broadly across
the sky and may reflect off the water — a spectacular shot if it happens.

We are near the peak of Solar Cycle 25 (2025–2026), making this the best
window in a decade to try.

---

## Goals

- Poll NOAA for geomagnetic storm watches/warnings automatically
- Send an ntfy alert when a qualifying storm is detected
- Automatically start a timelapse session from ~10 PM through 4 AM on storm nights
- Skip capture if cloud cover makes it pointless (optional quality-of-life)
- Store aurora timelapses separately from sunset timelapses for easy review

---

## Data Source: NOAA Space Weather

**Primary API:** NOAA Space Weather Prediction Center (SWPC)

```
https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json
```

Returns a JSON array of 3-hour Kp forecast values for the next ~3 days.
Each row: `[datetime_utc, kp_predicted, kp_observed, kp_estimated]`

**Alternate / simpler endpoint (current conditions):**

```
https://services.swpc.noaa.gov/json/planetary_k_index_1m.json
```

Returns 1-minute Kp observations for the past ~24 hours.

**Storm scale thresholds:**

| NOAA G-scale | Kp | Oregon visibility        |
|--------------|----|--------------------------|
| G1           | 5  | No — too far south       |
| G2           | 6  | Unlikely                 |
| G3           | 7  | Possible on dark nights  |
| G4           | 8  | Likely, including coast  |
| G5           | 9  | Broad sky coverage       |

**Trigger threshold:** Kp ≥ 7 forecast OR observed to initiate a session.
Kp ≥ 8 warrants a high-priority ntfy alert.

---

## Architecture

### New file: `aurora_timelapse.py`

A standalone daemon (similar to `sunset_timelapse.py`) that:

1. **Polls NOAA** every 30 minutes during the day, every 15 minutes at night
2. **Decides** whether tonight warrants an aurora capture window
3. **Starts capture** at 10 PM local time if storm is forecast or active (Kp ≥ 7)
4. **Stops capture** at 4 AM or when storm subsides (Kp falls below 5 for 90+ min)
5. **Assembles** an MP4 at session end using ffmpeg (same pipeline as sunset)
6. **Sends ntfy alerts** at storm detection and session start/end

### Systemd service: `pumphouse-aurora.service`

Run alongside the existing `pumphouse-timelapse.service`. Both services can
coexist because `web_timelapse.py` already checks for active frame directories
before opening an RTSP connection — aurora captures should use a distinct
directory to avoid collisions.

---

## Key Implementation Details

### Frame capture

Reuse the same RTSP approach from `sunset_timelapse.py`:

```python
CAMERA_IP    = "192.168.1.81"
CAMERA_PORT  = 554
FRAME_DIR    = Path("/home/pi/timelapses/aurora")
FRAME_INTERVAL = 10  # seconds — shorter for dynamic event
CROP_BOTTOM  = 120   # keep in sync with sunset_timelapse.py
```

Night scenes need longer exposure — the camera's RTSP stream handles this
automatically (no change needed), but a note in the code that frames may be
dark without post-processing is worth adding.

### NOAA polling module

```python
# aurora_monitor.py (helper, imported by aurora_timelapse.py)

NOAA_FORECAST_URL = (
    "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"
)
KP_TRIGGER   = 7   # start capture session
KP_ALERT_HIGH = 8  # high-priority ntfy notification

def fetch_kp_forecast() -> list[tuple[datetime, float]]:
    """Return list of (utc_datetime, kp_value) for next 24 hours."""

def max_kp_tonight(tz="America/Los_Angeles") -> float:
    """Return highest forecast Kp between 10 PM and 4 AM local tonight."""

def storm_active_now() -> float:
    """Return current 1-minute Kp observation."""
```

### Session logic

```
10 PM check:
    if max_kp_tonight() >= KP_TRIGGER:
        send ntfy: "Aurora alert — Kp {kp} forecast tonight"
        start capture loop

During capture (every 15 min check):
    if storm_active_now() >= KP_ALERT_HIGH:
        send ntfy high-priority: "Strong aurora — Kp {kp} now"
    if time > 4 AM:
        stop capture, assemble video
    if Kp < 5 for 90+ consecutive minutes after midnight:
        stop early, assemble video

Morning:
    send ntfy with video link if frames were captured
```

### ntfy notifications

Use existing `monitor.ntfy.send_notification()`:

```python
# Storm detected — evening alert
send_notification(
    title="Aurora Alert — Newport",
    message=f"Kp {kp} forecast tonight. Aurora session starting at 10 PM.",
    priority="high",
    tags=["milky_way", "night_with_stars"],
    click_url=DASHBOARD_URL,
)

# Strong storm active mid-session
send_notification(
    title="Strong Aurora NOW — Kp {kp}",
    message="High geomagnetic activity. Check the camera.",
    priority="urgent",
    tags=["rotating_light"],
    click_url=f"{DASHBOARD_URL}snapshot",
)

# Session complete
send_notification(
    title="Aurora session complete",
    message=f"Captured {frame_count} frames. Timelapse ready.",
    priority="default",
    tags=["movie_camera"],
    click_url=f"{DASHBOARD_URL}timelapse?aurora=1",
)
```

### Cloud cover check (optional enhancement)

Use the existing NWS KONP observation already in `weather_api.py`. If current
conditions report "Overcast" or "Fog", log a warning but still capture (fog
can clear, and partial clearing creates dramatic shots). Do not skip the session
based on clouds alone — the whole night is long enough for breaks.

### Output storage

```
/home/pi/timelapses/aurora/
    YYYY-MM-DD/          ← frame directory (deleted after assembly)
    YYYY-MM-DD-aurora.mp4
    aurora.log
```

Keep aurora videos indefinitely (events are rare). Separate from
`/home/pi/timelapses/` sunset videos.

---

## Web Dashboard Integration

Add a `/timelapse/aurora` route (or `?aurora=1` param) to `web_timelapse.py`
to browse and play aurora timelapses. Minimal change — the existing video
serving logic can be reused with a different base directory.

Optionally add a small "Aurora" section to `status.html` showing:
- Current Kp index (live from NOAA)
- Tonight's forecast max Kp
- Link to latest aurora timelapse if one exists

---

## Systemd Service Template

```ini
# terraform/services/pumphouse-aurora.service
[Unit]
Description=Pumphouse Aurora Timelapse
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/src/pumphouse
ExecStart=/home/pi/src/pumphouse/venv/bin/python aurora_timelapse.py
Restart=always
RestartSec=60

[Install]
WantedBy=multi-user.target
```

---

## Phased Implementation

### Phase 1 — NOAA polling + notifications only
- `aurora_monitor.py`: fetch Kp, evaluate tonight's forecast
- Cron or simple polling loop, ntfy alerts only
- No camera involvement yet
- **Validate:** do alerts fire correctly on storm nights?

### Phase 2 — Aurora timelapse daemon
- `aurora_timelapse.py`: full capture + assembly loop
- Uses Phase 1 module for storm detection
- Store videos in `/home/pi/timelapses/aurora/`
- ntfy at session start and end

### Phase 3 — Dashboard integration
- Kp status widget on `status.html`
- `/timelapse/aurora` web route to browse captures

---

## What Success Looks Like

At solar maximum (2025–2026), realistically expect:
- **1–3 capture sessions per year** that meet the Kp ≥ 7 threshold at night
- **Fog/cloud interference** will reduce usable captures further
- **1 genuinely good video** per year is a realistic best case from a
  west-facing view

The system costs nothing to run and needs no manual intervention. If a Kp 9
event happens on a clear Newport night, the camera will already be rolling.

---

## Notes for Implementation

- `sunset_timelapse.py` is a useful reference for the full capture loop,
  ffmpeg assembly, RTSP connection handling, and retention logic
- Secrets (camera credentials, ntfy topic) follow the existing pattern:
  loaded from `~/.config/pumphouse/secrets.conf`
- Location constants (lat/lon, timezone) are already defined in
  `sunset_timelapse.py` and can be shared or duplicated
- The NOAA SWPC APIs are public, no API key required
- Both aurora and sunset services can run simultaneously; use distinct
  `/tmp/timelapse-frames/aurora-YYYY-MM-DD/` and
  `/tmp/timelapse-frames/YYYY-MM-DD/` directories to avoid collision
