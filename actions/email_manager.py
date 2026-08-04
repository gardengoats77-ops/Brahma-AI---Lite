"""
Email Manager Plugin for REX
Provides Gmail and Outlook integration: read, compose, search, and organize emails.
Supports OAuth2 authentication for both providers.
"""

import json
import base64
import re
import os
from pathlib import Path
from typing import Optional
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import parsedate_to_datetime

import requests
from core.error_handler import log_error


BASE_DIR = Path(__file__).parent.parent
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
EMAIL_CACHE_DIR = BASE_DIR / "config" / "email_cache"
EMAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _get_api_keys() -> dict:
    """Load API keys from config."""
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_oauth_tokens(provider: str) -> dict:
    """Load OAuth tokens for a provider."""
    token_file = EMAIL_CACHE_DIR / f"{provider}_tokens.json"
    try:
        if token_file.exists():
            return json.loads(token_file.read_text(encoding="utf-8"))
    except Exception as _e:
        log_error(_e, context="actions.email_manager", severity="debug")
    return {}


def _save_oauth_tokens(provider: str, tokens: dict) -> None:
    """Save OAuth tokens for a provider."""
    token_file = EMAIL_CACHE_DIR / f"{provider}_tokens.json"
    token_file.write_text(json.dumps(tokens, indent=2), encoding="utf-8")


def _refresh_gmail_token() -> str:
    """Refresh Gmail OAuth token."""
    tokens = _load_oauth_tokens("gmail")
    if not tokens.get("refresh_token"):
        return ""

    keys = _get_api_keys()
    client_id = keys.get("gmail_client_id", "")
    client_secret = keys.get("gmail_client_secret", "")

    if not client_id or not client_secret:
        return ""

    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": tokens["refresh_token"],
        "grant_type": "refresh_token",
    })

    if resp.status_code == 200:
        data = resp.json()
        tokens["access_token"] = data["access_token"]
        tokens["expires_in"] = data.get("expires_in", 3600)
        _save_oauth_tokens("gmail", tokens)
        return data["access_token"]
    return ""


def _refresh_outlook_token() -> str:
    """Refresh Outlook OAuth token."""
    tokens = _load_oauth_tokens("outlook")
    if not tokens.get("refresh_token"):
        return ""

    keys = _get_api_keys()
    client_id = keys.get("outlook_client_id", "")
    client_secret = keys.get("outlook_client_secret", "")

    if not client_id or not client_secret:
        return ""

    resp = requests.post("https://login.microsoftonline.com/common/oauth2/v2.0/token", data={
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": tokens["refresh_token"],
        "grant_type": "refresh_token",
        "scope": "Mail.Read Mail.Send Mail.ReadWrite",
    })

    if resp.status_code == 200:
        data = resp.json()
        tokens["access_token"] = data["access_token"]
        tokens["expires_in"] = data.get("expires_in", 3600)
        _save_oauth_tokens("outlook", tokens)
        return data["access_token"]
    return ""


def _get_gmail_access_token() -> str:
    """Get valid Gmail access token."""
    tokens = _load_oauth_tokens("gmail")
    return tokens.get("access_token", "") or _refresh_gmail_token()


def _get_outlook_access_token() -> str:
    """Get valid Outlook access token."""
    tokens = _load_oauth_tokens("outlook")
    return tokens.get("access_token", "") or _refresh_outlook_token()


# ═══════════════════════════════════════════════════════════════════
# Gmail Implementation
# ═══════════════════════════════════════════════════════════════════

def gmail_read_emails(query: str = "is:unread", max_results: int = 10) -> str:
    """Read emails from Gmail using the Gmail API."""
    token = _get_gmail_access_token()
    if not token:
        return "❌ Gmail not authenticated. Run: REX, authenticate Gmail"

    headers = {"Authorization": f"Bearer {token}"}

    # Search emails
    resp = requests.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
        headers=headers,
        params={"q": query, "maxResults": max_results}
    )

    if resp.status_code != 200:
        return f"❌ Gmail API error: {resp.status_code} - {resp.text[:200]}"

    messages = resp.json().get("messages", [])
    if not messages:
        return f"📭 No emails found for query: {query}"

    output = f"📧 Gmail Inbox ({len(messages)} emails)\n"
    output += "=" * 50 + "\n\n"

    for msg_stub in messages[:max_results]:
        msg_resp = requests.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_stub['id']}",
            headers=headers,
            params={"format": "metadata", "metadataHeaders": "From,Subject,Date"}
        )

        if msg_resp.status_code == 200:
            msg = msg_resp.json()
            headers_list = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

            from_addr = headers_list.get("From", "Unknown")
            subject = headers_list.get("Subject", "(No Subject)")
            date = headers_list.get("Date", "")
            snippet = msg.get("snippet", "")[:100]

            # Check if read
            labels = msg.get("labelIds", [])
            is_unread = "UNREAD" in labels
            status = "🔵" if is_unread else "⚪"

            output += f"{status} From: {from_addr}\n"
            output += f"   Subject: {subject}\n"
            output += f"   Date: {date}\n"
            output += f"   Preview: {snippet}...\n\n"

    return output


