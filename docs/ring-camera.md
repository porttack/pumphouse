# Ring Camera Integration

Displays a live snapshot from a Ring camera on the dashboard alongside the e-paper display and the most recent sunset timelapse frame. On desktop the three images share a row at equal height; on mobile the Ring snapshot stacks below the e-paper display.

## How it works

- The dashboard fetches `/ring-snapshot`, which proxies a JPEG from Ring's API.
- Results are cached to disk for 10 minutes (`RING_CACHE_MINUTES`) and shared across all processes (gunicorn workers, monitor daemon, email notifier) — Ring's API is called at most once per 10 minutes regardless of load.
- The OAuth token is saved to disk and refreshed automatically — no re-authentication needed unless you change your Ring password.
- If Ring is unreachable the image is silently hidden and the other two images resize to fill the row.

## One-time setup

```bash
cd /home/pi/src/pumphouse && source venv/bin/activate
python3 bin/ring_setup.py
```

The script will:
1. Prompt for your Ring account email and password.
2. Handle the 2FA code sent to your phone or email.
3. Save an OAuth token to `~/.config/pumphouse/ring_token.json`.
4. Print the names of all Ring cameras on the account.

## Configuration

`~/.config/pumphouse/secrets.conf` — optional, only needed if you have more than one Ring camera:

```
RING_CAMERA_NAME=Front Door
```

Leave this unset to use the first camera found on the account.

`config.py` constants (not normally changed):

| Constant | Default | Purpose |
|---|---|---|
| `RING_CAMERA_NAME` | `''` | Camera name; first device if blank |
| `RING_TOKEN_FILE` | `~/.config/pumphouse/ring_token.json` | OAuth token path |
| `RING_CACHE_FILE` | `~/.config/pumphouse/ring_snapshot_cache.jpg` | Shared JPEG cache |
| `RING_CACHE_MINUTES` | `10` | Cache TTL in minutes |

## Token management

The token file is created by `ring_setup.py` and updated automatically by the web server whenever Ring issues a refreshed token. The refresh token is valid for roughly 12 months. If it expires (or you change your Ring password), run `ring_setup.py` again.

The token file contains sensitive OAuth credentials — it is not committed to git (it lives outside the repo in `~/.config/`).

## Snapshot freshness

Ring uploads snapshots periodically from the camera. Without an active live-view session, snapshots are typically 1–5 minutes old. The 10-minute server-side cache adds up to 10 additional minutes of lag.

---

## Vehicle counting

Each snapshot is run through a vehicle detector at fetch time. The count is overlaid on the image and embedded in the JPEG EXIF (`UserComment` tag) so downstream consumers (epaper display, alert logic) can read it without re-running inference.

### Algorithm

Two detectors run in combination; their results are summed:

1. **YOLOv8n** (`models/yolov8n.onnx`) — primary detector. Counts objects in COCO classes 2, 3, 5, 7 (car, motorcycle, bus, truck) with confidence ≥ 0.15, then applies NMS at 0.60 IoU. Falls back to YOLOv4-tiny (OpenCV DNN) if the ONNX model is absent.
2. **Background subtraction** — secondary detector for vehicles that YOLO misses because they are partially occluded. Compares a configurable zone (`_BG_ZONE`, default right 45% × middle 45% of frame) against the nearest-hour reference image. Returns +1 if ≥ 8% of zone pixels differ by more than 35 intensity units.

The final count is `YOLO count + background count`.

### File storage

| Path | Description |
|---|---|
| `~/.config/pumphouse/ring_snapshot_cache.jpg` | Latest snapshot (JPEG with timestamp/count overlay and EXIF count). Refreshed every `RING_CACHE_MINUTES` (10 min). Shared by all processes via a lock file. |
| `~/.config/pumphouse/ring_snapshot_cache.lock` | Advisory lock preventing concurrent Ring API calls across gunicorn workers and the monitor daemon. |
| `~/.config/pumphouse/vehicle_count.json` | Last-known count with Unix timestamp. Read by the e-paper display without re-running inference. TTL: 2 hours (stale after that, skipped 00–08). |
| `~/.config/pumphouse/ring_reference/` | Per-hour empty-driveway reference images used by the background subtraction detector (see below). |
| `~/.config/pumphouse/ring_reference/HH.jpg` | One JPEG per hour slot (00–23), e.g. `08.jpg` is the reference for 8 AM. Updated continuously; the nearest available slot is used when an exact match doesn't exist. |

### Building the reference library

The background subtraction detector is only effective once reference images exist for each daylight hour. References are saved automatically by the monitor when:

- The property is **unoccupied** (per reservation data)
- YOLO detects **0 vehicles**
- It is currently **daytime** (between sunrise and sunset)

To build baseline images faster — especially during a known-empty period — run the dedicated script in the background:

```bash
nohup /home/pi/src/pumphouse/venv/bin/python3 \
    /home/pi/src/pumphouse/bin/build_ring_baseline.py \
    >> /tmp/ring_baseline.log 2>&1 &
```

The script fetches a fresh snapshot every `RING_CACHE_MINUTES` (10 min), validates with YOLO, and saves a reference if the driveway is empty. Logs show per-fetch results and which hour slots still need coverage:

```
17:40:00 INFO Hour 17 — reference saved  (0 vehicles)
17:40:00 INFO Coverage: 2/16 daylight slots  covered=[12, 17]  missing=[5, 6, 7, ...]
```

References are continuously refreshed as the script runs, so lighting conditions stay accurate across seasons.

### Alerts

When the property is unoccupied and the vehicle count changes:

- **Arrival (any count increase)** — high-priority push notification with a link to the Ring snapshot.
- **Arrival from zero (0 → N)** — email with the Ring snapshot image embedded inline.
- **Departure** — default-priority push notification.

All alerts are rate-limited by `MIN_NOTIFICATION_INTERVAL` (shared with other alert types).

### Tuning constants (ring_camera.py)

| Constant | Default | Effect |
|---|---|---|
| `_BG_ZONE` | `(0.55, 0.15, 1.0, 0.60)` | Fraction of frame used for background subtraction `(x1, y1, x2, y2)` |
| `_BG_DIFF_THRESHOLD` | `35` | Per-pixel intensity difference to count as "changed" (0–255) |
| `_BG_COVERAGE_FRAC` | `0.08` | Fraction of zone pixels that must differ to declare a vehicle present |
| `_CONF` (YOLO) | `0.15` | Minimum class confidence to count a detection |
| `_NMS` (YOLO) | `0.60` | IoU threshold for non-max suppression |

## Troubleshooting

| Symptom | Fix |
|---|---|
| Ring image missing from dashboard | Check `/ring-snapshot` in browser — error message indicates the cause |
| `Ring not configured` | Run `bin/ring_setup.py` |
| `Ring token unreadable` | Delete `~/.config/pumphouse/ring_token.json` and re-run setup |
| `Ring snapshot error: ...` | Usually a transient network issue; stale cache is served if available |
| Unclosed session warnings (setup script) | Cosmetic only; token was saved successfully |

## Logs

All Ring activity is logged to the systemd journal. Filter with:

```bash
# Live (both services)
sudo journalctl -u pumphouse-web -u pumphouse-monitor -f | grep -i "ring"

# History only
sudo journalctl -u pumphouse-web -u pumphouse-monitor | grep -i "ring"
```

Log levels: **INFO** on successful fetch, **WARNING** on recoverable issues (missing token, no devices, None returned), **ERROR** on API failures.
