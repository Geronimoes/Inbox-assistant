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
from feedback_handler import process_feedback_dir
from attachment_handler import AttachmentHandler
from notifier import Notifier


def load_config() -> dict:
    """Load configuration from config.yaml."""
    config_path = Path(__file__).parent.parent / "config.yaml"
    if not config_path.exists():
        print("✗ config.yaml not found. Copy config.example.yaml and fill it in.")
        sys.exit(1)
    return yaml.safe_load(config_path.read_text())


# Required fields in every staging JSON file (must match n8n/README.md schema)
_REQUIRED_STAGING_FIELDS = {"id", "thread_id", "subject", "from", "to",
                             "date", "snippet", "body_text"}


def load_from_staging(staging_dir: Path) -> list[dict]:
    """Load email JSON files dropped by n8n into the staging/ directory.

    Returns a list of email dicts (same format as gmail_client._parse_message,
    plus an optional 'attachments' list added by n8n). Successfully parsed
    files are moved to staging/processed/ so they are never re-processed.

    If staging/ is empty or contains no valid JSON files, returns [] and the
    caller falls back to Gmail API fetch.
    """
    json_files = sorted(staging_dir.glob("*.json"))
    if not json_files:
        return []

    processed_dir = staging_dir / "processed"
    processed_dir.mkdir(exist_ok=True)

    emails = []
    for path in json_files:
        try:
            email = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            print(f"  ⚠ Skipping {path.name}: invalid JSON — {e}")
            continue

        # Validate required fields — schema must match n8n/README.md
        missing = _REQUIRED_STAGING_FIELDS - set(email.keys())
        if missing:
            print(f"  ⚠ Skipping {path.name}: missing fields: "
                  f"{', '.join(sorted(missing))}")
            print(f"    See n8n/README.md for the required JSON schema.")
            continue

        # Ensure optional fields have safe defaults
        email.setdefault("labels", [])
        email.setdefault("attachments", [])

        emails.append(email)
        # Move to processed/ — prevents re-processing on next run
        path.rename(processed_dir / path.name)

    if emails:
        print(f"  Loaded {len(emails)} email(s) from staging/")
    elif json_files:
        print(f"  {len(json_files)} file(s) in staging/ but none were valid.")

    return emails


def load_state(data_dir: Path) -> tuple[set, dict]:
    """Load processed email IDs and thread state from processed.json.

    Returns:
        processed_ids: Set of email IDs already handled (for deduplication).
        thread_state:  Dict of thread_id → {status, subject, ...} for
                       closed-loop tracking and feedback handler.

    Automatically migrates the old v1 format {"ids": [...]} to the new
    v2 format that also tracks thread status.
    """
    processed_file = data_dir / "processed.json"
    if not processed_file.exists():
        return set(), {}

    data = json.loads(processed_file.read_text())

    # v1 format migration: {"ids": [...]} → v2 with threads dict
    if "ids" in data and "threads" not in data:
        ids = set(data["ids"])
        # Migrate: create stub thread entries from the flat ID list
        threads = {eid: {"email_id": eid, "status": "open",
                         "subject": "", "classified_at": None,
                         "handled_at": None}
                   for eid in ids}
        return ids, threads

    threads = data.get("threads", {})
    ids = set(data.get("ids", []))
    # Ensure ids set is consistent with threads dict
    ids |= {v["email_id"] for v in threads.values() if v.get("email_id")}
    return ids, threads


