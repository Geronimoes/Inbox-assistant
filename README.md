# Inbox Briefing Assistant

An AI-powered email triage system that runs on your VPS, reads your Gmail inbox,
and delivers a daily morning briefing with prioritized emails, draft replies, and
urgent alerts.

## What It Does

Every morning (and optionally throughout the day), the system:

1. **Fetches** new emails from Gmail via the API
2. **Classifies** each email using the Claude API into priority tiers
3. **Generates** a formatted briefing email sent to your inbox
4. **Drafts replies** for routine messages, saved as Gmail drafts
5. **Alerts immediately** if something is truly urgent

## Architecture

```
Gmail Inbox (university mail forwarded here)
      │
      ▼
┌──────────────────────────────────────────┐
│  VPS Cron Jobs                           │
│                                          │
│  fetch_and_triage.py  (runs at 6:30 AM)  │
│       │                                  │
│       ├─→ Claude API: classify + draft   │
│       ├─→ Send briefing email to self    │
│       └─→ Save draft replies in Gmail    │
│                                          │
│  urgent_check.py  (runs every 2 hours)   │
│       │                                  │
│       └─→ Send alert email if urgent     │
└──────────────────────────────────────────┘
```

## Project Structure

```
inbox-assistant/
├── README.md              ← You are here
├── SETUP.md               ← Step-by-step setup instructions
├── config.example.yaml    ← Configuration template
├── src/
│   ├── gmail_client.py    ← Gmail API connection & operations
│   ├── classifier.py      ← Claude API email classification
│   ├── briefing.py        ← Morning briefing generator
│   ├── drafter.py         ← Draft reply composer
│   ├── alerter.py         ← Urgent email alerter
│   └── utils.py           ← Shared utilities
├── templates/
│   └── briefing.html      ← Email template for the morning briefing
├── prompts/
│   ├── classify.md        ← Classification prompt for Claude
│   └── draft_reply.md     ← Reply drafting prompt for Claude
├── data/
│   └── processed.json     ← Tracks which emails have been processed
├── cron/
│   └── install.sh         ← Cron job installer
└── requirements.txt       ← Python dependencies
```

## Quick Start

1. Read `SETUP.md` and follow the steps
2. Or, if using Claude Code on your VPS, just say:

   > "Read the SETUP.md in ~/inbox-assistant and walk me through it step by step"

## Privacy & Safety Notes

- **Read-only by default.** The system reads emails and creates drafts.
  It never sends emails on your behalf (except the briefing to yourself).
- **No data leaves your VPS** except API calls to Google and Anthropic.
- **Processed email IDs** are stored locally to avoid re-processing.
- **You review all drafts** before sending — nothing goes out automatically.
