#!/usr/bin/env python3
"""
Email Archiver — saves individual emails as Markdown notes in the Obsidian vault.

Used by fetch_and_triage.py (ongoing archiving of all non-noise emails) and
project_fetch.py (retroactive backfill). Produces a flat directory of markdown
files with rich YAML frontmatter for querying via Obsidian Bases or Dataview.

The archive state file (data/archive-state.json) tracks which gmail_ids have
already been archived to prevent duplicates across runs.

Directory: {vault_path}/{archive.vault_folder}/  (default: inbox-emails/)
"""

import json
import re
import sys
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import yaml


# ── Email address extraction ────────────────────────────────────────────────

def extract_email_address(addr_str: str) -> str:
    """Extract bare email address from 'Display Name <email@example.com>' format.

    Returns the lowercased email address, or the original string lowercased
    if no angle-bracket format is found.
    """
    if "<" in addr_str and ">" in addr_str:
        return addr_str.split("<")[1].split(">")[0].strip().lower()
    return addr_str.strip().lower()


def extract_all_contacts(email: dict) -> list[str]:
    """Extract all unique email addresses from from/to/cc fields.

    Returns a deduplicated, sorted list of bare email addresses.
    """
    contacts = set()
    for field in ("from", "to", "cc"):
        raw = email.get(field, "")
        if not raw:
            continue
        # Handle comma-separated lists of addresses
        for part in raw.split(","):
            part = part.strip()
            if part:
                addr = extract_email_address(part)
                if "@" in addr:
                    contacts.add(addr)
    return sorted(contacts)


# ── Project matching (extracted from project_fetch.py) ──────────────────────

def matches_project(email: dict, project: dict) -> bool:
    """Return True if this email is relevant to the given project.

    Matches on:
    - Subject contains any project keyword (case-insensitive), OR
    - Any collaborator name or email_fragment appears in from/to/cc (case-insensitive)
    """
    subject = email.get("subject", "").lower()
    from_to_cc = " ".join([
        email.get("from", ""),
        email.get("to", ""),
        email.get("cc", ""),
    ]).lower()

    for keyword in project.get("keywords", []):
        if keyword.lower() in subject:
            return True

    for collab in project.get("collaborators", []):
        name = collab.get("name", "").lower()
        fragment = collab.get("email_fragment", "").lower()
        if name and name in from_to_cc:
            return True
        if fragment and fragment in from_to_cc:
            return True

    return False


def find_matching_projects(email: dict, projects: list[dict]) -> list[str]:
    """Return list of project IDs that match this email."""
    return [p["id"] for p in projects if matches_project(email, p)]


# ── Filename sanitisation ───────────────────────────────────────────────────

# Collapses any chain of reply/forward prefixes (Re:, RE:, Fw:, FWD:, and
# common localised variants — Aw/Antw (DE/NL), Sv (SE), Vs (FI), Enc (PT),
# Tr (FR), Rv (ES)) into a single canonical "Re: ". Case-insensitive, and
# colon-optional (Exchange forwarding sometimes drops the colon). Applied
# only to the filename derivation; the original subject is preserved in
# frontmatter.
_REPLY_PREFIX_RE = re.compile(
    # Longest alternatives first (Python regex alternation is leftmost,
    # so "fw" would otherwise swallow only the first two chars of "FWD").
    r"^\s*(?:(?:fwd|antw|enc|re|fw|aw|sv|vs|tr|rv)\s*:?\s*)+",
    re.IGNORECASE,
)

# Max bytes for the subject portion of the filename. Total stem (date +
# subject + [gmail_id]) is hard-capped at _MAX_STEM_BYTES to leave room
# for Syncthing's "~YYYYMMDD-HHMMSS" versioning suffix under the NTFS
# 255-byte per-component limit.
_MAX_SUBJECT_BYTES = 120
_MAX_STEM_BYTES = 180


