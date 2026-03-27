"""
Attachment handler for the Inbox Assistant.

Processes email attachments that n8n has saved to disk. For each attachment:
  1. Extracts text (PDFs, Word docs) or structured data (.ics calendar events)
  2. Classifies the content using an LLM
  3. Moves the file to the correct attachments/ subdirectory
  4. Returns a summary dict for inclusion in the briefing

Supported types:
  - PDF  (.pdf)         — text extraction + LLM classification
  - Word (.docx)        — text extraction + LLM classification
  - Calendar (.ics)     — structured event parsing (no LLM needed)
  - Other               — filename/MIME classification only (no text extraction)

Usage:
    from attachment_handler import AttachmentHandler
    handler = AttachmentHandler(project_root, llm_client)
    summaries = handler.process_email_attachments(email_id, attachments)

Test:
    python src/attachment_handler.py --test-file attachments/other/sample.pdf
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path


# Maps attachment category names to subdirectory names
CATEGORY_DIRS = {
    "paper":      "papers",
    "submission": "submissions",
    "form":       "forms",
    "invoice":    "invoices",
    "document":   "other",
    "calendar":   "calendar",
    "other":      "other",
}

# MIME types that can be processed for text
PDF_MIMES   = {"application/pdf"}
DOCX_MIMES  = {"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
               "application/msword"}
CAL_MIMES   = {"text/calendar", "application/ics"}

# Max characters of extracted text to send to the LLM
TEXT_EXTRACT_LIMIT = 2000


class AttachmentHandler:
    """Classify and file email attachments."""

    def __init__(self, project_root: Path, llm_client=None):
        """
        Args:
            project_root: Root directory of the inbox-assistant project.
            llm_client:   LLMClient instance. If None, LLM classification
                          is skipped (file is moved but not classified).
        """
        self.attachments_root = project_root / "attachments"
        self.prompt_path = project_root / "prompts" / "attachment_classify.md"
        self.llm = llm_client
        self._classify_prompt: str | None = None

    # ── Public interface ──────────────────────────────────────────────────────

    def process_email_attachments(
        self, email_id: str, attachments: list[dict]
    ) -> list[dict]:
        """Process all attachments for a single email.

        Args:
            email_id:    The email's ID (used to link summaries back to emails).
            attachments: List of attachment dicts from the staging JSON.
                         Each must have 'filename', 'mime_type', 'local_path'.

        Returns:
            List of summary dicts — one per successfully processed attachment.
        """
        summaries = []
        for att in attachments:
            local_path_str = att.get("local_path", "")
            filename = att.get("filename", "unknown")
            mime_type = att.get("mime_type", "application/octet-stream")

            if not local_path_str:
                # n8n didn't download this attachment yet — skip
                summaries.append({
                    "email_id": email_id,
                    "filename": filename,
                    "mime_type": mime_type,
                    "category": "other",
                    "summary": f"{filename} — not yet downloaded",
                    "saved_to": None,
                    "calendar_event": None,
                })
                continue

            local_path = Path(local_path_str)
            if not local_path.exists():
                print(f"  ⚠ Attachment not found on disk: {local_path}")
                continue

            summary = self._process_one(email_id, local_path, filename, mime_type)
            if summary:
                summaries.append(summary)

        return summaries

    # ── Per-attachment processing ─────────────────────────────────────────────

    def _process_one(
        self, email_id: str, path: Path, filename: str, mime_type: str
    ) -> dict | None:
        """Process a single attachment file."""
        ext = path.suffix.lower()

        # Dispatch by type
        if mime_type in CAL_MIMES or ext == ".ics":
            return self._process_calendar(email_id, path, filename)

        elif mime_type in PDF_MIMES or ext == ".pdf":
            text = self._extract_pdf_text(path)
            return self._classify_and_file(email_id, path, filename, mime_type, text)

        elif mime_type in DOCX_MIMES or ext == ".docx":
            text = self._extract_docx_text(path)
            return self._classify_and_file(email_id, path, filename, mime_type, text)

        else:
            # Unknown type — file as 'other' without text extraction
            return self._classify_and_file(
                email_id, path, filename, mime_type, text=""
            )

    def _classify_and_file(
        self,
        email_id: str,
        path: Path,
        filename: str,
        mime_type: str,
        text: str,
    ) -> dict:
        """Classify an attachment via LLM, move it to the right folder, return summary."""
        category = "other"
        summary_text = f"{filename}"

        if self.llm and (text or filename):
            category, summary_text = self._llm_classify(filename, mime_type, text)

        # Move to the appropriate subdirectory
        dest_dir = self.attachments_root / CATEGORY_DIRS.get(category, "other")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = self._unique_dest(dest_dir, filename)
        shutil.move(str(path), str(dest_path))

        print(f"  📎 {filename} → {CATEGORY_DIRS.get(category, 'other')}/ [{category}]")

        return {
            "email_id": email_id,
            "filename": filename,
            "mime_type": mime_type,
            "category": category,
            "summary": summary_text,
            "saved_to": str(dest_path),
            "calendar_event": None,
        }

    def _process_calendar(
        self, email_id: str, path: Path, filename: str
    ) -> dict | None:
        """Parse a .ics calendar invitation and return structured event data."""
        try:
            from icalendar import Calendar
        except ImportError:
            print("  ⚠ icalendar not installed — skipping .ics parsing.")
            print("    Run: pip install icalendar")
            return self._classify_and_file(
                email_id, path, filename, "text/calendar", text=""
            )

        try:
            cal = Calendar.from_ical(path.read_bytes())
        except Exception as e:
            print(f"  ⚠ Could not parse {filename}: {e}")
            return None

        event_data: dict = {}
        for component in cal.walk():
            if component.name == "VEVENT":
                def _str(key: str) -> str:
                    val = component.get(key)
                    return str(val) if val else ""

                dtstart = component.get("DTSTART")
                dtend   = component.get("DTEND")

                event_data = {
                    "summary":   _str("SUMMARY"),
                    "dtstart":   str(dtstart.dt) if dtstart else "",
                    "dtend":     str(dtend.dt)   if dtend   else "",
                    "organizer": _str("ORGANIZER").replace("mailto:", ""),
                    "location":  _str("LOCATION"),
                    "description": _str("DESCRIPTION")[:200],
                }
                break  # take the first VEVENT

        # Move to calendar directory
        dest_dir = self.attachments_root / "calendar"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = self._unique_dest(dest_dir, filename)
        shutil.move(str(path), str(dest_path))

        event_summary = event_data.get("summary", filename)
        start = event_data.get("dtstart", "")
        organizer = event_data.get("organizer", "")
        human_summary = f"Meeting invite: {event_summary}"
        if start:
            human_summary += f" ({start[:10]})"
        if organizer:
            human_summary += f" — from {organizer}"

        print(f"  📅 {filename} → calendar/ [{event_summary[:50]}]")

        return {
            "email_id": email_id,
            "filename": filename,
            "mime_type": "text/calendar",
            "category": "calendar",
            "summary": human_summary,
            "saved_to": str(dest_path),
            "calendar_event": event_data,
        }

    # ── Text extraction ───────────────────────────────────────────────────────

    def _extract_pdf_text(self, path: Path) -> str:
        """Extract plain text from a PDF file using pdfminer.six."""
        try:
            from pdfminer.high_level import extract_text
        except ImportError:
            print("  ⚠ pdfminer.six not installed — PDF text extraction skipped.")
            print("    Run: pip install pdfminer.six")
            return ""

        try:
            text = extract_text(str(path))
            return (text or "").strip()[:TEXT_EXTRACT_LIMIT]
        except Exception as e:
            print(f"  ⚠ Could not extract text from {path.name}: {e}")
            return ""

    def _extract_docx_text(self, path: Path) -> str:
        """Extract plain text from a .docx file using python-docx."""
        try:
            from docx import Document
        except ImportError:
            print("  ⚠ python-docx not installed — Word text extraction skipped.")
            print("    Run: pip install python-docx")
            return ""

        try:
            doc = Document(str(path))
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            return text[:TEXT_EXTRACT_LIMIT]
        except Exception as e:
            print(f"  ⚠ Could not extract text from {path.name}: {e}")
            return ""

    # ── LLM classification ────────────────────────────────────────────────────

    def _llm_classify(
        self, filename: str, mime_type: str, text: str
    ) -> tuple[str, str]:
        """Ask the LLM to classify an attachment. Returns (category, summary)."""
        if self._classify_prompt is None:
            if not self.prompt_path.exists():
                print(f"  ⚠ Attachment classify prompt not found: {self.prompt_path}")
                return "other", filename
            self._classify_prompt = self.prompt_path.read_text()

        user_message = (
            f"Filename: {filename}\n"
            f"MIME type: {mime_type}\n"
            f"Text extract:\n{text[:TEXT_EXTRACT_LIMIT] if text else '(no text extracted)'}"
        )

        try:
            response = self.llm.complete(
                "attachment_classify",
                system_prompt=self._classify_prompt,
                user_message=user_message,
                max_tokens=100,
            )
        except Exception as e:
            print(f"  ⚠ LLM classification failed for {filename}: {e}")
            return "other", filename

        # Strip markdown code fences if present
        response = response.strip()
        if response.startswith("```"):
            response = response.split("\n", 1)[1].rsplit("```", 1)[0]

        try:
            result = json.loads(response)
            category = result.get("category", "other")
            summary  = result.get("summary", filename)
            # Validate category is a known value
            if category not in CATEGORY_DIRS:
                category = "other"
            return category, summary
        except json.JSONDecodeError:
            print(f"  ⚠ Could not parse classification response for {filename}")
            return "other", filename

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _unique_dest(dest_dir: Path, filename: str) -> Path:
        """Return a destination path that doesn't overwrite an existing file."""
        dest = dest_dir / filename
        if not dest.exists():
            return dest
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        counter = 1
        while dest.exists():
            dest = dest_dir / f"{stem}-{counter}{suffix}"
            counter += 1
        return dest


