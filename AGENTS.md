# Inbox Assistant

## What This Project Is

An AI-powered email triage assistant for Jeroen, a university professor in the social
sciences at UCM (University College Maastricht). It:

- Fetches work emails forwarded from UCM Exchange to Gmail (label: `_UCM-redirect`)
- Classifies each email into URGENT / ACTION / FYI / NOISE using Claude
- Generates draft replies in Jeroen's writing style (inline in the briefing, not sent automatically)
- Produces a daily briefing sent to Jeroen's UCM email address, and written as an Obsidian daily note
- Sends urgent alerts and a morning ping via Telegram
- Maintains a writing style corpus via a BCC feedback loop (code ready; automation pending)
- Saves and classifies email attachments

**Jeroen is not a programmer.** Automated coding agents should make the code changes. Keep
code clear, well-commented, and prefer verbose error messages over silent failures.
Use "Jeroen" not "the professor" in prompts and messages.

---

## Architecture

Email ingestion is direct Gmail API — **n8n is not used in the current setup**.
The `staging/` directory and `n8n/` workflows exist for a possible future migration
but are currently inactive. Always assume direct Gmail API fetch unless told otherwise.

```
UCM Exchange → Gmail forward (_UCM-redirect label)
      │
      ▼
fetch_and_triage.py  (cron 06:30, venv python)
      │
      ├── LLM: classify + draft  (llm_client.py → Anthropic)
      ├── Briefing email → Jeroen's UCM address  (gmail_client.py)
      ├── Obsidian note → ~/syncthing/data/Notes/inbox-briefings/
      ├── Email archive → ~/syncthing/data/Notes/inbox-emails/mail/  (email_archiver.py)
      │     └── _index.json regenerated after each run
      ├── Telegram ping  (notifier.py)
      └── Stats → /home/jeroen/caddy/sites/inbox-dashboard/index.html
```

---

## Directory Conventions

| Directory | Purpose |
|-----------|---------|
| `src/` | All Python modules. Run scripts from the project root, not from inside `src/`. |
| `prompts/` | LLM system prompts (plain Markdown). Loaded at runtime — edit freely. |
| `staging/` | Drop-zone for n8n JSON (currently unused). |
| `staging/feedback/` | BCC feedback files land here when the automation is set up. |
| `writing-samples/samples/` | Auto-populated by the BCC loop. Never commit. |
| `writing-samples/curated/` | Manually managed example sent emails for style training. |
| `writing-samples/style-profile.md` | LLM-generated weekly style guide. |
| `attachments/` | Saved email attachments, sorted by type. |
| `data/` | `processed.json` (thread state), `weekly-stats.json` (dashboard), `archive-state.json` (email archive). |
| `{vault}/inbox-emails/` | Unified email archive root (contains views, index, and CLAUDE.md). |
| `{vault}/inbox-emails/mail/` | All non-noise emails as individual markdown files (flat directory). |
| `{vault}/inbox-emails/mail/assets/` | Saved email attachments from the archive. |
| `{vault}/inbox-emails/_index.json` | JSON index of all email frontmatter (auto-regenerated after each triage/backfill). |
| `{vault}/inbox-emails/_views/` | Obsidian Base files for project/category views. |
| `{vault}/TASKS.md` | Master task list in Obsidian vault (auto-generated, user-editable). |
| `{vault}/Tasks/` | Per-project and complex task detail files (auto-generated). |
| `logs/` | Cron output logs. |
| `dashboard/` | Local copy of generated HTML (also written to Caddy sites directory). |
| `n8n/` | n8n workflow JSON files — future use, not currently active. |
| `cron/` | Cron installer and Caddy config reference. |
| `docs/` | Archived documents (e.g. original design conversation). |

---

## Configuration

- `config.yaml` is the single source of all settings and API keys. Never commit it.
- All features are gated behind config values — missing or commented-out sections are silently skipped.
- Key current settings:
  - `gmail.scan_labels: [_UCM-redirect]`
  - `briefing.send_to`: Jeroen's UCM address
  - `obsidian.vault_path: ~/syncthing/data/Notes`
  - `drafts.save_to_gmail: false` (drafts are inline in briefing)
  - `dashboard.output_path`: points to Caddy static files dir

---

## Files That Must NEVER Be Committed

```
config.yaml
credentials.json
token.json
writing-samples/samples/
writing-samples/curated/*.txt
writing-samples/curated/*.md
data/processed.json
data/project-export-state.json
data/archive-state.json
data/weekly-stats.json
```

---

## Entry Points

