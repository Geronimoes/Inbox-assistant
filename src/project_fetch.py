#!/usr/bin/env python3
"""
Project Email Archiver — saves project-related emails to the Obsidian vault.

Fetches emails from Gmail (using the same label configured in config.yaml)
and filters them by project keywords and collaborator names/email addresses.
Each matching email is saved as a Markdown file in a project-specific folder
inside the Obsidian vault, with full metadata in YAML frontmatter.

Files are written once and never overwritten — the state file
(data/project-export-state.json) tracks which emails have already been
exported so re-runs only process new mail.

Usage:
    python src/project_fetch.py               # all projects, last 24h
    python src/project_fetch.py --all         # full history (up to 500 emails)
    python src/project_fetch.py --hours 72    # custom lookback window
    python src/project_fetch.py --project wicked-problems
    python src/project_fetch.py --dry-run     # preview without writing files

Optional cron (add manually once you're happy with the output):
    0 20 * * * cd /home/jeroen/projects/inbox-assistant && env/bin/python src/project_fetch.py >> logs/project-fetch.log 2>&1

NOTE: The Gmail API returns at most 500 emails per call. The --all flag uses
the maximum (500). For very large inboxes a full backfill may miss the oldest
emails. Pagination support is a future enhancement.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import yaml

# Import GmailClient from the same src/ directory.
# Run this script from the project root: python src/project_fetch.py
sys.path.insert(0, str(Path(__file__).parent))
from gmail_client import GmailClient


# ── Config loading ───────────────────────────────────────────────────────────

def load_config() -> dict:
    """Load configuration from config.yaml (project root)."""
    config_path = Path(__file__).parent.parent / "config.yaml"
    if not config_path.exists():
        print("✗ config.yaml not found. Copy config.example.yaml and fill it in.")
        sys.exit(1)
    return yaml.safe_load(config_path.read_text())


# ── Export state (tracks which emails have already been saved) ───────────────

def load_export_state(data_dir: Path) -> dict:
    """Load the export state file.

    Returns a dict mapping project ID → list of already-exported email IDs.
    Returns an empty dict if the file doesn't exist yet (first run).
    Exits loudly if the file exists but contains invalid JSON — we never
    silently discard state, as that would cause emails to be re-exported.
    """
    state_file = data_dir / "project-export-state.json"
    if not state_file.exists():
        return {}
    try:
        return json.loads(state_file.read_text())
    except json.JSONDecodeError as e:
        print(f"✗ project-export-state.json is corrupted: {e}")
        print(f"  Fix or delete {state_file} and re-run.")
        sys.exit(1)


def save_export_state(data_dir: Path, state: dict) -> None:
    """Save the export state file atomically (.tmp → rename)."""
    state_file = data_dir / "project-export-state.json"
    payload = dict(state)
    payload["_updated"] = datetime.now().isoformat()
    tmp = data_dir / "project-export-state.tmp"
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(state_file)


# ── Email matching ───────────────────────────────────────────────────────────

def matches_project(email: dict, project: dict) -> bool:
    """Return True if this email is relevant to the given project.

    Matches on:
    - Subject contains any project keyword (case-insensitive), OR
    - Any collaborator name or email_fragment appears in from/to/cc (case-insensitive)
    """
    subject = email.get("subject", "").lower()
    # Build a single string of all address fields for collaborator matching
    from_to_cc = " ".join([
        email.get("from", ""),
        email.get("to", ""),
        email.get("cc", ""),
    ]).lower()

    # Check keywords against subject
    for keyword in project.get("keywords", []):
        if keyword.lower() in subject:
            return True

    # Check collaborators against from/to/cc
    for collab in project.get("collaborators", []):
        name = collab.get("name", "").lower()
        fragment = collab.get("email_fragment", "").lower()
        if name and name in from_to_cc:
            return True
        if fragment and fragment in from_to_cc:
            return True

    return False


# ── Filename sanitisation ────────────────────────────────────────────────────

def sanitize_filename(date_str: str, subject: str) -> str:
    """Build a safe filename from a date string and email subject.

    Format: "YYYY-MM-DD Some Subject Here.md"
    - Replaces non-alphanumeric characters (except spaces and hyphens) with spaces
    - Collapses multiple spaces into one
    - Truncates subject at 60 characters on a word boundary
    - Strips leading/trailing whitespace and hyphens from the subject part
    """
    # Keep letters, digits, spaces, hyphens. Replace everything else with space.
    clean = re.sub(r"[^\w\s-]", " ", subject, flags=re.UNICODE)
    # Collapse multiple spaces
    clean = re.sub(r"\s+", " ", clean).strip()
    # Truncate at word boundary within 60 chars
    if len(clean) > 60:
        clean = clean[:60].rsplit(" ", 1)[0]
    clean = clean.strip(" -")
    if not clean:
        clean = "no-subject"
    return f"{date_str} {clean}.md"


def resolve_filename(dest_dir: Path, date_str: str, subject: str) -> Path:
    """Return a Path that doesn't collide with existing files.

    If YYYY-MM-DD Subject.md already exists, tries
    YYYY-MM-DD Subject-2.md, -3, ... up to -99.
    """
    base_name = sanitize_filename(date_str, subject)
    stem = base_name[:-3]  # strip .md
    candidate = dest_dir / base_name
    if not candidate.exists():
        return candidate
    for n in range(2, 100):
        candidate = dest_dir / f"{stem}-{n}.md"
        if not candidate.exists():
            return candidate
    # Practically impossible, but fail clearly rather than overwriting
    raise RuntimeError(f"Could not find a free filename for '{base_name}' in {dest_dir}")


# ── Note writing ─────────────────────────────────────────────────────────────

def parse_email_date(date_header: str, project_name: str) -> tuple[datetime, str]:
    """Parse RFC 2822 date header to (datetime, 'YYYY-MM-DD' string).

    Falls back to today's date with a warning if parsing fails (some automated
    senders use non-standard formats). We always write the note — a wrong date
    in the filename is recoverable; a crash is not.
    """
    if date_header:
        try:
            dt = parsedate_to_datetime(date_header)
            # Convert to UTC-aware, then to local date string
            return dt, dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    today = datetime.now()
    print(f"  ⚠ Could not parse date '{date_header}' — using today's date.")
    return today, today.strftime("%Y-%m-%d")


def build_frontmatter(email: dict, project: dict, date_str: str) -> str:
    """Build YAML frontmatter block for the Obsidian note."""
    # Collect fields; omit cc if empty to keep notes tidy
    fields = {
        "date": date_str,
        "subject": email.get("subject", "(no subject)"),
        "from": email.get("from", ""),
        "to": email.get("to", ""),
    }
    cc = email.get("cc", "").strip()
    if cc:
        fields["cc"] = cc
    fields["thread_id"] = email.get("thread_id", "")
    fields["gmail_id"] = email.get("id", "")
    fields["project"] = project["name"]
    fields["tags"] = ["project-email", project["id"]]

    # Use yaml.dump for correct quoting, then strip the trailing newline
    return "---\n" + yaml.dump(fields, allow_unicode=True, default_flow_style=False) + "---"


def write_email_note(
    email: dict,
    project: dict,
    vault_path: Path,
    dry_run: bool,
) -> Path:
    """Write one email as a Markdown note to the project vault folder.

    Returns the path of the file written (or that would have been written).
    """
    _, date_str = parse_email_date(email.get("date", ""), project["name"])
    dest_dir = vault_path / project["vault_folder"]
    dest = resolve_filename(dest_dir, date_str, email.get("subject", "no-subject"))

    if dry_run:
        print(f"  [DRY RUN] Would write: {dest}")
        return dest

    dest_dir.mkdir(parents=True, exist_ok=True)

    frontmatter = build_frontmatter(email, project, date_str)
    body = email.get("body_text", "").strip()
    content = frontmatter + "\n\n" + body + "\n"

    # Atomic write: write to .tmp first, then rename
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(dest)

    print(f"  Written: {dest}")
    return dest


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Export project-related emails from Gmail to Obsidian Markdown notes."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview matches and file paths without writing anything.",
    )
    parser.add_argument(
        "--project", metavar="PROJECT_ID",
        help="Only process this project ID (default: all projects in config).",
    )
    parser.add_argument(
        "--hours", type=int, default=None,
        help="How many hours back to fetch (overrides config lookback_hours).",
    )
    parser.add_argument(
        "--all", dest="fetch_all", action="store_true",
        help="Fetch all available history (uses max_results=500, ~5 year window).",
    )
    args = parser.parse_args()

    # ── Load config ──────────────────────────────────────────────────────────
    config = load_config()
    project_root = Path(__file__).parent.parent
    data_dir = project_root / "data"

    projects_config = config.get("projects")
    if not projects_config:
        print("✗ No 'projects' section found in config.yaml.")
        print("  Add a projects: section — see config.example.yaml for the format.")
        sys.exit(1)

    # ── Resolve vault path ───────────────────────────────────────────────────
    obsidian_cfg = config.get("obsidian", {})
    vault_path_raw = obsidian_cfg.get("vault_path")
    if not vault_path_raw:
        print("✗ obsidian.vault_path is not set in config.yaml.")
        sys.exit(1)
    vault_path = Path(vault_path_raw).expanduser()
    if not vault_path.exists():
        print(f"✗ Obsidian vault path does not exist: {vault_path}")
        print("  Check the vault_path setting in config.yaml.")
        sys.exit(1)

    # ── Select projects ──────────────────────────────────────────────────────
    if args.project:
        selected = [p for p in projects_config if p["id"] == args.project]
        if not selected:
            known_ids = [p["id"] for p in projects_config]
            print(f"✗ Project '{args.project}' not found in config.yaml.")
            print(f"  Known project IDs: {', '.join(known_ids)}")
            sys.exit(1)
    else:
        selected = projects_config

    # ── Load state ───────────────────────────────────────────────────────────
    state = load_export_state(data_dir)

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

    # ── Process each project ─────────────────────────────────────────────────
    global_labels = gmail_cfg.get("scan_labels", ["INBOX"])
    default_lookback = gmail_cfg.get("lookback_hours", 24)

    total_written = 0

    for project in selected:
        print(f"\n── Project: {project['name']} ({'DRY RUN' if args.dry_run else 'live'}) ──")

        # Determine fetch window
        if args.fetch_all:
            hours = 5 * 365 * 24  # ~5 years
            max_results = 500     # Gmail API maximum per call
        elif args.hours is not None:
            hours = args.hours
            max_results = 100
        else:
            hours = default_lookback
            max_results = 100

        # Use project-specific labels if specified, else global default
        labels = project.get("scan_labels", global_labels)

        # Fetch emails from Gmail
        try:
            emails = gmail.fetch_recent_emails(hours=hours, labels=labels, max_results=max_results)
        except Exception as e:
            print(f"  ✗ Failed to fetch emails: {e}")
            continue

        # Filter out already-exported emails
        already_exported = set(state.get(project["id"], []))
        new_emails = [e for e in emails if e["id"] not in already_exported]

        # Filter to project-relevant emails
        matched = [e for e in new_emails if matches_project(e, project)]

        print(f"   Fetched {len(emails)}, {len(matched)} matched, "
              f"{len(emails) - len(new_emails)} already exported → {len(matched)} new")

        if not matched:
            print("   Nothing new to export.")
            continue

        # Write notes
        exported_ids = list(already_exported)
        for email in matched:
            try:
                write_email_note(email, project, vault_path, dry_run=args.dry_run)
                if not args.dry_run:
                    exported_ids.append(email["id"])
                    total_written += 1
            except Exception as e:
                print(f"  ✗ Failed to write note for email {email['id']}: {e}")

        # Save state after each project (so a crash mid-run doesn't lose progress)
        if not args.dry_run:
            state[project["id"]] = exported_ids
            save_export_state(data_dir, state)

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n✓ Done. {total_written} note(s) written to {vault_path}.")
    if args.dry_run:
        print("  (Dry run — no files were written and state was not updated.)")


if __name__ == "__main__":
    main()
