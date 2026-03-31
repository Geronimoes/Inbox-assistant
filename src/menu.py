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


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

HEADER = (
    f"{BOLD}{CYAN}╔══════════════════════════════════════╗{RESET}\n"
    f"{BOLD}{CYAN}║        Inbox Assistant               ║{RESET}\n"
    f"{BOLD}{CYAN}╚══════════════════════════════════════╝{RESET}"
)


def _build_index():
    """Return (numbered_items, menu_text).
    numbered_items: dict mapping str(n) -> item dict.
    menu_text: the full rendered menu string."""
    numbered = {}
    lines = ["\n" + HEADER + "\n"]
    n = 1
    for header, items in GROUPS:
        lines.append(f"  {BOLD}{YELLOW}{header}{RESET}")
        for item in items:
            key = str(n)
            numbered[key] = item
            lines.append(f"  {str(n).rjust(3)}  {item['label']}")
            n += 1
        lines.append("")
    lines.append(f"    {BOLD}i{RESET}  Install to PATH (~/.local/bin/inbox)")
    lines.append(f"    {BOLD}q{RESET}  Quit")
    lines.append("")
    return numbered, "\n".join(lines)


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------

def _run(item):
    print(f"\n{BOLD}Running: {' '.join(item['cmd'])}{RESET}\n")
    result = subprocess.run(item['cmd'])
    if result.returncode != 0:
        print(f"\n{BOLD}Command exited with code {result.returncode}.{RESET}")
    input(f"\n{DIM}Press Enter to return to menu...{RESET}")


# ---------------------------------------------------------------------------
# PATH install
# ---------------------------------------------------------------------------

def _install(inbox_script: pathlib.Path):
    bin_dir = pathlib.Path.home() / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    link = bin_dir / "inbox"
    if link.is_symlink():
        link.unlink()
    link.symlink_to(inbox_script)
    print(f"\n{BOLD}Installed:{RESET} {link}  ->  {inbox_script}")
    import os
    path_env = os.environ.get("PATH", "")
    if str(bin_dir) not in path_env:
        print(
            f"\n{YELLOW}Note:{RESET} {bin_dir} is not in your $PATH.\n"
            f"Add this to your shell profile (~/.bashrc or ~/.zshrc):\n"
            f"  export PATH=\"$HOME/.local/bin:$PATH\""
        )
    input(f"\n{DIM}Press Enter to return to menu...{RESET}")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    # argv[1] is the real path of the `inbox` script, passed by the bash wrapper.
    # Fall back to inferring from __file__ if invoked directly.
    if len(sys.argv) > 1:
        inbox_script = pathlib.Path(sys.argv[1]).resolve()
    else:
        inbox_script = (pathlib.Path(__file__).parent.parent / "inbox").resolve()

    numbered, menu_text = _build_index()

    while True:
        subprocess.run(["clear"])
        print(menu_text)
        try:
            choice = input("  Enter choice: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{DIM}Goodbye.{RESET}\n")
            sys.exit(0)

        if choice == "q":
            print(f"\n{DIM}Goodbye.{RESET}\n")
            sys.exit(0)
        elif choice == "i":
            _install(inbox_script)
        elif choice in numbered:
            _run(numbered[choice])
        # Invalid input: silently loop (menu redraws on next iteration).


if __name__ == "__main__":
    main()
