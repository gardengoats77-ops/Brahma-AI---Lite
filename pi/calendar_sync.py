"""Calendar sync for REX-OMEGA Pi voice assistant.

Reads Google Calendar (via google-api-python-client) or Outlook Calendar
(via MSAL + Graph API) and formats events into voice-friendly strings
for the "Hey Rex, what's today?" command.

If no calendar credentials are configured, all functions degrade
gracefully to "Calendar not configured".

OAuth tokens are stored in ~/.config/rex-remote/calendar_config.json.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("brahma.calendar")

# ── Configuration ─────────────────────────────────────────────────────────

DEFAULT_CONFIG_DIR = Path(os.path.expanduser("~/.config/rex-remote"))
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "calendar_config.json"

CONFIG_PATH = DEFAULT_CONFIG_PATH


def _load_config() -> Optional[dict]:
    """Load calendar config from disk. Returns None if not configured."""
    if not CONFIG_PATH.exists():
        return None
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to read calendar config: %s", e)
        return None


# ── Time formatting helpers ───────────────────────────────────────────────

def _format_time(iso_str: str) -> str:
    """Convert an ISO datetime to a voice-friendly time like '9am' or '1:30pm'."""
    try:
        dt = datetime.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return iso_str

    hour = dt.hour
    minute = dt.minute
    ampm = "am" if hour < 12 else "pm"
    display_hour = hour % 12
    if display_hour == 0:
        display_hour = 12
    if minute == 0:
        return f"{display_hour}{ampm}"
    return f"{display_hour}:{minute:02d}{ampm}"


def _extract_event_time(event: dict) -> str:
    """Extract a display time from a calendar event (Google or Outlook format)."""
    # Google Calendar format
    start = event.get("start", {})
    if isinstance(start, dict):
        if "dateTime" in start:
            return _format_time(start["dateTime"])
        if "date" in start:
            return "all day"
    # Outlook Graph format
    if isinstance(start, str):
        return _format_time(start)
    return ""


def _extract_event_date(event: dict) -> str:
    """Extract the event date for grouping (Google or Outlook format)."""
    start = event.get("start", {})
    if isinstance(start, dict):
        if "dateTime" in start:
            try:
                dt = datetime.datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
                return dt.strftime("%Y-%m-%d")
            except (ValueError, AttributeError):
                pass
        if "date" in start:
            return start["date"]
    if isinstance(start, str):
        try:
            dt = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            pass
    return ""


def _extract_summary(event: dict) -> str:
    """Extract event summary/subject from Google or Outlook format."""
    return event.get("summary", event.get("subject", "Untitled event"))


# ── Event fetching (provider-agnostic) ───────────────────────────────────

def _fetch_events(
    days_ahead: int = 0,
    max_results: int = 10,
) -> list[dict]:
    """Fetch events for today (+ days_ahead) from the configured provider.

    Returns a list of normalized event dicts:
        [{"summary": str, "start": str, "end": str}, ...]

    Returns empty list if not configured or on error.
    """
    config = _load_config()
    if not config:
        return []

    provider = config.get("provider", "google").lower()

    try:
        if provider == "google":
            return _fetch_google_events(config, days_ahead, max_results)
        if provider == "outlook":
            return _fetch_outlook_events(config, days_ahead, max_results)
        log.warning("Unknown calendar provider: %s", provider)
        return []
    except Exception as e:
        log.warning("Calendar fetch failed (%s): %s", provider, e)
        return []


def _fetch_google_events(
    config: dict, days_ahead: int, max_results: int
) -> list[dict]:
    """Fetch events from Google Calendar API."""
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials

    google_cfg = config.get("google", {})
    token_path = google_cfg.get("token_json")

    if not token_path or not Path(token_path).exists():
        log.warning("Google Calendar token not found: %s", token_path)
        return []

    with open(token_path, "r", encoding="utf-8") as f:
        token_info = json.load(f)

    creds = Credentials.from_authorized_user_info(token_info)
    service = build("calendar", "v3", credentials=creds)

    now = datetime.datetime.now(datetime.timezone.utc)
    target_date = now + datetime.timedelta(days=days_ahead)
    day_start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + datetime.timedelta(days=1)

    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=day_start.isoformat(),
            timeMax=day_end.isoformat(),
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    events = events_result.get("items", [])
    return [
        {
            "summary": _extract_summary(e),
            "start": e.get("start", {}),
            "end": e.get("end", {}),
        }
        for e in events
    ]


def _fetch_outlook_events(
    config: dict, days_ahead: int, max_results: int
) -> list[dict]:
    """Fetch events from Outlook via Microsoft Graph API."""
    import msal
    import requests

    outlook_cfg = config.get("outlook", {})
    client_id = outlook_cfg.get("client_id", "")
    tenant_id = outlook_cfg.get("tenant_id", "common")
    token_cache_path = outlook_cfg.get("token_cache_json")

    if not client_id:
        log.warning("Outlook client_id not configured")
        return []

    # Load token cache
    token_cache = msal.SerializableTokenCache()
    if token_cache_path and Path(token_cache_path).exists():
        with open(token_cache_path, "r", encoding="utf-8") as f:
            token_cache.deserialize(f.read())

    app = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        token_cache=token_cache,
    )

    # Acquire token
    scopes = ["Calendars.Read"]
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(scopes, account=accounts[0])
    else:
        result = None

    if not result:
        log.warning("Outlook token acquisition failed — user needs to authenticate")
        return []

    access_token = result["access_token"]

    # Calculate target date range
    now = datetime.datetime.now(datetime.timezone.utc)
    target_date = now + datetime.timedelta(days=days_ahead)
    day_start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + datetime.timedelta(days=1)

    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        "startDateTime": day_start.isoformat(),
        "endDateTime": day_end.isoformat(),
        "$top": max_results,
        "$orderby": "start/dateTime",
    }

    resp = requests.get(
        "https://graph.microsoft.com/v1.0/me/calendarview",
        headers=headers,
        params=params,
        timeout=15,
    )
    resp.raise_for_status()

    raw_events = resp.json().get("value", [])
    return [
        {
            "summary": _extract_summary(e),
            "start": e.get("start", {}).get("dateTime", ""),
            "end": e.get("end", {}).get("dateTime", ""),
        }
        for e in raw_events
    ]


# ── Public voice-friendly API ────────────────────────────────────────────

def calendar_today() -> str:
    """Return a voice-friendly summary of today's events.

    Returns "Calendar not configured" if no credentials exist,
    otherwise a string like:
        "You have 3 events today: 9am Standup, 1pm Lunch with John, 3pm Review"
    """
    config = _load_config()
    if not config:
        return "Calendar not configured"

    events = _fetch_events(days_ahead=0)

    if not events:
        return "You have no events today. Enjoy your free day!"

    return _format_events(events, "today")


def calendar_tomorrow() -> str:
    """Return a voice-friendly summary of tomorrow's events."""
    config = _load_config()
    if not config:
        return "Calendar not configured"

    events = _fetch_events(days_ahead=1)

    if not events:
        return "You have no events tomorrow. Enjoy your free day!"

    return _format_events(events, "tomorrow")


