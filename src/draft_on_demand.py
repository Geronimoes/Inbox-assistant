#!/usr/bin/env python3
"""
On-demand draft generator — triggered by forwarding to jeroenm+draft@gmail.com.

Watches a Gmail label (_draft-request) for forwarded emails. When found:
  1. Extracts the original email from the forwarded message
  2. Fetches the full thread context from Gmail
  3. If the email matches a project, loads recent project emails for context
  4. Generates a context-aware draft reply using Sonnet
  5. Emails the draft to Jeroen's UCM address
  6. Removes the _draft-request label so the email isn't re-processed

Designed to run via cron every 2 minutes during work hours:
    */2 8-20 * * 1-5  cd /path && python src/draft_on_demand.py

Usage:
    python src/draft_on_demand.py              # Process pending requests
    python src/draft_on_demand.py --dry-run    # Preview without sending
"""

import argparse
import re
import sys
from pathlib import Path

import yaml

from gmail_client import GmailClient
from llm_client import LLMClient
from drafter import DraftComposer
from style_manager import StyleManager
from email_archiver import find_context_emails, extract_email_address


def load_config() -> dict:
    config_path = Path(__file__).parent.parent / "config.yaml"
    if not config_path.exists():
        sys.exit(1)
    return yaml.safe_load(config_path.read_text())


def find_project_for_email(email: dict, projects: list[dict]) -> dict | None:
    """Check if an email matches a configured project by keywords or collaborators."""
    subject = email.get("subject", "").lower()
    from_addr = email.get("from", "").lower()
    to_addr = email.get("to", "").lower()
    cc_addr = email.get("cc", "").lower()
    all_addrs = f"{from_addr} {to_addr} {cc_addr}"

    for project in projects:
        # Check keywords against subject
        for kw in project.get("keywords", []):
            if kw.lower() in subject:
                return project

        # Check collaborators against from/to/cc
        for collab in project.get("collaborators", []):
            fragment = collab.get("email_fragment", "").lower()
            name = collab.get("name", "").lower()
            if fragment and fragment in all_addrs:
                return project
            if name and name in all_addrs:
                return project

    return None