def _truncate_utf8(s: str, max_bytes: int) -> str:
    """Truncate ``s`` so its UTF-8 encoding is <= ``max_bytes``, without
    splitting a multibyte character. Tries to break on a word boundary."""
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    clipped = encoded[:max_bytes]
    # Back off until we're on a valid UTF-8 boundary
    while clipped and (clipped[-1] & 0xC0) == 0x80:
        clipped = clipped[:-1]
    out = clipped.decode("utf-8", errors="ignore")
    # Prefer trimming at the last whitespace for readability
    if " " in out:
        out = out.rsplit(" ", 1)[0]
    return out.strip(" -")


def sanitize_filename(date_str: str, subject: str, gmail_id: str = "") -> str:
    """Build a safe filename from a date string, email subject, and
    optional gmail_id.

    Format: ``YYYY-MM-DD Some Subject [abc123].md``

    The gmail_id discriminator (first 6 chars) makes the filename stable
    across re-imports and eliminates reliance on ``-2``/``-3`` numeric
    suffixes for uniqueness. It's only omitted when no id is passed
    (for backward compatibility with callers that don't have one).
    """
    if _REPLY_PREFIX_RE.match(subject):
        subject = "Re: " + _REPLY_PREFIX_RE.sub("", subject, count=1)

    clean = re.sub(r"[^\w\s-]", " ", subject, flags=re.UNICODE)
    clean = re.sub(r"\s+", " ", clean).strip(" -")
    if not clean:
        clean = "no-subject"
    clean = _truncate_utf8(clean, _MAX_SUBJECT_BYTES)

    disc = gmail_id[:6] if gmail_id else ""
    stem = f"{date_str} {clean}" + (f" [{disc}]" if disc else "")
    stem = _truncate_utf8(stem, _MAX_STEM_BYTES)
    return f"{stem}.md"


def resolve_filename(
    dest_dir: Path, date_str: str, subject: str, gmail_id: str = ""
) -> Path:
    """Return a Path that doesn't collide with existing files.

    Collision detection is case-insensitive so the result is safe on
    NTFS/APFS peers (they treat case-variants as the same file).
    """
    base_name = sanitize_filename(date_str, subject, gmail_id)
    stem = base_name[:-3]

    existing = set()
    if dest_dir.exists():
        existing = {p.name.lower() for p in dest_dir.iterdir() if p.is_file()}

    if base_name.lower() not in existing:
        return dest_dir / base_name
    for n in range(2, 100):
        candidate = f"{stem}-{n}.md"
        if candidate.lower() not in existing:
            return dest_dir / candidate
    raise RuntimeError(f"Could not find a free filename for '{base_name}' in {dest_dir}")


# ── Date parsing ────────────────────────────────────────────────────────────

def parse_email_date(date_header: str) -> tuple[datetime, str]:
    """Parse RFC 2822 date header to (datetime, 'YYYY-MM-DD' string).

    Falls back to today's date if parsing fails.
    """
    if date_header:
        try:
            dt = parsedate_to_datetime(date_header)
            return dt, dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    today = datetime.now()
    print(f"  ⚠ Could not parse date '{date_header}' — using today's date.")
    return today, today.strftime("%Y-%m-%d")


# ── Archive state (tracks which emails have been archived) ──────────────────

def load_archive_state(data_dir: Path) -> set[str]:
    """Load the set of already-archived gmail_ids.

    Returns an empty set if the file doesn't exist (first run).
    Exits loudly if the file is corrupted.
    """
    state_file = data_dir / "archive-state.json"
    if not state_file.exists():
        return set()
    try:
        data = json.loads(state_file.read_text())
        return set(data.get("archived_ids", []))
    except json.JSONDecodeError as e:
        print(f"✗ archive-state.json is corrupted: {e}")
        print(f"  Fix or delete {state_file} and re-run.")
        sys.exit(1)