def calendar_add(summary: str, start: str, end: str) -> str:
    """Add an event to the calendar.

    Args:
        summary: Event title (e.g., "Team meeting")
        start: ISO datetime string for start
        end: ISO datetime string for end

    Returns a confirmation string or error message.
    """
    config = _load_config()
    if not config:
        return "Calendar not configured"

    provider = config.get("provider", "google").lower()

    try:
        if provider == "google":
            return _add_google_event(config, summary, start, end)
        if provider == "outlook":
            return _add_outlook_event(config, summary, start, end)
        return f"Unsupported calendar provider: {provider}"
    except Exception as e:
        log.warning("calendar_add failed: %s", e)
        return f"Could not add event: {e}"


def _format_events(events: list[dict], day_label: str) -> str:
    """Format a list of events into a voice-friendly string."""
    count = len(events)
    parts = []
    for e in events:
        time_str = _extract_event_time(e)
        summary = _extract_summary(e)
        if time_str:
            parts.append(f"{time_str} {summary}")
        else:
            parts.append(summary)

    joined = ", ".join(parts)
    return f"You have {count} event{'s' if count != 1 else ''} {day_label}: {joined}"


def _add_google_event(config: dict, summary: str, start: str, end: str) -> str:
    """Add an event via Google Calendar API."""
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials

    google_cfg = config.get("google", {})
    token_path = google_cfg.get("token_json")

    if not token_path or not Path(token_path).exists():
        return "Calendar not configured — Google token missing"

    with open(token_path, "r", encoding="utf-8") as f:
        token_info = json.load(f)

    creds = Credentials.from_authorized_user_info(token_info)
    service = build("calendar", "v3", credentials=creds)

    event_body = {
        "summary": summary,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }

    created = service.events().insert(calendarId="primary", body=event_body).execute()
    log.info("Google Calendar event created: %s", created.get("htmlLink"))
    return f"Added '{summary}' to your calendar"


def _add_outlook_event(config: dict, summary: str, start: str, end: str) -> str:
    """Add an event via Outlook Graph API."""
    import msal
    import requests

    outlook_cfg = config.get("outlook", {})
    client_id = outlook_cfg.get("client_id", "")
    tenant_id = outlook_cfg.get("tenant_id", "common")
    token_cache_path = outlook_cfg.get("token_cache_json")

    if not client_id:
        return "Calendar not configured — Outlook client_id missing"

    token_cache = msal.SerializableTokenCache()
    if token_cache_path and Path(token_cache_path).exists():
        with open(token_cache_path, "r", encoding="utf-8") as f:
            token_cache.deserialize(f.read())

    app = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        token_cache=token_cache,
    )

    scopes = ["Calendars.ReadWrite"]
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(scopes, account=accounts[0])
    else:
        result = None

    if not result:
        return "Calendar not configured — Outlook authentication required"

    access_token = result["access_token"]
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    event_body = {
        "subject": summary,
        "start": {"dateTime": start, "timeZone": "UTC"},
        "end": {"dateTime": end, "timeZone": "UTC"},
    }

    resp = requests.post(
        "https://graph.microsoft.com/v1.0/me/events",
        headers=headers,
        json=event_body,
        timeout=15,
    )
    resp.raise_for_status()

    log.info("Outlook Calendar event created: %s", summary)
    return f"Added '{summary}' to your calendar"