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
DASHBOARD="45 6 * * * cd $SCRIPT_DIR && $PYTHON src/dashboard.py >> $LOG_DIR/dashboard.log 2>&1"
PROJECT_FETCH="30 8-20/1 * * * cd $SCRIPT_DIR && $PYTHON src/project_fetch.py >> $LOG_DIR/project-fetch.log 2>&1"
PROJECT_DISCOVER="0 4 * * 0 cd $SCRIPT_DIR && $PYTHON src/project_discover.py >> $LOG_DIR/project-discover.log 2>&1"
AFTERNOON_1="0 13 * * 1-5 cd $SCRIPT_DIR && $PYTHON src/fetch_and_triage.py --mini >> $LOG_DIR/briefing.log 2>&1"
AFTERNOON_2="0 17 * * 1-5 cd $SCRIPT_DIR && $PYTHON src/fetch_and_triage.py --mini >> $LOG_DIR/briefing.log 2>&1"
DRAFT_ON_DEMAND="*/2 8-20 * * 1-5 cd $SCRIPT_DIR && $PYTHON src/draft_on_demand.py >> $LOG_DIR/draft-on-demand.log 2>&1"

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
  echo "$PROJECT_FETCH"
  echo "$PROJECT_DISCOVER"
  echo "$AFTERNOON_1"
  echo "$AFTERNOON_2"
  echo "$DRAFT_ON_DEMAND"
) | crontab -

echo "✓ Cron jobs installed:"
echo "  • Morning briefing:    6:30 AM daily"
echo "  • Urgent checks:       every 2 hours, 8 AM–8 PM"
echo "  • Style regeneration:  Sunday 2:00 AM"
echo "  • Dashboard refresh:   6:45 AM daily"
echo "  • Project email fetch: every hour, 8:30 AM–8:30 PM"
echo "  • Project discovery:   Sunday 4:00 AM"
echo "  • Afternoon updates:  1:00 PM + 5:00 PM, Mon–Fri"
echo "  • Draft on demand:   every 2 min, 8 AM–8 PM, Mon–Fri"
echo ""
echo "To adjust times, run: crontab -e"
echo "To view logs:         tail -f $LOG_DIR/briefing.log"
