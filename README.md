# Inbox Briefing Assistant

An AI-powered email triage system for a university professor. It runs on a VPS,
reads forwarded work email from Gmail, and delivers a daily briefing with
prioritised emails, draft replies, and urgent alerts. All non-noise emails are
archived as individual Markdown files in an Obsidian vault with rich YAML
frontmatter, enabling project views, contact history, and draft context retrieval.

## What It Does

Every morning at 6:30 AM the system:

1. **Fetches** emails tagged with `_UCM-redirect` from Gmail (university mail forwarded here)
2. **Classifies** each email into URGENT / ACTION / FYI / NOISE using Claude
3. **Drafts replies** for action items in the correct language and tone
4. **Generates a briefing** — sent to the university email address and written as an Obsidian daily note
5. **Pings Telegram** with a count summary (urgent, action, FYI, noise)
6. **Alerts via Telegram** if urgent emails arrive between checks (every 2 hours, 8 AM–8 PM)
7. **Archives emails** — saves all non-noise emails as individual Markdown files in the Obsidian vault with rich YAML frontmatter (project tags, contacts, category, attachments)
8. **Regenerates the index** — `_index.json` is rebuilt after each run for fast agent-based search
9. **Tracks stats** for the weekly dashboard

Separately:

10. **On-demand drafts** — forward any email to `+draft` address for a draft reply within 2 minutes
11. **Retroactive backfill** — classify and archive older emails by project, contact, or keyword
12. **Project discovery** — weekly LLM scan suggests new projects to track

## Architecture

```
University mail (UCM Exchange)
      │ forwarded via Gmail filter
      ▼
Gmail inbox  ──label: _UCM-redirect──►  Gmail API
                                              │
                                    fetch_and_triage.py  (cron 06:30)
                                              │
          ┌──────────┬──────────┬─────────────┼──────────────┐
          ▼          ▼          ▼             ▼              ▼
     Claude API  Obsidian   Telegram    email_archiver   Stats
     classify+   daily      morning     saves non-noise  dashboard
     draft       note       ping        emails to vault
          │                             + _index.json
          ▼
     Briefing email → university address

Obsidian vault (inbox-emails/):
    mail/*.md           ← individual emails with YAML frontmatter
    mail/assets/        ← saved attachments
    _index.json         ← frontmatter index (auto-regenerated)
    _views/*.base       ← Obsidian Base views per project/category
```

## Project Structure

```
inbox-assistant/
├── CLAUDE.md                  ← Instructions for Claude Code
├── config.yaml                ← Your settings (never commit)
├── config.example.yaml        ← Template — start here
├── requirements.txt
│
├── src/
│   ├── fetch_and_triage.py    ← Main orchestrator (entry point)
│   ├── email_archiver.py      ← Unified email archive: save, index, context retrieval
│   ├── project_fetch.py       ← Project email backfill (manual, uses email_archiver)
│   ├── project_discover.py    ← LLM-based project suggestion discovery
│   ├── archive_cleanup.py     ← Archive maintenance: report, deduplicate, prune
│   ├── migrate_project_archive.py ← One-off migration to unified archive
│   ├── gmail_client.py        ← Gmail API: fetch, send, draft, archive, attachments
│   ├── llm_client.py          ← Multi-provider LLM abstraction
│   ├── classifier.py          ← Email classification
│   ├── drafter.py             ← Draft reply composer
│   ├── briefing.py            ← HTML email + Obsidian Markdown generator
│   ├── style_manager.py       ← Writing style corpus management
│   ├── feedback_handler.py    ← BCC feedback loop processor (see Roadmap)
│   ├── attachment_handler.py  ← PDF/DOCX/ICS attachment classifier
│   ├── notifier.py            ← Telegram notifications
│   ├── task_writer.py         ← Task extraction to Obsidian TASKS.md
│   ├── draft_on_demand.py     ← On-demand draft via email forwarding
│   ├── urgent_check.py        ← Runs every 2 hours, alerts on new urgent mail
│   ├── menu.py                ← Interactive CLI menu for common operations
│   └── dashboard.py           ← Weekly stats HTML generator
│
├── prompts/
│   ├── classify.md            ← Classification rules and persona (edit freely)
│   ├── draft_reply.md         ← Draft reply style guide (edit freely)
│   ├── style_profile.md       ← Style analysis prompt (auto-used)
│   └── attachment_classify.md ← Attachment classification prompt
│
├── writing-samples/
│   ├── curated/               ← Manually added example emails (.txt)
│   ├── samples/               ← Auto-populated by BCC loop (never commit)
│   └── style-profile.md       ← LLM-generated weekly style guide
│
├── attachments/               ← Saved email attachments by type
│   ├── papers/  submissions/  forms/  invoices/  calendar/  other/
│
├── staging/                   ← Drop-zone for n8n JSON (currently unused)
│   └── feedback/              ← BCC feedback files go here (see Roadmap)
│
├── data/
│   ├── processed.json              ← Processed email IDs + thread state (never commit)
│   ├── archive-state.json          ← Tracks which emails are archived (never commit)
│   ├── project-export-state.json   ← Legacy project export state (never commit)
│   └── weekly-stats.json           ← Dashboard data (never commit)
│
├── dashboard/                 ← Generated HTML (also written to Caddy sites/)
├── logs/                      ← Cron output logs
├── n8n/                       ← n8n workflow JSONs (future use)
└── cron/
    ├── install.sh             ← Installs all cron jobs
    └── caddy-dashboard.snippet ← Caddy config reference (see SETUP.md)
```

