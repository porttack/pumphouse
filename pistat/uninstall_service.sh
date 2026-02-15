#!/bin/bash
# Uninstallation script for e-paper display daemon

set -e

SERVICE_NAME="epaper-display.service"
SYSTEMD_DIR="/etc/systemd/system"

echo "========================================="
echo "E-Paper Display Service Uninstaller"
echo "========================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Error: This script must be run as root (use sudo)"
    exit 1
fi

# Stop service if running
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "Stopping service..."
    systemctl stop "$SERVICE_NAME"
fi

# Disable service
if systemctl is-enabled --quiet "$SERVICE_NAME"; then
    echo "Disabling service..."
    systemctl disable "$SERVICE_NAME"
fi

# Remove service file
if [ -f "$SYSTEMD_DIR/$SERVICE_NAME" ]; then
    echo "Removing service file..."
    rm "$SYSTEMD_DIR/$SERVICE_NAME"
fi

# Reload systemd
echo "Reloading systemd daemon..."
systemctl daemon-reload

echo ""
echo "========================================="
echo "Uninstallation Complete!"
echo "========================================="
echo ""
echo "Note: Log files and daemon script remain in place."
echo "Remove manually if needed."
echo ""

