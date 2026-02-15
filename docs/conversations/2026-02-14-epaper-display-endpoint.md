# E-Paper Display BMP Endpoint

**Date**: 2026-02-14

## Goal

Create an unauthenticated web endpoint on the pumphouse Pi that generates a 1-bit BMP image sized for a 2.13-inch e-Paper display (250x122 pixels). A second Raspberry Pi fetches this image via `wget` and displays it using `Image.open().convert('1')`.

## Design Decisions

### Image Format
- 1-bit BMP (monochrome) — native format for e-Paper displays
- 250x122 pixels matching the 2.13" display in landscape mode
- Generated server-side using Pillow (`PIL`)

### Layout Evolution

**Initial layout**: Gallons (left), percent (right), filled area graph below with no labels.

**Iterative improvements through conversation**:
1. **Y-axis labels**: Added min/max percent labels on the graph, with a minimum 5% tank capacity spread to prevent flat-looking graphs
2. **X-axis context**: "Nh ago" on the left, timestamp on the right
3. **Timestamp source**: Changed from "now" to current date/time, then to the actual last tank reading time from mypt.in (subtracting `tank_data_age_seconds` from the snapshot timestamp), then to live data from mypt.in when available
4. **Center label**: Added "tank" between gallons and percent, then changed to stacked "available" / "water" for clarity to non-well users
5. **Live data**: Endpoint fetches live tank data from mypt.in (30s timeout) for current gallons/percent, falling back to snapshots.csv if the fetch fails
6. **Low water warning**: "Save Water" overlay using XOR pixel inversion over the graph
7. **Occupancy awareness**: Different behavior for tenants vs. owners/unoccupied

### Occupancy-Aware Display Modes

The display adapts based on who is viewing it:

- **Tenant + low water**: Full-screen "Save Water" with "Tank filling slowly" — no technical data, just a clear message for guests
- **Owner/unoccupied + low water**: Normal graph with "Save Water" XOR overlay — owner still sees the data
- **Normal (tank OK)**: Full graph with inverted occupancy bar at bottom showing "occupied"/"unoccupied" and next check-in date

Owner vs. tenant is determined by the reservation `Type` field in reservations.csv (e.g., "Owner Stay" vs. "Airbnb").

### Testing Overrides

CGI parameters added for testing without waiting for real conditions:
- `tenant=yes|no` — force tenant or owner mode
- `occupied=yes|no` — force occupancy status
- `threshold=N` — override low-water percent threshold

## Implementation

### Files Modified
- `monitor/web.py` — Added `/api/epaper.bmp` route (~280 lines)
- `monitor/config.py` — Added `EPAPER_CONSERVE_WATER_THRESHOLD` and `EPAPER_OWNER_STAY_TYPES`

### Files Created
- `EPAPER.md` — User-facing documentation

### Key Technical Details

- Uses Pillow for image generation (already a dependency for matplotlib)
- Fonts: DejaVuSans-Bold (22pt large), DejaVuSans (14pt medium, 11pt small) with fallback to default
- XOR pixel inversion for overlays — text is readable whether over black (filled graph) or white (empty area)
- Graph fill: dark polygon under the curve with white line on top for contrast
- Live tank data fetched via `get_tank_data()` with 30s timeout; graph always uses snapshots.csv for historical data

### Dependencies
- `Pillow` (already installed — used by matplotlib)
- `flask` (existing web server)
- No new dependencies required
