#!/bin/bash
# Install cron jobs for the Inbox Briefing Assistant.
# Run this once after setup is complete.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"

mkdir -p "$LOG_DIR"

echo "Installing cron jobs for Inbox Briefing Assistant..."
echo "Project directory: $SCRIPT_DIR"

# Build cron entries
MORNING_BRIEFING="30 6 * * * cd $SCRIPT_DIR && /usr/bin/python3 src/fetch_and_triage.py >> $LOG_DIR/briefing.log 2>&1"
URGENT_CHECK="0 8-20/2 * * * cd $SCRIPT_DIR && /usr/bin/python3 src/urgent_check.py >> $LOG_DIR/urgent.log 2>&1"

# Check if already installed
EXISTING=$(crontab -l 2>/dev/null || true)

if echo "$EXISTING" | grep -q "fetch_and_triage.py"; then
    echo "⚠ Cron jobs already installed. To reinstall, run:"
    echo "  crontab -e  (and remove existing inbox-assistant lines)"
    exit 0
fi

# Install
(echo "$EXISTING"; echo ""; echo "# Inbox Briefing Assistant"; echo "$MORNING_BRIEFING"; echo "$URGENT_CHECK") | crontab -

echo "✓ Cron jobs installed:"
echo "  • Morning briefing: 6:30 AM daily"
echo "  • Urgent checks: every 2 hours, 8 AM - 8 PM"
echo ""
echo "To adjust times, run: crontab -e"
echo "To view logs: tail -f $LOG_DIR/briefing.log"
