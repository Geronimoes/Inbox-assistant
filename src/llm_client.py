"""
Multi-provider LLM client for the Inbox Assistant.

Provides a single interface for calling different LLM providers (Anthropic,
Google Gemini, Groq). Which provider and model to use for each task is
configured in config.yaml under llm.tasks.

Usage:
    from llm_client import LLMClient
    client = LLMClient(config["llm"])
    response = client.complete("classification", system_prompt, user_message)

Test:
    python src/llm_client.py --test
"""

import argparse
import sys
from pathlib import Path


class LLMClient:
    """Routes LLM calls to the correct provider based on config."""

    SUPPORTED_PROVIDERS = ("anthropic", "google", "groq")

    def __init__(self, llm_config: dict):
        """
        Args:
            llm_config: The 'llm' section from config.yaml, containing
                        'providers' (API keys) and 'tasks' (provider+model per task).
        """
        self.providers_config = llm_config.get("providers", {})
        self.tasks_config = llm_config.get("tasks", {})

        # Lazily initialised SDK clients — created on first use
        self._anthropic_client = None
        self._google_client = None
        self._groq_client = None

    def complete(
        self,
        task_name: str,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 2000,
    ) -> str:
        """
        Send a prompt to the LLM configured for the given task.

        Args:
            task_name:     Key from llm.tasks in config (e.g. "classification")
            system_prompt: The system/instruction prompt
            user_message:  The user turn content
            max_tokens:    Maximum tokens in the response

        Returns:
            The model's response as a plain string.

        Raises:
            ValueError: If the task or provider is not configured correctly.
            RuntimeError: If the API call fails.
        """
        task_cfg = self._get_task_config(task_name)
        provider = task_cfg["provider"]
        model = task_cfg["model"]

        print(f"  [LLM] {task_name} → {provider}/{model}")

        if provider == "anthropic":
            return self._call_anthropic(model, system_prompt, user_message, max_tokens)
        elif provider == "google":
            return self._call_google(model, system_prompt, user_message, max_tokens)
        elif provider == "groq":
            return self._call_groq(model, system_prompt, user_message, max_tokens)
        else:
            raise ValueError(
                f"Unknown provider '{provider}' for task '{task_name}'. "
                f"Supported providers: {', '.join(self.SUPPORTED_PROVIDERS)}"
            )

    # ── Provider implementations ─────────────────────────────────────────────

    def _call_anthropic(
        self, model: str, system_prompt: str, user_message: str, max_tokens: int
    ) -> str:
        client = self._get_anthropic_client()
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text.strip()

    def _call_google(
        self, model: str, system_prompt: str, user_message: str, max_tokens: int
    ) -> str:
        import google.generativeai as genai

        genai_client = self._get_google_client()
        gemini_model = genai_client.GenerativeModel(
            model_name=model,
            system_instruction=system_prompt,
            generation_config={"max_output_tokens": max_tokens},
        )
        response = gemini_model.generate_content(user_message)
        return response.text.strip()

    def _call_groq(
        self, model: str, system_prompt: str, user_message: str, max_tokens: int
    ) -> str:
        client = self._get_groq_client()
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content.strip()

    # ── Lazy client initialisation ───────────────────────────────────────────

    def _get_anthropic_client(self):
        if self._anthropic_client is None:
            api_key = self._require_api_key("anthropic")
            from anthropic import Anthropic
            self._anthropic_client = Anthropic(api_key=api_key)
        return self._anthropic_client

    def _get_google_client(self):
        if self._google_client is None:
            api_key = self._require_api_key("google")
            try:
                import google.generativeai as genai
            except ImportError:
                raise RuntimeError(
                    "google-generativeai is not installed. "
                    "Run: pip install google-generativeai"
                )
            genai.configure(api_key=api_key)
            self._google_client = genai
        return self._google_client

    def _get_groq_client(self):
        if self._groq_client is None:
            api_key = self._require_api_key("groq")
            try:
                from groq import Groq
            except ImportError:
                raise RuntimeError(
                    "groq is not installed. Run: pip install groq"
                )
            self._groq_client = Groq(api_key=api_key)
        return self._groq_client

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _get_task_config(self, task_name: str) -> dict:
        """Return the {provider, model} config for a task, with clear errors."""
        if task_name not in self.tasks_config:
            raise ValueError(
                f"Task '{task_name}' is not defined in llm.tasks in config.yaml. "
                f"Defined tasks: {', '.join(self.tasks_config.keys()) or '(none)'}"
            )
        task_cfg = self.tasks_config[task_name]
        for field in ("provider", "model"):
            if field not in task_cfg:
                raise ValueError(
                    f"llm.tasks.{task_name} is missing the '{field}' field in config.yaml."
                )
        return task_cfg

    def _require_api_key(self, provider: str) -> str:
        """Return API key for a provider, raising a clear error if missing."""
        key = (
            self.providers_config
            .get(provider, {})
            .get("api_key", "")
        )
        if not key or key.startswith("YOUR_") or "..." in key:
            provider_names = {
                "anthropic": "Anthropic (sk-ant-...)",
                "google": "Google AI Studio (AIza...)",
                "groq": "Groq (gsk_...)",
            }
            raise ValueError(
                f"API key for '{provider}' is missing or is still a placeholder. "
                f"Set llm.providers.{provider}.api_key in config.yaml.\n"
                f"Get a key from: {provider_names.get(provider, provider)}"
            )
        return key


# ── CLI test mode ─────────────────────────────────────────────────────────────

def _run_test(config_path: Path) -> None:
    """Test each configured LLM provider with a simple ping."""
    import yaml

    if not config_path.exists():
        print(f"ERROR: config.yaml not found at {config_path}")
        print("Copy config.example.yaml to config.yaml and fill in your API keys.")
        sys.exit(1)

    config = yaml.safe_load(config_path.read_text())
    llm_cfg = config.get("llm")
    if not llm_cfg:
        print("ERROR: No 'llm' section found in config.yaml.")
        print("See config.example.yaml for the required structure.")
        sys.exit(1)

    client = LLMClient(llm_cfg)
    tasks = llm_cfg.get("tasks", {})
    providers_tested = {}

    print(f"\nTesting {len(tasks)} configured task(s)...\n")

    for task_name, task_cfg in tasks.items():
        provider = task_cfg.get("provider", "?")
        model = task_cfg.get("model", "?")
        key = f"{provider}/{model}"

        if key in providers_tested:
            print(f"  {task_name:25s} → {key} (already tested, skipping)")
            continue

        print(f"  {task_name:25s} → {key}")
        try:
            response = client.complete(
                task_name,
                system_prompt="You are a test assistant.",
                user_message="Reply with only the single word: PONG",
                max_tokens=10,
            )
            if "PONG" in response.upper():
                print(f"    OK  Response: {response!r}")
            else:
                print(f"    OK  Response (unexpected format): {response!r}")
            providers_tested[key] = True
        except ValueError as e:
            print(f"    CONFIG ERROR: {e}")
            providers_tested[key] = False
        except Exception as e:
            print(f"    API ERROR: {e}")
            providers_tested[key] = False

    successes = sum(1 for v in providers_tested.values() if v)
    total = len(providers_tested)
    print(f"\nResult: {successes}/{total} provider(s) working.\n")
    if successes < total:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLM client test")
    parser.add_argument("--test", action="store_true", help="Test all configured providers")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent.parent / "config.yaml"),
        help="Path to config.yaml",
    )
    args = parser.parse_args()

    if args.test:
        _run_test(Path(args.config))
    else:
        parser.print_help()
