#!/bin/bash
# Fetch Ecobee temperature and cache it
# This script is run via cron to periodically update the temperature cache

# Change to the pumphouse directory
cd /home/pi/src/pumphouse || exit 1

# Activate the virtual environment and run the fetch script
/home/pi/src/pumphouse/venv/bin/python3 /home/pi/src/pumphouse/fetch_ecobee_temp.py >> /home/pi/src/pumphouse/ecobee_temp_fetch.log 2>&1

# Log completion
echo "$(date '+%Y-%m-%d %H:%M:%S') - Ecobee temperature fetch completed" >> /home/pi/src/pumphouse/ecobee_temp_fetch.log