| Command | Purpose | When to use |
|---------|---------|-------------|
| `python src/fetch_and_triage.py --dry-run` | Full triage, no side effects | Testing |
| `python src/fetch_and_triage.py` | Full production run | Cron 06:30 |
| `python src/fetch_and_triage.py --mini` | Afternoon update (compact email, no Obsidian note) | Cron 13:00 + 17:00 Mon–Fri |
| `python src/fetch_and_triage.py --regenerate-style` | Rebuild writing style profile | Cron Sunday 02:00 |
| `python src/urgent_check.py` | Check for urgent items only | Cron every 2 hrs 08:00–20:00 |
| `python src/dashboard.py` | Regenerate dashboard HTML | Cron 06:45 daily |
| `python src/gmail_client.py --auth --headless` | Re-authenticate Gmail OAuth | When token expires |
| `python src/project_fetch.py --all --dry-run` | Preview project email backfill | Testing |
| `python src/project_fetch.py --all` | Backfill all project emails | Initial setup / retroactive |
| `python src/project_discover.py --dry-run` | Preview project suggestions | Testing |
| `python src/project_discover.py` | Discover and email new project suggestions | Cron Sunday 04:00 |
| `python src/draft_on_demand.py` | Process on-demand draft requests | Cron every 2 min Mon–Fri 08:00–20:00 |
| `python src/draft_on_demand.py --dry-run` | Preview draft requests | Testing |
| `python src/project_discover.py --hours 4320` | Retroactive discovery (6 months) | One-off deep scan |
| `python src/fetch_and_triage.py --backfill --hours 720` | Backfill archive: last 30 days | One-off retroactive |
| `python src/fetch_and_triage.py --backfill --project ID` | Backfill archive for a project | Targeted retroactive |
| `python src/fetch_and_triage.py --backfill --contact EMAIL` | Backfill archive for a contact | Targeted retroactive |
| `python src/fetch_and_triage.py --backfill --keyword WORD` | Backfill archive by subject keyword | Targeted retroactive |
| `python src/archive_cleanup.py --report` | Archive size/age/contact report | On-demand |
| `python src/archive_cleanup.py --deduplicate` | Find duplicate archived emails | On-demand |
| `python src/archive_cleanup.py --prune-attachments` | Preview old large attachment cleanup | On-demand |
| `python src/migrate_project_archive.py --dry-run` | Preview project archive migration | One-off |
| `python src/migrate_project_archive.py` | Migrate project archives to inbox-emails/ | One-off |
| `python src/migrate_project_archive.py --source DIR --project-id ID` | Migrate from a custom source directory | One-off |

Always activate the virtualenv first: `source env/bin/activate`

The cron jobs use the full path `env/bin/python` and are already installed.

---

## Gmail Re-authentication (when token expires)

The VPS has no browser. Authentication requires SSH port forwarding via Tailscale:

1. **Local machine** (PowerShell): `ssh -L 8080:localhost:8080 jeroen@100.103.152.23 -i .\.ssh\hetzner_key`
2. **VPS**: `source env/bin/activate && python src/gmail_client.py --auth --headless`
3. Visit the printed Google URL in your local browser — it redirects to `localhost:8080` through the tunnel

---

## Testing Components in Isolation

| Component | Test command |
|-----------|-------------|
| LLM client | `python src/llm_client.py --test` |
| Notifier | `python src/notifier.py --test` |
| Gmail connection | `python src/gmail_client.py --test` |
| Attachment handler | `python src/attachment_handler.py --test-file attachments/other/sample.pdf` |
| Feedback handler | `python src/feedback_handler.py --test-email staging/feedback/sample.json` |

---

## Common Tasks

| Task | How to do it |
|------|-------------|
| Change classification rules | Edit `prompts/classify.md` |
| Change draft reply style | Edit `prompts/draft_reply.md` |
| Add a writing sample manually | Put a `.txt` in `writing-samples/curated/` |
| Switch LLM model for a task | Edit `llm.tasks` in `config.yaml` |
| Re-authenticate Gmail | `python src/gmail_client.py --auth --headless` (see above) |
| Change Obsidian briefing format | Edit `briefing.py` → `generate_markdown()` |
| Change HTML briefing format | Edit `briefing.py` → `_render_section()` |
| Enable task extraction | Set `tasks.enabled: true` in `config.yaml` |
| Change task file location | Edit `tasks.obsidian_file` / `tasks.detail_folder` in `config.yaml` |
| Request an on-demand draft | Forward email to `jeroenm+draft@gmail.com` (processed within 2 min) |
| Process old emails retroactively | `python src/fetch_and_triage.py --hours 336 --no-drafts` |
| Backfill archive (general) | `python src/fetch_and_triage.py --backfill --hours 720` |
| Backfill archive (one project) | `python src/fetch_and_triage.py --backfill --project wicked-problems` |
| Backfill archive (one contact) | `python src/fetch_and_triage.py --backfill --contact "alice@uni.nl"` |
| Backfill archive (keyword) | `python src/fetch_and_triage.py --backfill --keyword "assessment"` |
| Add a new project to archive | Add an entry to `projects:` in `config.yaml` (see README) |
| Initial project email backfill | `python src/project_fetch.py --all` |
| Archive report (size, contacts) | `python src/archive_cleanup.py --report` |
| Deduplicate archived emails | `python src/archive_cleanup.py --deduplicate --confirm` |
| Prune old large attachments | `python src/archive_cleanup.py --prune-attachments --older-than 180 --min-size 5 --confirm` |
| Migrate project archives | `python src/migrate_project_archive.py` (one-off, then `--delete`) |
| Discover new projects to archive | `python src/project_discover.py --dry-run` (or wait for Sunday email) |
| Retroactive project discovery | `python src/project_discover.py --hours 4320` (6 months lookback) |