def save_archive_state(data_dir: Path, archived_ids: set[str]) -> None:
    """Save the archive state file atomically."""
    state_file = data_dir / "archive-state.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "_updated": datetime.now().isoformat(),
        "count": len(archived_ids),
        "archived_ids": sorted(archived_ids)[-10000:],  # cap at 10k
    }
    tmp = data_dir / "archive-state.tmp"
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(state_file)


# ── Attachment handling ─────────────────────────────────────────────────────

def should_save_attachment(att: dict, category: str, archive_cfg: dict) -> tuple[bool, str | None]:
    """Decide whether to save this attachment. Returns (should_save, skip_reason).

    Args:
        att:         Attachment metadata dict from gmail_client._parse_message
        category:    Email classification category (URGENT, ACTION, FYI)
        archive_cfg: The archive: section from config.yaml
    """
    att_cfg = archive_cfg.get("attachments", {})

    # Skip inline attachments (signature images, logos)
    if att.get("is_inline", False):
        return False, "inline"

    # Check if we save attachments for this email category
    save_for = att_cfg.get("save_for", ["URGENT", "ACTION"])
    if category not in save_for:
        return False, f"skipped for {category} category"

    filename = att.get("filename", "")
    if not filename:
        return False, "missing filename"

    # Check excluded extensions
    ext = Path(filename).suffix.lower()
    exclude_ext = att_cfg.get("exclude_extensions", [".ics", ".vcf"])
    # Normalise to lowercase with leading dot
    exclude_ext = [e if e.startswith(".") else f".{e}" for e in exclude_ext]
    if ext in exclude_ext:
        return False, "excluded extension"

    # Check excluded MIME prefixes
    mime = att.get("mime_type", "").lower()
    for prefix in att_cfg.get("exclude_mime_prefixes", []):
        if mime.startswith(prefix.lower()):
            return False, "excluded MIME type"

    # Check size limit
    max_mb = att_cfg.get("max_size_mb", 10)
    max_bytes = int(max_mb * 1024 * 1024)
    size = att.get("size_bytes", 0)
    if size > max_bytes:
        size_mb = size / (1024 * 1024)
        return False, f"too large ({size_mb:.1f}MB > {max_mb}MB)"

    return True, None


def save_attachment(

    email: dict,
    att: dict,
    assets_dir: Path,
    gmail_client,
    dry_run: bool = False,
) -> tuple[str, Path] | None:
    """Download and save a single attachment. Returns (filename, saved_path) or None."""
    filename = att.get("filename", "").strip()
    att_id = att.get("attachment_id", "")
    if not filename or not att_id:
        return None

    safe_name = re.sub(r"[^\w\s.\-]", "_", filename).strip()
    save_path = assets_dir / safe_name

    # Collision handling
    if save_path.exists() and not dry_run:
        stem, suffix = save_path.stem, save_path.suffix
        for n in range(2, 100):
            candidate = assets_dir / f"{stem}_{n}{suffix}"
            if not candidate.exists():
                save_path = candidate
                break

    if dry_run:
        size = att.get("size_bytes", 0)
        size_str = f"{size / 1024:.0f} KB" if size else "unknown size"
        print(f"    [DRY RUN] Would save attachment: assets/{save_path.name} ({size_str})")
        return (filename, save_path)

    try:
        assets_dir.mkdir(parents=True, exist_ok=True)
        data = gmail_client.download_attachment(email["id"], att_id)
        save_path.write_bytes(data)
        size_str = f"{len(data) / 1024:.0f} KB"
        print(f"    Attachment: assets/{save_path.name} ({size_str})")
        return (filename, save_path)
    except Exception as e:
        print(f"    ✗ Failed to download attachment '{filename}': {e}")
        return None


# ── Frontmatter builder ────────────────────────────────────────────────────

