# Email Notification Setup Guide

Email notifications are now configured in your pumphouse monitoring system! This guide will help you set up email alerts.

## What You Get

- **HTML email alerts** with the alert reason as the subject line
- **Embedded chart images** showing tank level history
- **Link to full dashboard** for more details
- **Priority-based formatting** (green for info, orange for warnings, red for urgent)

## Configuration Steps

### 1. Set Up Secrets File

First, copy the template and create your secrets file:

```bash
# Copy the template
cp secrets.conf.template ~/.config/pumphouse/secrets.conf

# Set secure permissions (only you can read it)
chmod 600 ~/.config/pumphouse/secrets.conf
```

This file will store your email password securely and is **never committed to git**.

### 2. Edit the Configuration File

Edit `/home/pi/src/pumphouse/monitor/config.py` and configure the email settings (lines 40-48):

```python
# Email Notification Configuration
ENABLE_EMAIL_NOTIFICATIONS = True  # Already enabled!
EMAIL_TO = "onblackberryhill@gmail.com"  # Already set to your email
EMAIL_FROM = ""  # Set to your sending email address
EMAIL_SMTP_SERVER = "smtp.gmail.com"  # Default is Gmail
EMAIL_SMTP_PORT = 587  # Default is correct for Gmail
EMAIL_SMTP_USER = ""  # Set to your email username
EMAIL_SMTP_PASSWORD = ""  # Set to your App Password (see below)
```

### 3. Gmail Setup (Recommended)

If you want to use Gmail to send emails, follow these steps:

#### Step 1: Enable 2-Step Verification
1. Go to https://myaccount.google.com/security
2. Under "How you sign in to Google", enable **2-Step Verification** if not already enabled

#### Step 2: Create an App Password
1. Go to https://myaccount.google.com/apppasswords
2. You may need to sign in again
3. Under "Select app", choose **Mail**
4. Under "Select device", choose **Other** and type "Pumphouse Monitor"
5. Click **Generate**
6. Google will show you a 16-character password (e.g., `abcd efgh ijkl mnop`)
7. **Copy this password** - you'll need it for the config

#### Step 3: Add Password to Secrets File
Edit `~/.config/pumphouse/secrets.conf`:

```bash
nano ~/.config/pumphouse/secrets.conf
```

Add your App Password (replace with the actual 16-character password, no spaces):
```
EMAIL_SMTP_PASSWORD=abcdefghijklmnop
```

**Important:** Use the App Password, NOT your regular Gmail password!

#### Step 4: Update Config
Edit `/home/pi/src/pumphouse/monitor/config.py`:

```python
EMAIL_FROM = "youremail@gmail.com"  # Your Gmail address
EMAIL_SMTP_USER = "youremail@gmail.com"  # Same as EMAIL_FROM
# EMAIL_SMTP_PASSWORD is loaded from secrets file automatically
```

### 4. Other Email Providers

If you're not using Gmail, you'll need to find your provider's SMTP settings:

#### Common SMTP Settings

**Outlook/Hotmail:**
```python
EMAIL_SMTP_SERVER = "smtp-mail.outlook.com"
EMAIL_SMTP_PORT = 587
```

**Yahoo:**
```python
EMAIL_SMTP_SERVER = "smtp.mail.yahoo.com"
EMAIL_SMTP_PORT = 587
```

**Custom/Other:**
- Contact your email provider for SMTP server and port
- You may need to enable "less secure apps" or create an app-specific password

### 5. Test Your Configuration

Once configured, test that email notifications work:

```bash
cd /home/pi/src/pumphouse
python -m monitor.check --test-email
```

This will:
- Show your current configuration
- Send a test email with a chart embedded
- Report success or provide troubleshooting tips

You should receive an email at `onblackberryhill@gmail.com` with:
- Subject: "🏠 Pumphouse Email Test"
- Embedded chart image
- Link to your dashboard

**Check your spam folder** if you don't see it!

### 6. Restart the Monitor

After configuration, restart your monitoring service to enable email alerts:

```bash
sudo systemctl restart pumphouse-monitor
```

## Email Alert Types

You'll receive emails for these events:

1. **Tank Level Alerts**
   - Tank dropping below threshold levels (1000, 750, 500, 250 gallons)
   - Tank filling above threshold levels (500, 750, 1000, 1200, 1400 gallons)

2. **Tank Full Confirmed**
   - Float sensor confirms tank is full

3. **Override Auto-Shutoff**
   - System automatically turns off override when tank reaches threshold

4. **Well Recovery**
   - Tank gained 50+ gallons in last 24 hours

5. **Well May Be Dry**
   - No significant refill in 4+ days

## Troubleshooting

### Test Command Shows Errors

Run the test command with debug output:
```bash
python -m monitor.check --test-email
```

Common issues:

1. **Authentication Failed**
   - Make sure you're using an App Password (Gmail), not your regular password
   - Verify EMAIL_SMTP_USER matches your email address

2. **Connection Timeout**
   - Check internet connection
   - Verify SMTP server and port are correct
   - Check if firewall is blocking port 587

3. **Email Not Received**
   - Check spam/junk folder
   - Verify EMAIL_TO is correct
   - Some email providers may block emails from new senders

### Still Not Working?

Check the monitor logs for errors:
```bash
sudo journalctl -u pumphouse-monitor -f
```

Or run the monitor in debug mode:
```bash
cd /home/pi/src/pumphouse
python -m monitor --debug
```

## Disable Email Notifications

To temporarily disable email notifications without removing your configuration:

Edit `/home/pi/src/pumphouse/monitor/config.py`:
```python
ENABLE_EMAIL_NOTIFICATIONS = False
```

Then restart:
```bash
sudo systemctl restart pumphouse-monitor
```

## Security Notes

- **Never commit your App Password to version control**
- Store EMAIL_SMTP_PASSWORD securely
- Consider using environment variables for sensitive credentials
- App Passwords can be revoked at any time from your Google Account settings

## Configuration File Location

The configuration is stored in:
```
/home/pi/src/pumphouse/monitor/config.py
```

You can also create a config file at:
```
~/.config/pumphouse/monitor.conf
```

This allows you to override settings without modifying the main config.py file.