def append_daily_stats(data_dir: Path, classifications: list[dict],
                       drafts: list[dict]) -> None:
    """Append today's email counts to data/weekly-stats.json.

    Keeps a rolling 90-day window. Each entry is one dict per calendar day;
    if the script runs more than once on the same day the counts accumulate.
    """
    stats_file = data_dir / "weekly-stats.json"
    stats: list[dict] = []
    if stats_file.exists():
        try:
            stats = json.loads(stats_file.read_text())
            if not isinstance(stats, list):
                stats = []
        except (json.JSONDecodeError, OSError):
            stats = []

    today = datetime.now().strftime("%Y-%m-%d")
    entry = next((s for s in stats if s.get("date") == today), None)
    if entry is None:
        entry = {"date": today, "urgent": 0, "action": 0,
                 "fyi": 0, "noise": 0, "total": 0, "drafts": 0}
        stats.append(entry)

    for cls in classifications:
        cat = cls.get("category", "")
        if cat == "URGENT":
            entry["urgent"] += 1
        elif cat == "ACTION":
            entry["action"] += 1
        elif cat == "FYI":
            entry["fyi"] += 1
        elif cat == "NOISE":
            entry["noise"] += 1
    entry["total"] = (entry["urgent"] + entry["action"]
                      + entry["fyi"] + entry["noise"])
    entry["drafts"] += len(drafts)

    # Keep 90 days
    stats = sorted(stats, key=lambda s: s["date"])[-90:]

    tmp = stats_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(stats, indent=2))
    tmp.replace(stats_file)


