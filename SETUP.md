# Setup Guide — Inbox Briefing Assistant

This guide reflects the current production setup on the VPS. Follow it if you
ever need to rebuild from scratch, or use it as a reference.

---

## Prerequisites

- VPS (Hetzner) running Ubuntu, accessible via Tailscale
- Python 3.12 with a virtualenv at `env/`
- Gmail account with university mail forwarded via `_UCM-redirect` label
- Anthropic API key
- Telegram bot (created via @BotFather)
- Caddy reverse proxy running in Docker
- Cloudflare Access protecting the dashboard subdomain

---

## Step 1: Clone and set up Python environment

```bash
cd ~/projects
git clone git@github.com:Geronimoes/Inbox-assistant.git inbox-assistant
cd inbox-assistant
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

---

## Step 2: Configure

```bash
cp config.example.yaml config.yaml
nano config.yaml   # fill in all values (see below)
```

Key config values to set:

| Section | Key | Value |
|---------|-----|-------|
| `gmail.your_email` | Your Gmail address | `jeroenm@gmail.com` |
| `gmail.scan_labels` | Label for UCM-forwarded mail | `_UCM-redirect` |
| `briefing.send_to` | Where to receive briefings | UCM address |
| `briefing.timezone` | Your timezone | `Europe/Amsterdam` |
| `obsidian.vault_path` | Path to Obsidian vault | `~/syncthing/data/Notes` |
| `llm.providers.anthropic.api_key` | Anthropic key | `sk-ant-...` |
| `notifications.telegram.bot_token` | From @BotFather | `123456:ABC...` |
| `notifications.telegram.chat_id` | From @userinfobot | `987654321` |
| `dashboard.output_path` | Caddy static files dir | `/home/jeroen/caddy/sites/inbox-dashboard/index.html` |
| `drafts.save_to_gmail` | Skip Gmail drafts (use Obsidian instead) | `false` |

---

## Step 3: Gmail OAuth credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create or select a project → **APIs & Services → Library → Gmail API → Enable**
3. **Credentials → Create → OAuth 2.0 Client ID** — type: **Desktop app**
4. Download as `credentials.json` and place it in the project root

### Authenticate (headless VPS)

Since the VPS has no browser, use SSH port forwarding via Tailscale:

**On your local machine** (PowerShell):
```powershell
ssh -L 8080:localhost:8080 jeroen@100.103.152.23 -i .\.ssh\hetzner_key
```

**On the VPS:**
```bash
source env/bin/activate
python src/gmail_client.py --auth --headless
```

Visit the printed Google URL in your local browser. After approving, your browser
is redirected to `localhost:8080` — the tunnel delivers this to the VPS.
`token.json` is saved automatically.

**Re-authentication** (when token expires, usually after 6 months):
```bash
python src/gmail_client.py --auth --headless
```

---

## Step 4: Test each component

```bash
source env/bin/activate

# Gmail connection
python src/gmail_client.py --test

# Telegram notification
python src/notifier.py --test

# Full dry run (no emails sent, no state saved)
python src/fetch_and_triage.py --dry-run

# Check generated Obsidian note
cat ~/syncthing/data/Notes/inbox-briefings/$(date +%Y-%m-%d).md
```

---

## Step 5: Install cron jobs

```bash
bash cron/install.sh
crontab -l   # verify
```

This installs:
- `06:30` daily — morning briefing
- Every 2 hrs `08:00–20:00` — urgent check
- Sunday `02:00` — writing style regeneration
- Sunday `03:00` — dashboard refresh

---

## Step 6: Dashboard (Caddy)

The dashboard is served from `/home/jeroen/caddy/sites/inbox-dashboard/`.
The `dashboard.output_path` in `config.yaml` writes directly to this directory.

The Caddyfile block (already configured):
```
@inbox host inbox.moes.me
handle @inbox {
    root * /srv/inbox-dashboard
    file_server
    encode gzip
}
```

Protect with Cloudflare Access: Zero Trust → Applications → add `inbox.moes.me`.

---

## Step 7: Writing style profile

Add a few sent emails as plain text files to `writing-samples/curated/`, then:

```bash
python src/fetch_and_triage.py --regenerate-style
```

The profile is regenerated automatically every Sunday at 2 AM from then on.
Drafts will use it on the next run.

---

## Step 8: Retroactive email import

To process emails from the past N hours (useful for first-time setup):

```bash
# Preview (no state saved, no email sent)
python src/fetch_and_triage.py --dry-run --hours 336   # 2 weeks

# Real run — classifies, sends one large briefing, saves state
python src/fetch_and_triage.py --hours 336 --no-drafts
```

`--no-drafts` skips generating replies for old emails you won't use.
All processed emails will appear under today's date in `weekly-stats.json`.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Gmail authentication failed` | Run `python src/gmail_client.py --auth --headless` |
| `Invalid label: _UCM-redirect` | Label name is resolved automatically — check it exists in Gmail |
| `Telegram API error 404` | Check for trailing spaces in `bot_token` in `config.yaml` |
| Briefing not arriving | Check `logs/briefing.log`; verify `briefing.send_to` is set |
| Obsidian note not written | Check `obsidian.vault_path` exists and is writable |
| Dashboard shows no data | Run a real (non-dry-run) `fetch_and_triage.py` first |
| Draft replies contain em-dashes | This is a prompt bug — `prompts/draft_reply.md` has a rule against them |

---

## File locations (quick reference)

| File | Purpose | Commit? |
|------|---------|---------|
| `config.yaml` | All settings and API keys | **Never** |
| `credentials.json` | Google OAuth client secret | **Never** |
| `token.json` | Google OAuth token | **Never** |
| `data/processed.json` | Processed email IDs | **Never** |
| `data/weekly-stats.json` | Dashboard data | **Never** |
| `writing-samples/samples/` | BCC-collected sent emails | **Never** |
| `writing-samples/curated/` | Manually added writing samples | **Never** |
| `writing-samples/style-profile.md` | Generated style guide | OK to commit |
| `prompts/*.md` | LLM prompts | OK to commit |
