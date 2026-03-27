"""
Draft reply composer using a configurable LLM provider.

For emails classified as needing a reply, generates draft responses
matching the professor's voice and saves them as Gmail drafts. The
provider and model are set in config.yaml under llm.tasks.draft_replies.

The draft prompt is loaded from prompts/draft_reply.md and can be prepended
with a writing style profile (from writing-samples/style-profile.md) to
improve how closely drafts match the professor's actual voice.
"""

import json
from pathlib import Path


class DraftComposer:
    """Compose draft replies using the configured LLM provider."""

    def __init__(self, llm_client, style_profile: str = ""):
        """
        Args:
            llm_client:    An LLMClient instance from llm_client.py.
            style_profile: Optional writing style profile text to inject
                           into the draft prompt (from style_manager.py).
        """
        self.llm = llm_client
        self.style_profile = style_profile
        self.draft_prompt = self._build_prompt()

    def _build_prompt(self) -> str:
        """Load the draft reply prompt and inject the style profile if available."""
        prompt_path = Path(__file__).parent.parent / "prompts" / "draft_reply.md"
        base_prompt = prompt_path.read_text()
        if self.style_profile:
            return base_prompt.replace("{STYLE_PROFILE}", self.style_profile)
        # If no style profile yet, remove the placeholder cleanly
        return base_prompt.replace("{STYLE_PROFILE}", "")

    def compose_draft(self, email: dict, classification: dict) -> str | None:
        """Compose a draft reply for a single email.
        
        Returns the draft text, or None if no draft is needed.
        """
        if not classification.get("needs_draft", False):
            return None

        tone = classification.get("draft_tone", "professional")
        language = classification.get("reply_language", "en")
        suggested_action = classification.get("suggested_action", "")

        user_message = (
            f"Draft a reply to this email.\n\n"
            f"**Tone:** {tone}\n"
            f"**Language:** {'Dutch' if language == 'nl' else 'English'}\n"
            f"**Suggested action:** {suggested_action}\n\n"
            f"**Original email:**\n"
            f"From: {email['from']}\n"
            f"Subject: {email['subject']}\n"
            f"Date: {email['date']}\n\n"
            f"{email.get('body_text', email.get('snippet', ''))[:3000]}\n\n"
            f"---\n"
            f"Return ONLY the email body text (no subject line, no metadata). "
            f"Use [PLACEHOLDER] format for anything the professor needs to decide."
        )

        draft_text = self.llm.complete(
            "draft_replies",
            system_prompt=self.draft_prompt,
            user_message=user_message,
            max_tokens=1024,
        )
        return draft_text

    def compose_batch(self, emails: list[dict],
                      classifications: list[dict]) -> list[dict]:
        """Compose drafts for all emails that need them.
        
        Returns a list of dicts with email_id, draft_text, subject, to.
        """
        # Build lookup from email_id to email
        email_lookup = {e["id"]: e for e in emails}
        
        drafts = []
        for cls in classifications:
            if not cls.get("needs_draft", False):
                continue

            email_id = cls["email_id"]
            email = email_lookup.get(email_id)
            if not email:
                continue

            draft_text = self.compose_draft(email, cls)
            if draft_text:
                # Determine reply subject
                subject = email["subject"]
                if not subject.lower().startswith("re:"):
                    subject = f"Re: {subject}"

                # Extract reply-to address
                from_addr = email["from"]
                # Handle "Name <email>" format
                if "<" in from_addr:
                    from_addr = from_addr.split("<")[1].rstrip(">")

                drafts.append({
                    "email_id": email_id,
                    "thread_id": email["thread_id"],
                    "to": from_addr,
                    "subject": subject,
                    "draft_text": draft_text,
                    "tone": cls.get("draft_tone", "professional"),
                    "language": cls.get("reply_language", "en"),
                })

                print(f"  ✓ Draft composed for: {email['subject'][:50]}")

        return drafts
