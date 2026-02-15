# E-Paper Display Daemon

Monitors pump house status and updates e-ink display every 5 minutes using partial refresh (no flashing).

## Files

- `epaper_daemon.py` - Main daemon service
- `epaper-display.service` - Systemd service definition
- `install_service.sh` - Installation script
- `uninstall_service.sh` - Removal script

## Installation
```bash
cd /home/pi/src/pumphouse/pistat
sudo ./install_service.sh
```

The installer will:
- Set proper permissions on daemon script
- Copy service file to /etc/systemd/system/
- Enable service to start on boot
- Start the service immediately

## Uninstallation
```bash
cd /home/pi/src/pumphouse/pistat
sudo ./uninstall_service.sh
```

## Management
```bash
# Check service status
sudo systemctl status epaper-display.service

# View live logs
sudo journalctl -u epaper-display.service -f

# View log file
tail -f epaper_daemon.log

# Restart service
sudo systemctl restart epaper-display.service

# Stop service
sudo systemctl stop epaper-display.service

# Start service
sudo systemctl start epaper-display.service
```

## Configuration

Edit `epaper_daemon.py` to change:
- `UPDATE_INTERVAL` - How often to update (default: 300 seconds / 5 minutes)
- `IMAGE_URL` - Source of display image
- `LOG_FILE` - Location of log file

After changing configuration:
```bash
sudo systemctl restart epaper-display.service
```

## How It Works

The daemon uses Waveshare's partial refresh feature to update the display without flashing:

1. **On startup**: Full refresh to clear display (will flash once)
2. **First update**: Sets base image and displays content
3. **Subsequent updates**: Uses `displayPartial()` for smooth updates without flashing
4. **Every 5 minutes**: Fetches new image from server and updates display

The partial refresh only changes pixels that are different, eliminating the distracting full-screen flash.

## Troubleshooting

**Service won't start:**
```bash
# Check for errors
sudo journalctl -u epaper-display.service -n 50

# Verify daemon script runs manually
cd /home/pi/src/pumphouse/pistat
source venv/bin/activate
python epaper_daemon.py
```

**Display not updating:**
- Check network connectivity to image server
- Verify cached image exists: `ls -lh last_display.bmp`
- Check daemon log: `tail -f epaper_daemon.log`

**Display flashing on every update:**
- This should only happen on the first boot after starting the service
- If it continues flashing, check that `displayPartial()` is being called (check logs)
- May indicate the display lost its base image - restart the service

**Permission errors:**
- Ensure pi user owns files: `sudo chown -R pi:pi /home/pi/src/pumphouse/pistat`
- Verify venv is accessible: `ls -la venv/bin/python`

**SSL/Certificate warnings:**
- The daemon disables SSL warnings for self-signed certificates
- This is normal for internal servers with self-signed certs
- Image is fetched over HTTPS but certificate validation is bypassed

## Display Ghosting

With partial refresh, you may eventually see ghosting (faint remnants of previous images). If this becomes noticeable:

1. Restart the service (performs full refresh on startup):
```bash
   sudo systemctl restart epaper-display.service
```

2. Or modify the daemon to periodically do full refreshes (e.g., once per hour)

## File Locations

- Service definition: `/etc/systemd/system/epaper-display.service`
- Daemon script: `/home/pi/src/pumphouse/pistat/epaper_daemon.py`
- Log file: `/home/pi/src/pumphouse/pistat/epaper_daemon.log`
- Cached image: `/home/pi/src/pumphouse/pistat/last_display.bmp`
- Virtual environment: `/home/pi/src/pumphouse/pistat/venv/`

## Version Control

Add to `.gitignore`:
```
venv/
*.log
last_display.bmp
*.pyc
__pycache__/
```

Files to commit:
- `epaper_daemon.py`
- `epaper-display.service`
- `install_service.sh`
- `uninstall_service.sh`
- `README.md`
- `lib/` (Waveshare libraries)
