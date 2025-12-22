#!/bin/bash
# Setup cron job to scrape reservations twice daily

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
UPDATE_SCRIPT="$SCRIPT_DIR/update_reservations.sh"
LOG_FILE="$SCRIPT_DIR/reservation_updates.log"

# Cron job will run at 8 AM and 8 PM daily
CRON_SCHEDULE_1="0 8 * * *"
CRON_SCHEDULE_2="0 20 * * *"

# Create cron command
CRON_CMD="$UPDATE_SCRIPT >> $LOG_FILE 2>&1"

# Check if cron jobs already exist
EXISTING_CRON=$(crontab -l 2>/dev/null | grep -F "update_reservations.sh" || true)

if [ -n "$EXISTING_CRON" ]; then
    echo "Cron job for reservation scraper already exists:"
    echo "$EXISTING_CRON"
    echo ""
    read -p "Replace existing cron job? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 1
    fi
    # Remove existing entries
    crontab -l 2>/dev/null | grep -v "update_reservations.sh" | crontab -
fi

# Add new cron jobs
(crontab -l 2>/dev/null; echo "$CRON_SCHEDULE_1 $CRON_CMD") | crontab -
(crontab -l 2>/dev/null; echo "$CRON_SCHEDULE_2 $CRON_CMD") | crontab -

echo "✓ Cron jobs added successfully!"
echo ""
echo "Reservations will be scraped twice daily at:"
echo "  - 8:00 AM"
echo "  - 8:00 PM"
echo ""
echo "Log file:   $LOG_FILE"
echo ""
echo "Current crontab:"
crontab -l | grep "update_reservations.sh"
echo ""
echo "To view logs: tail -f $LOG_FILE"
echo "To test now: $UPDATE_SCRIPT"
