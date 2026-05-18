#!/usr/bin/env bash
# Captures Pi system state needed to rebuild from scratch on a new card.
# Does NOT back up: secrets, timelapse videos, logs, or pip caches.
# Run this periodically and pull the tarball to a Mac for safekeeping.

set -euo pipefail

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="/tmp/pi-backup-${TIMESTAMP}"
TARBALL="/tmp/pi-backup-${TIMESTAMP}.tar.gz"

echo "==> Creating backup in ${BACKUP_DIR}"
mkdir -p "${BACKUP_DIR}"

# Installed apt packages
echo "    apt packages..."
dpkg --get-selections | grep -v deinstall > "${BACKUP_DIR}/apt-packages.txt"

# pip packages from all known venvs
echo "    pip packages..."
pip3 list --format=freeze > "${BACKUP_DIR}/pip-system.txt" 2>/dev/null || true

VENV_DIRS=(
    "/home/pi/src/pumphouse/venv"
    "/home/pi/wyze/venv"
)
for venv in "${VENV_DIRS[@]}"; do
    if [ -f "${venv}/bin/pip" ]; then
        name=$(basename "$(dirname "${venv}")")
        "${venv}/bin/pip" list --format=freeze > "${BACKUP_DIR}/pip-${name}.txt" 2>/dev/null || true
        echo "    pip (${name})..."
    fi
done

# Crontabs
echo "    crontabs..."
crontab -l > "${BACKUP_DIR}/crontab-pi.txt" 2>/dev/null || echo "(empty)" > "${BACKUP_DIR}/crontab-pi.txt"
sudo crontab -l > "${BACKUP_DIR}/crontab-root.txt" 2>/dev/null || echo "(empty)" > "${BACKUP_DIR}/crontab-root.txt"

# Installed systemd service files (the /etc copies, not the repo copies)
echo "    systemd services..."
mkdir -p "${BACKUP_DIR}/systemd"
sudo cp /etc/systemd/system/pumphouse-*.service "${BACKUP_DIR}/systemd/" 2>/dev/null || true
sudo cp /etc/systemd/system/cloudflared*.service "${BACKUP_DIR}/systemd/" 2>/dev/null || true
sudo chown pi:pi "${BACKUP_DIR}/systemd/"*.service 2>/dev/null || true

# Key config files (no secrets)
echo "    config files..."
mkdir -p "${BACKUP_DIR}/config"
[ -f ~/.config/pumphouse/monitor.conf ] && cp ~/.config/pumphouse/monitor.conf "${BACKUP_DIR}/config/" || true

# Network (WiFi connection names only — not passwords)
echo "    network connections..."
nmcli connection show 2>/dev/null | awk 'NR>1 {print $1, $3}' > "${BACKUP_DIR}/network-connections.txt" || true

# System info snapshot
echo "    system info..."
uname -a > "${BACKUP_DIR}/system-info.txt"
cat /etc/os-release >> "${BACKUP_DIR}/system-info.txt"
echo "" >> "${BACKUP_DIR}/system-info.txt"
df -h >> "${BACKUP_DIR}/system-info.txt"

# Bundle it up
echo "==> Creating tarball ${TARBALL}"
tar -czf "${TARBALL}" -C /tmp "$(basename "${BACKUP_DIR}")"
rm -rf "${BACKUP_DIR}"

SIZE=$(du -sh "${TARBALL}" | cut -f1)
echo ""
echo "Done. Backup: ${TARBALL} (${SIZE})"
echo ""
echo "Pull to Mac with:"
echo "  scp pi@192.168.1.144:${TARBALL} ~/pi-backups/"
echo ""
echo "Or stream directly:"
echo "  ssh pi@192.168.1.144 'cat ${TARBALL}' > ~/pi-backups/pi-backup-${TIMESTAMP}.tar.gz"
