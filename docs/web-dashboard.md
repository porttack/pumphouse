# Web Dashboard

HTTPS web interface for real-time monitoring and historical data. Served by `pumphouse-web` (Flask + gunicorn on port 6443).

---

## Initial Setup

Generate an SSL certificate (one-time):

```bash
# Self-signed (browser will warn — click "Advanced" and proceed)
./generate_cert.sh
```

For a proper Let's Encrypt cert on your `tplinkdns.com` hostname, see [docs/pi-setup.md](pi-setup.md#8-ssl-certificate).

---

## Starting the Web Server

The web server runs via systemd (recommended):

```bash
# Check status
sudo systemctl status pumphouse-web

# View logs
sudo journalctl -u pumphouse-web -f

# Restart after code changes
sudo systemctl restart pumphouse-web
```

For manual testing only (not for production):

```bash
# Start web server directly (HTTPS on port 6443)
python -m monitor.web

# Run without SSL (HTTP only, for quick local testing)
python -m monitor.web --no-ssl
```

---

## Access

- **LAN URL**: `https://your-pi-hostname:6443/`
- **Public URL**: `https://onblackberryhill.com/` (via Cloudflare CDN — timelapse viewer only)
- **Default username**: `admin`
- **Default password**: `pumphouse`

### Custom Credentials

Set environment variables before starting, or configure in the systemd service override:

```bash
export PUMPHOUSE_USER=yourusername
export PUMPHOUSE_PASS=yourpassword
python -m monitor.web
```

---

## Dashboard Features

### Image Row
Three images share a full-width row on desktop at an equal computed height; on mobile they stack vertically:

- **E-paper display** — current tank graph (links to full-size JPEG)
- **Sunset snapshot** — most recent timelapse frame (links to timelapse viewer); hidden if no timelapse exists
- **Ring camera** — live Ring snapshot with baked-in timestamp, server-cached for 60 seconds (links to Ring app on mobile, raw JPEG on desktop); only shown in `?owner` / `?manager` mode

The dashboard auto-refreshes every 60 minutes.

### Sensor Panel
- Live pressure state (HIGH/LOW)
- Float switch state (OPEN/CLOSED)
- Temperature and humidity (from AHT20 in Pi enclosure)
- Tank level with visual progress bar and gallons
- Occupancy status (OCCUPIED/UNOCCUPIED) with next check-in/out date

### Tank Chart
- Interactive time-series chart with selectable ranges: **6h · 12h · 24h · 3d · 7d · 14d**
- **Visual stagnation detection**: Orange dots on 6-hour periods with ≤30 gal gain; green dots on filling periods
- Configurable default range (default: 72 hours / 3 days): `DASHBOARD_DEFAULT_HOURS` in `config.py`

### Statistics Bar
- Tank level change over last 1 hour and 24 hours
- Pressure HIGH percentages
- Last 50+ gallon refill event

### Reservations Table
- Current and upcoming reservations (next 6 weeks)
- Check-in / check-out dates, guest name, nights, type, income
- Privacy protection: income and repeat columns hidden unless authenticated

### Recent Data
- Last 10 snapshots
- Last 20 events (TANK_LEVEL events hidden by default to reduce noise)
- Auto-refresh every 5 minutes

### Well GPH
- 3-week average slow-fill and fast-fill GPH displayed in the status panel

---

## API Endpoints

| Route | Description |
|-------|-------------|
| `GET /` | Web dashboard (requires auth); `?owner` enables control buttons + income totals |
| `GET /?hours=N` | Dashboard filtered to last N hours of events (`?days=N` also accepted) |
| `GET /water` | Public water tank status page (embedded e-paper image, auto-refreshes) |
| `GET /water?hours=N` | Water status with explicit graph timespan (`?days=N` also accepted) |
| `GET /watch/pressure_low` | Toggle pressure-LOW ntfy watch flag (owner mode) |
| `GET /sunset` | Live camera JPEG from Amcrest via RTSP; `?enhance=1` for contrast boost |
| `GET /snapshot` | Live camera frame with weather panel; `?info=0` for raw JPEG |
| `GET /timelapse` | Redirects to most recent timelapse |
| `GET /timelapse/YYYY-MM-DD` | Timelapse viewer for specific date |
| `GET /timelapse/latest.mp4` | Redirect to most recent MP4 |
| `GET /api/epaper.bmp` | 250×122 1-bit BMP for e-paper display / iPhone widget |
| `GET /api/epaper.jpg` | Color JPEG at 4× resolution; `?hours=N`, `?public=yes`, `?tenant=no` |
| `GET /api/ratings/YYYY-MM-DD` | JSON rating `{count, avg}` from Cloudflare KV |
| `GET /control/<token>` | Remote relay action via secret URL (from email buttons) |
| `GET /ring-snapshot` | Ring camera JPEG with baked-in timestamp; 60-second server cache; 503 if Ring unavailable |

