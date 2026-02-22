# Pi Setup Guide

Complete setup guide for a fresh Raspberry Pi 4 running the Pumphouse Monitor system. Follow these steps in order when setting up from scratch.

---

## 1. Initial OS & System Setup

```bash
# Update packages
sudo apt update && sudo apt upgrade -y

# Install essentials
sudo apt install -y vim git curl
```

### Configure WiFi Networks

```bash

# Rename the default "preconfigured" connection to something meaningful
sudo nmcli connection modify "preconfigured" connection.id "OnBlackberryHill"
```

### Enable Linger (User Services at Boot)

```bash
# Allow user systemd services to start at boot without interactive login
loginctl enable-linger
```

### Remote Access

```bash
# Enable Raspberry Pi Connect (remote browser-based access)
rpi-connect on
rpi-connect signin

# Set a strong password
passwd

# Enable VNC for remote desktop (if needed)
sudo raspi-config   # Interface Options → VNC → Enable
```

---

## 2. Enable I2C (for AHT20 Temp/Humidity Sensor)

```bash
sudo raspi-config   # Interface Options → I2C → Enable
sudo reboot

# Verify I2C is active — should show device at 0x38
sudo apt install -y i2c-tools
sudo i2cdetect -y 1
```

---

## 3. Camera Driver (ArduCam IMX708)

Only needed if using an ArduCam IMX708 camera module directly attached to the Pi (not the Amcrest IP camera).

```bash
wget -O install_pivariety_pkgs.sh \
    https://github.com/ArduCAM/Arducam-Pivariety-V4L2-Driver/releases/download/install_script/install_pivariety_pkgs.sh
chmod +x install_pivariety_pkgs.sh
./install_pivariety_pkgs.sh -p imx708_kernel_driver
sudo reboot

# Edit camera config if needed
sudo vim /boot/firmware/config.txt
```

---

## 4. WiringPi (for Relay Control)

WiringPi is required for the `gpio` command used by `control.sh` and the relay control code. Install from source because the package is no longer in Debian repos.

```bash
git clone https://github.com/WiringPi/WiringPi
cd WiringPi/
./build
cd ~

# Verify
gpio -v
gpio readall
```

> **Why WiringPi?** Using the `gpio` CLI command instead of Python RPi.GPIO for relay control avoids multi-process GPIO conflicts when multiple Python processes need GPIO access simultaneously.

---

## 5. Required System Packages

```bash
sudo apt install -y \
    python3-pip \
    python3-venv \
    ffmpeg \               # Required for timelapse assembly
    i2c-tools \            # I2C sensor diagnostics
    chromium-browser \     # Ecobee web scraping (Selenium)
    chromium-chromedriver  # Selenium WebDriver for Ecobee
```

---

## 6. Python Virtual Environment

```bash
cd ~/src/pumphouse
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## 7. Configuration Files

### Create the config directory and secrets file

```bash
mkdir -p ~/.config/pumphouse
cp ~/src/pumphouse/secrets.conf.template ~/.config/pumphouse/secrets.conf
chmod 600 ~/.config/pumphouse/secrets.conf
```

### Edit secrets (fill in all values)

```bash
nano ~/.config/pumphouse/secrets.conf
```

Key values to set:

```ini
# Email
EMAIL_SMTP_PASSWORD=your-gmail-app-password

# Camera
CAMERA_USER=guest
CAMERA_PASS=your-camera-password

# Remote relay control tokens (generate with: python3 -c "import secrets; print(secrets.token_urlsafe(32))")
SECRET_OVERRIDE_ON_TOKEN=
SECRET_OVERRIDE_OFF_TOKEN=
SECRET_BYPASS_ON_TOKEN=
SECRET_BYPASS_OFF_TOKEN=
SECRET_PURGE_TOKEN=

# ntfy push notifications
NTFY_TOPIC=pumphouse-yourname-randomnumber

# Tank sensor URL
TANK_URL=https://www.mypt.in/s/your-tank-url

# Web server hostname (for email links)
PUMPHOUSE_HOST=your-hostname.tplinkdns.com
PUMPHOUSE_PORT=6443

# TrackHS reservation scraper
TRACKHS_USERNAME=your-email@example.com
TRACKHS_PASSWORD=your-password