## Cron Schedule

> **⚠️ Summer mode (set 2026-06-27 — restore in the last week of August):** only the
> archive-only run below is active. All other jobs are disabled in the crontab, and
> `drafts` / `tasks` / `alerts` are set to `false` in `config.yaml`, to cut paid API usage
> over the break. Restore by reverting the active line to the plain daily `fetch_and_triage.py`,
> uncommenting the disabled crontab lines, and flipping the three config flags back to `true`.

**Currently active:**

| Time | Job | Purpose |
|------|-----|---------|
| Every 2 hrs, 07:00–21:00 | `fetch_and_triage.py --backfill --hours 4` | Archive-only: classify (Haiku) + archive + reindex; no briefing/drafts/Telegram |

**Disabled for summer (full schedule — re-enable in late August):**

| Time | Job | Purpose |
|------|-----|---------|
| 06:30 daily | `fetch_and_triage.py` | Morning briefing (full pipeline) |
| 06:45 daily | `dashboard.py` | Refresh dashboard HTML |
| Every 2 hrs, 08:00–20:00 | `urgent_check.py` | Urgent alerts |
| Every 15 min, 07:00–23:00 | `update_tasks.py` | Task housekeeping (sync checked-off items) |
| Sunday 02:00 | `fetch_and_triage.py --regenerate-style` | Rebuild writing style profile |
| Sunday 04:00 | `project_discover.py` | Discover new project suggestions |

> Drift note: earlier versions listed `--mini` afternoon runs (13:00/17:00) and a
> `draft_on_demand.py` job (every 2 min); neither was ever installed in the live crontab, so
> they have been removed. `update_tasks.py` (every 15 min) *was* running but undocumented — now listed.

## Customisation

| What to change | Where |
|----------------|-------|
| Classification rules / priorities | `prompts/classify.md` |
| Draft reply tone and style | `prompts/draft_reply.md` |
| Add a manual writing sample | Drop a `.txt` in `writing-samples/curated/` |
| Switch LLM model for a task | Edit `llm.tasks` in `config.yaml` |
| Obsidian vault path | `obsidian.vault_path` in `config.yaml` |
| Telegram bot / chat ID | `notifications.telegram` in `config.yaml` |
| Which Gmail label to scan | `gmail.scan_labels` in `config.yaml` |
| Re-authenticate Gmail | `python src/gmail_client.py --auth --headless` |
| Add or configure a project archive | Edit `projects:` in `config.yaml` (see below) |

## Scripts Reference

All scripts are run from the project root with the virtualenv active
(`source env/bin/activate`).

### `src/fetch_and_triage.py` — Main orchestrator

The primary entry point. Fetches emails, classifies them, composes drafts,
generates the briefing, and sends it.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| *(none)* | | | Full production run |
| `--dry-run` | flag | off | Classify and preview without sending email, saving state, or pinging Telegram. Writes `data/preview.html` and `data/preview.md`. |
| `--hours N` | int | from config | Override the lookback window (e.g. `--hours 48` for yesterday + today) |
| `--no-drafts` | flag | off | Skip draft reply generation (saves API cost for large/retroactive runs) |
| `--mini` | flag | off | Afternoon update mode: compact briefing email (URGENT + ACTION only), skip Obsidian note, Telegram ping for new items |
| `--regenerate-style` | flag | off | Rebuild `writing-samples/style-profile.md` from the writing corpus, then exit without processing email |

