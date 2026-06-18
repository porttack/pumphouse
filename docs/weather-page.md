# Public Weather Page

Public-facing weather page at `/weather`, DNS-aliased to `weather.onblackberryhill.com`.
No authentication required. Auto-refreshes every 20 minutes.

## Architecture

Self-contained Flask blueprint in `monitor/web_weather.py`, registered in `web.py`.
All HTML, CSS, and JS are inline (single response, no template files).

## Data Sources

| Data                  | Source                          | Update frequency  |
|-----------------------|---------------------------------|-------------------|
| Current conditions    | `snapshots.csv` (last row)      | Every 15 min      |
| 18h temperature chart | `snapshots.csv` (last 18h)      | Every 15 min      |
| 6h temp forecast      | Open-Meteo hourly API           | Per page load     |
| 10-day forecast       | Open-Meteo daily + hourly API   | Per page load     |
| 12-month history      | `snapshots-*.csv.gz` archives   | Per page load     |
| Sunrise/sunset        | Open-Meteo daily API            | Per page load     |
| Tide predictions      | NOAA CO-OPS API (station 9435380, South Beach Newport) | Per page load |
| Sunset slideshow      | `/timelapse/{date}/snapshot` JPGs | Static files     |
| Sunset scores         | `clip_scores.json` (hybrid color+CLIP scorer) | Updated by `score_sunset.py` |

## Page Sections (top to bottom)

### Hero Slideshow
- Ken Burns effect cycling through sunset photos (7s per slide, 1.4s crossfade)
- 2 most recent sunsets + 13 randomly sampled from photos scoring >= 660 in `clip_scores.json`
- "Live" button loads current camera snapshot with 30s Ken Burns, then resumes slideshow
- Date badge links to that day's timelapse page

### Conditions Card
- **Weather description** (e.g. "Clear") from `current_weather_desc()`
- **18h + 6h temperature chart** (Chart.js sparkline): solid line for history, dashed for forecast.
  Forecast is interpolated to 15-min intervals to match historical data density.
  Low labeled in blue, high in red. Sunrise/sunset icons at top of chart.
  Current temp displayed large on the right with update timestamp.
- **Moolack Beach tides**: beach photo background strip with dark overlay.
  Shows 1 past + 5 upcoming high/low tides from NOAA station 9435380.
  Header displays "Moolack Beach" and current tide level.
- **Stats grid** (4 tiles): Humidity, Wind Gust, Pressure, Sunrise & Sunset

### 10-Day Forecast
- Horizontal scrollable cards, one per day
- Afternoon best-code heuristic: uses `min()` of hourly weather codes 10am-5pm
  to avoid Oregon coast marine-layer pessimism
- Wind speed and gust on separate lines per card

### 12-Month Temperature & Wind Chart
- Chart.js with 365-day spine; days without data show as gaps
- Dual y-axes: temperature (left, orange), wind gusts (right, blue dashed)
- X-axis shows month names only (tick on the 1st of each month)

### Rent CTA
- "Check Availability" button linking to Meredith Lodging listing

### Footer
- Links to Sunset Timelapses, NWS Newport, Weather Station

## Key Design Decisions

- **Afternoon best-code**: Oregon coast daily `weather_code` = "most severe of day", which
  overstates cloudiness due to morning marine layer. Fix: fetch hourly codes and use
  `min()` of codes between 10am-5pm.
- **Forecast interpolation**: Open-Meteo returns hourly forecasts (1 point/hour) while
  historical data is every 15 minutes. Chart.js category axis spaces all points equally,
  so without interpolation the 6h forecast appeared as only ~8% of chart width. Interpolating
  to 15-min intervals gives correct visual proportions.
- **Sunset selection**: Random sampling from scored photos (rather than top-N) ensures
  each page load shows different sunsets. Threshold of 660 keeps roughly the top third.
- **Tide station**: NOAA 9435380 (South Beach, Newport, OR) is ~3 miles from Moolack Beach,
  the closest available tide prediction station.

## Caching

- `Cache-Control: public, max-age=300, stale-if-error=172800` on the response
- Page auto-refreshes via `<meta http-equiv="refresh" content="1200">` (20 min)

## External API Calls (per page load)

1. Open-Meteo forecast (daily + hourly weather codes, sunrise/sunset)
2. Open-Meteo hourly temperature (6h forecast for the sparkline)
3. NOAA tide curve (6-min interval predictions, 48h window)
4. NOAA tide high/low events (48h window)
