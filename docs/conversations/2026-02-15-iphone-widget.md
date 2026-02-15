# iPhone Widget for Tank Status

**Date**: 2026-02-15

## Goal

Display the tank status BMP on an iPhone home screen widget that refreshes every ~5 minutes, reusing the existing `/api/epaper.bmp` endpoint.

## Solution

Use the free **Scriptable** iOS app to create a widget that fetches the BMP image and displays it as the widget background.

### Why Scriptable?

- Free, no subscription
- Supports fetching images from URLs
- Supports custom refresh intervals
- Can display images as widget backgrounds
- No server-side changes needed — reuses the existing BMP endpoint

### Implementation

The script (`pistat/scriptable-widget.js`) does the following:

1. Fetches the BMP from the pumphouse server
2. Sets it as the widget background image
3. Applies a subtle blue gradient overlay for better iOS readability
4. Requests a 5-minute refresh (iOS may throttle to 5-15 minutes)
5. Falls back to a blue background with error text if the fetch fails

### Key Details

- **No `?hours=` parameter**: The URL has no hours parameter, so the server picks the default based on occupancy (24h for tenants, 72h for owner/unoccupied)
- **SSL**: Scriptable handles self-signed certificates without issues
- **Widget size**: Medium recommended for readability; small also works
- **iOS refresh throttling**: Apple doesn't guarantee exact refresh intervals. The `refreshAfterDate` is a hint, not a guarantee. Real-world refresh is typically 5-15 minutes depending on battery level and usage patterns.

### Files Created

- `pistat/scriptable-widget.js` — The Scriptable widget code
- `pistat/IPHONE_WIDGET.md` — Setup instructions suitable for sharing with family members
