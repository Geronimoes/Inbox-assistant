# n8n Integration Guide

This directory contains importable n8n workflows that handle email ingestion
for the Inbox Assistant. n8n acts as the bridge between Gmail and the Python
triage engine — it handles authentication, fetching, and filtering, so the
Python code never needs direct Gmail API access during normal operation.

## Overview

| Workflow | File | Purpose |
|---|---|---|
| Email Ingestion | `workflow-email-ingestion.json` | Fetches university emails every 30 min, writes to `staging/` |
| BCC Feedback Loop | `workflow-bcc-feedback.json` | Monitors your BCC alias, feeds writing style corpus |

---

## Prerequisites

Before importing the workflows, make sure:

1. **n8n is running** on your VPS (you already have this)
2. **Gmail credential is set up in n8n** (see Step 1 below)
3. **The project directory is at** `/home/jeroen/projects/inbox-assistant/`
   (if it's somewhere else, you'll update two paths in the workflows)

---

## Step 1 — Add Gmail Credential to n8n

The workflows use the same Gmail account your forwarding is already set up on.

1. Open your n8n instance in a browser
2. Go to **Settings → Credentials → New Credential**
3. Search for **Gmail OAuth2**
4. Fill in the OAuth details:
   - You can use the same `credentials.json` file from the project
   - Or create a new OAuth client in Google Cloud Console for n8n specifically
5. Click **Connect my account** and complete the OAuth flow
6. Name it something recognisable, e.g. **"Gmail (Professor)"**
7. **Note the credential ID** — you'll see it in the URL when you open it
   (e.g., `https://your-n8n.domain/credentials/abc123` → ID is `abc123`)

---

## Step 2 — Import the Email Ingestion Workflow

1. In n8n, go to **Workflows → Import from File**
2. Select `n8n/workflow-email-ingestion.json`
3. The workflow opens with 5 nodes. You need to configure **2 things**:

### Configure node: "Fetch University Emails"
- Click the node to open it
- Under **Credential**, select the Gmail credential you created in Step 1
- Under **Filters → Search**, find the `q` field and update the label filter:
  - Current value: `label:university newer_than:1d`
  - Replace `university` with **your actual Gmail label name** for forwarded university mail
  - To find your label name: open Gmail → look in the left sidebar for the label applied by your forwarding filter
  - Common label names: `university`, your university's domain, `work`, or the name you gave it

### Configure node: "Write to Staging"
- Only needed if your project is **not** at `/home/jeroen/projects/inbox-assistant/`
- If it is at that path, no change needed

4. Click **Save**

### Optional: "Trigger Triage Engine" node
The last node in the workflow optionally runs the Python triage script
immediately after writing to staging, rather than waiting for the cron job.

- **Keep it enabled** if you want near-real-time processing (emails processed within 30 min)
- **Disable it** if you prefer to let the 6:30 AM cron job handle it (emails batch-processed once daily)

To disable: click the node → toggle "Disabled" in the top right.

---

## Step 3 — Test the Email Ingestion Workflow

Before activating, test manually:

1. Click **Execute Workflow** (the play button) to run it once
2. Watch the nodes light up green — each should show the number of items processed
3. After it runs, check the staging directory:
   ```
   ls ~/projects/inbox-assistant/staging/
   ```
   You should see `.json` files, one per email fetched

4. Look at one to confirm the format is correct:
   ```
   cat ~/projects/inbox-assistant/staging/email-<ID>.json | python -m json.tool
   ```
5. Run the Python script in dry-run to confirm it picks them up:
   ```
   cd ~/projects/inbox-assistant
   source env/bin/activate
   python src/fetch_and_triage.py --dry-run
   ```
   You should see "Loaded N email(s) from staging/" in the output.

If the test looks good, click the toggle at the top of the workflow to **Activate** it.

---

## Step 4 — Import the BCC Feedback Workflow

1. In n8n, go to **Workflows → Import from File**
2. Select `n8n/workflow-bcc-feedback.json`
3. Configure **2 things**:

### Configure node: "Fetch BCC Feedback Emails"
- Set the Gmail credential (same one as above)
- In the **Filters → Search** field, find the `q` value:
  - Current: `to:YOUR_GMAIL_ADDRESS+inbox-log@gmail.com newer_than:1d`
  - Replace `YOUR_GMAIL_ADDRESS` with your actual Gmail address
  - Example: `to:j.vanderberg+inbox-log@gmail.com newer_than:1d`
  - The `+inbox-log` alias does **not** need to be created — Gmail handles `+` aliases automatically

