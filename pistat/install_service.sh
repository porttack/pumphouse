#!/bin/bash
# Installation script for e-paper display daemon
# Sets up systemd service and ensures proper permissions

set -e  # Exit on any error

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SERVICE_NAME="epaper-display.service"
SERVICE_FILE="${SCRIPT_DIR}/${SERVICE_NAME}"
SYSTEMD_DIR="/etc/systemd/system"
DAEMON_SCRIPT="${SCRIPT_DIR}/epaper_daemon.py"

echo "========================================="
echo "E-Paper Display Service Installer"
echo "========================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Error: This script must be run as root (use sudo)"
    exit 1
fi

# Check if service file exists
if [ ! -f "$SERVICE_FILE" ]; then
    echo "Error: Service file not found: $SERVICE_FILE"
    exit 1
fi

# Check if daemon script exists
if [ ! -f "$DAEMON_SCRIPT" ]; then
    echo "Error: Daemon script not found: $DAEMON_SCRIPT"
    exit 1
fi

# Make daemon script executable
echo "Setting permissions on daemon script..."
chmod +x "$DAEMON_SCRIPT"
chown pi:pi "$DAEMON_SCRIPT"

# Stop service if it's already running
if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "Stopping existing service..."
    systemctl stop "$SERVICE_NAME"
fi

# Copy service file to systemd directory
echo "Installing service file to $SYSTEMD_DIR..."
cp "$SERVICE_FILE" "$SYSTEMD_DIR/$SERVICE_NAME"
chmod 644 "$SYSTEMD_DIR/$SERVICE_NAME"

# Reload systemd to recognize the new/updated service
echo "Reloading systemd daemon..."
systemctl daemon-reload

# Enable service to start on boot
echo "Enabling service to start on boot..."
systemctl enable "$SERVICE_NAME"

# Start the service
echo "Starting service..."
systemctl start "$SERVICE_NAME"

# Wait a moment for service to start
sleep 2

# Check service status
echo ""
echo "========================================="
echo "Service Status:"
echo "========================================="
systemctl status "$SERVICE_NAME" --no-pager || true

echo ""
echo "========================================="
echo "Installation Complete!"
echo "========================================="
echo ""
echo "Useful commands:"
echo "  View status:  sudo systemctl status $SERVICE_NAME"
echo "  View logs:    sudo journalctl -u $SERVICE_NAME -f"
echo "  Restart:      sudo systemctl restart $SERVICE_NAME"
echo "  Stop:         sudo systemctl stop $SERVICE_NAME"
echo "  Disable:      sudo systemctl disable $SERVICE_NAME"
echo ""
echo "Log file location: ${SCRIPT_DIR}/epaper_daemon.log"
echo ""