# Cloudflare (for ratings KV)
CLOUDFLARE_ACCOUNT_ID=
CLOUDFLARE_KV_NAMESPACE_ID=
CLOUDFLARE_KV_API_TOKEN=
RATINGS_BACKEND=cloudflare_kv
```

### Optional: override monitor settings

```bash
nano ~/.config/pumphouse/monitor.conf
```

```ini
# Example overrides (all optional — defaults are in monitor/config.py)
TANK_HEIGHT_INCHES=58
TANK_CAPACITY_GALLONS=1400
ENABLE_NOTIFICATIONS=True
```

See [docs/installation.md](installation.md) for the full config reference.

---

## 8. SSL Certificate

The web server requires a TLS certificate. Two options:

### Option A: Self-signed (quick, browser will warn)

```bash
cd ~/src/pumphouse
./generate_cert.sh
```

### Option B: Let's Encrypt (recommended for the tplinkdns hostname)

```bash
sudo apt install -y certbot
sudo certbot certonly --standalone -d your-hostname.tplinkdns.com
# Cert lands in /etc/letsencrypt/live/your-hostname.tplinkdns.com/
# Copy or symlink to pumphouse/cert.pem and pumphouse/key.pem
```

> **Note**: Check `pumphouse/certs/` — a Let's Encrypt cert may already be present from a previous setup.

---

## 9. Persistent Journal Logging

Enable persistent journaling so logs survive reboots (critical for diagnosing unexpected restarts):

```bash
sudo mkdir -p /var/log/journal
sudo systemd-tmpfiles --create --prefix /var/log/journal
sudo journalctl --flush
```

Verify with `journalctl --list-boots` — should show history beyond just boot 0.

---

## 10. Systemd Services

```bash
# Install all service files
cd ~/src/pumphouse
./install-services.sh

# Or install manually:
sudo cp pumphouse-*.service /etc/systemd/system/
sudo systemctl daemon-reload

# Enable and start
sudo systemctl enable --now pumphouse-monitor pumphouse-web pumphouse-timelapse
```

### Service management cheat sheet

```bash
# Status
sudo systemctl status pumphouse-monitor pumphouse-web pumphouse-timelapse

# Logs (live)
sudo journalctl -u pumphouse-monitor -f
sudo journalctl -u pumphouse-web -f
sudo journalctl -u pumphouse-timelapse -f

# Restart after code changes
sudo systemctl restart pumphouse-monitor pumphouse-web

# If you edit a .service file:
sudo cp pumphouse-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart pumphouse-monitor pumphouse-web pumphouse-timelapse
```

---

## 11. Cloudflare Tunnel

For public access to the timelapse viewer at `onblackberryhill.com`:

```bash
# Add Cloudflare package repo
curl -L https://pkg.cloudflare.com/cloudflare-main.gpg \
    | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null

echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
    https://pkg.cloudflare.com/cloudflared any main" \
    | sudo tee /etc/apt/sources.list.d/cloudflared.list

sudo apt update && sudo apt install -y cloudflared

# Install tunnel as a system service (token from Cloudflare dashboard)
sudo cloudflared service install <token-from-dashboard>
sudo systemctl enable --now cloudflared
```

See [docs/cloudflare.md](cloudflare.md) for the complete step-by-step Cloudflare setup.

---

## 12. Cron Jobs

```bash
crontab -e
```

Add these entries:

```cron
# System health monitoring every 5 minutes
*/5 * * * * /home/pi/src/pumphouse/system_health_monitor.sh

# Reservation scraping at 8 AM and 8 PM daily
0 8  * * * /home/pi/src/pumphouse/update_reservations.sh >> /home/pi/src/pumphouse/reservation_updates.log 2>&1
0 20 * * * /home/pi/src/pumphouse/update_reservations.sh >> /home/pi/src/pumphouse/reservation_updates.log 2>&1

# Daily GPH metric logging at 2 AM
0 2 * * * cd /home/pi/src/pumphouse && /home/pi/src/pumphouse/venv/bin/python3 log_daily_gph.py >> /home/pi/src/pumphouse/gph_log.txt 2>&1
```

---

## 13. Verify Everything

```bash
# Service status
sudo systemctl status pumphouse-monitor pumphouse-web pumphouse-timelapse cloudflared

# GPIO relay state
gpio readall

# I2C sensor (should show 0x38 for AHT20)
sudo i2cdetect -y 1

# Test web server (should return dashboard HTML)
curl -sk https://localhost:6443/ | head -5

# Test push notification
cd ~/src/pumphouse && source venv/bin/activate
python -m monitor.check --test-notification

# Test email
python -m monitor.check --test-email

# Check recent logs
tail -20 ~/src/pumphouse/system_health.log
sudo journalctl -u pumphouse-monitor -n 30
```

---

## Node.js + Wrangler (Optional — for Cloudflare Worker CLI deploys)

Only needed if you want to deploy the Cloudflare ratings Worker from the Pi via CLI instead of pasting in the dashboard:

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
sudo npm install -g wrangler

# Deploy worker (reads credentials from secrets.conf)
cd ~/src/pumphouse
./cloudflare/deploy.sh
```

---

## Troubleshooting

### GPIO busy error

```bash
pkill -f "python -m monitor"
python3 -c "import RPi.GPIO as GPIO; GPIO.cleanup()"
```

### I2C sensor not detected

```bash
ls /dev/i2c*
sudo i2cdetect -y 1
# Check wiring: VCC→3.3V, GND→GND, SDA→BCM2 (pin 3), SCL→BCM3 (pin 5)
```

### Service won't start

```bash
sudo journalctl -u pumphouse-monitor -n 50
# Look for Python import errors or missing secrets
```

### Web server not responding

```bash
sudo systemctl status pumphouse-web
sudo journalctl -u pumphouse-web -n 20
# Check cert.pem and key.pem exist in ~/src/pumphouse/
```