### Configure node: "Write to Feedback Staging"
- Only update if your project path differs from the default

4. Save and activate

### How to use the BCC feedback loop

From now on, whenever you reply to an email that the system drafted or flagged, simply **add your BCC alias to the BCC field** before sending:

```
BCC: yourname+inbox-log@gmail.com
```

This tells the system:
1. ✓ This thread is **handled** — remove it from future "needs reply" lists
2. ✓ This email is a **writing sample** — add it to the style corpus

You don't need to do this for every email — just for replies where the draft quality matters to you, or when you want to mark something as definitively handled.

---

## Staging JSON Schema

This is the exact format every file in `staging/` must match. The Python
script validates incoming files against this schema and skips any that are
malformed (printing a clear error message).

```json
{
  "id": "18e4a2b9c1d3f5a7",
  "thread_id": "18e4a2b9c1d3f5a7",
  "subject": "Re: Thesis supervision meeting",
  "from": "Marta Kowalski <m.kowalski@student.university.edu>",
  "to": "j.professor@university.edu",
  "date": "Thu, 27 Mar 2026 09:15:00 +0100",
  "snippet": "Dear Professor, I wanted to ask about...",
  "body_text": "Dear Professor,\n\nI wanted to ask about our next supervision meeting...",
  "labels": ["INBOX", "university"],
  "attachments": [
    {
      "filename": "thesis-chapter3.pdf",
      "mime_type": "application/pdf",
      "attachment_id": "ANGjdJ8...",
      "local_path": ""
    }
  ]
}
```

**Required fields:** `id`, `thread_id`, `subject`, `from`, `to`, `date`, `snippet`, `body_text`

**Optional fields:** `labels` (defaults to `[]`), `attachments` (defaults to `[]`)

**Notes:**
- `body_text` should be plain text, max 3000 characters (longer text is fine, it will just be truncated by the Python classifier)
- `date` should be in RFC 2822 format (as Gmail sends it) but the system is tolerant of other formats
- `attachments[].local_path` can be empty string if attachments have not yet been saved to disk — the attachment handler will skip items with no local path

---

## Troubleshooting

**Workflow runs but no files appear in staging/**
- Check that the Gmail label filter matches your actual label name
- Check the Execute Command node output for permission errors
- Verify the path in the write command matches your actual project directory

**"missing fields" errors in Python output**
- The JSON schema from n8n doesn't match the required fields
- Open the "Transform to Staging Format" Code node in n8n and compare its output to the schema above
- Run the workflow with a test execution and inspect the output of each node

**Gmail credential keeps expiring in n8n**
- This is a known Google OAuth limitation. n8n usually handles token refresh automatically.
- If it stops working, re-authenticate the credential in n8n Settings → Credentials

**The triage engine isn't picking up staged files**
- Check if files are in `staging/` or have already been moved to `staging/processed/`
- If they were moved, the script already processed them — check `logs/briefing.log`
- If staging is empty, run: `python src/fetch_and_triage.py --dry-run` to see the full output

---

## Manual Testing (without n8n)

You can test the staging integration at any time by dropping a manually crafted
JSON file into `staging/`:

```bash
cat > ~/projects/inbox-assistant/staging/test-email.json << 'EOF'
{
  "id": "test-001",
  "thread_id": "test-thread-001",
  "subject": "Test email from n8n simulation",
  "from": "Test Sender <test@example.com>",
  "to": "professor@gmail.com",
  "date": "Thu, 27 Mar 2026 10:00:00 +0100",
  "snippet": "This is a test email to verify staging works.",
  "body_text": "Dear Professor,\n\nThis is a test email to verify that the n8n staging integration is working correctly.\n\nBest regards,\nTest",
  "labels": ["INBOX"],
  "attachments": []
}
EOF
```

Then run:
```bash
cd ~/projects/inbox-assistant
source env/bin/activate
python src/fetch_and_triage.py --dry-run
```

You should see the test email classified and included in the preview output.
After the run, check that `test-email.json` has been moved to `staging/processed/`.
