#!/usr/bin/env python3
"""
Urgent email checker — runs every 2 hours via cron.

Checks for new emails since the last check, and if any are classified
as URGENT, sends an immediate alert email.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

from gmail_client import GmailClient
from llm_client import LLMClient
from classifier import EmailClassifier


def load_config() -> dict:
    config_path = Path(__file__).parent.parent / "config.yaml"
    if not config_path.exists():
        sys.exit(1)
    return yaml.safe_load(config_path.read_text())


def is_quiet_hours(config: dict) -> bool:
    """Check if we're in quiet hours (no alerts)."""
    from zoneinfo import ZoneInfo
    
    tz_name = config.get("briefing", {}).get("timezone", "Europe/Amsterdam")
    now = datetime.now(ZoneInfo(tz_name))
    
    alert_config = config.get("alerts", {})
    quiet_start = alert_config.get("quiet_hours_start", 21)
    quiet_end = alert_config.get("quiet_hours_end", 7)

    if quiet_start > quiet_end:
        # E.g., 21:00 - 07:00 (overnight)
        return now.hour >= quiet_start or now.hour < quiet_end
    else:
        return quiet_start <= now.hour < quiet_end


def main():
    config = load_config()
    project_root = Path(__file__).parent.parent

    # Check if alerts are enabled
    if not config.get("alerts", {}).get("enabled", True):
        return

    # Respect quiet hours
    if is_quiet_hours(config):
        return

    # ── Connect and fetch ────────────────────────────────
    gmail = GmailClient(
        credentials_file=str(project_root / config["gmail"]["credentials_file"]),
        token_file=str(project_root / config["gmail"]["token_file"]),
    )
    gmail.authenticate()

    # Check last 2 hours (matching the cron interval)
    check_hours = config.get("alerts", {}).get("check_interval_hours", 2)
    emails = gmail.fetch_recent_emails(hours=check_hours, max_results=20)

    if not emails:
        return

    # ── Classify ─────────────────────────────────────────
    llm = LLMClient(config["llm"])
    classifier = EmailClassifier(llm)
    classifications = classifier.classify_batch(emails)
    urgent = classifier.get_urgent(classifications)

    if not urgent:
        return

    # ── Send alert ───────────────────────────────────────
    alert_to = config.get("alerts", {}).get(
        "alert_to", config["gmail"]["your_email"]
    )

    # Build a simple alert email
    items_html = ""
    for item in urgent:
        items_html += (
            f"<div style='background: #fef2f2; border-left: 3px solid #dc2626; "
            f"padding: 10px 14px; margin-bottom: 8px; border-radius: 4px;'>"
            f"<strong>{item.get('summary', '')}</strong><br>"
            f"<span style='color: #666; font-size: 13px;'>"
            f"{item.get('suggested_action', '')}</span></div>"
        )

    html_body = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 500px; 
                margin: 0 auto; color: #333;">
        <h2 style="color: #dc2626;">⚡ Urgent Email Alert</h2>
        <p>{len(urgent)} email(s) flagged as urgent:</p>
        {items_html}
        <p style="color: #94a3b8; font-size: 12px; margin-top: 16px;">
            Check your inbox and Gmail drafts for prepared replies.
        </p>
    </div>
    """

    subject = f"⚡ URGENT: {len(urgent)} email(s) need your attention"
    gmail.send_email(alert_to, subject, html_body)
    print(f"✓ Urgent alert sent for {len(urgent)} items.")


if __name__ == "__main__":
    main()