def gmail_search_emails(query: str, max_results: int = 10) -> str:
    """Search Gmail emails with advanced query syntax."""
    return gmail_read_emails(query=query, max_results=max_results)


def gmail_compose_email(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> str:
    """Compose and send an email via Gmail."""
    token = _get_gmail_access_token()
    if not token:
        return "❌ Gmail not authenticated. Run: REX, authenticate Gmail"

    msg = MIMEMultipart()
    msg["to"] = to
    msg["subject"] = subject
    if cc:
        msg["cc"] = cc
    if bcc:
        msg["bcc"] = bcc
    msg.attach(MIMEText(body, "plain"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.post(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        headers=headers,
        json={"raw": raw}
    )

    if resp.status_code == 200:
        return f"✅ Email sent to {to}\n   Subject: {subject}"
    else:
        return f"❌ Failed to send email: {resp.status_code} - {resp.text[:200]}"


def gmail_mark_read(message_ids: list) -> str:
    """Mark emails as read in Gmail."""
    token = _get_gmail_access_token()
    if not token:
        return "❌ Gmail not authenticated."

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.post(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/batchModify",
        headers=headers,
        json={"ids": message_ids, "removeLabelIds": ["UNREAD"]}
    )

    if resp.status_code == 200:
        return f"✅ Marked {len(message_ids)} emails as read"
    return f"❌ Failed to mark as read: {resp.status_code}"


def gmail_archive(message_ids: list) -> str:
    """Archive emails in Gmail (remove from inbox)."""
    token = _get_gmail_access_token()
    if not token:
        return "❌ Gmail not authenticated."

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.post(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/batchModify",
        headers=headers,
        json={"ids": message_ids, "removeLabelIds": ["INBOX"]}
    )

    if resp.status_code == 200:
        return f"✅ Archived {len(message_ids)} emails"
    return f"❌ Failed to archive: {resp.status_code}"


def gmail_delete(message_ids: list) -> str:
    """Delete emails from Gmail (move to trash)."""
    token = _get_gmail_access_token()
    if not token:
        return "❌ Gmail not authenticated."

    headers = {"Authorization": f"Bearer {token}"}
    deleted = 0
    for mid in message_ids:
        resp = requests.delete(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}/trash",
            headers=headers
        )
        if resp.status_code == 200:
            deleted += 1

    return f"✅ Moved {deleted}/{len(message_ids)} emails to trash"


def gmail_get_unread_count() -> str:
    """Get unread email count from Gmail."""
    token = _get_gmail_access_token()
    if not token:
        return "❌ Gmail not authenticated."

    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/labels/INBOX",
        headers=headers
    )

    if resp.status_code == 200:
        data = resp.json()
        total = data.get("messagesTotal", 0)
        unread = data.get("messagesUnread", 0)
        return f"📬 Gmail Inbox: {unread} unread of {total} total"

    return f"❌ Failed to get inbox count: {resp.status_code}"


# ═══════════════════════════════════════════════════════════════════
# Outlook Implementation (Microsoft Graph API)
# ═══════════════════════════════════════════════════════════════════

def outlook_read_emails(folder: str = "inbox", top: int = 10, filter_read: bool = False) -> str:
    """Read emails from Outlook using Microsoft Graph API."""
    token = _get_outlook_access_token()
    if not token:
        return "❌ Outlook not authenticated. Run: REX, authenticate Outlook"

    headers = {"Authorization": f"Bearer {token}"}

    url = f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder}/messages"
    params = {"$top": top, "$select": "from,subject,bodyPreview,receivedDateTime,isRead,importance,hasAttachments"}

    if filter_read:
        params["$filter"] = "isRead eq false"

    resp = requests.get(url, headers=headers, params=params)

    if resp.status_code != 200:
        return f"❌ Outlook API error: {resp.status_code} - {resp.text[:200]}"

    messages = resp.json().get("value", [])
    if not messages:
        return f"📭 No emails found in {folder}"

    output = f"📧 Outlook {folder.title()} ({len(messages)} emails)\n"
    output += "=" * 50 + "\n\n"

    for msg in messages:
        from_info = msg.get("from", {}).get("emailAddress", {})
        from_name = from_info.get("name", "Unknown")
        from_addr = from_info.get("address", "")
        subject = msg.get("subject", "(No Subject)")
        date = msg.get("receivedDateTime", "")
        preview = (msg.get("bodyPreview", "") or "")[:100]
        is_read = msg.get("isRead", True)
        importance = msg.get("importance", "normal")
        has_attach = msg.get("hasAttachments", False)

        status = "⚪" if is_read else "🔵"
        if importance == "high":
            status = "🔴"
        attach_icon = " 📎" if has_attach else ""

        output += f"{status}{attach_icon} From: {from_name} <{from_addr}>\n"
        output += f"   Subject: {subject}\n"
        output += f"   Date: {date}\n"
        output += f"   Preview: {preview}...\n\n"

    return output


