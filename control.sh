#!/bin/bash
# Relay control script using gpio command (avoids Python GPIO conflicts)
#
# Relay pin assignments (BCM numbering):
#   BCM 26 - Bypass valve (emergency bypass)
#   BCM 19 - Supply override (primary inlet override)
#   BCM 13 - Purge valve (spindown filter purge)
#   BCM 6  - Reserved
#
# Relays are ACTIVE LOW: 0=ON, 1=OFF

set -e

BYPASS_PIN=26
OVERRIDE_PIN=19
PURGE_PIN=13
RESERVED_PIN=6

PURGE_DURATION=10  # Default purge duration in seconds

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

usage() {
    echo "Usage: $0 <command> [options]"
    echo ""
    echo "Commands:"
    echo "  purge [DURATION]    - Trigger spindown filter purge (default: ${PURGE_DURATION}s)"
    echo "  bypass on|off       - Turn bypass valve ON or OFF"
    echo "  override on|off     - Turn supply override ON or OFF"
    echo "  status              - Show current relay states"
    echo ""
    echo "Examples:"
    echo "  $0 purge            # Purge for ${PURGE_DURATION} seconds"
    echo "  $0 purge 15         # Purge for 15 seconds"
    echo "  $0 bypass on        # Enable bypass valve"
    echo "  $0 bypass off       # Disable bypass valve"
    echo "  $0 override on      # Enable supply override"
    echo "  $0 status           # Show all relay states"
    exit 1
}

# Setup a pin as output
setup_pin() {
    local pin=$1
    gpio -g mode "$pin" out
}

# Set pin to ON (LOW = 0 for active-low relays)
pin_on() {
    local pin=$1
    gpio -g write "$pin" 0
}

# Set pin to OFF (HIGH = 1 for active-low relays)
pin_off() {
    local pin=$1
    gpio -g write "$pin" 1
}

# Read pin state
pin_read() {
    local pin=$1
    gpio -g read "$pin"
}

# Get relay state (0=ON, 1=OFF for active-low)
relay_state() {
    local pin=$1
    local state=$(pin_read "$pin")
    if [ "$state" = "0" ]; then
        echo "ON"
    else
        echo "OFF"
    fi
}

# Purge command
do_purge() {
    local duration=${1:-$PURGE_DURATION}

    echo -e "${YELLOW}Triggering spindown filter purge for ${duration} seconds...${NC}"

    setup_pin "$PURGE_PIN"
    pin_on "$PURGE_PIN"
    echo -e "${GREEN}Purge valve OPEN${NC}"

    sleep "$duration"

    pin_off "$PURGE_PIN"
    echo -e "${GREEN}Purge valve CLOSED${NC}"
    echo "Purge complete."
}

# Bypass command
do_bypass() {
    local action=$1

    if [ -z "$action" ]; then
        echo "Error: Must specify 'on' or 'off'"
        usage
    fi

    setup_pin "$BYPASS_PIN"

    case "$action" in
        on)
            pin_on "$BYPASS_PIN"
            echo -e "${RED}⚠ Bypass valve: ON${NC}"
            ;;
        off)
            pin_off "$BYPASS_PIN"
            echo -e "${GREEN}Bypass valve: OFF${NC}"
            ;;
        *)
            echo "Error: Invalid action '$action'. Use 'on' or 'off'"
            usage
            ;;
    esac
}

# Override command
do_override() {
    local action=$1

    if [ -z "$action" ]; then
        echo "Error: Must specify 'on' or 'off'"
        usage
    fi

    setup_pin "$OVERRIDE_PIN"

    case "$action" in
        on)
            pin_on "$OVERRIDE_PIN"
            echo -e "${RED}⚠ Supply override: ON${NC}"
            ;;
        off)
            pin_off "$OVERRIDE_PIN"
            echo -e "${GREEN}Supply override: OFF${NC}"
            ;;
        *)
            echo "Error: Invalid action '$action'. Use 'on' or 'off'"
            usage
            ;;
    esac
}

# Status command
do_status() {
    echo "============================================"
    echo "RELAY STATUS"
    echo "============================================"

    # Setup pins as output to read them
    setup_pin "$BYPASS_PIN"
    setup_pin "$OVERRIDE_PIN"
    setup_pin "$PURGE_PIN"
    setup_pin "$RESERVED_PIN"

    local bypass=$(relay_state "$BYPASS_PIN")
    local override=$(relay_state "$OVERRIDE_PIN")
    local purge=$(relay_state "$PURGE_PIN")
    local reserved=$(relay_state "$RESERVED_PIN")

    echo "Bypass (BCM $BYPASS_PIN):    $bypass"
    echo "Override (BCM $OVERRIDE_PIN):  $override"
    echo "Purge (BCM $PURGE_PIN):     $purge"
    echo "Reserved (BCM $RESERVED_PIN):   $reserved"

    if [ "$bypass" = "ON" ]; then
        echo -e "${RED}⚠ WARNING: Bypass valve is ON${NC}"
    fi
    if [ "$override" = "ON" ]; then
        echo -e "${RED}⚠ WARNING: Supply override is ON${NC}"
    fi
}

# Main
if [ $# -lt 1 ]; then
    usage
fi

COMMAND=$1
shift

case "$COMMAND" in
    purge)
        do_purge "$@"
        ;;
    bypass)
        do_bypass "$@"
        ;;
    override)
        do_override "$@"
        ;;
    status)
        do_status
        ;;
    *)
        echo "Error: Unknown command '$COMMAND'"
        usage
        ;;
esac
