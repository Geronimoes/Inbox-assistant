# CLAUDE.md — Inbox Assistant

## What This Project Is

An AI-powered email triage assistant for a university professor (social sciences, European university). It:
- Receives emails via n8n (which fetches from Gmail and drops JSON files in `staging/`)
- Classifies each email into URGENT / ACTION / FYI / NOISE using an LLM
- Generates draft replies in the professor's writing style
- Produces a daily briefing as an Obsidian note (and HTML email as fallback)
- Sends urgent alerts via Telegram
- Maintains a writing style corpus that improves drafts over time via a BCC feedback loop
- Saves and classifies email attachments

The professor is **not a programmer**. All code changes should be made by Claude Code following explicit instructions. Keep code clear, well-commented, and prefer verbose error messages over silent failures.

## Architecture Authority

Until CLAUDE.md is fully settled, `conversation.md` in this directory is the authoritative source for all architectural decisions. If there is any ambiguity about why something is designed the way it is, read that file.

## Directory Conventions

| Directory | Purpose |
|-----------|---------|
| `src/` | All Python modules. Always run scripts from the project root, not from inside `src/`. |
| `prompts/` | LLM system prompts (plain Markdown). Loaded at runtime — edit freely without touching code. |
| `staging/` | n8n drops email JSON files here. Python picks them up, then moves to `staging/processed/`. |
| `staging/feedback/` | n8n drops BCC feedback emails here. Processed by `feedback_handler.py`. |
| `writing-samples/samples/` | Auto-populated by the BCC loop. Never commit — contains real email text. |
| `writing-samples/curated/` | Manually managed best-example emails for style training. |
| `writing-samples/style-profile.md` | LLM-generated weekly summary of the professor's writing style. |
| `attachments/` | Saved email attachments, sorted into subdirectories by type. |
| `data/` | State files: `processed.json` (thread tracking), `weekly-stats.json` (dashboard data). |
| `logs/` | Cron job output logs. Rotated automatically. |
| `dashboard/` | Generated static HTML. Served by Caddy as a file server. |
| `n8n/` | n8n workflow JSON files (importable) and setup documentation. |
| `cron/` | Cron installer script and Caddy config snippet. |

## Configuration

- Copy `config.example.yaml` to `config.yaml` before first run.
- `config.yaml` is the single source of all settings and API keys. There is no `.env` file.
- All new features are gated behind config values — if a section is missing or commented out, that feature is silently skipped.

## Files That Must NEVER Be Committed

```
config.yaml
credentials.json
token.json
writing-samples/samples/
data/processed.json
data/weekly-stats.json
```

## Entry Points

| Command | Purpose | When to use |
|---------|---------|-------------|
| `python src/fetch_and_triage.py --dry-run` | Full triage, no side effects | Testing |
| `python src/fetch_and_triage.py` | Full production run | Cron at 06:30 |
| `python src/fetch_and_triage.py --regenerate-style` | Rebuild writing style profile | Cron Sunday 02:00 |
| `python src/urgent_check.py` | Check for urgent items only | Cron every 2 hrs 08:00–20:00 |
| `python src/dashboard.py` | Regenerate dashboard HTML | Cron Sunday 03:00 |
| `python src/gmail_client.py --auth --headless` | Re-authenticate Gmail OAuth | When token expires |

## Testing Components in Isolation

| Component | Test command |
|-----------|-------------|
| LLM client | `python src/llm_client.py --test` |
| Notifier | `python src/notifier.py --test` |
| Attachment handler | `python src/attachment_handler.py --test-file attachments/other/sample.pdf` |
| Feedback handler | `python src/feedback_handler.py --test-email staging/feedback/sample.json` |
| Style manager | Drop a .txt in `writing-samples/samples/`, run `python src/fetch_and_triage.py --regenerate-style --dry-run` |

## Common Tasks

| Task | How to do it |
|------|-------------|
| Change how emails are classified | Edit `prompts/classify.md` |
| Change the tone or style of draft replies | Edit `prompts/draft_reply.md` |
| Add a writing sample manually | Put a plain .txt file in `writing-samples/curated/` |
| Switch which LLM model is used for a task | Edit the relevant entry under `llm.tasks` in `config.yaml` |
| Re-authenticate Gmail (token expired) | Run `python src/gmail_client.py --auth --headless` |
| Add a new attachment type | Edit `src/attachment_handler.py` and `prompts/attachment_classify.md` |
| Change the Telegram alert format | Edit `src/notifier.py` |
| Change the Obsidian briefing format | Edit `briefing.py` → `generate_markdown()` |
| Import n8n workflows | See `n8n/README.md` |

## n8n Integration

n8n is the email ingestion layer. It fetches emails from Gmail, extracts attachments, and writes JSON files to `staging/`. The Python triage engine picks up these files and falls back to direct Gmail API fetch if `staging/` is empty.

The exact JSON schema that n8n must produce is documented in `n8n/README.md`.

## LLM Configuration

All LLM calls are routed through `src/llm_client.py`. Each task (classification, draft_replies, etc.) specifies a provider and model in `config.yaml`:

```yaml
llm:
  tasks:
    classification: {provider: anthropic, model: claude-haiku-4-5-20251001}
    draft_replies: {provider: anthropic, model: claude-sonnet-4-6}
```

To start with only Anthropic (minimum working setup), set all tasks to `provider: anthropic`. Add other providers incrementally.

## Error Philosophy

- **Never silently fall back to a different LLM provider** — if a configured provider's API key is missing, raise a clear error naming which key is needed.
- **Always log what's happening** — every major step in the triage pipeline should print to stdout so cron logs are useful for debugging.
- **Gmail token expiry** is the most common operational failure. If it happens, the script should print: `Gmail authentication failed. Run: python src/gmail_client.py --auth --headless`

## Cron Schedule (after full setup)

```
30 6    * * *   python /home/jeroen/projects/inbox-assistant/src/fetch_and_triage.py
0  8-20/2 * * * python /home/jeroen/projects/inbox-assistant/src/urgent_check.py
0  2    * * 0   python /home/jeroen/projects/inbox-assistant/src/fetch_and_triage.py --regenerate-style
0  3    * * 0   python /home/jeroen/projects/inbox-assistant/src/dashboard.py
```
