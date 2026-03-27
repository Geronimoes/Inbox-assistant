#!/usr/bin/env python3
"""
Inbox Briefing Assistant — Main orchestrator.

Fetches recent emails, classifies them, generates drafts, and sends
a morning briefing. Designed to run via cron on a VPS.

Usage:
    python src/fetch_and_triage.py              # Full run
    python src/fetch_and_triage.py --dry-run    # Preview without sending/drafting
    python src/fetch_and_triage.py --hours 4    # Custom lookback window
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

from gmail_client import GmailClient
from llm_client import LLMClient
from classifier import EmailClassifier
from drafter import DraftComposer
from briefing import BriefingGenerator
from style_manager import StyleManager


def load_config() -> dict:
    """Load configuration from config.yaml."""
    config_path = Path(__file__).parent.parent / "config.yaml"
    if not config_path.exists():
        print("✗ config.yaml not found. Copy config.example.yaml and fill it in.")
        sys.exit(1)
    return yaml.safe_load(config_path.read_text())


def load_processed(data_dir: Path) -> set:
    """Load set of already-processed email IDs."""
    processed_file = data_dir / "processed.json"
    if processed_file.exists():
        data = json.loads(processed_file.read_text())
        return set(data.get("ids", []))
    return set()


def save_processed(data_dir: Path, processed_ids: set) -> None:
    """Save processed email IDs (keep last 5000 to prevent unbounded growth)."""
    ids_list = sorted(processed_ids)[-5000:]
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "processed.json").write_text(
        json.dumps({"ids": ids_list, "updated": datetime.now().isoformat()})
    )


def main():
    parser = argparse.ArgumentParser(description="Inbox Briefing Assistant")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without sending emails or creating drafts")
    parser.add_argument("--hours", type=int, default=None,
                        help="Override lookback hours from config")
    parser.add_argument("--no-drafts", action="store_true",
                        help="Skip draft creation")
    parser.add_argument("--regenerate-style", action="store_true",
                        help="Regenerate the writing style profile from corpus, then exit")
    args = parser.parse_args()

    config = load_config()
    project_root = Path(__file__).parent.parent
    data_dir = project_root / "data"

    # ── 0. Style profile (--regenerate-style flag) ───────
    # Initialise style manager (used both here and for draft composition)
    style_manager = StyleManager(project_root)

    if args.regenerate_style:
        print("\n── Regenerating writing style profile...")
        llm_config = config.get("llm")
        if not llm_config:
            print("✗ No 'llm' section in config.yaml. See config.example.yaml.")
            sys.exit(1)
        llm = LLMClient(llm_config)
        ok = style_manager.regenerate_style_profile(llm)
        if ok:
            print("\n✓ Style profile updated. Drafts will use it from the next run.")
        else:
            print("\n✗ Style profile could not be generated (see above).")
        return

    # ── 1. Connect to Gmail ──────────────────────────────
    print("\n── Connecting to Gmail...")
    gmail = GmailClient(
        credentials_file=str(project_root / config["gmail"]["credentials_file"]),
        token_file=str(project_root / config["gmail"]["token_file"]),
    )
    gmail.authenticate()

    # ── 2. Fetch recent emails ───────────────────────────
    hours = args.hours or config["gmail"]["lookback_hours"]
    print(f"\n── Fetching emails from the last {hours} hours...")
    emails = gmail.fetch_recent_emails(
        hours=hours,
        labels=config["gmail"].get("scan_labels", ["INBOX"]),
    )

    if not emails:
        print("No new emails. Nothing to do.")
        return

    # Filter out already-processed emails
    processed = load_processed(data_dir)
    new_emails = [e for e in emails if e["id"] not in processed]

    if not new_emails:
        print("All emails already processed. Nothing new.")
        return

    print(f"  {len(new_emails)} new emails to process "
          f"({len(emails) - len(new_emails)} already processed)")

    # ── 3. Initialise LLM client ─────────────────────────
    llm_config = config.get("llm")
    if not llm_config:
        print("✗ No 'llm' section in config.yaml. See config.example.yaml.")
        sys.exit(1)
    llm = LLMClient(llm_config)

    # ── 4. Classify emails ───────────────────────────────
    print(f"\n── Classifying {len(new_emails)} emails...")
    classifier = EmailClassifier(llm)
    classifications = classifier.classify_batch(new_emails)

    urgent = classifier.get_urgent(classifications)
    actionable = classifier.get_actionable(classifications)
    fyi = classifier.get_fyi(classifications)
    noise = classifier.get_noise(classifications)

    print(f"  ⚡ {len(urgent)} urgent")
    print(f"  📋 {len(actionable) - len(urgent)} action needed")
    print(f"  🔵 {len(fyi)} FYI")
    print(f"  ⚪ {len(noise)} noise")

    # ── 5. Compose draft replies ─────────────────────────
    drafts = []
    if not args.no_drafts and config.get("drafts", {}).get("enabled", True):
        needs_draft = [c for c in classifications if c.get("needs_draft")]
        if needs_draft:
            print(f"\n── Composing {len(needs_draft)} draft replies...")
            style_profile = style_manager.load_style_profile()
            if style_profile:
                print("  Using personalised writing style profile.")
            composer = DraftComposer(llm, style_profile=style_profile)
            drafts = composer.compose_batch(new_emails, classifications)

    # ── 6. Generate briefing ─────────────────────────────
    print("\n── Generating briefing...")
    briefing_config = config.get("briefing", {})
    generator = BriefingGenerator(
        timezone=briefing_config.get("timezone", "Europe/Amsterdam"),
        max_fyi_items=briefing_config.get("max_fyi_items", 10),
        show_noise_count=briefing_config.get("show_noise_count", True),
    )
    subject, html_body = generator.generate(classifications, drafts)

    # ── 7. Deliver ───────────────────────────────────────
    if args.dry_run:
        print(f"\n── DRY RUN — would send briefing: {subject}")
        print(f"  Would create {len(drafts)} drafts")
        print(f"  Would archive {len(noise)} noise items")

        # Save preview
        preview_path = project_root / "data" / "preview.html"
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_text(html_body)
        print(f"\n  Preview saved to: {preview_path}")

        # Print classification details
        print("\n── Classification details:")
        for cls in classifications:
            icon = {"URGENT": "⚡", "ACTION": "📋",
                    "FYI": "🔵", "NOISE": "⚪"}.get(cls["category"], "?")
            draft_mark = " [draft]" if cls.get("needs_draft") else ""
            print(f"  {icon} {cls['summary'][:70]}{draft_mark}")
    else:
        # Send briefing email
        print(f"\n── Sending briefing: {subject}")
        send_to = briefing_config.get("send_to", config["gmail"]["your_email"])
        gmail.send_email(send_to, subject, html_body)

        # Save drafts to Gmail
        prefix = config.get("drafts", {}).get("draft_prefix", "[AI Draft] ")
        for draft in drafts:
            gmail.create_draft(
                to=draft["to"],
                subject=f"{prefix}{draft['subject']}",
                body=draft["draft_text"],
                thread_id=draft.get("thread_id"),
            )

        # Archive noise (if enabled)
        if config.get("archive", {}).get("auto_archive_noise", False):
            noise_ids = [c["email_id"] for c in noise]
            if noise_ids:
                print(f"\n── Archiving {len(noise_ids)} noise items...")
                gmail.archive_messages(noise_ids)

        # Mark as processed
        new_processed = processed | {e["id"] for e in new_emails}
        save_processed(data_dir, new_processed)

    print("\n✓ Done!")


if __name__ == "__main__":
    main()
