"""
Notification dispatcher for the Inbox Assistant.

Sends urgent alerts and briefing summaries via Telegram.
Falls back to email (via Gmail API) if Telegram is not configured.

Configuration (config.yaml):
    notifications:
      telegram:
        bot_token: "123456:ABC..."
        chat_id: "987654321"

Usage:
    from notifier import Notifier
    notifier = Notifier(config, gmail_client)
    notifier.send_urgent(urgent_items)
    notifier.send_briefing_summary(urgent_count, action_count, fyi_count)

Test:
    python src/notifier.py --test
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

import requests


class Notifier:
    """Send notifications via Telegram (with optional email fallback)."""

    def __init__(self, config: dict, gmail_client=None):
        """
        Args:
            config:       Full config dict (reads notifications.telegram and alerts).
            gmail_client: Authenticated GmailClient instance for email fallback.
                          May be None if Telegram is configured.
        """
        notif_cfg = config.get("notifications", {})
        tg_cfg = notif_cfg.get("telegram", {})

        self.bot_token = tg_cfg.get("bot_token", "")
        self.chat_id = str(tg_cfg.get("chat_id", ""))
        self.telegram_enabled = bool(self.bot_token and self.chat_id)

        self.gmail = gmail_client
        self.alert_cfg = config.get("alerts", {})
        self.gmail_cfg = config.get("gmail", {})

    # ── Public interface ──────────────────────────────────────────────────────

    def send_urgent(self, urgent_items: list[dict]) -> bool:
        """Send an urgent alert for the given classified email items.

        Tries Telegram first; falls back to Gmail email if not configured.

        Returns True if the notification was sent successfully.
        """
        if not urgent_items:
            return True

        count = len(urgent_items)
        subject = f"⚡ URGENT: {count} email(s) need your attention"

        if self.telegram_enabled:
            lines = [f"⚡ *{count} urgent email(s) need attention*\n"]
            for item in urgent_items:
                summary = item.get("summary", "")
                action = item.get("suggested_action", "")
                lines.append(f"• {summary}")
                if action:
                    lines.append(f"  _{action}_")
            lines.append("\nCheck Gmail for draft replies.")
            return self._send_telegram("\n".join(lines))

        elif self.gmail:
            html_body = self._build_urgent_html(urgent_items)
            alert_to = self.alert_cfg.get(
                "alert_to", self.gmail_cfg.get("your_email", "")
            )
            try:
                self.gmail.send_email(alert_to, subject, html_body)
                print(f"✓ Urgent alert sent via email to {alert_to}.")
                return True
            except Exception as e:
                print(f"  ⚠ Failed to send urgent email alert: {e}")
                return False

        else:
            print("  ⚠ No notification method configured (no Telegram, no Gmail).")
            return False

    def send_briefing_summary(
        self, urgent: int, action: int, fyi: int, noise: int = 0
    ) -> bool:
        """Send a brief morning Telegram ping summarising the briefing.

        Only sends if Telegram is configured — this is a convenience ping,
        not critical enough to fall back to email.

        Returns True if sent, False if skipped or failed.
        """
        if not self.telegram_enabled:
            return False

        now = datetime.now()
        time_str = now.strftime("%H:%M")

        parts = []
        if urgent:
            parts.append(f"⚡ {urgent} urgent")
        if action:
            parts.append(f"📋 {action} to reply")
        if fyi:
            parts.append(f"🔵 {fyi} FYI")
        if noise:
            parts.append(f"⚪ {noise} noise")

        summary_line = " · ".join(parts) if parts else "All clear"
        message = f"📬 *Inbox briefing ({time_str})*\n{summary_line}\n\nFull briefing in your email."
        return self._send_telegram(message)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _send_telegram(self, message: str) -> bool:
        """POST a message to the Telegram Bot API. Returns True on success."""
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown",
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.ok:
                print(f"✓ Telegram notification sent.")
                return True
            else:
                print(f"  ⚠ Telegram API error {resp.status_code}: {resp.text[:200]}")
                return False
        except requests.RequestException as e:
            print(f"  ⚠ Telegram request failed: {e}")
            return False

    def _build_urgent_html(self, urgent_items: list[dict]) -> str:
        """Build an HTML email body for an urgent alert."""
        items_html = ""
        for item in urgent_items:
            items_html += (
                f"<div style='background: #fef2f2; border-left: 3px solid #dc2626; "
                f"padding: 10px 14px; margin-bottom: 8px; border-radius: 4px;'>"
                f"<strong>{item.get('summary', '')}</strong><br>"
                f"<span style='color: #666; font-size: 13px;'>"
                f"{item.get('suggested_action', '')}</span></div>"
            )
        return f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 500px;
                margin: 0 auto; color: #333;">
        <h2 style="color: #dc2626;">⚡ Urgent Email Alert</h2>
        <p>{len(urgent_items)} email(s) flagged as urgent:</p>
        {items_html}
        <p style="color: #94a3b8; font-size: 12px; margin-top: 16px;">
            Check your inbox and Gmail drafts for prepared replies.
        </p>
    </div>
    """


# ── CLI test mode ──────────────────────────────────────────────────────────────

def _run_test(config: dict) -> None:
    """Send a test Telegram message to verify the configuration."""
    notif_cfg = config.get("notifications", {})
    tg_cfg = notif_cfg.get("telegram", {})

    bot_token = tg_cfg.get("bot_token", "")
    chat_id = tg_cfg.get("chat_id", "")

    if not bot_token or not chat_id:
        print("✗ Telegram not configured in config.yaml.")
        print("  Add under notifications.telegram: bot_token and chat_id")
        sys.exit(1)

    notifier = Notifier(config)
    print(f"Sending test message to chat_id {chat_id}...")
    ok = notifier._send_telegram(
        "✅ *Inbox Assistant* — Telegram notifications are working!"
    )
    if ok:
        print("✓ Test message sent. Check your Telegram.")
    else:
        print("✗ Test message failed — check bot_token and chat_id.")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Notifier test")
    parser.add_argument("--test", action="store_true",
                        help="Send a test Telegram message")
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent
    config_path = project_root / "config.yaml"

    if not config_path.exists():
        print("✗ config.yaml not found.")
        sys.exit(1)

    import yaml
    config = yaml.safe_load(config_path.read_text())

    if args.test:
        _run_test(config)
    else:
        parser.print_help()