def outlook_search_emails(query: str, top: int = 10) -> str:
    """Search Outlook emails using KQL search syntax."""
    token = _get_outlook_access_token()
    if not token:
        return "❌ Outlook not authenticated."

    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(
        "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
        headers=headers,
        params={
            "$top": top,
            "$search": f'"{query}"',
            "$select": "from,subject,bodyPreview,receivedDateTime,isRead,importance"
        }
    )

    if resp.status_code != 200:
        return f"❌ Outlook search error: {resp.status_code}"

    messages = resp.json().get("value", [])
    if not messages:
        return f"📭 No emails found matching: {query}"

    output = f"🔍 Outlook Search Results: {query}\n"
    output += "=" * 50 + "\n\n"

    for msg in messages:
        from_info = msg.get("from", {}).get("emailAddress", {})
        from_name = from_info.get("name", "Unknown")
        subject = msg.get("subject", "(No Subject)")
        date = msg.get("receivedDateTime", "")
        preview = (msg.get("bodyPreview", "") or "")[:100]

        output += f"📧 From: {from_name}\n"
        output += f"   Subject: {subject}\n"
        output += f"   Date: {date}\n"
        output += f"   Preview: {preview}...\n\n"

    return output


def outlook_compose_email(to: str, subject: str, body: str, cc: str = "", importance: str = "normal") -> str:
    """Compose and send an email via Outlook (Microsoft Graph API)."""
    token = _get_outlook_access_token()
    if not token:
        return "❌ Outlook not authenticated."

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    email_data = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
            "importance": importance,
        },
        "saveToSentItems": "true"
    }

    if cc:
        email_data["message"]["ccRecipients"] = [{"emailAddress": {"address": cc}}]

    resp = requests.post(
        "https://graph.microsoft.com/v1.0/me/sendMail",
        headers=headers,
        json=email_data
    )

    if resp.status_code == 202:
        return f"✅ Email sent to {to}\n   Subject: {subject}"
    return f"❌ Failed to send email: {resp.status_code} - {resp.text[:200]}"


def outlook_mark_read(message_ids: list) -> str:
    """Mark emails as read in Outlook."""
    token = _get_outlook_access_token()
    if not token:
        return "❌ Outlook not authenticated."

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    updated = 0
    for mid in message_ids:
        resp = requests.patch(
            f"https://graph.microsoft.com/v1.0/me/messages/{mid}",
            headers=headers,
            json={"isRead": True}
        )
        if resp.status_code == 200:
            updated += 1

    return f"✅ Marked {updated}/{len(message_ids)} emails as read"


