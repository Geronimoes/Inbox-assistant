"""
Gmail API client for the Inbox Briefing Assistant.

Handles authentication, fetching emails, creating drafts, and sending
the briefing email. Uses Google's official API client library.
"""

import http.server
import os
import sys
import json
import base64
import argparse
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# If modifying scopes, delete token.json and re-authenticate.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
]


def _run_headless_auth(flow):
    """Run OAuth via a local HTTP server on port 8080 (for SSH tunnel use).

    Waits for Google's redirect to localhost:8080/?code=... and exchanges it
    for a token. Only captures requests that actually contain an auth code,
    ignoring browser preflight/favicon requests.
    """
    result = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                # Store the full redirect URL — needed for PKCE token exchange
                result["authorization_response"] = (
                    "http://localhost:8080" + self.path
                )
                result["state"] = params["state"][0] if "state" in params else ""
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<h1>Authentication successful!</h1>"
                    b"<p>You can close this tab and return to the terminal.</p>"
                )
            else:
                # Favicon, preflight, or direct visit — ignore
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<p>Waiting for Google to redirect here after you "
                    b"approve access. Please use the URL from the terminal.</p>"
                )

        def log_message(self, *args):
            pass  # suppress request logs

    server = http.server.HTTPServer(("localhost", 8080), _Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    flow.redirect_uri = "http://localhost:8080/"
    auth_url, _ = flow.authorization_url(prompt="consent")
    print(f"Please visit this URL to authorise:\n\n  {auth_url}\n")
    print("(Do not visit localhost:8080 directly — Google will redirect there"
          " automatically after you approve.)\n")

    while not result.get("authorization_response"):
        time.sleep(0.2)

    server.shutdown()

    # Sync the state so the CSRF check passes
    if result.get("state"):
        flow.oauth2session._state = result["state"]

    # Allow http://localhost redirect (oauthlib requires https by default)
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    flow.fetch_token(authorization_response=result["authorization_response"])
    del os.environ["OAUTHLIB_INSECURE_TRANSPORT"]
    return flow.credentials


class GmailClient:
    """Thin wrapper around the Gmail API."""

    def __init__(self, credentials_file: str = "credentials.json",
                 token_file: str = "token.json"):
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.service = None

    def authenticate(self, headless: bool = False) -> None:
        """Authenticate with Gmail API using OAuth2.
        
        On first run, opens a browser (or prints a URL for headless servers)
        for the user to approve access. Subsequent runs use the saved token.
        """
        creds = None

        if os.path.exists(self.token_file):
            creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, SCOPES
                )
                if headless:
                    # For headless VPS: start a local auth server on port 8080,
                    # then tunnel it via SSH from your local machine:
                    #   ssh -L 8080:localhost:8080 <tailscale-ip>
                    # then visit http://localhost:8080 in your local browser.
                    print("\nStarting local auth server on port 8080...")
                    print("On your local machine, run:")
                    print("  ssh -L 8080:localhost:8080 <your-vps-hostname>")
                    print("Then open http://localhost:8080 in your browser.\n")
                    creds = _run_headless_auth(flow)
                else:
                    # Opens browser locally
                    creds = flow.run_local_server(port=0)

            with open(self.token_file, "w") as token:
                token.write(creds.to_json())

        self.service = build("gmail", "v1", credentials=creds)
        print("✓ Gmail API authenticated successfully.")

    def fetch_recent_emails(self, hours: int = 24,
                            labels: list[str] | None = None,
                            max_results: int = 100) -> list[dict]:
        """Fetch emails from the last N hours.
        
        Returns a list of dicts with: id, thread_id, subject, from, to, date,
        snippet, body_text, labels.
        """
        if not self.service:
            raise RuntimeError("Not authenticated. Call authenticate() first.")

        after_timestamp = int(
            (datetime.now() - timedelta(hours=hours)).timestamp()
        )
        query = f"after:{after_timestamp}"

        if labels is None:
            labels = ["INBOX"]

        results = self.service.users().messages().list(
            userId="me",
            q=query,
            labelIds=labels,
            maxResults=max_results,
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            print(f"No new emails in the last {hours} hours.")
            return []

        emails = []
        for msg_meta in messages:
            msg = self.service.users().messages().get(
                userId="me",
                id=msg_meta["id"],
                format="full",
            ).execute()
            emails.append(self._parse_message(msg))

        print(f"✓ Fetched {len(emails)} emails from the last {hours} hours.")
        return emails

    def _parse_message(self, msg: dict) -> dict:
        """Extract useful fields from a Gmail API message object."""
        headers = {h["name"].lower(): h["value"]
                   for h in msg["payload"]["headers"]}

        body_text = self._extract_body(msg["payload"])

        return {
            "id": msg["id"],
            "thread_id": msg["threadId"],
            "subject": headers.get("subject", "(no subject)"),
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "date": headers.get("date", ""),
            "snippet": msg.get("snippet", ""),
            "body_text": body_text[:3000],  # Truncate very long emails
            "labels": msg.get("labelIds", []),
        }

    def _extract_body(self, payload: dict) -> str:
        """Recursively extract plain text body from message payload."""
        if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

        for part in payload.get("parts", []):
            text = self._extract_body(part)
            if text:
                return text

        return ""

    def create_draft(self, to: str, subject: str, body: str,
                     thread_id: str | None = None,
                     in_reply_to: str | None = None) -> dict:
        """Create a draft reply in Gmail.
        
        If thread_id is provided, the draft appears as a reply in the thread.
        """
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        draft_body = {"message": {"raw": raw}}
        if thread_id:
            draft_body["message"]["threadId"] = thread_id

        draft = self.service.users().drafts().create(
            userId="me", body=draft_body
        ).execute()

        print(f"  ✓ Draft created: {subject}")
        return draft

    def send_email(self, to: str, subject: str, body_html: str) -> dict:
        """Send an HTML email (used for briefing delivery)."""
        message = MIMEMultipart("alternative")
        message["to"] = to
        message["subject"] = subject
        message.attach(MIMEText(body_html, "html"))

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        sent = self.service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()

        print(f"✓ Briefing email sent: {subject}")
        return sent

    def archive_messages(self, message_ids: list[str]) -> None:
        """Archive messages by removing the INBOX label."""
        for msg_id in message_ids:
            self.service.users().messages().modify(
                userId="me",
                id=msg_id,
                body={"removeLabelIds": ["INBOX"]},
            ).execute()

        print(f"✓ Archived {len(message_ids)} messages.")


def main():
    parser = argparse.ArgumentParser(description="Gmail client for Inbox Assistant")
    parser.add_argument("--auth", action="store_true", help="Run authentication flow")
    parser.add_argument("--headless", action="store_true", help="Use headless auth (for VPS)")
    parser.add_argument("--test", action="store_true", help="Test connection")
    args = parser.parse_args()

    # Look for credentials in project root
    project_root = Path(__file__).parent.parent
    creds_file = project_root / "credentials.json"
    token_file = project_root / "token.json"

    client = GmailClient(str(creds_file), str(token_file))

    if args.auth:
        client.authenticate(headless=args.headless)
        print("Authentication complete! Token saved.")
        return

    if args.test:
        client.authenticate()
        emails = client.fetch_recent_emails(hours=4, max_results=5)
        for e in emails:
            print(f"  [{e['date'][:20]}] {e['from'][:40]} — {e['subject'][:60]}")
        return


if __name__ == "__main__":
    main()
