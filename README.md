# Inbox Briefing Assistant

An AI-powered email triage system for a university professor. It runs on a VPS,
reads forwarded work email from Gmail, and delivers a daily briefing with
prioritised emails, draft replies, and urgent alerts.

## What It Does

Every morning at 6:30 AM the system:

1. **Fetches** emails tagged with `_UCM-redirect` from Gmail (university mail forwarded here)
2. **Classifies** each email into URGENT / ACTION / FYI / NOISE using Claude
3. **Drafts replies** for action items in the correct language and tone
4. **Generates a briefing** — sent to the university email address and written as an Obsidian daily note
5. **Pings Telegram** with a count summary (urgent, action, FYI, noise)
6. **Alerts via Telegram** if urgent emails arrive between checks (every 2 hours, 8 AM–8 PM)
7. **Tracks stats** for the weekly dashboard

## Architecture

```
University mail (UCM Exchange)
      │ forwarded via Gmail filter
      ▼
Gmail inbox  ──label: _UCM-redirect──►  Gmail API
                                              │
                              fetch_and_triage.py  (cron 06:30)
                                              │
                        ┌─────────────────────┼──────────────────────┐
                        ▼                     ▼                      ▼
               Claude API              Obsidian vault          Telegram bot
               classify + draft        daily note              morning ping
                        │
                        ▼
               Briefing email → university address
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
│   ├── gmail_client.py        ← Gmail API: fetch, send, draft, archive
│   ├── llm_client.py          ← Multi-provider LLM abstraction
│   ├── classifier.py          ← Email classification
│   ├── drafter.py             ← Draft reply composer
│   ├── briefing.py            ← HTML email + Obsidian Markdown generator
│   ├── style_manager.py       ← Writing style corpus management
│   ├── feedback_handler.py    ← BCC feedback loop processor (see Roadmap)
│   ├── attachment_handler.py  ← PDF/DOCX/ICS attachment classifier
│   ├── notifier.py            ← Telegram notifications
│   ├── urgent_check.py        ← Runs every 2 hours, alerts on new urgent mail
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
│   ├── processed.json         ← Processed email IDs + thread state (never commit)
│   └── weekly-stats.json      ← Dashboard data (never commit)
│
├── dashboard/                 ← Generated HTML (also written to Caddy sites/)
├── logs/                      ← Cron output logs
├── n8n/                       ← n8n workflow JSONs (future use)
└── cron/
    ├── install.sh             ← Installs all cron jobs
    └── caddy-dashboard.snippet ← Caddy config reference (see SETUP.md)
```

## Cron Schedule

| Time | Job | Purpose |
|------|-----|---------|
| 06:30 daily | `fetch_and_triage.py` | Morning briefing |
| Every 2 hrs, 08:00–20:00 | `urgent_check.py` | Urgent alerts |
| Sunday 02:00 | `fetch_and_triage.py --regenerate-style` | Rebuild writing style profile |
| Sunday 03:00 | `dashboard.py` | Refresh dashboard HTML |

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
| `--regenerate-style` | flag | off | Rebuild `writing-samples/style-profile.md` from the writing corpus, then exit without processing email |

**Examples:**
```bash
python src/fetch_and_triage.py --dry-run              # safe preview
python src/fetch_and_triage.py --dry-run --hours 48   # preview last 2 days
python src/fetch_and_triage.py --hours 336 --no-drafts  # retroactive 2-week import
python src/fetch_and_triage.py --regenerate-style     # rebuild style profile
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
