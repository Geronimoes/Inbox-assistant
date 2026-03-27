#!/bin/bash
# Install cron jobs for the Inbox Briefing Assistant.
# Run this once after setup is complete.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
PYTHON="$SCRIPT_DIR/env/bin/python"

mkdir -p "$LOG_DIR"

echo "Installing cron jobs for Inbox Briefing Assistant..."
echo "Project directory: $SCRIPT_DIR"
echo "Python:           $PYTHON"

# Build cron entries
MORNING_BRIEFING="30 6 * * * cd $SCRIPT_DIR && $PYTHON src/fetch_and_triage.py >> $LOG_DIR/briefing.log 2>&1"
URGENT_CHECK="0 8-20/2 * * * cd $SCRIPT_DIR && $PYTHON src/urgent_check.py >> $LOG_DIR/urgent.log 2>&1"
REGEN_STYLE="0 2 * * 0 cd $SCRIPT_DIR && $PYTHON src/fetch_and_triage.py --regenerate-style >> $LOG_DIR/style.log 2>&1"
DASHBOARD="0 3 * * 0 cd $SCRIPT_DIR && $PYTHON src/dashboard.py >> $LOG_DIR/dashboard.log 2>&1"

# Check if already installed
EXISTING=$(crontab -l 2>/dev/null || true)

if echo "$EXISTING" | grep -q "fetch_and_triage.py"; then
    echo "⚠ Cron jobs already installed. To reinstall, run:"
    echo "  crontab -e  (and remove existing inbox-assistant lines)"
    exit 0
fi

# Install
(
  echo "$EXISTING"
  echo ""
  echo "# Inbox Briefing Assistant"
  echo "$MORNING_BRIEFING"
  echo "$URGENT_CHECK"
  echo "$REGEN_STYLE"
  echo "$DASHBOARD"
) | crontab -

echo "✓ Cron jobs installed:"
echo "  • Morning briefing:    6:30 AM daily"
echo "  • Urgent checks:       every 2 hours, 8 AM–8 PM"
echo "  • Style regeneration:  Sunday 2:00 AM"
echo "  • Dashboard refresh:   Sunday 3:00 AM"
echo ""
echo "To adjust times, run: crontab -e"
echo "To view logs:         tail -f $LOG_DIR/briefing.log"
