# E-Paper Display Endpoint

Generates a 1-bit BMP image for a 2.13-inch e-Paper display (250x122 pixels, landscape) showing water tank status. Designed to be fetched via `wget` from a remote Raspberry Pi.

## Endpoint

```
GET /api/epaper.bmp
```

Unauthenticated. Served on the existing pumphouse web server (port 6443).

## Query Parameters

| Parameter   | Default | Description |
|-------------|---------|-------------|
| `hours`     | 3       | Hours of history to show in the graph |
| `tenant`    | auto    | Override occupancy type: `yes` = force tenant mode, `no` = force owner/unoccupied |
| `occupied`  | auto    | Override occupancy: `yes` = force occupied, `no` = force unoccupied |
| `threshold` | config  | Override low-water threshold (percent), e.g. `95` |

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
│    │ ░░░occupied  next: 2/25░░░ │
│    3h ago              2/14 22:15│
└──────────────────────────────────┘
```

- **Header**: Gallons (left), percent full (right), "available water" (center, small)
- **Graph**: Filled area chart of tank level over the requested time window
  - Y-axis: Min/max percent labels
  - X-axis: `Nh ago` (left), last reading timestamp from mypt.in (right)
  - Minimum 5% of tank capacity between Y-axis top and bottom
- **Occupancy bar**: Inverted (XOR) bar at bottom of graph showing "occupied" or "unoccupied" with next check-in date
- **Low water overlay**: When tank <= threshold, "Save Water" is XOR'd over the graph center

### Tenant + Low Water Mode

When a non-owner guest is checked in AND tank is at or below the low-water threshold, the display switches to a simplified full-screen warning:

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

## Configuration

In `monitor/config.py`:

```python
# E-Paper Display Configuration
EPAPER_CONSERVE_WATER_THRESHOLD = 50   # Tank % at or below which triggers "Save Water" (None to disable)
EPAPER_OWNER_STAY_TYPES = ['Owner Stay', 'Owner Stay, Full Clean']  # Reservation types that count as owner
```

## Data Sources

- **Tank level (header)**: Live fetch from mypt.in with 30-second timeout; falls back to latest `snapshots.csv` row
- **Graph**: Historical data from `snapshots.csv` (15-minute intervals)
- **Occupancy**: `reservations.csv` — reservation Type field determines owner vs. tenant
- **Reading timestamp**: Derived from live `last_updated` or snapshot timestamp minus `tank_data_age_seconds`

## Usage on Remote Pi

Fetch the image:

```bash
wget -q --no-check-certificate -O /tmp/epaper.bmp \
  "https://PUMPHOUSE_IP:6443/api/epaper.bmp?hours=3"
```

Display on e-Paper:

```python
from PIL import Image
img = Image.open('/tmp/epaper.bmp').convert('1')
# send to e-Paper display buffer...
```

## Testing

Use CGI overrides to test all display modes without waiting for actual conditions:

```bash
# Normal mode (owner/unoccupied, tank OK)
wget ... "https://host:6443/api/epaper.bmp"

# Tenant + low water (full-screen warning)
wget ... "https://host:6443/api/epaper.bmp?tenant=yes&threshold=95"

# Owner + low water (graph with Save Water overlay)
wget ... "https://host:6443/api/epaper.bmp?tenant=no&threshold=95"

# Unoccupied with next checkin shown
wget ... "https://host:6443/api/epaper.bmp?occupied=no"
```
