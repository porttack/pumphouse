# Ecobee Integration Setup

This document explains how to set up and use the Ecobee integration to fetch temperature and vacation mode data from your thermostats.

## Overview

There are **two approaches** to fetch Ecobee data:

### Option 1: API-Based (Recommended if you have developer access)
[fetch_ecobee_data.py](fetch_ecobee_data.py) - Uses official Ecobee API
- ✅ Reliable, officially supported
- ✅ Clean JSON responses
- ❌ Requires developer account (not available as of March 2024)

### Option 2: Web Scraping (Fallback for everyone else)
[scrape_ecobee.py](scrape_ecobee.py) - Scrapes the Ecobee consumer portal
- ✅ Works with regular user account
- ✅ No developer access needed
- ⚠️ May break if Ecobee changes their website
- ⚠️ Requires investigation to find data endpoints

Both scripts fetch:
- Current temperature from the "Living Room Ecobee" thermostat
- Vacation mode status for the "Blackberry Hill" house

---

## Web Scraping Approach (Recommended)

If you don't have Ecobee developer access, use the web scraping approach:

### 1. Install Python Dependencies

All dependencies are already in requirements.txt (requests, beautifulsoup4).

### 2. Configure Credentials

Add your regular Ecobee account credentials to `~/.config/pumphouse/secrets.conf`:

```
ECOBEE_USERNAME=your-email@example.com
ECOBEE_PASSWORD=your-password-here
```

### 3. Run the Scraper

```bash
cd /home/pi/src/pumphouse
./scrape_ecobee.py --debug
```

### 4. Investigate the Portal Structure

The Ecobee portal likely uses JavaScript to load data dynamically. The script will:
- Log in successfully
- Save HTML pages to debug files (`ecobee_portal.html`, etc.)
- Attempt to find temperature and vacation data

**Next Steps After First Run:**
1. Check the saved HTML files to understand the page structure
2. Look for:
   - AJAX/API endpoints that load thermostat data
   - Embedded JSON data in `<script>` tags
   - DOM elements containing temperature/vacation info
3. Update the scraper to extract data from the correct locations

**Alternative: Use Selenium**

If the portal is heavily JavaScript-based, we may need to use Selenium with a headless browser:
```bash
pip install selenium
```

This allows the JavaScript to execute and populate the page before scraping.

---

## API Approach (If You Have Developer Access)

### 1. Ecobee Developer Account and API Key

**IMPORTANT**: As of March 2024, Ecobee is **not accepting new developer accounts**. If you don't already have an API key, use the web scraping approach above.

If you already have an Ecobee developer account:
1. Log in to https://www.ecobee.com/developers/
2. Create a new app or use an existing one
3. Copy your API key

### 2. Install Python Dependencies

```bash
cd /home/pi/src/pumphouse
source venv/bin/activate
pip install -r requirements.txt
```

This will install the `pyecobee` library (version 1.3.0+).

### 3. Configure API Key

Add your Ecobee API key to `~/.config/pumphouse/secrets.conf`:

```
ECOBEE_API_KEY=your-ecobee-api-key-here
```

See [secrets.conf.template](secrets.conf.template) for the full template.

## First-Time Setup (Authorization)

The first time you run the script, you need to authorize it with your Ecobee account using a PIN-based flow:

```bash
./fetch_ecobee_data.py
```

The script will:
1. Display a PIN code
2. Ask you to visit https://www.ecobee.com/consumerportal/index.html
3. Guide you through authorizing the app

Steps:
1. Go to the Ecobee website
2. Log in to your account
3. Click "My Apps" in the menu
4. Click "Add Application"
5. Enter the PIN shown by the script
6. Press ENTER in the terminal after authorizing

The script will then:
- Request OAuth access and refresh tokens
- Save them to `~/.config/pumphouse/ecobee_tokens.json`
- Automatically refresh tokens on future runs (tokens expire every hour)

## Usage

### Basic Usage

```bash
./fetch_ecobee_data.py
```

Output:
```
Ecobee Data Fetcher
============================================================
Target Thermostat: Living Room Ecobee
Target Location: Blackberry Hill
============================================================

✓ Successfully fetched data:
  Temperature: 68.5°F
  Vacation Mode: INACTIVE
  Thermostat: Living Room Ecobee
  Location: Blackberry Hill
  Timestamp: 2024-12-23T10:30:00.123456
```

### Debug Mode

To see detailed API communication:

