#!/bin/bash
# System Health Monitor for Raspberry Pi
# Logs system vitals to help diagnose unexpected reboots

LOG_FILE="/home/pi/src/pumphouse/system_health.log"
DATE=$(date '+%Y-%m-%d %H:%M:%S')

# Get temperature
TEMP=$(vcgencmd measure_temp | cut -d'=' -f2)

# Get throttling status (undervoltage, thermal throttling, etc.)
THROTTLED=$(vcgencmd get_throttled)

# Get memory usage
MEM_INFO=$(free -h | grep Mem | awk '{print "Total:"$2" Used:"$3" Free:"$4" Available:"$7}')

# Get CPU load
LOAD=$(uptime | awk -F'load average:' '{print $2}')

# Get disk usage
DISK=$(df -h / | tail -1 | awk '{print "Used:"$3"/"$2" ("$5")"}')

# Get uptime
UPTIME=$(uptime -p)

# Log everything
echo "[$DATE] Temp=$TEMP | Throttled=$THROTTLED | Mem=$MEM_INFO | Load=$LOAD | Disk=$DISK | Uptime=$UPTIME" >> "$LOG_FILE"

# Check for critical conditions and log warnings
THROTTLED_VALUE=$(echo "$THROTTLED" | cut -d'=' -f2)
if [ "$THROTTLED_VALUE" != "0x0" ]; then
    echo "[$DATE] WARNING: Throttling detected! $THROTTLED" >> "$LOG_FILE"
    logger -t system_health "WARNING: Throttling detected! $THROTTLED"
fi
