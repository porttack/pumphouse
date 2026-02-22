# E-Paper Display

Two-part system for showing water tank status on a 2.13-inch e-Paper display (250×122 pixels, landscape).

1. **Server-side** (`monitor/web.py`): `/api/epaper.bmp` endpoint generates a 1-bit BMP image
2. **Client-side** (`pistat/`): Daemon on a remote Raspberry Pi fetches and displays the image every 5 minutes

---

## Architecture

```
┌─────────────────┐   HTTPS    ┌─────────────────────┐
│  Pumphouse Pi   │◄──────────│  Display Pi (pistat) │
│  /api/epaper.bmp│  every 5m  │  2.13" e-Paper       │
│  (port 6443)    │           │  partial refresh      │
└─────────────────┘            └─────────────────────┘
```

---

## Endpoint

```
GET /api/epaper.bmp
```

Unauthenticated. Served on the existing pumphouse web server (port 6443).

### Query Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `hours` | 24 or 72 | Hours of history for graph (24 for tenants, 72 for owner/unoccupied) |
| `tenant` | auto | Override occupancy type: `yes` = force tenant mode, `no` = force owner/unoccupied |
| `occupied` | auto | Override occupancy: `yes` = force occupied, `no` = force unoccupied |
| `threshold` | config | Override low-water threshold (percent), e.g. `95` |

---

## Display Modes

### Normal Mode (owner or unoccupied)

```
┌──────────────────────────────────┐
│ 1290 gal  available   92%       │
│             water               │
│──────────────────────────────────│
│ 93%│▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓          │
│    │▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓        │
│    │▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓     │
│ 90%│▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │
│    │░occupied until 2/20░░░░░░░ │
│    72h ago             2/14 22:15│
└──────────────────────────────────┘
```

- **Header**: Gallons (left), percent full (right), "available water" (center, small)
- **Graph**: Filled area chart of tank level over the requested time window
  - Y-axis: min/max percent labels; minimum 5% span between top and bottom
  - X-axis: `Nh ago` (left), last reading timestamp from mypt.in (right)
- **Occupancy bar**: Inverted (XOR) bar at bottom of graph:
  - `occupied until M/DD` — with checkout date when occupied
  - `next checkin M/DD` — with check-in date when unoccupied
  - `unoccupied` — when no upcoming reservations
- **Low water overlay**: When tank ≤ threshold, "Save Water" is XOR'd over the graph center

### Tenant + Low Water Mode

When a non-owner guest is checked in AND the tank is at or below the low-water threshold, the display shows a full-screen warning instead:

```
┌──────────────────────────────────┐
│                                  │
│       S a v e   W a t e r        │
│                                  │
│       Tank filling slowly        │
│                                  │
└──────────────────────────────────┘
```

No graph, no data — just a clear message for the guest.

---

## Configuration

In `monitor/config.py`:

```python
EPAPER_CONSERVE_WATER_THRESHOLD = 50      # Tank % at or below which triggers "Save Water" (None to disable)
EPAPER_OWNER_STAY_TYPES = ['Owner Stay', 'Owner Stay, Full Clean']  # Reservation types that count as owner
EPAPER_DEFAULT_HOURS_TENANT = 24          # Default graph hours for tenant occupancy
EPAPER_DEFAULT_HOURS_OTHER = 72           # Default graph hours for owner or unoccupied
```

---

## Data Sources

| Data | Source |
|------|--------|
| Tank level (header) | Live fetch from mypt.in with 30 s timeout; falls back to latest `snapshots.csv` row |
| Graph | Historical data from `snapshots.csv` (15-minute intervals) |
| Occupancy | `reservations.csv` — reservation Type field determines owner vs. tenant |
| Reading timestamp | Derived from live `last_updated` or snapshot timestamp minus `tank_data_age_seconds` |

---

## Display Daemon (pistat/)

The `pistat/` directory contains a daemon for the remote Raspberry Pi with the e-Paper display attached. It fetches the BMP and updates the display using partial refresh (no flashing).

**Key features:**
- Partial refresh — only changed pixels update, no full-screen flash
- Falls back to cached image on network failures
- Full refresh on startup to clear ghosting
- Runs as a systemd service (`epaper-display.service`)
- 5-minute update interval (configurable)

**Quick install (on the display Pi):**
```bash
cd /home/pi/src/pumphouse/pistat
sudo ./install_service.sh
```

See [pistat/README.md](../pistat/README.md) for full installation, configuration, and troubleshooting.

---

## iPhone Widget

The same BMP endpoint can be displayed as an iPhone home screen widget using the free Scriptable app. The widget fetches the image every ~5 minutes.

See [pistat/IPHONE_WIDGET.md](../pistat/IPHONE_WIDGET.md) for setup instructions.

---

## Testing

Use query parameters to test all display modes without waiting for actual conditions:

```bash
# Normal mode (owner/unoccupied, tank OK)
wget --no-check-certificate -O epaper.bmp "https://host:6443/api/epaper.bmp"

# Tenant + low water (full-screen "Save Water" warning)
wget --no-check-certificate -O epaper.bmp "https://host:6443/api/epaper.bmp?tenant=yes&threshold=95"

# Owner + low water (graph with Save Water overlay)
wget --no-check-certificate -O epaper.bmp "https://host:6443/api/epaper.bmp?tenant=no&threshold=95"

# Unoccupied with next check-in shown
wget --no-check-certificate -O epaper.bmp "https://host:6443/api/epaper.bmp?occupied=no"

# Custom hours
wget --no-check-certificate -O epaper.bmp "https://host:6443/api/epaper.bmp?hours=6"
```
