"""
Writing style corpus manager.

Manages the professor's writing style corpus — a collection of sent emails
used to generate a style profile that improves draft replies over time.

How it works:
  1. Sent emails arrive via the BCC feedback loop (feedback_handler.py)
     and are saved as .txt files in writing-samples/samples/
  2. Emails manually placed in writing-samples/curated/ are treated as
     high-quality examples
  3. Weekly, regenerate_style_profile() analyses the corpus and writes
     a concise style summary to writing-samples/style-profile.md
  4. drafter.py injects that summary into the draft prompt so drafts
     gradually sound more like the professor

Usage:
    from style_manager import StyleManager
    manager = StyleManager(project_root)
    profile = manager.load_style_profile()        # "" if no profile yet
    manager.regenerate_style_profile(llm_client)  # call weekly
"""

from datetime import datetime
from pathlib import Path


class StyleManager:
    """Load and regenerate the professor's writing style profile."""

    def __init__(self, project_root: Path):
        self.root = project_root
        self.samples_dir = project_root / "writing-samples" / "samples"
        self.curated_dir = project_root / "writing-samples" / "curated"
        self.profile_path = project_root / "writing-samples" / "style-profile.md"
        self.prompt_path = project_root / "prompts" / "style_profile.md"

    def load_style_profile(self) -> str:
        """Return the current style profile text, or '' if none exists yet."""
        if self.profile_path.exists():
            return self.profile_path.read_text().strip()
        return ""

    def count_samples(self) -> dict:
        """Return counts of available writing samples."""
        samples = list(self.samples_dir.glob("*.txt")) if self.samples_dir.exists() else []
        curated = list(self.curated_dir.glob("*.txt")) + \
                  list(self.curated_dir.glob("*.md")) \
                  if self.curated_dir.exists() else []
        return {"samples": len(samples), "curated": len(curated)}

    def regenerate_style_profile(self, llm_client) -> bool:
        """
        Analyse the writing corpus and regenerate the style profile.

        Reads all .txt files from samples/ and curated/, sends them to the
        LLM with the style_profile.md prompt, and writes the result to
        writing-samples/style-profile.md.

        Args:
            llm_client: An LLMClient instance from llm_client.py.

        Returns:
            True if the profile was successfully regenerated, False if there
            were too few samples to generate a meaningful profile.
        """
        counts = self.count_samples()
        total = counts["samples"] + counts["curated"]

        if total == 0:
            print("  No writing samples found yet. Profile not updated.")
            print(f"  Add .txt files to: {self.samples_dir}")
            print(f"  Or curated examples to: {self.curated_dir}")
            return False

        if total < 3:
            print(f"  Only {total} sample(s) found — at least 3 are recommended.")
            print("  Generating a basic profile anyway.")

        # Load the system prompt
        if not self.prompt_path.exists():
            print(f"  ERROR: Style profile prompt not found at {self.prompt_path}")
            return False

        system_prompt = self.prompt_path.read_text()

        # Collect samples — curated ones first (treated as higher quality)
        corpus_parts = []

        curated_files = sorted(self.curated_dir.glob("*.txt")) + \
                        sorted(self.curated_dir.glob("*.md")) \
                        if self.curated_dir.exists() else []
        for path in curated_files:
            text = path.read_text().strip()
            if text:
                corpus_parts.append(f"[CURATED EXAMPLE — {path.name}]\n{text}")

        sample_files = sorted(self.samples_dir.glob("*.txt")) \
                       if self.samples_dir.exists() else []
        for path in sample_files:
            text = path.read_text().strip()
            if text:
                corpus_parts.append(f"[SENT EMAIL — {path.name}]\n{text}")

        if not corpus_parts:
            print("  All sample files are empty. Profile not updated.")
            return False

        # Trim to avoid exceeding context limits: ~100 samples max
        # Curated always included; samples trimmed if corpus is very large
        max_samples = 100
        if len(corpus_parts) > max_samples:
            n_curated = len(curated_files)
            corpus_parts = corpus_parts[:n_curated] + corpus_parts[n_curated:max_samples]
            print(f"  Large corpus: using {n_curated} curated + "
                  f"{max_samples - n_curated} samples (of {total} total)")

        corpus_text = "\n\n---\n\n".join(corpus_parts)

        user_message = (
            f"Here are {len(corpus_parts)} email(s) written by the professor. "
            f"Analyse them and produce the style profile as instructed.\n\n"
            f"{corpus_text}"
        )

        print(f"  Analysing {len(corpus_parts)} writing samples "
              f"({counts['curated']} curated, {counts['samples']} auto-collected)...")

        profile_text = llm_client.complete(
            "style_profile",
            system_prompt=system_prompt,
            user_message=user_message,
            max_tokens=1500,
        )

        # Write atomically: write to .tmp then rename
        tmp_path = self.profile_path.with_suffix(".tmp")
        tmp_path.write_text(
            f"<!-- Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} "
            f"from {len(corpus_parts)} sample(s) -->\n\n"
            + profile_text
        )
        tmp_path.replace(self.profile_path)

        print(f"  Style profile updated: {self.profile_path}")
        return True
