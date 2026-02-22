# Push Notifications

Real-time phone alerts for important tank events using ntfy.sh.

---

## Setup

### 1. Install the ntfy app

- **iOS**: "ntfy" on the App Store
- **Android**: "ntfy" on Google Play
- **Web**: https://ntfy.sh

### 2. Choose a unique topic name

Pick something that others won't guess:
```
pumphouse-yourname-randomnumber
```
Example: `pumphouse-smith-89234`

### 3. Configure in `monitor/config.py`

```python
ENABLE_NOTIFICATIONS = True
NTFY_TOPIC = "pumphouse-yourname-randomnumber"
NTFY_SERVER = "https://ntfy.sh"
```

Or set `NTFY_TOPIC` in `~/.config/pumphouse/secrets.conf` (preferred — keeps topic out of git).

### 4. Subscribe in the ntfy app

Open the app → tap "+" → enter your topic name → done.

### 5. Test

```bash
source venv/bin/activate
python -m monitor.check --test-notification
```

### 6. Restart the monitor

```bash
sudo systemctl restart pumphouse-monitor
```

---

## Notification Events

### 1. Tank Level Thresholds

Alerts when the tank crosses configured levels going up or down.

```python
NOTIFY_TANK_DECREASING = [1000, 750, 500, 250]   # Alert when dropping through these
NOTIFY_TANK_INCREASING = [500, 750, 1000, 1200]   # Alert when filling through these
```

- "Tank Dropping — crossed 1000 gallons down"
- "Tank Filling — crossed 750 gallons up"
- Bounce protection: won't re-alert if water fluctuates around a threshold

### 2. Well Recovery

Detects when the well starts producing water again after a stagnant period.

```python
NOTIFY_WELL_RECOVERY_THRESHOLD = 50          # Gallons gained to count as recovery
NOTIFY_WELL_RECOVERY_STAGNATION_HOURS = 6    # Hours of flat/declining before recovery alert
```

- "Well Recovery Detected — Tank gained 50+ gallons after stagnation period"
- Smart detection: 6+ hours of flat/declining levels followed by 50+ gallon gain
- Prevents duplicate alerts by tracking the low-point timestamp

### 3. High Flow Detection (Fast Fill Mode)

Alerts when the shared well's float activates, causing sustained high-flow fill.

```python
NOTIFY_HIGH_FLOW_ENABLED = True    # Enable high flow alerts
NOTIFY_HIGH_FLOW_GPH = 60          # GPH threshold for fast fill detection
NOTIFY_HIGH_FLOW_WINDOW_HOURS = 6  # How far back to look
NOTIFY_HIGH_FLOW_AVERAGING = 2     # Average over N snapshots
```

- "High Flow Detected — Tank filling at 65 GPH (fast fill mode active)"
- Helps decide whether to manually adjust the bypass relay based on current occupancy

### 4. Backflush Detection

Tracks carbon filter backflush events (large overnight water usage).

```python
NOTIFY_BACKFLUSH_ENABLED = True            # Enable backflush detection
NOTIFY_BACKFLUSH_THRESHOLD = 50            # Gallons lost to trigger detection
NOTIFY_BACKFLUSH_WINDOW_SNAPSHOTS = 2      # Look back N snapshots
NOTIFY_BACKFLUSH_TIME_START = "00:00"      # Backflush detection window start
NOTIFY_BACKFLUSH_TIME_END = "04:30"        # Backflush detection window end
```

- "Backflush Detected — Carbon filter backflush used ~85 gallons"
- Detects large tank drops (50+ gallons) during the configured overnight window (12am–4:30am)

### 5. Well May Be Dry

Warns if no significant refill in several days.

```python
NOTIFY_WELL_DRY_DAYS = 4   # Days without 50+ gallon refill before alert
```

- "Well May Be Dry — No 50+ gallon refill in 4.2 days"

### 6. Float Sensor Confirmation (Tank Full)

```python
NOTIFY_FLOAT_CONFIRMATIONS = 3   # Consecutive OPEN readings required
```

- "Tank Full Confirmed — Float sensor confirmed FULL for 3+ readings"

### 7. Override Auto-Shutoff

```python
NOTIFY_OVERRIDE_SHUTOFF = True   # Alert on auto-shutoff
```

- "Override Auto-Shutoff — Tank reached 1410 gal, override turned off"

---

## Spam Prevention

```python
MIN_NOTIFICATION_INTERVAL = 300   # Minimum 5 minutes between same-type alerts
```

---

## Self-Hosting ntfy (Optional)

Run your own ntfy server for complete privacy and no rate limits:

```bash
# On a server with Docker
docker run -d --name ntfy -p 8080:80 binwiederhier/ntfy serve

# Update config
NTFY_SERVER = "http://your-server-ip:8080"
```

Note: Self-hosting requires port forwarding if you want alerts from outside your network.