def build_archive_frontmatter(
    email: dict,
    classification: dict | None,
    matching_projects: list[str],
    saved_attachment_names: list[str],
    skipped_attachments: list[str] | None = None,
    task_extracted: bool = False,
) -> str:
    """Build YAML frontmatter for an archived email note.

    Args:
        email:                 The email dict from gmail_client
        classification:        Classification result (may be None for backfill)
        matching_projects:     List of project IDs this email matches
        saved_attachment_names: List of filenames that were saved to assets/
        skipped_attachments:   List of strings describing skipped attachments (filename + reason)
        task_extracted:        Whether task_writer extracted tasks from this email
    """
    _, date_str = parse_email_date(email.get("date", ""))

    category = "ACTION"  # default for backfill / unclassified
    priority = "normal"
    language = "en"
    if classification:
        category = classification.get("category", "ACTION")
        priority = classification.get("priority", "normal")
        language = classification.get("reply_language", "en")

    contacts = extract_all_contacts(email)

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
    fields["category"] = category
    fields["priority"] = priority

    if matching_projects:
        fields["inbox-projects"] = matching_projects

    fields["contacts"] = contacts
    fields["language"] = language

    # Attachment tracking
    all_attachments = email.get("attachment_metadata", [])
    genuine_attachments = [a for a in all_attachments if not a.get("is_inline")]
    fields["has_attachments"] = len(genuine_attachments) > 0
    
    if saved_attachment_names:
        # Format as Obsidian WikiLinks so they are clickable in Properties view
        fields["saved_attachments"] = [f"[[assets/{name}]]" for name in saved_attachment_names]
    
    if skipped_attachments:
        fields["skipped_attachments"] = skipped_attachments

    # Task integration
    fields["task_extracted"] = task_extracted

    # Tags: inbox-email + category tag + project tags
    tags = ["inbox-email", f"inbox-{category.lower()}"]
    tags.extend(matching_projects)
    fields["tags"] = tags

    return "---\n" + yaml.dump(fields, allow_unicode=True, default_flow_style=False) + "---"


# ── Core archive function ──────────────────────────────────────────────────