def outlook_archive(message_ids: list) -> str:
    """Archive emails in Outlook (move to Archive folder)."""
    token = _get_outlook_access_token()
    if not token:
        return "❌ Outlook not authenticated."

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # First, get or create Archive folder
    resp = requests.get(
        "https://graph.microsoft.com/v1.0/me/mailFolders",
        headers=headers,
        params={"$filter": "displayName eq 'Archive'"}
    )

    archive_id = None
    if resp.status_code == 200:
        folders = resp.json().get("value", [])
        if folders:
            archive_id = folders[0]["id"]

    if not archive_id:
        return "❌ Archive folder not found"

    moved = 0
    for mid in message_ids:
        resp = requests.post(
            f"https://graph.microsoft.com/v1.0/me/messages/{mid}/move",
            headers=headers,
            json={"destinationId": archive_id}
        )
        if resp.status_code == 200:
            moved += 1

    return f"✅ Archived {moved}/{len(message_ids)} emails"


def outlook_delete(message_ids: list) -> str:
    """Delete emails from Outlook (move to Deleted Items)."""
    token = _get_outlook_access_token()
    if not token:
        return "❌ Outlook not authenticated."

    headers = {"Authorization": f"Bearer {token}"}
    deleted = 0
    for mid in message_ids:
        resp = requests.delete(
            f"https://graph.microsoft.com/v1.0/me/messages/{mid}",
            headers=headers
        )
        if resp.status_code == 204:
            deleted += 1

    return f"✅ Deleted {deleted}/{len(message_ids)} emails"


def outlook_get_unread_count() -> str:
    """Get unread email count from Outlook."""
    token = _get_outlook_access_token()
    if not token:
        return "❌ Outlook not authenticated."

    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(
        "https://graph.microsoft.com/v1.0/me/mailFolders/inbox",
        headers=headers,
        params={"$select": "totalItemCount,unreadItemCount"}
    )

    if resp.status_code == 200:
        data = resp.json()
        total = data.get("totalItemCount", 0)
        unread = data.get("unreadItemCount", 0)
        return f"📬 Outlook Inbox: {unread} unread of {total} total"

    return f"❌ Failed to get inbox count: {resp.status_code}"


# ═══════════════════════════════════════════════════════════════════
# Unified Provider-Agnostic Interface
# ═══════════════════════════════════════════════════════════════════

def _detect_provider() -> str:
    """Auto-detect which email provider is configured."""
    tokens_gmail = _load_oauth_tokens("gmail")
    tokens_outlook = _load_oauth_tokens("outlook")

    if tokens_gmail.get("access_token") or tokens_gmail.get("refresh_token"):
        return "gmail"
    if tokens_outlook.get("access_token") or tokens_outlook.get("refresh_token"):
        return "outlook"
    return "none"


def email_read(provider: str = "auto", query: str = "", max_results: int = 10) -> str:
    """Read emails from the configured provider."""
    if provider == "auto":
        provider = _detect_provider()

    if provider == "gmail":
        q = query or "in:inbox"
        return gmail_read_emails(query=q, max_results=max_results)
    elif provider == "outlook":
        return outlook_read_emails(top=max_results, filter_read=bool(query))
    else:
        return "❌ No email provider configured. Set up Gmail or Outlook in config/api_keys.json"


def email_search(provider: str = "auto", query: str = "", max_results: int = 10) -> str:
    """Search emails from the configured provider."""
    if provider == "auto":
        provider = _detect_provider()

    if not query:
        return "❌ Please provide a search query"

    if provider == "gmail":
        return gmail_search_emails(query=query, max_results=max_results)
    elif provider == "outlook":
        return outlook_search_emails(query=query, top=max_results)
    else:
        return "❌ No email provider configured"


