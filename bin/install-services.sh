#!/bin/bash
# Install pumphouse systemd services

set -e

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
SERVICES_DIR="$PROJECT_DIR/terraform/services"

echo "Installing Pumphouse systemd services..."

# Copy service files to systemd directory
sudo cp "$SERVICES_DIR/pumphouse-monitor.service" /etc/systemd/system/
sudo cp "$SERVICES_DIR/pumphouse-web.service" /etc/systemd/system/

# Reload systemd to recognize new services
sudo systemctl daemon-reload

echo ""
echo "Services installed successfully!"
echo ""
echo "To enable and start the services:"
echo "  sudo systemctl enable pumphouse-monitor"
echo "  sudo systemctl enable pumphouse-web"
echo "  sudo systemctl start pumphouse-monitor"
echo "  sudo systemctl start pumphouse-web"
echo ""
echo "Or enable and start both at once:"
echo "  sudo systemctl enable --now pumphouse-monitor pumphouse-web"
echo ""
echo "To view logs:"
echo "  sudo journalctl -u pumphouse-monitor -f"
echo "  sudo journalctl -u pumphouse-web -f"
echo ""
echo "To check status:"
echo "  sudo systemctl status pumphouse-monitor"
echo "  sudo systemctl status pumphouse-web"
echo ""
echo "IMPORTANT: Edit pumphouse-web.service to set your web credentials:"
echo "  sudo nano /etc/systemd/system/pumphouse-web.service"
echo "  (Change PUMPHOUSE_USER and PUMPHOUSE_PASS)"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl restart pumphouse-web"