---

## Owner Mode (`?owner`) and Manager Mode (`?manager`)

Append `?owner` or `?manager` to the dashboard URL to unlock elevated features:

| Feature | `?owner` | `?manager` |
|---------|----------|------------|
| Ring camera image | Yes | Yes |
| Control buttons (relays, watch toggle) | Yes | No |
| Income totals in reservations | Yes | No |
| Extended default time range (120 h) | Yes | No |

- **Security**: secret relay tokens are only embedded in the HTML when `?owner` is present — plain visitors and `?manager` users never see them.

Buttons turn red when the corresponding relay/flag is active. Clicking any button redirects back to `/?owner`.

---

## Water Status Page (`/water`)

Public-facing page showing the current e-paper tank graph. No occupancy or income data.

- `?hours=N` — show the last N hours on the graph (e.g. `/water?hours=48`)
- `?days=N` — alias for hours (e.g. `/water?days=7`)
- Via Cloudflare: serves `public=yes` image (no dashboard link)
- Direct Pi access: image links to full dashboard; includes Image/Dashboard/Weather links
- Cache: 10 minutes normally; 1 minute when a custom timespan is specified

---

## Data Files & Rotation

Snapshots and events are stored in `~/.local/share/pumphouse/`:

- `snapshots.csv` — one row per 15-minute snapshot
- `events.csv` — one row per event (pump cycles, alerts, relay changes, etc.)

`rotate_snapshots.py` archives rows older than 60 days to monthly gzip files:

```
~/.local/share/pumphouse/snapshots-YYYY-MM.csv.gz
~/.local/share/pumphouse/events-YYYY-MM.csv.gz
```

Runs via cron on the 1st of each month at 3am:
```
0 3 1 * * /home/pi/src/pumphouse/venv/bin/python3 /home/pi/src/pumphouse/rotate_snapshots.py
```

---

## Camera Snapshot (`/sunset`)

Proxies a live JPEG from the Amcrest IP camera at `192.168.1.81` using HTTP Digest authentication.

- No authentication required on the route itself
- `?enhance=1` applies percentile stretch on the LAB L channel for better low-light exposure
- Retry logic: 3 attempts with 2 s delay to handle camera throttling
- Credentials: set `CAMERA_USER` and `CAMERA_PASS` in `~/.config/pumphouse/secrets.conf`

---

## Timelapse Viewer (`/timelapse`)

See [docs/timelapse.md](timelapse.md) for full timelapse viewer documentation, routes, keyboard shortcuts, weather strip, and ratings.

---

## Configuration

Key settings in `monitor/config.py`:

```python
DASHBOARD_DEFAULT_HOURS = 72         # Default time range for chart
DASHBOARD_EMAIL_URL = None           # Custom URL for email links (uses PUMPHOUSE_HOST if None)
ENABLE_DAILY_STATUS_EMAIL = True     # Send daily status email
DAILY_STATUS_EMAIL_TIME = "06:00"    # 24-hour format
DAILY_STATUS_EMAIL_CHART_HOURS = 72  # Chart window in daily email
ENABLE_CHECKOUT_REMINDER = True      # Email reminder when tenant checks out
CHECKOUT_REMINDER_TIME = "11:00"     # 24-hour format
```

---

## Architecture Notes

The web server runs independently from the monitor daemon. Both must be running to collect data and view it. The monitor writes `events.csv` and `snapshots.csv`; the web server reads them.

The web server also runs the timelapse viewer — if the timelapse daemon produces new MP4s, they are served immediately at `/timelapse` without restarting the web server.