**Examples:**
```bash
python src/fetch_and_triage.py --dry-run              # safe preview
python src/fetch_and_triage.py --dry-run --hours 48   # preview last 2 days
python src/fetch_and_triage.py --hours 336 --no-drafts  # retroactive 2-week import
python src/fetch_and_triage.py --mini                  # afternoon update (compact email)
python src/fetch_and_triage.py --regenerate-style     # rebuild style profile
```

---

### `src/email_archiver.py` — Unified email archive

Core module for all email archiving. Used by `fetch_and_triage.py` (ongoing triage),
`project_fetch.py` (retroactive backfill), and `draft_on_demand.py` (context retrieval).

All non-noise emails are saved as individual Markdown files in a flat directory
(`inbox-emails/mail/`) with rich YAML frontmatter. Project membership, categories,
and contacts are encoded in frontmatter fields and queried via Obsidian Bases or
the JSON index (`_index.json`).

**Frontmatter schema:**

```yaml
---
date: "2026-04-02"
subject: "RE: PRO3030 assessment plan"
from: "\"Deelman, Annechien\" <a.deelman@maastrichtuniversity.nl>"
to: "\"Moes, Jeroen\" <jeroen.moes@maastrichtuniversity.nl>"
cc: "\"Savelberg, Hans\" <hans.savelberg@maastrichtuniversity.nl>"
thread_id: "18abc123def"
gmail_id: "18abc123def456"
category: ACTION                    # URGENT / ACTION / FYI
priority: normal                    # high / normal / low
inbox-projects:                     # list (email can belong to multiple)
  - wicked-problems
contacts:                           # all email addresses involved
  - a.deelman@maastrichtuniversity.nl
  - jeroen.moes@maastrichtuniversity.nl
  - hans.savelberg@maastrichtuniversity.nl
language: en                        # en / nl
has_attachments: true
saved_attachments:
  - "Assessment Plan UCM PRO3030.xlsx"
task_extracted: false
tags:
  - inbox-email                     # always present
  - inbox-action                    # category tag (inbox-urgent/inbox-action/inbox-fyi)
  - wicked-problems                 # project tags
---
```

**JSON index:** After each archiving run, `_index.json` is regenerated with
frontmatter from all archived emails. Agents can read this single file to search
the archive without opening hundreds of individual files.

---

### `src/project_fetch.py` — Project email backfill

Fetches emails related to configured projects and archives them via
`email_archiver.py`. Used for retroactive backfill only — ongoing archiving
happens automatically during triage runs.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| *(none)* | | | Export all projects, last 24 hours |
| `--all` | flag | off | Fetch full history (up to 500 matching emails) |
| `--hours N` | int | from config | Custom lookback window |
| `--project ID` | string | all projects | Run for a single project by its `id` |
| `--dry-run` | flag | off | Preview without writing |

**Project configuration** (`config.yaml`):

```yaml
projects:
  - id: wicked-problems
    name: "Wicked Problems"
    vault_folder: "inbox-emails"    # unified archive (all projects share this)
    since: "2025-08-01"
    attachment_max_size_mb: 7
    exclude_extensions: [".ics"]
    keywords: ["Wicked Problems", "PRO3030"]
    collaborators:
      - name: "Annechien Deelman"
        email_fragment: "deelman"
      - name: "Hans Savelberg"
        email_fragment: "savelberg"
```

---

### `src/draft_on_demand.py` — On-demand draft generator

Forward any email to `jeroenm+draft@gmail.com` to request a draft reply within
2 minutes. The script fetches the full thread context from Gmail and loads
relevant archived emails (same thread, same sender) from the unified archive
for additional context.

The draft is emailed to your UCM address — no Telegram notification.

**Gmail setup required:** Create a filter in Gmail:
- Matches: `to:jeroenm+draft@gmail.com`
- Action: Apply label `_draft-request`, Skip inbox

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| *(none)* | | | Process pending draft requests |
| `--dry-run` | flag | off | Preview without sending or modifying labels |

**Configuration** (`config.yaml`):

```yaml
draft_on_demand:
  enabled: true
  label: "_draft-request"
  send_to: "jeroen.moes@maastrichtuniversity.nl"
```

---

### `src/urgent_check.py` — Urgent email checker

Fetches the most recent emails and sends a Telegram alert if any are URGENT.
Respects quiet hours configured in `config.yaml`. Run by cron every 2 hours.