class EmailArchiver:
    """Archives emails as Markdown notes in the Obsidian vault."""

    def __init__(self, config: dict, gmail_client=None):
        """
        Args:
            config:       Full config dict from config.yaml
            gmail_client: Authenticated GmailClient (needed for attachment downloads)
        """
        self.config = config
        self.gmail = gmail_client
        self.projects = config.get("projects", [])

        archive_cfg = config.get("archive", {})
        self.enabled = archive_cfg.get("enabled", False)
        self.archive_cfg = archive_cfg
        self.categories = set(
            archive_cfg.get("categories", ["URGENT", "ACTION", "FYI"])
        )

        # Resolve vault and archive paths
        obsidian_cfg = config.get("obsidian", {})
        vault_raw = obsidian_cfg.get("vault_path", "")
        self.vault_path = Path(vault_raw).expanduser() if vault_raw else None

        vault_folder = archive_cfg.get("vault_folder", "inbox-emails")
        mail_folder = archive_cfg.get("mail_folder", "mail")
        self.archive_base = self.vault_path / vault_folder if self.vault_path else None
        self.archive_dir = self.archive_base / mail_folder if self.archive_base else None
        self.assets_dir = self.archive_dir / archive_cfg.get("assets_folder", "assets") if self.archive_dir else None

        # Load state
        self.data_dir = Path(config.get("_project_root", ".")) / "data"
        self.archived_ids = load_archive_state(self.data_dir)

    def archive_email(
        self,
        email: dict,
        classification: dict | None = None,
        task_extracted: bool = False,
        dry_run: bool = False,
    ) -> Path | None:
        """Archive a single email as a Markdown note.

        Returns the path of the written file, or None if skipped.
        """
        if not self.enabled or not self.archive_dir:
            return None

        gmail_id = email.get("id", "")

        # Skip if already archived
        if gmail_id in self.archived_ids:
            return None

        # Check category filter
        category = "ACTION"  # default
        if classification:
            category = classification.get("category", "ACTION")
        if category not in self.categories:
            return None

        # Determine matching projects
        matching_projects = find_matching_projects(email, self.projects)

        # Handle attachments
        saved_names = []
        skipped_info = []
        att_links = []
        attachments = email.get("attachment_metadata", [])
        if attachments and self.gmail and self.assets_dir:
            for att in attachments:
                should_save, reason = should_save_attachment(att, category, self.archive_cfg)
                if should_save:
                    result = save_attachment(
                        email, att, self.assets_dir, self.gmail, dry_run=dry_run,
                    )
                    if result:
                        # Use the actual filename as saved on disk for the link
                        saved_names.append(result[1].name)
                        att_links.append(result)
                elif reason != "inline":  # Don't clutter with signature images
                    filename = att.get("filename", "unknown")
                    skipped_info.append(f"{filename} ({reason})")

        # Build note content
        frontmatter = build_archive_frontmatter(
            email, classification, matching_projects, saved_names,
            skipped_attachments=skipped_info,
            task_extracted=task_extracted,
        )

        
        # Build attachment callout (at the top)
        attachment_section = ""
        if att_links:
            links = "\n".join(
                f"- [[assets/{saved.name}|{name}]]"
                for name, saved in att_links
            )
            attachment_section = f"> [!paperclip] Attachments\n{links}\n\n"

        body = email.get("body_text", "").strip()
        content = f"{frontmatter}\n\n{attachment_section}{body}\n"

        # Resolve filename and write
        _, date_str = parse_email_date(email.get("date", ""))
        dest = resolve_filename(
            self.archive_dir,
            date_str,
            email.get("subject", "no-subject"),
            email.get("gmail_id", ""),
        )


        if dry_run:
            print(f"    [DRY RUN] Would archive: {dest.name}")
            return dest

        self.archive_dir.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(dest)

        # Update state
        self.archived_ids.add(gmail_id)

        return dest

    def save_state(self) -> None:
        """Persist the archive state to disk. Call after processing a batch."""
        save_archive_state(self.data_dir, self.archived_ids)

    def archive_batch(
        self,
        emails: list[dict],
        classifications: list[dict],
        task_email_ids: set[str] | None = None,
        dry_run: bool = False,
    ) -> int:
        """Archive a batch of emails. Returns count of files written.

        Args:
            emails:          List of email dicts
            classifications: Corresponding classification results
            task_email_ids:  Set of email IDs that had tasks extracted
            dry_run:         If True, preview without writing
        """
        if not self.enabled:
            return 0

        task_email_ids = task_email_ids or set()

        # Build classification lookup by email_id
        cls_lookup = {c["email_id"]: c for c in classifications}
        email_lookup = {e["id"]: e for e in emails}

        count = 0
        for cls in classifications:
            eid = cls.get("email_id", "")
            email = email_lookup.get(eid)
            if not email:
                continue

            result = self.archive_email(
                email,
                classification=cls,
                task_extracted=(eid in task_email_ids),
                dry_run=dry_run,
            )
            if result:
                count += 1

        if not dry_run and count > 0:
            self.save_state()
            self.regenerate_index()

        return count

    def regenerate_index(self) -> int:
        """Regenerate the JSON index from the archive directory.

        Returns the number of emails indexed.
        """
        if not self.archive_dir:
            return 0
        count = generate_index(self.archive_dir)
        print(f"  Updated _index.json: {count} emails indexed")
        return count


# ── Context retrieval for draft composition ─────────────────────────────────

