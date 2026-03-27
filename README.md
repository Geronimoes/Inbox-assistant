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
