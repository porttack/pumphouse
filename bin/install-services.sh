#!/bin/bash
# Install pumphouse systemd services

set -e

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
SERVICES_DIR="$PROJECT_DIR/terraform/services"

echo "Installing Pumphouse systemd services..."

# Copy service files to systemd directory
sudo cp "$SERVICES_DIR/pumphouse-monitor.service" /etc/systemd/system/
sudo cp "$SERVICES_DIR/pumphouse-web.service" /etc/systemd/system/
sudo cp "$SERVICES_DIR/pumphouse-audio.service" /etc/systemd/system/

# Reload systemd to recognize new services
sudo systemctl daemon-reload

# ── Audio: create ~/.asoundrc if dosatron_in is not already defined ───────────
ASOUNDRC="$HOME/.asoundrc"
if grep -q "dosatron_in" "$ASOUNDRC" 2>/dev/null; then
    echo "~/.asoundrc already defines dosatron_in — skipping."
else
    # Detect the USB audio card name (falls back to "Device" if not found)
    CARD_NAME=$(arecord -l 2>/dev/null | grep -i "USB" | sed 's/.*\[\(.*\)\].*/\1/' | head -1)
    CARD_NAME="${CARD_NAME:-Device}"

    cat >> "$ASOUNDRC" << EOF

pcm.dosatron_in {
    type dsnoop
    ipc_key 1024
    slave {
        pcm "hw:CARD=${CARD_NAME},DEV=0"
        channels 1
        rate 44100
        format S16_LE
    }
}
EOF
    echo "Added dosatron_in (hw:CARD=${CARD_NAME},DEV=0) to ~/.asoundrc"

    # Quick sanity check — non-fatal if the device isn't plugged in yet
    if arecord -D dosatron_in -d 1 -r 44100 -c 1 -f S16_LE /dev/null 2>/dev/null; then
        echo "dosatron_in device test: OK"
    else
        echo "WARNING: dosatron_in device test failed — USB mic may not be connected."
        echo "         Plug it in and verify with: arecord -D dosatron_in -d 1 -r 44100 -c 1 -f S16_LE /dev/null"
    fi
fi

echo ""
echo "Services installed successfully!"
echo ""
echo "To enable and start the services:"
echo "  sudo systemctl enable pumphouse-monitor"
echo "  sudo systemctl enable pumphouse-web"
echo "  sudo systemctl enable pumphouse-audio"
echo "  sudo systemctl start pumphouse-monitor"
echo "  sudo systemctl start pumphouse-web"
echo "  sudo systemctl start pumphouse-audio"
echo ""
echo "Or enable and start all at once:"
echo "  sudo systemctl enable --now pumphouse-monitor pumphouse-web pumphouse-audio"
echo ""
echo "To view logs:"
echo "  sudo journalctl -u pumphouse-monitor -f"
echo "  sudo journalctl -u pumphouse-web -f"
echo "  sudo journalctl -u pumphouse-audio -f"
echo ""
echo "To check status:"
echo "  sudo systemctl status pumphouse-monitor"
echo "  sudo systemctl status pumphouse-web"
echo "  sudo systemctl status pumphouse-audio"
echo ""
echo "IMPORTANT: Edit pumphouse-web.service to set your web credentials:"
echo "  sudo nano /etc/systemd/system/pumphouse-web.service"
echo "  (Change PUMPHOUSE_USER and PUMPHOUSE_PASS)"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl restart pumphouse-web"