```bash
./fetch_ecobee_data.py --debug
```

This will show:
- All available thermostats in your account
- API request/response details
- Token refresh operations
- Full data extraction process

## How It Works

### Authentication (OAuth 2.0)

The Ecobee API uses OAuth 2.0 with a PIN-based flow:

1. **Authorization**: App requests a PIN, user enters it on ecobee.com
2. **Token Request**: App exchanges PIN for access/refresh tokens
3. **Token Storage**: Tokens saved to `~/.config/pumphouse/ecobee_tokens.json`
4. **Token Refresh**: Access tokens expire every hour and are auto-refreshed

### Data Fetching

The script uses the `pyecobee` library to:

1. Load saved OAuth tokens (or perform initial authorization)
2. Refresh tokens if expired
3. Request thermostat data with:
   - `include_runtime=True` - Gets current temperature
   - `include_events=True` - Gets vacation holds
   - `include_location=True` - Gets house location
4. Find the "Living Room Ecobee" thermostat
5. Extract temperature (converted from Fahrenheit * 10)
6. Check for active vacation events

### Data Structure

The script returns:

```python
{
    'temperature': 68.5,              # Current temp in °F
    'vacation_mode': False,            # True if vacation active
    'vacation_name': None,             # Name of active vacation or None
    'thermostat_name': 'Living Room Ecobee',
    'location': 'Blackberry Hill',
    'timestamp': '2024-12-23T10:30:00.123456'
}
```

## Integration Options

You can integrate this script into your monitoring system in several ways:

### Option 1: Scheduled CSV Export (like Reservations)

Create a script similar to [scrape_reservations.py](scrape_reservations.py) that:
- Runs every hour via cron
- Fetches Ecobee data
- Appends to a CSV file (e.g., `ecobee_data.csv`)
- Used by the web dashboard for display

### Option 2: Real-Time API (like Occupancy)

Create a module similar to [monitor/occupancy.py](monitor/occupancy.py) that:
- Caches data in memory
- Refreshes on demand (with rate limiting)
- Used directly by the web dashboard

### Option 3: Background Service

Add Ecobee polling to the main monitor service that:
- Fetches data every hour
- Stores in a shared state file
- Includes in dashboard status

## Future Enhancements

### Vacation Mode Control

To enable turning vacation mode on/off, you'll need to:

1. Use the `pyecobee` library's `create_vacation()` method:
```python
ecobee_service.create_vacation(
    thermostat_id,
    name="Vacation",
    cool_hold_temp=75,
    heat_hold_temp=65,
    start_date_time="2024-12-25 00:00:00",
    end_date_time="2024-12-30 00:00:00"
)
```

2. Use the `delete_vacation()` method to cancel:
```python
ecobee_service.delete_vacation(thermostat_id, vacation_name)
```

3. Create a simple web API endpoint or control script similar to [control.sh](control.sh)

See the [Ecobee API Examples](https://www.ecobee.com/home/developer/api/examples/index.shtml) for more details.

## Troubleshooting

### "No saved tokens found"

This is normal on first run. Follow the PIN authorization process.

### "Token refresh failed"

Tokens may have expired completely. Delete `~/.config/pumphouse/ecobee_tokens.json` and re-authorize.

### "Thermostat 'Living Room Ecobee' not found"

The script expects a thermostat named exactly "Living Room Ecobee". Check:
- Run with `--debug` to see all available thermostats
- Edit the `TARGET_THERMOSTAT` variable in the script if needed

### "Location is 'X', expected 'Blackberry Hill'"

This is just a warning. The script will still work, but verify you're getting data from the right house.

### Import Error: pyecobee

Make sure you've installed the requirements:
```bash
source venv/bin/activate
pip install -r requirements.txt
```

## API Rate Limits

The Ecobee API has rate limits:
- **Standard accounts**: Unknown (conservative estimate: ~100-200 requests/hour)
- **Typical strategy**: Query every 5-15 minutes for near-real-time data
- **Recommended for this use case**: Query every hour

The script handles token refreshes automatically, which count against rate limits but are minimal (every 60 minutes).

## References

- [Ecobee Developer Portal](https://www.ecobee.com/developers/)
- [Ecobee API Documentation](https://www.ecobee.com/home/developer/api/documentation/v1/index.shtml)
- [pyecobee Library](https://github.com/sherif-fanous/Pyecobee)
- [Ecobee API Examples](https://www.ecobee.com/home/developer/api/examples/index.shtml)
