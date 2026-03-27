"""
Email classifier using a configurable LLM provider.

Sends batches of emails to the LLM for classification into priority tiers
and returns structured results. The provider and model are set in config.yaml
under llm.tasks.classification.
"""

import json
from pathlib import Path


class EmailClassifier:
    """Classify emails using the configured LLM provider."""

    def __init__(self, llm_client):
        """
        Args:
            llm_client: An LLMClient instance from llm_client.py.
        """
        self.llm = llm_client
        self.classify_prompt = self._load_prompt("classify.md")

    def _load_prompt(self, filename: str) -> str:
        """Load a prompt template from the prompts/ directory."""
        prompt_path = Path(__file__).parent.parent / "prompts" / filename
        return prompt_path.read_text()

    def classify_batch(self, emails: list[dict]) -> list[dict]:
        """Classify a batch of emails.
        
        Sends emails to Claude in batches of 20 (to stay within context limits)
        and returns classification results.
        """
        results = []
        batch_size = 20

        for i in range(0, len(emails), batch_size):
            batch = emails[i:i + batch_size]
            batch_results = self._classify_single_batch(batch)
            results.extend(batch_results)

        return results

    def _classify_single_batch(self, emails: list[dict]) -> list[dict]:
        """Send a single batch to Claude for classification."""
        # Prepare email summaries for the prompt
        email_summaries = []
        for e in emails:
            summary = {
                "email_id": e["id"],
                "from": e["from"],
                "subject": e["subject"],
                "date": e["date"],
                "snippet": e["snippet"],
                # Include first ~1500 chars of body for context
                "body_preview": e.get("body_text", "")[:1500],
            }
            email_summaries.append(summary)

        user_message = (
            f"Classify the following {len(emails)} emails. "
            f"Return ONLY a JSON array with no other text.\n\n"
            f"Emails:\n{json.dumps(email_summaries, indent=2)}"
        )

        response_text = self.llm.complete(
            "classification",
            system_prompt=self.classify_prompt,
            user_message=user_message,
            max_tokens=4096,
        )
        # Strip markdown code fences if present
        if response_text.startswith("```"):
            response_text = response_text.split("\n", 1)[1]
            response_text = response_text.rsplit("```", 1)[0]

        try:
            classifications = json.loads(response_text)
        except json.JSONDecodeError:
            print(f"  ⚠ Failed to parse classification response. Raw:\n{response_text[:500]}")
            # Return safe defaults
            classifications = [
                {
                    "email_id": e["id"],
                    "category": "FYI",
                    "confidence": 0.5,
                    "summary": e["subject"],
                    "why": "Classification failed — defaulting to FYI",
                    "suggested_action": "Review manually",
                    "needs_draft": False,
                    "draft_tone": "professional",
                    "reply_language": "en",
                    "deadline": None,
                }
                for e in emails
            ]

        return classifications

    def get_urgent(self, classifications: list[dict]) -> list[dict]:
        """Filter for urgent emails only."""
        return [c for c in classifications if c.get("category") == "URGENT"]

    def get_actionable(self, classifications: list[dict]) -> list[dict]:
        """Filter for emails needing a reply (URGENT + ACTION)."""
        return [c for c in classifications
                if c.get("category") in ("URGENT", "ACTION")]

    def get_fyi(self, classifications: list[dict]) -> list[dict]:
        """Filter for FYI items."""
        return [c for c in classifications if c.get("category") == "FYI"]

    def get_noise(self, classifications: list[dict]) -> list[dict]:
        """Filter for noise/archivable items."""
        return [c for c in classifications if c.get("category") == "NOISE"]