def save_state(data_dir: Path, processed_ids: set, thread_state: dict) -> None:
    """Save processed IDs and thread state to processed.json.

    Keeps only the most recent 5000 IDs to prevent unbounded growth.
    Thread state is kept for all known threads (they're compact dicts).
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    ids_list = sorted(processed_ids)[-5000:]

    payload = {
        "version": 2,
        "updated": datetime.now().isoformat(),
        "ids": ids_list,
        "threads": thread_state,
    }
    # Write atomically
    tmp = data_dir / "processed.tmp"
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(data_dir / "processed.json")


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

    # ── 1. Load state + process BCC feedback ─────────────
    # Load processed IDs and thread state. Process any pending BCC feedback
    # files first — they mark threads as handled and add writing samples.
    staging_dir = project_root / "staging"
    processed_ids, thread_state = load_state(data_dir)

    feedback_dir = staging_dir / "feedback"
    if feedback_dir.exists():
        samples_dir = project_root / "writing-samples" / "samples"
        feedback_results = process_feedback_dir(feedback_dir, samples_dir, thread_state)
        if feedback_results:
            # Save updated thread state immediately so it's not lost
            save_state(data_dir, processed_ids, thread_state)
            print(f"  {len(feedback_results)} thread(s) marked as handled.")

    # ── 2. Email input — staging first, Gmail fallback ───
    staging_dir = project_root / "staging"
    gmail = GmailClient(
        credentials_file=str(project_root / config["gmail"]["credentials_file"]),
        token_file=str(project_root / config["gmail"]["token_file"]),
    )

    print("\n── Checking staging/ for emails from n8n...")
    emails = load_from_staging(staging_dir)

    if emails:
        print(f"  Using {len(emails)} email(s) from n8n staging/")
        print("  (Gmail API fetch skipped — emails came from n8n)")
    else:
        print("  staging/ is empty — falling back to Gmail API fetch.")
        print("\n── Connecting to Gmail...")
        try:
            gmail.authenticate()
        except Exception as e:
            print(f"\n✗ Gmail authentication failed: {e}")
            print("  Run: python src/gmail_client.py --auth --headless")
            sys.exit(1)

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
    new_emails = [e for e in emails if e["id"] not in processed_ids]

    if not new_emails:
        print("All emails already processed. Nothing new.")
        return

    n_already = len(emails) - len(new_emails)
    print(f"  {len(new_emails)} new email(s) to process "
          f"({n_already} already processed)")

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

    # Record classified emails in thread state for closed-loop tracking
    email_lookup = {e["id"]: e for e in new_emails}
    now_iso = datetime.now().isoformat()
    for cls in classifications:
        eid = cls.get("email_id", "")
        email = email_lookup.get(eid, {})
        tid = email.get("thread_id", eid)
        if tid:
            thread_state[tid] = {
                "email_id": eid,
                "status": "open",
                "subject": email.get("subject", ""),
                "classified_at": now_iso,
                "handled_at": None,
            }

    # ── 5. Process attachments ───────────────────────────
    # Build a lookup: email_id → list of attachment summaries
    # Only runs when emails have a non-empty 'attachments' list (n8n staging path)
    attachment_summaries: dict = {}
    emails_with_attachments = [e for e in new_emails if e.get("attachments")]
    if emails_with_attachments:
        print(f"\n── Processing attachments "
              f"({sum(len(e['attachments']) for e in emails_with_attachments)} file(s))...")
        att_handler = AttachmentHandler(project_root, llm_client=llm)
        for email in emails_with_attachments:
            summaries = att_handler.process_email_attachments(
                email["id"], email["attachments"]
            )
            if summaries:
                attachment_summaries[email["id"]] = summaries

    # ── 6. Compose draft replies ─────────────────────────
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

    # ── 7. Generate briefing ─────────────────────────────
    print("\n── Generating briefing...")
    briefing_config = config.get("briefing", {})
    generator = BriefingGenerator(
        timezone=briefing_config.get("timezone", "Europe/Amsterdam"),
        max_fyi_items=briefing_config.get("max_fyi_items", 10),
        show_noise_count=briefing_config.get("show_noise_count", True),
    )
    subject, html_body = generator.generate(
        classifications, drafts, attachment_summaries=attachment_summaries
    )
    markdown_body = generator.generate_markdown(
        classifications, drafts, attachment_summaries=attachment_summaries
    )

    # ── 8. Send Telegram briefing ping ───────────────────
    if not args.dry_run:
        notifier = Notifier(config)
        notifier.send_briefing_summary(
            urgent=len(urgent),
            action=len(actionable),
            fyi=len(fyi),
            noise=len(noise),
        )

    # ── 9. Write Obsidian note ────────────────────────────
    obsidian_cfg = config.get("obsidian", {})
    vault_path = obsidian_cfg.get("vault_path", "")
    if vault_path:
        briefing_folder = obsidian_cfg.get("briefing_folder", "inbox-briefings")
        try:
            note_path = generator.write_to_obsidian(
                markdown_body, vault_path, briefing_folder
            )
            print(f"  Obsidian note written: {note_path}")
        except OSError as e:
            print(f"  ⚠ Could not write Obsidian note: {e}")

    # ── 10. Deliver ──────────────────────────────────────
    if args.dry_run:
        print(f"\n── DRY RUN — would send briefing: {subject}")
        print(f"  Would create {len(drafts)} drafts")
        print(f"  Would archive {len(noise)} noise items")

        # Save HTML and Markdown previews
        preview_path = project_root / "data" / "preview.html"
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_text(html_body)
        md_preview_path = project_root / "data" / "preview.md"
        md_preview_path.write_text(markdown_body)
        print(f"\n  HTML preview:     {preview_path}")
        print(f"  Markdown preview: {md_preview_path}")

        # Print classification details
        print("\n── Classification details:")
        for cls in classifications:
            icon = {"URGENT": "⚡", "ACTION": "📋",
                    "FYI": "🔵", "NOISE": "⚪"}.get(cls["category"], "?")
            draft_mark = " [draft]" if cls.get("needs_draft") else ""
            print(f"  {icon} {cls['summary'][:70]}{draft_mark}")
    else:
        # Ensure Gmail is authenticated for output steps (drafts, send, archive).
        # If emails came from staging, Gmail may not have been authenticated yet.
        if not gmail.service:
            print("\n── Connecting to Gmail for output (drafts/send)...")
            try:
                gmail.authenticate()
            except Exception as e:
                print(f"\n✗ Gmail authentication failed: {e}")
                print("  Run: python src/gmail_client.py --auth --headless")
                sys.exit(1)

        # Send briefing email
        print(f"\n── Sending briefing: {subject}")
        send_to = briefing_config.get("send_to", config["gmail"]["your_email"])
        gmail.send_email(send_to, subject, html_body)

        # Save drafts to Gmail (only if save_to_gmail is enabled)
        drafts_cfg = config.get("drafts", {})
        if drafts_cfg.get("save_to_gmail", False) and drafts:
            prefix = drafts_cfg.get("draft_prefix", "[AI Draft] ")
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

        # Append daily stats for the dashboard
        append_daily_stats(data_dir, classifications, drafts)

        # Save state (processed IDs + thread tracking)
        new_processed = processed_ids | {e["id"] for e in new_emails}
        save_state(data_dir, new_processed, thread_state)

    print("\n✓ Done!")


if __name__ == "__main__":
    main()
