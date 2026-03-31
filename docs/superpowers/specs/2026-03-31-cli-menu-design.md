# CLI Menu Tool — Design Spec
_Date: 2026-03-31_

## Overview

A convenience CLI tool that wraps all inbox-assistant commands behind an interactive
numbered menu. It ensures the Python virtualenv is always active, works both as
`./inbox` from the project root and as `inbox` from anywhere on PATH.

---

## Files

Two new files are added; nothing existing is modified.

| File | Purpose |
|------|---------|
| `inbox` | Executable bash wrapper in the project root. Resolves its own location symlink-safely, activates the venv, and delegates to `src/menu.py`. |
| `src/menu.py` | All menu logic. Renders grouped numbered menu with ANSI color, dispatches commands, streams live output, handles PATH install. Stdlib only — no new dependencies. |

---

## Bash Wrapper (`inbox`)

- Uses `realpath "${BASH_SOURCE[0]}"` so symlinks resolve to the actual project directory.
- `cd`s to the project root before activating the venv, so all relative paths inside scripts stay correct.
- Sources `env/bin/activate`.
- If `env/` doesn't exist, prints a clear error and exits:
  ```
  Error: virtualenv not found at <project>/env/
  Run: python3 -m venv env && pip install -r requirements.txt
  ```
- Execs `python src/menu.py "$@"` — passes any CLI args through (reserved for future use).

---

## Python Menu (`src/menu.py`)

### Layout

Clears screen on each display. Header box, then groups with ANSI-colored section headers,
right-aligned numbers for double-digit alignment:

```
╔══════════════════════════════════════╗
║        Inbox Assistant               ║
╚══════════════════════════════════════╝

  Daily Operations
    1  Morning briefing (full run)
    2  Afternoon update (mini)
    3  Check urgent items now
    4  Regenerate dashboard

  Drafts
    5  Process on-demand drafts
    6  Preview draft requests (dry-run)

  Project Archive
    7  Incremental project fetch
    8  Backfill all projects (dry-run)
    9  Backfill all projects

  Project Discovery
   10  Discover new projects (dry-run)
   11  Discover new projects
   12  Retroactive discovery — 6 months

  Maintenance
   13  Regenerate writing style profile
   14  Re-authenticate Gmail

   i  Install to PATH (~/.local/bin/inbox)
   q  Quit

Enter choice:
```

### Menu data structure

Each item is a dict with `label` (display string) and `cmd` (list of args passed to
`subprocess.run`, relative to project root). Groups are a list of `(header, [items])` pairs.

### Command dispatch

- `subprocess.run(cmd)` with inherited stdin/stdout/stderr — live output streams to terminal.
- After the subprocess exits, if return code is non-zero: print `Command exited with code N`.
- Then: `Press Enter to return to menu` — waits for Enter before clearing and redisplaying.

### PATH install (option `i`)

1. Resolves the absolute path of the `inbox` script (passed in via `sys.argv` or `__file__` parent).
2. Creates `~/.local/bin/` if it doesn't exist.
3. Creates or overwrites symlink `~/.local/bin/inbox → <project>/inbox`.
4. Prints confirmation.
5. If `~/.local/bin` is not in `$PATH`, prints a one-time note:
   ```
   Note: add this to your shell profile to use 'inbox' from anywhere:
     export PATH="$HOME/.local/bin:$PATH"
   ```

### Quit

`q` or Ctrl-C exits cleanly with a short goodbye message.

---

## Command Reference

| # | Label | Command |
|---|-------|---------|
| 1 | Morning briefing (full run) | `python src/fetch_and_triage.py` |
| 2 | Afternoon update (mini) | `python src/fetch_and_triage.py --mini` |
| 3 | Check urgent items now | `python src/urgent_check.py` |
| 4 | Regenerate dashboard | `python src/dashboard.py` |
| 5 | Process on-demand drafts | `python src/draft_on_demand.py` |
| 6 | Preview draft requests (dry-run) | `python src/draft_on_demand.py --dry-run` |
| 7 | Incremental project fetch | `python src/project_fetch.py` |
| 8 | Backfill all projects (dry-run) | `python src/project_fetch.py --all --dry-run` |
| 9 | Backfill all projects | `python src/project_fetch.py --all` |
| 10 | Discover new projects (dry-run) | `python src/project_discover.py --dry-run` |
| 11 | Discover new projects | `python src/project_discover.py` |
| 12 | Retroactive discovery — 6 months | `python src/project_discover.py --hours 4320` |
| 13 | Regenerate writing style profile | `python src/fetch_and_triage.py --regenerate-style` |
| 14 | Re-authenticate Gmail | `python src/gmail_client.py --auth --headless` |

---

## Out of scope (this iteration)

- Configuration editing from the menu
- Showing last-run timestamps
- Log tailing / status display