def _validate_email(email: str) -> bool:
    """Basic email format validation."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email.strip()))


def email_compose(provider: str = "auto", to: str = "", subject: str = "", body: str = "", cc: str = "") -> str:
    """Compose and send an email."""
    if provider == "auto":
        provider = _detect_provider()

    if not to or not subject:
        return "❌ Please provide recipient (to) and subject"

    if not _validate_email(to):
        return f"❌ Invalid email address: {to}"

    if cc and not _validate_email(cc):
        return f"❌ Invalid CC email address: {cc}"

    if not body:
        body = "(No content)"

    if provider == "gmail":
        return gmail_compose_email(to=to, subject=subject, body=body, cc=cc)
    elif provider == "outlook":
        return outlook_compose_email(to=to, subject=subject, body=body, cc=cc)
    else:
        return "❌ No email provider configured"


def email_organize(provider: str = "auto", action: str = "mark_read", message_ids: list = None) -> str:
    """Organize emails: mark_read, archive, delete."""
    if provider == "auto":
        provider = _detect_provider()

    if not message_ids:
        return "❌ No message IDs provided"

    if provider == "gmail":
        if action == "mark_read":
            return gmail_mark_read(message_ids)
        elif action == "archive":
            return gmail_archive(message_ids)
        elif action == "delete":
            return gmail_delete(message_ids)
    elif provider == "outlook":
        if action == "mark_read":
            return outlook_mark_read(message_ids)
        elif action == "archive":
            return outlook_archive(message_ids)
        elif action == "delete":
            return outlook_delete(message_ids)
    else:
        return "❌ No email provider configured"

    return f"❌ Unknown action: {action}"


def email_unread_count(provider: str = "auto") -> str:
    """Get unread email count."""
    if provider == "auto":
        provider = _detect_provider()

    if provider == "gmail":
        return gmail_get_unread_count()
    elif provider == "outlook":
        return outlook_get_unread_count()
    else:
        return "❌ No email provider configured"


# ═══════════════════════════════════════════════════════════════════
# OAuth Setup Helpers
# ═══════════════════════════════════════════════════════════════════

def gmail_auth_url() -> str:
    """Generate Gmail OAuth authorization URL."""
    keys = _get_api_keys()
    client_id = keys.get("gmail_client_id", "")
    if not client_id:
        return "❌ Gmail client_id not configured in api_keys.json"

    redirect_uri = "http://localhost:8080/callback"
    scope = "https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/gmail.send"
    url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={client_id}&redirect_uri={redirect_uri}"
        f"&response_type=code&scope={scope}&access_type=offline&prompt=consent"
    )
    return f"🔗 Open this URL to authenticate Gmail:\n\n{url}"


def gmail_complete_auth(auth_code: str) -> str:
    """Complete Gmail OAuth with authorization code."""
    keys = _get_api_keys()
    client_id = keys.get("gmail_client_id", "")
    client_secret = keys.get("gmail_client_secret", "")

    if not client_id or not client_secret:
        return "❌ Gmail credentials not configured"

    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "code": auth_code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": "http://localhost:8080/callback",
        "grant_type": "authorization_code",
    })

    if resp.status_code == 200:
        data = resp.json()
        _save_oauth_tokens("gmail", {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "expires_in": data.get("expires_in", 3600),
        })
        return "✅ Gmail authenticated successfully!"
    return f"❌ Gmail auth failed: {resp.text[:200]}"


def outlook_auth_url() -> str:
    """Generate Outlook OAuth authorization URL."""
    keys = _get_api_keys()
    client_id = keys.get("outlook_client_id", "")
    if not client_id:
        return "❌ Outlook client_id not configured in api_keys.json"

    redirect_uri = "http://localhost:8080/callback"
    scope = "Mail.Read Mail.Send Mail.ReadWrite offline_access"
    url = (
        f"https://login.microsoftonline.com/common/oauth2/v2.0/authorize?"
        f"client_id={client_id}&redirect_uri={redirect_uri}"
        f"&response_type=code&scope={scope}&response_mode=query"
    )
    return f"🔗 Open this URL to authenticate Outlook:\n\n{url}"


def outlook_complete_auth(auth_code: str) -> str:
    """Complete Outlook OAuth with authorization code."""
    keys = _get_api_keys()
    client_id = keys.get("outlook_client_id", "")
    client_secret = keys.get("outlook_client_secret", "")

    if not client_id or not client_secret:
        return "❌ Outlook credentials not configured"

    resp = requests.post("https://login.microsoftonline.com/common/oauth2/v2.0/token", data={
        "code": auth_code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": "http://localhost:8080/callback",
        "grant_type": "authorization_code",
    })

    if resp.status_code == 200:
        data = resp.json()
        _save_oauth_tokens("outlook", {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "expires_in": data.get("expires_in", 3600),
        })
        return "✅ Outlook authenticated successfully!"
    return f"❌ Outlook auth failed: {resp.text[:200]}"


# ═══════════════════════════════════════════════════════════════════
# Tool Definitions for Registration
# ═══════════════════════════════════════════════════════════════════

EMAIL_TOOLS = [
    {
        "name": "email_read",
        "description": (
            "Reads emails from Gmail or Outlook inbox. "
            "Shows sender, subject, date, and preview. "
            "Auto-detects configured provider."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "provider": {"type": "STRING", "description": "gmail | outlook | auto (default: auto)"},
                "query": {"type": "STRING", "description": "Gmail query (e.g., 'is:unread', 'from:boss') or empty for all"},
                "max_results": {"type": "INTEGER", "description": "Max emails to show (default: 10)"}
            },
            "required": []
        }
    },
    {
        "name": "email_search",
        "description": (
            "Searches emails with advanced query syntax. "
            "Gmail: supports full Gmail search operators. "
            "Outlook: supports KQL search syntax."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "provider": {"type": "STRING", "description": "gmail | outlook | auto (default: auto)"},
                "query": {"type": "STRING", "description": "Search query (e.g., 'from:john subject:report')"},
                "max_results": {"type": "INTEGER", "description": "Max results (default: 10)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "email_compose",
        "description": (
            "Composes and sends an email via Gmail or Outlook. "
            "Supports To, CC, subject, and body."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "provider": {"type": "STRING", "description": "gmail | outlook | auto (default: auto)"},
                "to": {"type": "STRING", "description": "Recipient email address"},
                "subject": {"type": "STRING", "description": "Email subject"},
                "body": {"type": "STRING", "description": "Email body content"},
                "cc": {"type": "STRING", "description": "CC recipient (optional)"}
            },
            "required": ["to", "subject", "body"]
        }
    },
    {
        "name": "email_organize",
        "description": (
            "Organizes emails: mark as read, archive, or delete. "
            "Requires message IDs from email_read or email_search."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "provider": {"type": "STRING", "description": "gmail | outlook | auto (default: auto)"},
                "action": {"type": "STRING", "description": "mark_read | archive | delete"},
                "message_ids": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "List of message IDs to organize"}
            },
            "required": ["action", "message_ids"]
        }
    },
    {
        "name": "email_unread_count",
        "description": "Gets the unread email count from Gmail or Outlook.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "provider": {"type": "STRING", "description": "gmail | outlook | auto (default: auto)"}
            },
            "required": []
        }
    },
    {
        "name": "email_auth",
        "description": (
            "Manages email authentication. "
            "Actions: gmail_url, gmail_complete, outlook_url, outlook_complete, status."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "gmail_url | gmail_complete | outlook_url | outlook_complete | status"},
                "auth_code": {"type": "STRING", "description": "Authorization code for complete actions"}
            },
            "required": ["action"]
        }
    },
]


def handle_email_tool(tool_name: str, parameters: dict, speak=None) -> str:
    """Route email tool calls to appropriate functions."""
    try:
        if tool_name == "email_read":
            return email_read(
                provider=parameters.get("provider", "auto"),
                query=parameters.get("query", ""),
                max_results=parameters.get("max_results", 10)
            )
        elif tool_name == "email_search":
            return email_search(
                provider=parameters.get("provider", "auto"),
                query=parameters.get("query", ""),
                max_results=parameters.get("max_results", 10)
            )
        elif tool_name == "email_compose":
            return email_compose(
                provider=parameters.get("provider", "auto"),
                to=parameters.get("to", ""),
                subject=parameters.get("subject", ""),
                body=parameters.get("body", ""),
                cc=parameters.get("cc", "")
            )
        elif tool_name == "email_organize":
            return email_organize(
                provider=parameters.get("provider", "auto"),
                action=parameters.get("action", "mark_read"),
                message_ids=parameters.get("message_ids", [])
            )
        elif tool_name == "email_unread_count":
            return email_unread_count(
                provider=parameters.get("provider", "auto")
            )
        elif tool_name == "email_auth":
            action = parameters.get("action", "status")
            if action == "gmail_url":
                return gmail_auth_url()
            elif action == "gmail_complete":
                return gmail_complete_auth(parameters.get("auth_code", ""))
            elif action == "outlook_url":
                return outlook_auth_url()
            elif action == "outlook_complete":
                return outlook_complete_auth(parameters.get("auth_code", ""))
            elif action == "status":
                provider = _detect_provider()
                if provider == "none":
                    return "❌ No email provider configured. Use gmail_url or outlook_url to authenticate."
                return f"✅ Email provider: {provider.title()}"
            else:
                return f"❌ Unknown auth action: {action}"
        else:
            return f"❌ Unknown email tool: {tool_name}"
    except Exception as e:
        return f"❌ Email tool error: {e}"
