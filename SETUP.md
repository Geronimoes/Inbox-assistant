# Setup Guide — Inbox Briefing Assistant

This guide walks you through setting up the system. You can follow it manually
or ask Claude Code to walk you through it on your VPS.

---

## Prerequisites

- A VPS with Python 3.10+ installed
- A Gmail account (with university mail forwarded to it)
- A Google Cloud project (you already have this)
- An Anthropic API key (for Claude classification/drafting)

---

## Step 1: Google Cloud — Enable Gmail API

If you haven't already enabled the Gmail API in your existing Google Cloud project:

1. Go to https://console.cloud.google.com/
2. Select your project (or create a new one)
3. Go to **APIs & Services → Library**
4. Search for **"Gmail API"** and click **Enable**
5. Go to **APIs & Services → Credentials**
6. Click **Create Credentials → OAuth 2.0 Client ID**
   - Application type: **Desktop app** (simplest for a personal VPS tool)
   - Name: "Inbox Assistant" (or whatever you like)
7. Download the JSON file — save it as `credentials.json`

### Scopes needed

The system uses these Gmail API scopes:

| Scope | Purpose |
|-------|---------|
| `gmail.readonly` | Read emails for triage |
| `gmail.modify` | Mark emails as read, archive noise |
| `gmail.compose` | Create draft replies |
| `gmail.send` | Send the briefing email to yourself |

> **Note:** You can start with just `gmail.readonly` if you want to test
> the briefing feature first, then add the others later.

### First-time authentication

The first time you run the system, it will open a browser for OAuth consent.
Since your VPS is headless, you have two options:

**Option A — Run auth locally first:**
1. Copy `credentials.json` to your local machine
2. Run `python src/gmail_client.py --auth` locally
3. This opens a browser, you approve, and it creates `token.json`
4. Copy `token.json` back to your VPS

**Option B — Use the headless flow:**
1. Run `python src/gmail_client.py --auth` on your VPS
2. It will print a URL — open it in your phone/PC browser
3. Approve access, copy the authorization code back to the terminal

---

## Step 2: Anthropic API Key

1. Go to https://console.anthropic.com/
2. Create an API key (or use an existing one)
3. Note: This system uses Claude Sonnet for classification (cost-effective)
   - Typical usage: ~1000 emails/month ≈ $1-3/month in API costs

---

## Step 3: Configure the System

1. Copy the config template:
   ```bash
   cp config.example.yaml config.yaml
   ```

2. Edit `config.yaml` with your settings:
   ```bash
   nano config.yaml
   # Or ask Claude Code: "help me fill in config.yaml"
   ```

3. Place your credentials:
   ```bash
   # Put your Google OAuth credentials in the project root
   cp /path/to/credentials.json ~/inbox-assistant/credentials.json
   ```

---

## Step 4: Install Dependencies

```bash
cd ~/inbox-assistant
pip install -r requirements.txt
```

---

## Step 5: First Run (Test)

```bash
# Test Gmail connection
python src/gmail_client.py --test

# Run a single triage (dry run — no emails sent, no drafts created)
python src/fetch_and_triage.py --dry-run

# If that looks good, run for real
python src/fetch_and_triage.py
```

---

## Step 6: Set Up Cron Jobs

```bash
# Install the cron jobs
bash cron/install.sh

# Or manually add to crontab:
crontab -e

# Add these lines:
# Morning briefing at 6:30 AM (adjust timezone!)
30 6 * * * cd ~/inbox-assistant && python src/fetch_and_triage.py >> logs/briefing.log 2>&1

# Urgent check every 2 hours during working hours (8 AM - 8 PM)
0 8-20/2 * * * cd ~/inbox-assistant && python src/urgent_check.py >> logs/urgent.log 2>&1
```

---

## Step 7: Customize

### Classification categories

Edit `prompts/classify.md` to adjust how emails are categorized.
Default categories:

| Category | Description | Action |
|----------|-------------|--------|
| 🔴 URGENT | Needs reply today, has deadline | Alert + draft reply |
| 🟡 ACTION | Needs reply, not time-critical | Draft reply |
| 🔵 FYI | Worth knowing, no reply needed | Include in briefing |
| ⚪ NOISE | Newsletters, promos, automated | Archive or skip |

### Personal context

Edit `prompts/classify.md` to add context about your roles:

```markdown
## Context about the recipient

- Professor at a European university
- Fields: sociology, anthropology, political science, human geography
- Teaches undergraduate and graduate courses
- Supervises PhD students
- Sits on departmental committees
- Publishes in academic journals
- Languages: English (primary), Dutch (some admin correspondence)

## Priority signals

HIGH priority indicators:
- From PhD students you supervise
- Journal editors (revisions, decisions)
- Grant/funding bodies
- Department head or dean
- Messages with explicit deadlines

MEDIUM priority:
- Colleagues about shared projects
- Conference organizers
- Teaching assistants
- IT/admin with action items

LOW priority:
- Mailing list announcements
- Library notifications
- General university newsletters
- Automated system notifications
```

### Draft reply style

Edit `prompts/draft_reply.md` to match your voice. You can:
- Paste a few example emails you've sent so Claude learns your tone
- Specify formality levels (more formal for editors, warmer for students)
- Set language preferences (reply in the language the email was written in)

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| OAuth token expired | Run `python src/gmail_client.py --auth` again |
| Too many API calls | Increase the `check_interval` in config.yaml |
| Briefing too long | Adjust `max_fyi_items` in config.yaml |
| Wrong timezone | Set `timezone` in config.yaml |
| Missing emails | Check Gmail forwarding rules are working |

---

## Upgrading to Direct O365 Access (Phase 2)

If your university IT enables Microsoft Graph API access:

1. Register an app in Azure AD with `Mail.Read` delegated permissions
2. Swap `gmail_client.py` for `graph_client.py` (same interface)
3. The rest of the system stays identical — classifier, briefing,
   drafter all work the same regardless of email source

This is designed as a drop-in replacement, so the upgrade is painless.