def find_context_emails(
    archive_dir: Path,
    sender_email: str | None = None,
    thread_id: str | None = None,
    limit: int = 5,
    max_chars_per_email: int = 1500,
) -> str:
    """Find relevant archived emails for draft context.

    Searches the archive directory for emails matching the sender or thread,
    reads their content, and returns a combined context string.

    Args:
        archive_dir:          Path to the inbox-emails/ directory
        sender_email:         Email address to search in contacts field
        thread_id:            Gmail thread ID to match
        limit:                Max number of emails to return
        max_chars_per_email:  Truncation limit per email body

    Returns a single string with email bodies separated by '---' markers,
    suitable for injecting into an LLM prompt. Returns "" if no matches found.
    """
    if not archive_dir or not archive_dir.exists():
        return ""

    md_files = sorted(archive_dir.glob("*.md"), reverse=True)  # newest first
    if not md_files:
        return ""

    sender_email = sender_email.lower().strip() if sender_email else None
    matches = []

    for path in md_files:
        if len(matches) >= limit:
            break

        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        # Quick frontmatter extraction — only parse if it starts with ---
        if not text.startswith("---"):
            continue

        end_idx = text.find("---", 3)
        if end_idx == -1:
            continue

        frontmatter_text = text[3:end_idx]
        body = text[end_idx + 3:].strip()

        try:
            fm = yaml.safe_load(frontmatter_text)
        except yaml.YAMLError:
            continue

        if not isinstance(fm, dict):
            continue

        # Check thread match (highest priority)
        if thread_id and fm.get("thread_id") == thread_id:
            matches.append((fm.get("date", ""), fm, body))
            continue

        # Check sender match
        if sender_email:
            contacts = fm.get("contacts", [])
            if isinstance(contacts, list) and sender_email in contacts:
                matches.append((fm.get("date", ""), fm, body))
                continue

    if not matches:
        return ""

    # Sort by date (newest first), take limit
    matches.sort(key=lambda x: x[0], reverse=True)
    matches = matches[:limit]

    parts = []
    for date_str, fm, body in matches:
        truncated = body[:max_chars_per_email]
        parts.append(
            f"From: {fm.get('from', 'unknown')}\n"
            f"Date: {fm.get('date', 'unknown')}\n"
            f"Subject: {fm.get('subject', '')}\n\n"
            f"{truncated}\n"
        )

    return "\n---\n".join(parts)


# ── JSON index for agent context retrieval ───────────────────────────────────

def generate_index(archive_dir: Path, index_path: Path | None = None) -> int:
    """Generate a JSON index of all archived email frontmatter.

    Scans all .md files in archive_dir, extracts frontmatter, and writes a
    compact JSON index. Agents can read this single file to search the archive
    efficiently without opening hundreds of individual files.

    Args:
        archive_dir: Path to the mail/ directory containing archived .md files
        index_path:  Where to write the index. Defaults to archive_dir/../_index.json

    Returns the number of emails indexed.
    """
    if not archive_dir or not archive_dir.exists():
        return 0

    if index_path is None:
        index_path = archive_dir.parent / "_index.json"

    md_files = sorted(archive_dir.glob("*.md"))
    entries = []

    for path in md_files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        if not text.startswith("---"):
            continue

        end_idx = text.find("---", 3)
        if end_idx == -1:
            continue

        try:
            fm = yaml.safe_load(text[3:end_idx])
        except yaml.YAMLError:
            continue

        if not isinstance(fm, dict):
            continue

        # Build a compact index entry — frontmatter fields only, no body
        # Normalize date to string (YAML may parse dates as datetime.date)
        raw_date = fm.get("date", "")
        date_str = str(raw_date) if raw_date else ""

        entry = {
            "file": path.name,
            "date": date_str,
            "subject": fm.get("subject", ""),
            "from": fm.get("from", ""),
            "category": fm.get("category", ""),
            "inbox-projects": fm.get("inbox-projects", []),
            "contacts": fm.get("contacts", []),
            "thread_id": fm.get("thread_id", ""),
            "gmail_id": fm.get("gmail_id", ""),
            "has_attachments": fm.get("has_attachments", False),
            "tags": fm.get("tags", []),
        }
        entries.append(entry)

    # Sort by date descending (newest first)
    entries.sort(key=lambda e: e.get("date", ""), reverse=True)

    # Write atomically
    index_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = index_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(index_path)

    return len(entries)
