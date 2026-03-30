#!/usr/bin/env python3
"""
Project Discovery — suggests new email projects by analysing recent email traffic.

Fetches recent emails from Gmail and uses an LLM to identify clusters of related
emails that could be configured as archiving projects. Outputs suggestions with
ready-to-paste YAML config blocks, delivered via email and/or Obsidian note.

Runs weekly via cron (Sunday 04:00) or on demand for retroactive discovery.

Usage:
    python src/project_discover.py                    # 2-week analysis, sends email
    python src/project_discover.py --dry-run          # preview without sending
    python src/project_discover.py --hours 4320       # ~6 months retroactive
    python src/project_discover.py --min-emails 3     # minimum cluster size

Cron:
    0 4 * * 0 cd /home/jeroen/projects/inbox-assistant && env/bin/python src/project_discover.py >> logs/project-discover.log 2>&1
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

# Import from same src/ directory — run from project root.
sys.path.insert(0, str(Path(__file__).parent))
from gmail_client import GmailClient
from llm_client import LLMClient


# ── Config loading ───────────────────────────────────────────────────────────

def load_config() -> dict:
    """Load configuration from config.yaml (project root)."""
    config_path = Path(__file__).parent.parent / "config.yaml"
    if not config_path.exists():
        print("✗ config.yaml not found. Copy config.example.yaml and fill it in.")
        sys.exit(1)
    return yaml.safe_load(config_path.read_text())


# ── Existing project extraction ──────────────────────────────────────────────

def load_existing_projects(config: dict) -> list[dict]:
    """Extract existing project definitions for the LLM to exclude.

    Returns a simplified list with just keywords and collaborator names/fragments.
    """
    projects = config.get("projects", [])
    summaries = []
    for p in projects:
        summaries.append({
            "name": p.get("name", p.get("id")),
            "keywords": p.get("keywords", []),
            "collaborators": [
                c.get("name", c.get("email_fragment", ""))
                for c in p.get("collaborators", [])
            ],
        })
    return summaries


# ── Email metadata preparation ───────────────────────────────────────────────

def prepare_email_metadata(emails: list[dict]) -> list[dict]:
    """Strip emails to lightweight metadata for the LLM.

    Only includes from, to, cc, subject, date, and snippet — no body text.
    This keeps token usage low while providing enough signal for clustering.
    """
    metadata = []
    for e in emails:
        metadata.append({
            "from": e.get("from", ""),
            "to": e.get("to", ""),
            "cc": e.get("cc", ""),
            "subject": e.get("subject", ""),
            "date": e.get("date", ""),
            "snippet": e.get("snippet", ""),
        })
    return metadata


# ── LLM analysis ─────────────────────────────────────────────────────────────

def load_prompt() -> str:
    """Load the project discovery prompt from prompts/project_discover.md."""
    prompt_path = Path(__file__).parent.parent / "prompts" / "project_discover.md"
    if not prompt_path.exists():
        print("✗ prompts/project_discover.md not found.")
        sys.exit(1)
    return prompt_path.read_text()


def discover_projects(
    llm: LLMClient,
    email_metadata: list[dict],
    existing_projects: list[dict],
    min_emails: int,
) -> list[dict]:
    """Analyse email metadata and return suggested project clusters.

    Splits into batches if there are more than 150 emails, then merges.
    """
    system_prompt = load_prompt()
    batch_size = 150

    if len(email_metadata) <= batch_size:
        # Single call — most common case
        return _analyse_batch(
            llm, system_prompt, email_metadata, existing_projects, min_emails
        )

    # Multiple batches — split, analyse each, then merge
    print(f"  Splitting {len(email_metadata)} emails into batches of {batch_size}...")
    all_suggestions = []
    for i in range(0, len(email_metadata), batch_size):
        batch = email_metadata[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        print(f"  Analysing batch {batch_num} ({len(batch)} emails)...")
        suggestions = _analyse_batch(
            llm, system_prompt, batch, existing_projects, min_emails
        )
        all_suggestions.extend(suggestions)

    if len(all_suggestions) <= 1:
        return all_suggestions

    # Merge/deduplicate across batches
    print("  Merging results across batches...")
    return _merge_suggestions(llm, system_prompt, all_suggestions)


def _analyse_batch(
    llm: LLMClient,
    system_prompt: str,
    metadata: list[dict],
    existing_projects: list[dict],
    min_emails: int,
) -> list[dict]:
    """Run a single LLM call to analyse one batch of emails."""
    existing_text = json.dumps(existing_projects, indent=2)
    emails_text = json.dumps(metadata, indent=2)

    user_message = (
        f"Analyse the following {len(metadata)} emails and identify project clusters.\n"
        f"Return ONLY a JSON array — no explanation or commentary.\n\n"
        f"Minimum emails per project: {min_emails}\n\n"
        f"Already-configured projects (exclude these):\n{existing_text}\n\n"
        f"Email metadata:\n{emails_text}"
    )

    response_text = llm.complete(
        "project_discovery",
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=8192,
    )

    return _parse_json_response(response_text)


def _merge_suggestions(
    llm: LLMClient,
    system_prompt: str,
    suggestions: list[dict],
) -> list[dict]:
    """Merge and deduplicate suggestions from multiple batches.

    Falls back to naive deduplication if the LLM fails to return valid JSON.
    """
    user_message = (
        "IMPORTANT: Return ONLY a JSON array. No explanation, no reasoning, no "
        "commentary — just the JSON array.\n\n"
        "The following project suggestions came from analysing separate batches of "
        "emails. Merge duplicates (same project found in multiple batches): combine "
        "their email counts and sample subjects into a single entry, keeping the "
        "best config. Remove any that now fall below the minimum threshold. "
        "Return the merged JSON array.\n\n"
        f"Suggestions to merge:\n{json.dumps(suggestions, indent=2)}"
    )

    response_text = llm.complete(
        "project_discovery",
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=8192,
    )

    merged = _parse_json_response(response_text)
    if merged:
        return merged

    # Fallback: naive deduplication by project ID if LLM merge failed
    print("  ⚠ LLM merge failed — falling back to naive deduplication.")
    return _naive_dedup(suggestions)


def _naive_dedup(suggestions: list[dict]) -> list[dict]:
    """Simple deduplication: keep the suggestion with the highest email_count per ID."""
    by_id = {}
    for s in suggestions:
        config = s.get("suggested_config", {})
        pid = config.get("id", s.get("name", "unknown"))
        existing = by_id.get(pid)
        if existing is None or s.get("email_count", 0) > existing.get("email_count", 0):
            by_id[pid] = s
    # Sort by email count descending
    return sorted(by_id.values(), key=lambda s: s.get("email_count", 0), reverse=True)


def _parse_json_response(response_text: str) -> list[dict]:
    """Parse a JSON array from the LLM response, stripping code fences."""
    text = response_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]

    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        print(f"  ⚠ LLM returned non-array JSON. Skipping.")
        return []
    except json.JSONDecodeError:
        print(f"  ⚠ Failed to parse LLM response as JSON. First 500 chars:")
        print(f"    {response_text[:500]}")
        return []


# ── Report generation ────────────────────────────────────────────────────────

def generate_report_html(suggestions: list[dict], date_str: str,
                         hours: int) -> tuple[str, str]:
    """Generate an HTML email report of project suggestions.

    Returns (subject, html_body).
    """
    period = _describe_period(hours)
    subject = f"📂 Inbox Projects — {len(suggestions)} new suggestion(s) (week of {date_str})"

    if not suggestions:
        html = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI',
                     Roboto, sans-serif; max-width: 600px; margin: 0 auto;
                     color: #333; line-height: 1.5;">
        <h1 style="font-size: 20px; border-bottom: 2px solid #7c3aed;
                   padding-bottom: 8px; color: #1e293b;">
            📂 Project Discovery — {date_str}
        </h1>
        <p>No new project clusters found in your email from the last {period}.</p>
        <p style="color: #64748b; font-size: 13px;">
            For a deeper scan, run:
            <code>python src/project_discover.py --hours 4320</code>
        </p>
        </div>
        """
        return subject, html

    sections = []

    # Header
    sections.append(f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI',
                 Roboto, sans-serif; max-width: 600px; margin: 0 auto;
                 color: #333; line-height: 1.5;">
    <h1 style="font-size: 20px; border-bottom: 2px solid #7c3aed;
               padding-bottom: 8px; color: #1e293b;">
        📂 Project Discovery — {date_str}
    </h1>
    <p style="color: #64748b; font-size: 14px;">
        Found {len(suggestions)} potential project(s) in your email from the last {period}.
        Review below and add any that look useful to <code>config.yaml</code>.
    </p>
    """)

    # Each suggestion
    for i, s in enumerate(suggestions, 1):
        config = s.get("suggested_config", {})
        config_yaml = _format_config_yaml(config)
        sample_subjects = s.get("sample_subjects", [])
        samples_html = "".join(
            f"<li style='font-size: 13px; color: #475569;'>{subj}</li>"
            for subj in sample_subjects[:5]
        )

        sections.append(f"""
        <div style="background: #faf5ff; border-left: 4px solid #7c3aed;
                    border-radius: 6px; padding: 16px; margin: 16px 0;">
            <h2 style="font-size: 16px; margin: 0 0 4px; color: #1e293b;">
                {i}. {s.get('name', 'Unnamed')}
            </h2>
            <p style="color: #64748b; font-size: 13px; margin: 0 0 8px;">
                {s.get('email_count', '?')} emails · {s.get('date_range', '')}
            </p>
            <p style="font-size: 14px; margin: 0 0 12px;">
                {s.get('reasoning', '')}
            </p>
            <p style="font-size: 13px; font-weight: 600; margin: 0 0 4px;">
                Sample subjects:
            </p>
            <ul style="margin: 0 0 12px; padding-left: 20px;">
                {samples_html}
            </ul>
            <p style="font-size: 13px; font-weight: 600; margin: 0 0 4px;">
                Config to add to <code>config.yaml</code> under <code>projects:</code>
            </p>
            <pre style="background: #1e293b; color: #e2e8f0; padding: 12px;
                        border-radius: 4px; font-size: 12px; overflow-x: auto;
                        white-space: pre-wrap;">{config_yaml}</pre>
            <p style="font-size: 12px; color: #64748b; margin: 8px 0 0;">
                After adding, run:
                <code>python src/project_fetch.py --all --project {config.get('id', 'ID')}</code>
            </p>
        </div>
        """)

    # Footer
    sections.append(f"""
    <hr style="border: none; border-top: 1px solid #e2e8f0; margin-top: 24px;">
    <p style="color: #94a3b8; font-size: 12px;">
        Generated by Inbox Assistant — Project Discovery.
        Based on email from the last {period}.
        For a deeper scan: <code>python src/project_discover.py --hours 4320</code>
    </p>
    </div>
    """)

    return subject, "\n".join(sections)


def generate_report_markdown(suggestions: list[dict], date_str: str,
                             hours: int) -> str:
    """Generate an Obsidian Markdown report of project suggestions."""
    period = _describe_period(hours)
    lines = [
        f"# Project Discovery — {date_str}",
        "",
        f"Analysis of email from the last {period}. "
        f"Found **{len(suggestions)}** potential project(s).",
        "",
    ]

    if not suggestions:
        lines.append("No new project clusters found.")
        lines.append("")
        lines.append("For a deeper scan: `python src/project_discover.py --hours 4320`")
        return "\n".join(lines)

    for i, s in enumerate(suggestions, 1):
        config = s.get("suggested_config", {})
        config_yaml = _format_config_yaml(config)
        sample_subjects = s.get("sample_subjects", [])

        lines.append(f"## {i}. {s.get('name', 'Unnamed')}")
        lines.append("")
        lines.append(f"**{s.get('email_count', '?')} emails** · {s.get('date_range', '')}")
        lines.append("")
        lines.append(s.get("reasoning", ""))
        lines.append("")
        lines.append("**Sample subjects:**")
        for subj in sample_subjects[:5]:
            lines.append(f"- {subj}")
        lines.append("")
        lines.append("**Config for `config.yaml`:**")
        lines.append("")
        lines.append("```yaml")
        lines.append(config_yaml)
        lines.append("```")
        lines.append("")
        lines.append(
            f"After adding: `python src/project_fetch.py --all --project {config.get('id', 'ID')}`"
        )
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append(
        f"*Generated by Inbox Assistant — Project Discovery. "
        f"For a deeper scan: `python src/project_discover.py --hours 4320`*"
    )
    return "\n".join(lines)


def _format_config_yaml(config: dict) -> str:
    """Format a suggested config dict as a YAML block for copy-pasting."""
    lines = []
    lines.append(f"- id: {config.get('id', 'new-project')}")
    lines.append(f"  name: \"{config.get('name', 'New Project')}\"")
    lines.append(f"  vault_folder: \"{config.get('vault_folder', 'inbox-projects/new-project')}\"")
    if config.get("since"):
        lines.append(f"  since: \"{config['since']}\"")
    lines.append(f"  attachment_max_size_mb: 7")
    lines.append(f"  exclude_extensions: [\".ics\"]")

    keywords = config.get("keywords", [])
    if keywords:
        lines.append("  keywords:")
        for kw in keywords:
            lines.append(f"    - \"{kw}\"")

    collaborators = config.get("collaborators", [])
    if collaborators:
        lines.append("  collaborators:")
        for c in collaborators:
            name = c.get("name", "Unknown")
            fragment = c.get("email_fragment", "unknown")
            lines.append(f"    - name: \"{name}\"")
            lines.append(f"      email_fragment: \"{fragment}\"")

    return "\n".join(lines)


def _describe_period(hours: int) -> str:
    """Turn hours into a human-readable period string."""
    if hours <= 48:
        return f"{hours} hours"
    days = hours // 24
    if days <= 14:
        return f"{days} days"
    weeks = days // 7
    if weeks <= 8:
        return f"{weeks} weeks"
    months = days // 30
    return f"~{months} months"


# ── Obsidian note writing ────────────────────────────────────────────────────

def write_obsidian_note(markdown: str, vault_path: Path, dry_run: bool = False):
    """Write the discovery report as an Obsidian note.

    Writes to {vault_path}/inbox-briefings/project-suggestions/YYYY-MM-DD.md
    """
    now = datetime.now(ZoneInfo("Europe/Amsterdam"))
    date_str = now.strftime("%Y-%m-%d")

    dest_dir = vault_path / "inbox-briefings" / "project-suggestions"

    if dry_run:
        print(f"  Would write Obsidian note to: {dest_dir / f'{date_str}.md'}")
        return

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{date_str}.md"
    dest.write_text(markdown)
    print(f"  ✓ Obsidian note written: {dest}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Discover potential email projects by analysing recent email traffic."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview suggestions without sending email or writing files.",
    )
    parser.add_argument(
        "--hours", type=int, default=336,
        help="How many hours back to analyse (default: 336 = 2 weeks).",
    )
    parser.add_argument(
        "--min-emails", type=int, default=3,
        help="Minimum emails to consider something a project (default: 3).",
    )
    args = parser.parse_args()

    now = datetime.now(ZoneInfo("Europe/Amsterdam"))
    date_str = now.strftime("%-d %B %Y")

    print(f"── Project Discovery ({'DRY RUN' if args.dry_run else 'live'}) ──")
    print(f"   Analysing email from the last {_describe_period(args.hours)}")
    print(f"   Minimum emails per cluster: {args.min_emails}")

    # ── Load config ──────────────────────────────────────────────────────────
    config = load_config()
    project_root = Path(__file__).parent.parent

    # ── Check LLM task is configured ─────────────────────────────────────────
    llm_config = config.get("llm", {})
    tasks = llm_config.get("tasks", {})
    if "project_discovery" not in tasks:
        print("✗ LLM task 'project_discovery' not found in config.yaml.")
        print("  Add under llm.tasks:")
        print("    project_discovery:")
        print("      provider: \"anthropic\"")
        print("      model: \"claude-sonnet-4-6\"")
        sys.exit(1)

    llm = LLMClient(llm_config)

    # ── Authenticate Gmail ───────────────────────────────────────────────────
    gmail_cfg = config.get("gmail", {})
    credentials_file = project_root / gmail_cfg.get("credentials_file", "credentials.json")
    token_file = project_root / gmail_cfg.get("token_file", "token.json")

    gmail = GmailClient(
        credentials_file=str(credentials_file),
        token_file=str(token_file),
    )
    try:
        gmail.authenticate()
    except Exception as e:
        print(f"✗ Gmail authentication failed: {e}")
        print("  Run: python src/gmail_client.py --auth --headless")
        sys.exit(1)

    # ── Fetch emails ─────────────────────────────────────────────────────────
    global_labels = gmail_cfg.get("scan_labels", ["INBOX"])
    print(f"   Fetching emails...")

    try:
        emails = gmail.fetch_recent_emails(
            hours=args.hours,
            labels=global_labels,
            max_results=500,
        )
    except Exception as e:
        print(f"✗ Failed to fetch emails: {e}")
        sys.exit(1)

    print(f"   ✓ Fetched {len(emails)} emails.")

    if not emails:
        print("   No emails found in the given period. Nothing to analyse.")
        return

    # ── Prepare data ─────────────────────────────────────────────────────────
    existing_projects = load_existing_projects(config)
    if existing_projects:
        names = [p["name"] for p in existing_projects]
        print(f"   Excluding {len(existing_projects)} configured project(s): {', '.join(names)}")

    metadata = prepare_email_metadata(emails)

    # ── Analyse ──────────────────────────────────────────────────────────────
    print(f"   Analysing email patterns...")

    suggestions = discover_projects(llm, metadata, existing_projects, args.min_emails)

    print(f"   ✓ Found {len(suggestions)} potential project(s).")

    # ── Generate reports ─────────────────────────────────────────────────────
    subject, html = generate_report_html(suggestions, date_str, args.hours)
    markdown = generate_report_markdown(suggestions, date_str, args.hours)

    if args.dry_run:
        # Print the markdown report to stdout for preview
        print()
        print(markdown)
        print()

        # Also write a preview HTML file
        preview_path = project_root / "data" / "project-discover-preview.html"
        preview_path.write_text(html)
        print(f"   Preview HTML written to: {preview_path}")
        print("   (Dry run — no email sent, no Obsidian note written.)")
        return

    # ── Write Obsidian note ──────────────────────────────────────────────────
    obsidian_cfg = config.get("obsidian", {})
    vault_path_raw = obsidian_cfg.get("vault_path")
    if vault_path_raw:
        vault_path = Path(vault_path_raw).expanduser()
        if vault_path.exists():
            write_obsidian_note(markdown, vault_path)
        else:
            print(f"  ⚠ Obsidian vault path does not exist: {vault_path}")
    else:
        print("  ⚠ obsidian.vault_path not set — skipping Obsidian note.")

    # ── Send email ───────────────────────────────────────────────────────────
    briefing_cfg = config.get("briefing", {})
    send_to = briefing_cfg.get("send_to")
    if send_to:
        try:
            gmail.send_email(send_to, subject, html)
            print(f"   ✓ Report emailed to {send_to}")
        except Exception as e:
            print(f"  ✗ Failed to send email: {e}")
    else:
        print("  ⚠ briefing.send_to not set — skipping email delivery.")

    print(f"\n✓ Done. {len(suggestions)} project suggestion(s) reported.")


if __name__ == "__main__":
    main()