def build_draft_email_html(email: dict, draft_text: str,
                           project_name: str | None = None) -> str:
    """Build an HTML email containing the draft reply for Jeroen to use."""
    subject = email.get("subject", "(no subject)")
    from_addr = email.get("from", "")
    draft_escaped = (
        draft_text
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace("\n", "<br>")
    )

    project_line = ""
    if project_name:
        project_line = (
            f'<p style="color: #7c3aed; font-size: 13px;">'
            f'📁 Project: {project_name}</p>'
        )

    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI',
                 Roboto, sans-serif; max-width: 600px; margin: 0 auto;
                 color: #333; line-height: 1.5;">
        <h2 style="font-size: 18px; color: #7c3aed; border-bottom: 2px solid #7c3aed;
                   padding-bottom: 8px;">
            ✎ Draft Reply Ready
        </h2>
        <p style="color: #64748b; font-size: 14px;">
            Replying to: <strong>{subject}</strong><br>
            From: {from_addr}
        </p>
        {project_line}
        <div style="background: #f0fdf4; border-radius: 6px; padding: 16px;
                    border-left: 3px solid #166534; margin-top: 16px;
                    font-family: Georgia, serif; font-size: 14px;
                    white-space: pre-wrap; color: #1e293b;">
            {draft_escaped}
        </div>
        <p style="color: #94a3b8; font-size: 12px; margin-top: 16px;">
            This is an AI-generated draft. Review and edit before sending.
        </p>
    </div>
    """


def main():
    parser = argparse.ArgumentParser(description="On-demand draft generator")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without sending or modifying labels")
    args = parser.parse_args()

    config = load_config()
    project_root = Path(__file__).parent.parent

    # Check if feature is enabled
    dod_cfg = config.get("draft_on_demand", {})
    if not dod_cfg.get("enabled", False):
        return

    label_name = dod_cfg.get("label", "_draft-request")
    send_to = dod_cfg.get("send_to",
                          config.get("briefing", {}).get("send_to",
                                                         config["gmail"]["your_email"]))

    # ── Connect to Gmail ─────────────────────────────────
    gmail = GmailClient(
        credentials_file=str(project_root / config["gmail"]["credentials_file"]),
        token_file=str(project_root / config["gmail"]["token_file"]),
    )
    try:
        gmail.authenticate()
    except Exception as e:
        print(f"✗ Gmail authentication failed: {e}")
        print("  Run: python src/gmail_client.py --auth --headless")
        sys.exit(1)

    # ── Check for draft requests ─────────────────────────
    requests_list = gmail.fetch_by_label(label_name, max_results=5)

    if not requests_list:
        return  # Nothing to do — silent exit (runs every 2 min)

    print(f"── {len(requests_list)} draft request(s) found")

    # ── Initialise LLM + drafter ─────────────────────────
    llm_config = config.get("llm")
    if not llm_config:
        print("✗ No 'llm' section in config.yaml.")
        sys.exit(1)

    llm = LLMClient(llm_config)
    style_manager = StyleManager(project_root)
    style_profile = style_manager.load_style_profile()
    composer = DraftComposer(llm, style_profile=style_profile or "")

    projects = config.get("projects", [])
    vault_path = config.get("obsidian", {}).get("vault_path", "")

    # ── Process each request ─────────────────────────────
    for email in requests_list:
        subject = email.get("subject", "(no subject)")
        print(f"\n  Processing: {subject[:60]}")

        # Fetch thread context
        thread_context = None
        thread_id = email.get("thread_id")
        if thread_id:
            try:
                thread_context = gmail.fetch_thread(thread_id)
                if thread_context:
                    print(f"  Thread context: {len(thread_context)} message(s)")
            except Exception as e:
                print(f"  ⚠ Could not fetch thread: {e}")

        # Load context from the unified email archive (inbox-emails/)
        # Searches by sender email and thread_id for relevant prior correspondence.
        project = find_project_for_email(email, projects)
        project_context = None
        project_name = None
        if project:
            project_name = project.get("name", project.get("id"))

        if vault_path:
            archive_cfg = config.get("archive", {})
            vault_folder = archive_cfg.get("vault_folder", "inbox-emails")
            mail_folder = archive_cfg.get("mail_folder", "mail")
            archive_dir = Path(vault_path).expanduser() / vault_folder / mail_folder

            sender_email = extract_email_address(email.get("from", ""))
            project_context = find_context_emails(
                archive_dir,
                sender_email=sender_email,
                thread_id=thread_id,
                limit=5,
                max_chars_per_email=1500,
            )
            if project_context:
                ctx_label = f"Project: {project_name}" if project_name else "Archive"
                print(f"  {ctx_label} context loaded from vault")

        # Build a classification-like dict for the drafter
        # (the drafter expects a classification dict with needs_draft=True)
        classification = {
            "needs_draft": True,
            "draft_tone": "professional",
            "reply_language": "en",  # Will be overridden by actual detection
            "suggested_action": f"Reply to {email.get('from', 'sender')}",
        }

        # Simple language detection from email body
        body = email.get("body_text", "")
        dutch_indicators = ["beste", "geachte", "groeten", "bedankt",
                            "graag", "betreft", "hierbij"]
        if any(word in body.lower()[:500] for word in dutch_indicators):
            classification["reply_language"] = "nl"

        # Generate draft
        draft_text = composer.compose_draft(
            email, classification,
            thread_context=thread_context,
            project_context=project_context,
        )

        if not draft_text:
            print(f"  ⚠ No draft generated for: {subject[:50]}")
            continue

        print(f"  ✓ Draft composed ({len(draft_text)} chars)")

        if args.dry_run:
            print(f"\n  DRY RUN — would email draft to {send_to}")
            print(f"  Draft preview:\n  {draft_text[:200]}...")
        else:
            # Email the draft to Jeroen's UCM address
            draft_subject = f"✎ Draft Reply: {subject}"
            html_body = build_draft_email_html(
                email, draft_text, project_name=project_name
            )
            gmail.send_email(send_to, draft_subject, html_body)

            # Remove the label so this email isn't re-processed
            gmail.remove_label(email["id"], label_name)
            print(f"  ✓ Draft emailed to {send_to}")

    print("\n✓ Done!")


if __name__ == "__main__":
    main()
