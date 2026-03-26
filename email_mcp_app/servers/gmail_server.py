"""
Gmail MCP Server
Exposes Gmail send/read operations as MCP tools.
"""

import json
import base64
import os
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv(Path(__file__).parent.parent / ".env")

mcp = FastMCP("gmail-server")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

TOKEN_PATH = Path(__file__).parent.parent / "token.json"
CREDS_PATH = Path(os.getenv("GMAIL_CREDENTIALS_PATH", str(Path(__file__).parent.parent / "credentials.json")))


def _get_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDS_PATH.exists():
                raise FileNotFoundError(
                    f"credentials.json not found at {CREDS_PATH}. "
                    "Download it from Google Cloud Console → APIs & Services → Credentials."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


@mcp.tool()
def send_email(to: str, subject: str, body: str, cc: str = "") -> str:
    """
    Send an email via the authenticated Gmail account.
    Returns JSON with message_id, to, subject on success.
    """
    service = _get_service()

    msg = MIMEMultipart()
    msg["to"] = to
    msg["subject"] = subject
    if cc:
        msg["cc"] = cc
    msg.attach(MIMEText(body, "plain"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = service.users().messages().send(userId="me", body={"raw": raw}).execute()

    # Get sender address
    profile = service.users().getProfile(userId="me").execute()
    sender = profile.get("emailAddress", "me")

    return json.dumps({
        "success": True,
        "message_id": result["id"],
        "from": sender,
        "to": to,
        "cc": cc,
        "subject": subject,
    })


@mcp.tool()
def list_emails(max_results: int = 10, query: str = "") -> str:
    """
    List recent emails from Gmail.
    query: Gmail search syntax e.g. 'is:unread', 'from:someone@gmail.com', 'after:2024/01/01'
    Returns JSON array with id, from, to, subject, date, snippet for each email.
    """
    service = _get_service()

    results = service.users().messages().list(
        userId="me", maxResults=max_results, q=query
    ).execute()

    messages = results.get("messages", [])
    if not messages:
        return json.dumps([])

    emails = []
    for msg in messages:
        meta = service.users().messages().get(
            userId="me",
            id=msg["id"],
            format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date"],
        ).execute()
        headers = {h["name"]: h["value"] for h in meta["payload"]["headers"]}
        emails.append({
            "id": msg["id"],
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "snippet": meta.get("snippet", ""),
        })

    return json.dumps(emails, indent=2)


@mcp.tool()
def get_email(email_id: str) -> str:
    """
    Get the full content of a specific email by its Gmail message ID.
    Returns JSON with id, from, to, subject, date, body.
    """
    service = _get_service()

    msg = service.users().messages().get(
        userId="me", id=email_id, format="full"
    ).execute()

    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}

    # Extract plain-text body
    body = ""
    payload = msg["payload"]
    if "parts" in payload:
        for part in payload["parts"]:
            if part["mimeType"] == "text/plain" and part["body"].get("data"):
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                break
    elif payload["body"].get("data"):
        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    return json.dumps({
        "id": email_id,
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "subject": headers.get("Subject", ""),
        "date": headers.get("Date", ""),
        "body": body,
    }, indent=2)


@mcp.tool()
def get_my_email_address() -> str:
    """Return the authenticated Gmail account's email address."""
    service = _get_service()
    profile = service.users().getProfile(userId="me").execute()
    return profile.get("emailAddress", "unknown")


if __name__ == "__main__":
    mcp.run()
