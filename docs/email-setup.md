# Email Notification Setup

Rich HTML email alerts with embedded charts, sensor readings, and one-click relay control buttons.

---

## What You Get

- HTML emails with full system status and tank chart
- Dashboard link at the top of every email
- Tank level bar (percentage + gallons)
- Current sensor readings (float switch, pressure)
- 1-hour and 24-hour tank change statistics
- Embedded tank level history chart (default: 3 days)
- Priority-based formatting (green / orange / red)
- One-click relay control buttons (override, bypass, purge)
- Daily status email at configurable time (default: 6 AM)
- Smart check-in / checkout reminders
- Shortened subject lines with gallons first for mobile

---

## Setup

### 1. Create secrets file

```bash
cp secrets.conf.template ~/.config/pumphouse/secrets.conf
chmod 600 ~/.config/pumphouse/secrets.conf
```

### 2. Gmail App Password (recommended)

1. Go to https://myaccount.google.com/security → enable 2-Step Verification
2. Go to https://myaccount.google.com/apppasswords
3. Create an App Password for "Mail" → copy the 16-character password

### 3. Add password to secrets file

```bash
nano ~/.config/pumphouse/secrets.conf
```

```ini
EMAIL_SMTP_PASSWORD=abcdefghijklmnop
```

Use the App Password — **not** your regular Gmail password.

### 4. Configure email settings in `monitor/config.py`

```python
# Basic email settings
ENABLE_EMAIL_NOTIFICATIONS = True
EMAIL_TO = "your-email@gmail.com"
EMAIL_FROM = "your-email@gmail.com"
EMAIL_FRIENDLY_NAME = "Pumphouse"        # Name shown in From field
EMAIL_SMTP_USER = "your-email@gmail.com"
EMAIL_SMTP_SERVER = "smtp.gmail.com"
EMAIL_SMTP_PORT = 587

# Daily status email
ENABLE_DAILY_STATUS_EMAIL = True
DAILY_STATUS_EMAIL_TIME = "06:00"        # 24-hour format
DAILY_STATUS_EMAIL_CHART_HOURS = 72      # Hours of history in chart (3 days)

# Checkout reminder (integrates with reservations.csv)
ENABLE_CHECKOUT_REMINDER = True
CHECKOUT_REMINDER_TIME = "11:00"

# Dashboard link in emails
DASHBOARD_EMAIL_URL = None               # Defaults to PUMPHOUSE_HOST:PUMPHOUSE_PORT
```

### 5. Test

```bash
cd ~/src/pumphouse
source venv/bin/activate
python -m monitor.check --test-email
```

### 6. Restart the monitor

```bash
sudo systemctl restart pumphouse-monitor
```

---

## Other Email Providers

| Provider | SMTP Server | Port |
|----------|-------------|------|
| Gmail | `smtp.gmail.com` | 587 |
| Outlook/Hotmail | `smtp-mail.outlook.com` | 587 |
| Yahoo | `smtp.mail.yahoo.com` | 587 |

---

## Email Alert Types

| Alert | Trigger |
|-------|---------|
| Tank Dropping | Crosses decreasing threshold (1000, 750, 500, 250 gal) |
| Tank Filling | Crosses increasing threshold (500, 750, 1000, 1200 gal) |
| Tank Full Confirmed | Float sensor confirms FULL for N+ readings |
| Override Auto-Shutoff | Override turned off by overflow protection |
| Well Recovery | Tank gained 50+ gallons after stagnation |
| Well May Be Dry | No 50+ gallon refill in 4+ days |
| High Flow Detected | Fill rate > 60 GPH |
| Backflush Detected | 50+ gallon overnight loss |
| Daily Status | Every morning at configured time |
| Check-in Reminder | "Turn on heat!" when tenant checks in today |
| Checkout Reminder | "Turn down thermostat" when tenant checks out today |

---

## Remote Control via Email

Every alert email includes color-coded relay control buttons. See [docs/relay-control.md](relay-control.md#remote-control-via-email) for token setup.

---

## Troubleshooting

### Authentication failed
- Use an App Password, not your regular Gmail password
- Verify `EMAIL_SMTP_USER` matches your Gmail address

### Connection timeout
- Check internet connectivity
- Verify SMTP server and port in `config.py`
- Check if firewall blocks port 587

### Email not received
- Check spam/junk folder
- Verify `EMAIL_TO` is correct

### Check logs
```bash
sudo journalctl -u pumphouse-monitor -f
# or
python -m monitor --debug
```

### Disable without removing configuration

```python
# In monitor/config.py:
ENABLE_EMAIL_NOTIFICATIONS = False
```
Then: `sudo systemctl restart pumphouse-monitor`

---

## Security Notes

- Never commit your App Password to version control
- `EMAIL_SMTP_PASSWORD` is loaded from `secrets.conf` automatically
- App Passwords can be revoked at any time from your Google Account settings
