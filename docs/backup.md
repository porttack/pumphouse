# Pi Backup & Restore

A full SD card image backup is impractical on this Pi: the card is hard to physically remove, the 58GB card takes hours to image over the network, and most of that space is empty. Instead, we capture everything needed to rebuild on a new card in a few minutes.

**What this covers:**
- Installed apt packages (1700+)
- Python pip packages from all venvs
- Crontab
- Systemd service files
- Non-secret config overrides

**What is NOT backed up here (handled separately):**
- Source code → already in git (`git@github.com:porttack/pumphouse.git`)
- Secrets → `~/.config/pumphouse/secrets.conf` — back up manually and store somewhere safe (password manager, encrypted USB)
- Timelapse videos → `~/timelapses/` — large, back up separately if needed
- SSL certs → auto-renewed by certbot; if needed, back up `/etc/letsencrypt/` via `sudo tar`

---

## Creating a Backup

```bash
cd ~/src/pumphouse
./bin/backup.sh
```

This creates a tarball at `/tmp/pi-backup-YYYYMMDD-HHMMSS.tar.gz` (~1-2 MB).

Pull it to your Mac:

```bash
scp pi@192.168.1.144:/tmp/pi-backup-<timestamp>.tar.gz ~/pi-backups/
```

Run this monthly or before any significant system changes.

---

## Restoring on a New Pi

### 1. Flash a fresh OS

Download **Raspberry Pi OS (64-bit, Lite or Desktop)** from [raspberrypi.com/software](https://www.raspberrypi.com/software/) and flash it with Raspberry Pi Imager. In the Imager settings, pre-configure:
- Username: `pi`
- WiFi credentials
- Enable SSH

### 2. Initial system setup

Follow [docs/pi-setup.md](pi-setup.md) sections 1–4 (OS setup, I2C, camera driver, WiringPi).

### 3. Restore apt packages

Copy the backup tarball to the new Pi and extract:

```bash
scp ~/pi-backups/pi-backup-<timestamp>.tar.gz pi@<NEW_PI_IP>:/tmp/
ssh pi@<NEW_PI_IP>
tar -xzf /tmp/pi-backup-<timestamp>.tar.gz -C /tmp/
cd /tmp/pi-backup-*/
```

Restore packages:

```bash
sudo dpkg --set-selections < apt-packages.txt
sudo apt-get dselect-upgrade -y
```

This takes a while. Many packages will already be present from the base image.

### 4. Clone the repo

```bash
mkdir -p ~/src
git clone git@github.com:porttack/pumphouse.git ~/src/pumphouse
```

### 5. Rebuild Python venvs

```bash
cd ~/src/pumphouse
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

To verify the venv matches the backup exactly:

```bash
pip install -r /tmp/pi-backup-*/pip-pumphouse.txt
```

### 6. Restore secrets

Copy `secrets.conf` from your secure backup location:

```bash
mkdir -p ~/.config/pumphouse
cp /path/to/secrets.conf ~/.config/pumphouse/secrets.conf
chmod 600 ~/.config/pumphouse/secrets.conf
```

If you also had `monitor.conf` overrides, the backup contains them:

```bash
cp /tmp/pi-backup-*/config/monitor.conf ~/.config/pumphouse/ 2>/dev/null || true
```

### 7. Install systemd services

The service files in the repo match what's installed. Install them:

```bash
cd ~/src/pumphouse
sudo cp terraform/services/pumphouse-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pumphouse-monitor pumphouse-web pumphouse-timelapse
```

### 8. Restore crontab

```bash
crontab /tmp/pi-backup-*/crontab-pi.txt
```

Expected cron jobs:

```cron
*/5 * * * * /home/pi/src/pumphouse/system_health_monitor.sh
30 5 * * * /home/pi/src/pumphouse/ecobee/fetch_ecobee_temp_cron.sh
0 21 * * * /home/pi/src/pumphouse/ecobee/fetch_ecobee_temp_cron.sh
0 2 * * * cd /home/pi/src/pumphouse && /home/pi/src/pumphouse/venv/bin/python3 log_daily_gph.py >> /home/pi/src/pumphouse/gph_log.txt 2>&1
0 5,12,16,20,23 * * * /home/pi/src/pumphouse/bin/update_reservations.sh >> /home/pi/src/pumphouse/reservation_updates.log 2>&1
*/8 * * * * /home/pi/src/pumphouse/warm_cache.sh >> /home/pi/src/pumphouse/warm_cache.log 2>&1
0 3 1 * * /home/pi/src/pumphouse/venv/bin/python3 rotate_snapshots.py >> /home/pi/src/pumphouse/logs/rotate_snapshots.log 2>&1
0 */4 * * * cd /home/pi/src/pumphouse && /home/pi/src/pumphouse/venv/bin/python3 build_daily.py --include-today >> /home/pi/src/pumphouse/logs/build_daily.log 2>&1
0 4 * * * cd /home/pi/src/pumphouse && /home/pi/src/pumphouse/venv/bin/python3 build_pumpoff.py >> /home/pi/src/pumphouse/logs/build_pumpoff.log 2>&1
```

### 9. SSL certificate

See [docs/pi-setup.md](pi-setup.md) section 8. Restore from certbot or generate a new cert.

### 10. Cloudflare tunnel

See [docs/cloudflare.md](cloudflare.md). Re-run `sudo cloudflared service install <token>` with the token from the Cloudflare dashboard.

### 11. Verify

```bash
sudo systemctl status pumphouse-monitor pumphouse-web pumphouse-timelapse cloudflared
curl -sk https://localhost:6443/ | head -5
sudo i2cdetect -y 1
gpio readall
```