---

## LLM Configuration

All LLM calls go through `src/llm_client.py`. Each task specifies provider + model in `config.yaml`:

```yaml
llm:
  tasks:
    classification:   {provider: anthropic, model: claude-haiku-4-5-20251001}
    draft_replies:    {provider: anthropic, model: claude-sonnet-4-6}
    summarization:    {provider: anthropic, model: claude-haiku-4-5-20251001}
    style_profile:    {provider: anthropic, model: claude-haiku-4-5-20251001}
    attachment_classify: {provider: anthropic, model: claude-haiku-4-5-20251001}
    project_discovery:   {provider: anthropic, model: claude-sonnet-4-6}
```

Never silently fall back to a different provider — fail loudly with a named error if a key is missing.

---

## Error Philosophy

- **Fail loudly** — missing API key, missing label, bad config: print a clear message naming what's wrong.
- **Log every major step** — stdout is captured by cron, so each pipeline step should announce itself.
- **Gmail token expiry** is the most common failure. The script must print:
  `Gmail authentication failed. Run: python src/gmail_client.py --auth --headless`

---

## Cron Schedule

> **⚠️ SUMMER MODE (set 2026-06-27 — restore in the last week of August).** To cut paid
> API usage during the summer break, only a cheap archive-only run is active. All other
> jobs are disabled (commented out in the live crontab). Pre-change crontab backup:
> `cron/backups/crontab-20260627-163944.bak`.

**Currently active:**

```
0 7-21/2 * * * cd /home/jeroen/projects/inbox-assistant && env/bin/python src/fetch_and_triage.py --backfill --hours 4
```

Archive-only: fetch `_UCM-redirect` → classify (Haiku) → archive non-noise → regenerate
`_index.json`. No briefing email, Telegram, drafts, tasks, or Sonnet. Every 2h, 07:00–21:00,
4h lookback (dedup makes the overlap free).

**Disabled for summer (canonical full schedule — re-enable in late August):**

```
30 6     * * *     env/bin/python src/fetch_and_triage.py                     # morning briefing (full pipeline)
45 6     * * *     env/bin/python src/dashboard.py                            # dashboard HTML
0  8-20/2 * * *    env/bin/python src/urgent_check.py                         # urgent alerts (every 2h)
*/15 7-23 * * *    env/bin/python src/update_tasks.py                         # task housekeeping (every 15 min)
0  2     * * 0     env/bin/python src/fetch_and_triage.py --regenerate-style  # weekly style profile
0  4     * * 0     env/bin/python src/project_discover.py                     # weekly project discovery
```

Config flags `drafts.enabled` / `tasks.enabled` / `alerts.enabled` are also set to `false`
for summer; `archive.enabled` stays `true`. **To restore:** revert the active line to the
plain daily `fetch_and_triage.py`, uncomment the disabled crontab lines, and flip the three
config flags back to `true`.

> Drift note: the `--mini` afternoon runs (13:00/17:00) and `draft_on_demand.py` (every 2 min)
> that earlier appeared here were never actually installed in the live crontab — removed to
> match reality. `update_tasks.py` (every 15 min) *was* installed but had been undocumented.

---

## Pending / Roadmap Items

The following are known TODOs. Check the Roadmap section of `README.md` for details.

- **BCC feedback loop automation** — `feedback_handler.py` is ready; needs a Gmail filter
  and a fetch script to populate `staging/feedback/` automatically.
- **Dashboard Cloudflare Access** — `inbox.moes.me` needs a Cloudflare Access application
  configured to protect it.
- **Retroactive stats backfill** — `weekly-stats.json` only has data from first real run onwards.