No flags — designed to run unattended. Exits silently if no urgent email found
or if quiet hours are active.

---

### `src/gmail_client.py` — Gmail authentication and diagnostics

| Flag | Type | Required? | Description |
|------|------|-----------|-------------|
| `--auth` | flag | for first-time setup | Run the OAuth flow to create `token.json` |
| `--headless` | flag | with `--auth` on VPS | Use local HTTP server on port 8080 (requires SSH tunnel) |
| `--test` | flag | optional | Fetch 5 recent emails to verify the connection works |

**Examples:**
```bash
python src/gmail_client.py --auth --headless   # (re-)authenticate on VPS
python src/gmail_client.py --test              # verify Gmail API is working
```

---

### `src/notifier.py` — Telegram notification test

| Flag | Type | Required? | Description |
|------|------|-----------|-------------|
| `--test` | flag | yes (only flag) | Send a test message to verify bot token and chat ID |

```bash
python src/notifier.py --test
```

---

### `src/llm_client.py` — LLM provider test

| Flag | Type | Required? | Description |
|------|------|-----------|-------------|
| `--test` | flag | yes (only useful flag) | Send "Reply with only the word PONG" to each configured provider |

```bash
python src/llm_client.py --test
```

---

### `src/dashboard.py` — Dashboard generator

Reads `data/weekly-stats.json` and writes `dashboard/index.html` (or the path
set in `config.yaml` under `dashboard.output_path`).

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--open` | flag | off | Open the generated file in the default browser (local use only) |

```bash
python src/dashboard.py          # regenerate dashboard
```

---

### `src/attachment_handler.py` — Attachment processing test

| Flag | Argument | Required? | Description |
|------|----------|-----------|-------------|
| `--test-file PATH` | file path | yes (only useful flag) | Process a single file and print the classification result |

```bash
python src/attachment_handler.py --test-file attachments/other/sample.pdf
```

---

### `src/feedback_handler.py` — BCC feedback loop test

*(See Roadmap — the automation layer is not yet active.)*

| Flag | Argument | Required? | Description |
|------|----------|-----------|-------------|
| `--test-email PATH` | file path | yes (only useful flag) | Process a single feedback JSON file from `staging/feedback/` |

```bash
python src/feedback_handler.py --test-email staging/feedback/sample.json
```

---

## Privacy & Safety

- **Drafts are never sent automatically.** They appear inline in the briefing
  email and Obsidian note for you to copy-paste into your UCM reply client.
- **No data leaves the VPS** except API calls to Google (Gmail) and Anthropic (Claude).
- **Processed email IDs** are tracked locally to prevent re-processing.
- **Secrets** (`config.yaml`, `credentials.json`, `token.json`) are gitignored.

## Roadmap

### Near-term

- **BCC writing-sample loop** — When you send a reply, BCC `yourname+inbox-log@gmail.com`.
  A script (or n8n workflow) fetches those copies, strips the quoted text, and saves
  the sent text to `writing-samples/samples/` so the style profile improves over time.
  The code in `feedback_handler.py` is ready; what's needed is a Gmail filter +
  fetch script to populate `staging/feedback/` automatically.

- **Retroactive stats backfill** — The dashboard currently shows data from the first
  real run onwards. A small utility to parse Gmail history and backfill
  `weekly-stats.json` with per-day counts would make the chart immediately useful.

### Medium-term

- **Direct O365 / Microsoft Graph access** — If UCM IT enables Graph API access,
  `gmail_client.py` can be replaced with a `graph_client.py` (same interface),
  eliminating the Gmail forwarding step entirely.

- **Telegram interactive commands** — Reply to the morning Telegram ping with commands
  like `/handled 3` (mark item 3 as handled) or `/skip` (suppress today's follow-up).

- **n8n ingestion pipeline** — Workflow definitions already exist in `n8n/`. Activating
  them would allow richer pre-processing (attachment extraction on ingestion, label
  management) before emails reach the Python triage engine.

### Longer-term

- **Feedback-driven classification tuning** — Track which ACTION items Jeroen actually
  replies to (via the BCC loop) vs. ignores, and use that signal to refine the
  classification prompt automatically.

- **Calendar integration** — Cross-reference meeting invites with an existing calendar
  to flag conflicts or suggest scheduling windows in draft replies.

- **Multiple account support** — Add a second email source (e.g., a personal Gmail)
  as a separate labelled stream feeding the same triage engine.
