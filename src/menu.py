"""
inbox-assistant CLI menu.
Run via the `inbox` wrapper script in the project root.
"""
import sys
import subprocess
import pathlib

# ---------------------------------------------------------------------------
# ANSI colour helpers
# ---------------------------------------------------------------------------
RESET  = "\033[0m"
BOLD   = "\033[1m"
CYAN   = "\033[36m"
YELLOW = "\033[33m"
DIM    = "\033[2m"

# ---------------------------------------------------------------------------
# Menu definition
# Each item: {'label': str, 'cmd': list[str]}
# 'python' resolves to the venv python because the bash wrapper already
# activated the venv before invoking this script.
# ---------------------------------------------------------------------------
GROUPS = [
    ("Daily Operations", [
        {"label": "Morning briefing (full run)",     "cmd": ["python", "src/fetch_and_triage.py"]},
        {"label": "Afternoon update (mini)",         "cmd": ["python", "src/fetch_and_triage.py", "--mini"]},
        {"label": "Check urgent items now",          "cmd": ["python", "src/urgent_check.py"]},
        {"label": "Regenerate dashboard",            "cmd": ["python", "src/dashboard.py"]},
    ]),
    ("Drafts", [
        {"label": "Process on-demand drafts",        "cmd": ["python", "src/draft_on_demand.py"]},
        {"label": "Preview draft requests (dry-run)","cmd": ["python", "src/draft_on_demand.py", "--dry-run"]},
    ]),
    ("Project Archive", [
        {"label": "Incremental project fetch",       "cmd": ["python", "src/project_fetch.py"]},
        {"label": "Backfill all projects (dry-run)", "cmd": ["python", "src/project_fetch.py", "--all", "--dry-run"]},
        {"label": "Backfill all projects",           "cmd": ["python", "src/project_fetch.py", "--all"]},
    ]),
    ("Project Discovery", [
        {"label": "Discover new projects (dry-run)", "cmd": ["python", "src/project_discover.py", "--dry-run"]},
        {"label": "Discover new projects",           "cmd": ["python", "src/project_discover.py"]},
        {"label": "Retroactive discovery — 6 months", "cmd": ["python", "src/project_discover.py", "--hours", "4320"]},
    ]),
    ("Maintenance", [
        {"label": "Regenerate writing style profile","cmd": ["python", "src/fetch_and_triage.py", "--regenerate-style"]},
        {"label": "Re-authenticate Gmail",           "cmd": ["python", "src/gmail_client.py", "--auth", "--headless"]},
    ]),
]


def main():
    pass  # implemented in Task 3


if __name__ == "__main__":
    main()
