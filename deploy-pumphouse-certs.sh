#!/bin/bash

# --- Configuration ---
# Your pumphouse project directory
PROJECT_DIR="/home/pi/src/pumphouse"
# The user that runs your web application
APP_USER="pi"
# Your domain name
DOMAIN="REDACTED-HOST"

# --- Script ---
# Stop on any error
set -e

# The directory where Certbot stores the live certificates
LE_LIVE_DIR="/etc/letsencrypt/live/$DOMAIN"
# The directory where we will copy the certs for the app
APP_CERTS_DIR="$PROJECT_DIR/certs"

echo "Deploying new certificate for $DOMAIN to $APP_CERTS_DIR"

# Copy the certificate files
cp "$LE_LIVE_DIR/fullchain.pem" "$APP_CERTS_DIR/fullchain.pem"
cp "$LE_LIVE_DIR/privkey.pem" "$APP_CERTS_DIR/privkey.pem"

# Set ownership to the application user
chown -R "$APP_USER:$APP_USER" "$APP_CERTS_DIR"

# Set secure permissions
# -rw------- (600) for the private key
# -rw-r--r-- (644) for the full chain
chmod 600 "$APP_CERTS_DIR/privkey.pem"
chmod 644 "$APP_CERTS_DIR/fullchain.pem"

echo "Permissions set successfully."

# Restart the web application to load the new certificate
# This is the most complex part without a systemd service.
# This command finds the running web.py process and kills it.
# You would then need to restart it, ideally with a systemd service.
echo "Restarting pumphouse web server..."
pkill -f "python -m monitor.web" || true # Use '|| true' to not fail if it's not running

# NOTE: This hook only stops the server. You need a separate mechanism
# (like a systemd service) to ensure it restarts automatically.

echo "Deployment hook finished."
