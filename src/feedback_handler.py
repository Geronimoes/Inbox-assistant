"""
BCC feedback loop processor.

Processes emails from staging/feedback/ — these are copies of emails the
professor sent, captured by including the BCC alias (yourname+inbox-log@gmail.com)
in the BCC field when replying.

Two things happen for each feedback email:
  1. Thread status update: the original thread is marked as "handled" in
     data/processed.json, so it stops appearing in future briefings.
  2. Writing sample: the professor's outgoing email text is saved to
     writing-samples/samples/ to feed the style profile over time.

Usage:
    # Normally called automatically by fetch_and_triage.py on startup.
    # For standalone testing:
    python src/feedback_handler.py --test-email staging/feedback/sample.json
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


# Patterns that mark the start of quoted/forwarded text in email bodies.
# Anything below these lines is stripped before saving as a writing sample.
_QUOTE_MARKERS = [
    r"^On .+ wrote:$",          # "On Thu, 27 Mar 2026, Marta wrote:"
    r"^-{3,}",                   # "--- Original Message ---" or "---"
    r"^_{3,}",                   # "___ Forwarded message ___"
    r"^From: .+$",               # Start of forwarded block
    r"^>",                       # Quoted line ("> text")
]
_QUOTE_PATTERN = re.compile(
    "|".join(_QUOTE_MARKERS), re.MULTILINE | re.IGNORECASE
)


def strip_quoted_text(body: str) -> str:
    """Remove quoted/forwarded text from an email body.

    Finds the first line that looks like a quote marker and discards
    everything from that line onwards. Returns the trimmed professor's text.
    """
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if _QUOTE_PATTERN.match(line.strip()):
            # Keep everything above this line
            return "\n".join(lines[:i]).strip()
    return body.strip()


def _slugify(text: str, max_len: int = 40) -> str:
    """Convert a string to a filename-safe slug."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    return slug[:max_len]


def process_feedback_file(
    path: Path,
    samples_dir: Path,
    thread_state: dict,
) -> dict | None:
    """Process a single feedback JSON file from staging/feedback/.

    Args:
        path:         Path to the feedback JSON file.
        samples_dir:  Where to save the writing sample (.txt file).
        thread_state: The current threads dict from processed.json (modified in-place).

    Returns:
        A summary dict {thread_id, subject, action} if successful, else None.
    """
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ⚠ Skipping {path.name}: {e}")
        return None

    email_body = data.get("body_text", "").strip()
    thread_id = data.get("thread_id", "")
    subject = data.get("subject", "(no subject)")
    email_id = data.get("id", "")
    date_str = data.get("date", "")

    if not email_body:
        print(f"  ⚠ Skipping {path.name}: empty body_text")
        return None

    # ── 1. Save writing sample ──────────────────────────────
    sent_text = strip_quoted_text(email_body)

    if sent_text and len(sent_text) > 20:  # skip trivially short texts
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        subject_slug = _slugify(subject)
        sample_filename = f"{timestamp}-{subject_slug}.txt"
        sample_path = samples_dir / sample_filename

        samples_dir.mkdir(parents=True, exist_ok=True)

        # Save with metadata header for context
        header = (
            f"# Sent email — captured via BCC\n"
            f"# Subject: {subject}\n"
            f"# Date: {date_str}\n"
            f"# Thread: {thread_id}\n\n"
        )
        sample_path.write_text(header + sent_text)
        print(f"  ✓ Writing sample saved: {sample_filename}")
    else:
        print(f"  ⚠ Body too short to save as sample: {subject[:50]!r}")

    # ── 2. Mark thread as handled ───────────────────────────
    action = "new"
    if thread_id and thread_id in thread_state:
        thread_state[thread_id]["status"] = "handled"
        thread_state[thread_id]["handled_at"] = datetime.now().isoformat()
        action = "marked-handled"
        print(f"  ✓ Thread marked handled: {subject[:50]!r}")
    elif thread_id:
        # Thread wasn't tracked (maybe classified before this feature existed,
        # or email came from outside the monitored window). Record it anyway.
        thread_state[thread_id] = {
            "email_id": email_id,
            "status": "handled",
            "subject": subject,
            "classified_at": None,
            "handled_at": datetime.now().isoformat(),
        }
        action = "recorded-as-handled"
        print(f"  ✓ Thread recorded as handled: {subject[:50]!r}")

    # ── 3. Move feedback file to processed/ ─────────────────
    processed_dir = path.parent / "processed"
    processed_dir.mkdir(exist_ok=True)
    path.rename(processed_dir / path.name)

    return {"thread_id": thread_id, "subject": subject, "action": action}


def process_feedback_dir(
    feedback_dir: Path,
    samples_dir: Path,
    thread_state: dict,
) -> list[dict]:
    """Process all pending feedback files in staging/feedback/.

    Returns a list of summary dicts for each processed file.
    """
    json_files = sorted(feedback_dir.glob("*.json"))
    if not json_files:
        return []

    print(f"\n── Processing {len(json_files)} feedback email(s) from BCC loop...")
    results = []
    for path in json_files:
        result = process_feedback_file(path, samples_dir, thread_state)
        if result:
            results.append(result)

    return results


# ── CLI test mode ─────────────────────────────────────────────────────────────

def _run_test(test_path: Path, project_root: Path) -> None:
    """Process a single test feedback file and report results."""
    if not test_path.exists():
        print(f"ERROR: File not found: {test_path}")
        sys.exit(1)

    samples_dir = project_root / "writing-samples" / "samples"
    thread_state: dict = {}

    print(f"\nProcessing test feedback file: {test_path}\n")
    result = process_feedback_file(test_path, samples_dir, thread_state)

    if result:
        print(f"\nResult: {result}")
        print(f"\nThread state after processing:")
        print(json.dumps(thread_state, indent=2))
        print(f"\nSamples saved to: {samples_dir}")
        sample_files = sorted(samples_dir.glob("*.txt"))
        if sample_files:
            latest = sample_files[-1]
            print(f"\nLatest sample ({latest.name}):")
            print("─" * 40)
            print(latest.read_text()[:500])
    else:
        print("Processing failed — see errors above.")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BCC feedback loop processor")
    parser.add_argument(
        "--test-email",
        metavar="PATH",
        help="Process a single feedback JSON file for testing",
    )
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent

    if args.test_email:
        _run_test(Path(args.test_email), project_root)
    else:
        parser.print_help()