# ── CLI test mode ─────────────────────────────────────────────────────────────

def _run_test(test_file: Path, project_root: Path) -> None:
    """Process a single attachment file and report the result."""
    import yaml

    config_path = project_root / "config.yaml"
    llm = None
    if config_path.exists():
        config = yaml.safe_load(config_path.read_text())
        llm_cfg = config.get("llm")
        if llm_cfg:
            sys.path.insert(0, str(project_root / "src"))
            from llm_client import LLMClient
            llm = LLMClient(llm_cfg)
            print(f"LLM client loaded from config.yaml")
    else:
        print("No config.yaml found — running without LLM classification.")

    handler = AttachmentHandler(project_root, llm_client=llm)

    # Copy the test file to a temp location in other/ so it can be moved
    import tempfile, shutil as sh
    tmp = Path(tempfile.mktemp(suffix=test_file.suffix))
    sh.copy(str(test_file), str(tmp))
    print(f"\nTest file: {test_file}")
    print(f"Temp copy: {tmp}\n")

    mime_map = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".ics": "text/calendar",
    }
    mime_type = mime_map.get(test_file.suffix.lower(), "application/octet-stream")

    attachments = [{
        "filename": test_file.name,
        "mime_type": mime_type,
        "local_path": str(tmp),
    }]

    summaries = handler.process_email_attachments("test-email-id", attachments)
    print("\nResult:")
    print(json.dumps(summaries, indent=2, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Attachment handler test")
    parser.add_argument(
        "--test-file",
        metavar="PATH",
        help="Process a single file for testing",
    )
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent

    if args.test_file:
        _run_test(Path(args.test_file), project_root)
    else:
        parser.print_help()
