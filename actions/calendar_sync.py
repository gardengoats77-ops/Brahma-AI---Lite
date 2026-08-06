"""
Calendar Sync Plugin for REX
Provides Google Calendar and Outlook calendar integration.
Create, update, delete, and query calendar events.
Shares OAuth tokens with the email_manager for seamless authentication.
"""

import json
from pathlib import Path
from datetime import datetime, timedelta

import requests

from actions.email_manager import (
    _get_api_keys,
    _load_oauth_tokens,
    _get_gmail_access_token,
    _get_outlook_access_token,
    _detect_provider,
)

BASE_DIR = Path(__file__).parent.parent
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


# ═══════════════════════════════════════════════════════════════════
# Google Calendar Implementation
# ═══════════════════════════════════════════════════════════════════

def _gcal_headers() -> dict:
    token = _get_gmail_access_token()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def google_calendar_list_events(days: int = 7, max_results: int = 20) -> str:
    """List upcoming events from Google Calendar."""
    headers = _gcal_headers()
    if not headers:
        return "❌ Google Calendar not authenticated. Run: REX, authenticate Gmail"

    now = datetime.utcnow()
    time_min = now.isoformat() + "Z"
    time_max = (now + timedelta(days=days)).isoformat() + "Z"

    resp = requests.get(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        headers=headers,
        params={
            "timeMin": time_min,
            "timeMax": time_max,
            "maxResults": max_results,
            "singleEvents": "true",
            "orderBy": "startTime",
        }
    )

    if resp.status_code != 200:
        return f"❌ Google Calendar API error: {resp.status_code} - {resp.text[:200]}"

    events = resp.json().get("items", [])
    if not events:
        return f"📅 No upcoming events in the next {days} days"

    output = f"📅 Google Calendar — Next {days} days ({len(events)} events)\n"
    output += "=" * 50 + "\n\n"

    for evt in events:
        start = evt.get("start", {})
        start_str = start.get("dateTime", start.get("date", "All day"))
        summary = evt.get("summary", "(No title)")
        location = evt.get("location", "")
        description = (evt.get("description") or "")[:80]
        event_id = evt.get("id", "")

        # Parse datetime for display
        try:
            if "T" in start_str:
                dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                time_display = dt.strftime("%a %b %d, %I:%M %p")
            else:
                dt = datetime.strptime(start_str, "%Y-%m-%d")
                time_display = dt.strftime("%a %b %d (All day)")
        except Exception:
            time_display = start_str

        output += f"🗓️  {summary}\n"
        output += f"   📌 {time_display}\n"
        if location:
            output += f"   📍 {location}\n"
        if description:
            output += f"   📝 {description}...\n"
        output += f"   🆔 {event_id}\n\n"

    return output


def google_calendar_create_event(
    title: str, start: str, end: str,
    description: str = "", location: str = "",
    attendees: str = "", timezone: str = "Asia/Kolkata"
) -> str:
    """Create an event in Google Calendar."""
    headers = _gcal_headers()
    if not headers:
        return "❌ Google Calendar not authenticated."

    event_body = {
        "summary": title,
        "location": location,
        "description": description,
        "start": {
            "dateTime": start,
            "timeZone": timezone,
        },
        "end": {
            "dateTime": end,
            "timeZone": timezone,
        },
    }

    if attendees:
        attendee_list = [a.strip() for a in attendees.split(",") if a.strip()]
        event_body["attendees"] = [{"email": a} for a in attendee_list]

    resp = requests.post(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        headers=headers,
        json=event_body
    )

    if resp.status_code == 200 or resp.status_code == 201:
        data = resp.json()
        link = data.get("htmlLink", "")
        return f"✅ Event created: {title}\n   🔗 {link}"
    return f"❌ Failed to create event: {resp.status_code} - {resp.text[:200]}"


def google_calendar_update_event(
    event_id: str, title: str = "", start: str = "", end: str = "",
    description: str = "", location: str = ""
) -> str:
    """Update an existing event in Google Calendar."""
    headers = _gcal_headers()
    if not headers:
        return "❌ Google Calendar not authenticated."

    update_body = {}
    if title:
        update_body["summary"] = title
    if description:
        update_body["description"] = description
    if location:
        update_body["location"] = location
    if start:
        update_body["start"] = {"dateTime": start}
    if end:
        update_body["end"] = {"dateTime": end}

    if not update_body:
        return "❌ No fields to update"

    resp = requests.patch(
        f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
        headers=headers,
        json=update_body
    )

    if resp.status_code == 200:
        return f"✅ Event updated: {event_id}"
    return f"❌ Failed to update event: {resp.status_code} - {resp.text[:200]}"


def google_calendar_delete_event(event_id: str) -> str:
    """Delete an event from Google Calendar."""
    headers = _gcal_headers()
    if not headers:
        return "❌ Google Calendar not authenticated."

    resp = requests.delete(
        f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
        headers=headers
    )

    if resp.status_code == 200 or resp.status_code == 204:
        return f"✅ Event deleted: {event_id}"
    return f"❌ Failed to delete event: {resp.status_code} - {resp.text[:200]}"


def google_calendar_today() -> str:
    """Get today's events from Google Calendar."""
    return google_calendar_list_events(days=1, max_results=20)


def google_calendar_free_busy(start: str, end: str) -> str:
    """Check free/busy status for a time range."""
    headers = _gcal_headers()
    if not headers:
        return "❌ Google Calendar not authenticated."

    resp = requests.post(
        "https://www.googleapis.com/calendar/v3/freeBusy",
        headers=headers,
        json={
            "timeMin": start,
            "timeMax": end,
            "items": [{"id": "primary"}]
        }
    )

    if resp.status_code != 200:
        return f"❌ Free/busy check failed: {resp.status_code}"

    data = resp.json()
    calendars = data.get("calendars", {})
    primary = calendars.get("primary", {})
    busy_slots = primary.get("busy", [])

    if not busy_slots:
        return f"✅ You're free from {start} to {end}"

    output = f"⏰ Busy slots from {start} to {end}:\n"
    for slot in busy_slots:
        output += f"   🔴 {slot.get('start', '?')} → {slot.get('end', '?')}\n"

    return output


# ═══════════════════════════════════════════════════════════════════
# Outlook Calendar Implementation (Microsoft Graph API)
# ═══════════════════════════════════════════════════════════════════

def _outlook_headers() -> dict:
    token = _get_outlook_access_token()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def outlook_calendar_list_events(days: int = 7, max_results: int = 20) -> str:
    """List upcoming events from Outlook Calendar via Microsoft Graph API."""
    headers = _outlook_headers()
    if not headers:
        return "❌ Outlook Calendar not authenticated. Run: REX, authenticate Outlook"

    now = datetime.utcnow()
    start_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = (now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    resp = requests.get(
        "https://graph.microsoft.com/v1.0/me/calendar/events",
        headers=headers,
        params={
            "$top": max_results,
            "$filter": f"start/dateTime ge '{start_iso}' and start/dateTime le '{end_iso}'",
            "$orderby": "start/dateTime",
            "$select": "subject,start,end,location,isAllDay,isCancelled,bodyPreview,id,attendees"
        }
    )

    if resp.status_code != 200:
        return f"❌ Outlook Calendar API error: {resp.status_code} - {resp.text[:200]}"

    events = resp.json().get("value", [])
    if not events:
        return f"📅 No upcoming events in the next {days} days"

    output = f"📅 Outlook Calendar — Next {days} days ({len(events)} events)\n"
    output += "=" * 50 + "\n\n"

    for evt in events:
        subject = evt.get("subject", "(No title)")
        start_info = evt.get("start", {})
        end_info = evt.get("end", {})
        start_str = start_info.get("dateTime", "")
        is_all_day = evt.get("isAllDay", False)
        is_cancelled = evt.get("isCancelled", False)
        location_name = (evt.get("location", {}) or {}).get("displayName", "")
        preview = (evt.get("bodyPreview") or "")[:80]
        event_id = evt.get("id", "")
        attendees = evt.get("attendees", [])

        status = "❌ " if is_cancelled else ""

        try:
            if start_str:
                dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                time_display = dt.strftime("%a %b %d, %I:%M %p") if not is_all_day else dt.strftime("%a %b %d (All day)")
            else:
                time_display = "Unknown"
        except Exception:
            time_display = start_str

        output += f"{status}🗓️  {subject}\n"
        output += f"   📌 {time_display}\n"
        if location_name:
            output += f"   📍 {location_name}\n"
        if preview:
            output += f"   📝 {preview}...\n"
        if attendees:
            names = [a.get("emailAddress", {}).get("name", "") for a in attendees[:5]]
            names = [n for n in names if n]
            if names:
                output += f"   👥 {', '.join(names)}\n"
        output += f"   🆔 {event_id}\n\n"

    return output


def outlook_calendar_create_event(
    title: str, start: str, end: str,
    description: str = "", location: str = "",
    attendees: str = "", timezone: str = "India Standard Time"
) -> str:
    """Create an event in Outlook Calendar via Microsoft Graph API."""
    headers = _outlook_headers()
    if not headers:
        return "❌ Outlook Calendar not authenticated."

    event_body = {
        "subject": title,
        "start": {"dateTime": start, "timeZone": timezone},
        "end": {"dateTime": end, "timeZone": timezone},
    }

    if description:
        event_body["body"] = {"contentType": "Text", "content": description}
    if location:
        event_body["location"] = {"displayName": location}
    if attendees:
        attendee_list = [a.strip() for a in attendees.split(",") if a.strip()]
        event_body["attendees"] = [
            {"emailAddress": {"address": a, "name": a.split("@")[0]}}
            for a in attendee_list
        ]

    resp = requests.post(
        "https://graph.microsoft.com/v1.0/me/calendar/events",
        headers=headers,
        json=event_body
    )

    if resp.status_code in (200, 201):
        data = resp.json()
        link = data.get("webLink", "")
        return f"✅ Event created: {title}\n   🔗 {link}"
    return f"❌ Failed to create event: {resp.status_code} - {resp.text[:200]}"


def outlook_calendar_update_event(
    event_id: str, title: str = "", start: str = "", end: str = "",
    description: str = "", location: str = ""
) -> str:
    """Update an existing event in Outlook Calendar."""
    headers = _outlook_headers()
    if not headers:
        return "❌ Outlook Calendar not authenticated."

    update_body = {}
    if title:
        update_body["subject"] = title
    if description:
        update_body["body"] = {"contentType": "Text", "content": description}
    if location:
        update_body["location"] = {"displayName": location}
    if start:
        update_body["start"] = {"dateTime": start}
    if end:
        update_body["end"] = {"dateTime": end}

    if not update_body:
        return "❌ No fields to update"

    resp = requests.patch(
        f"https://graph.microsoft.com/v1.0/me/calendar/events/{event_id}",
        headers=headers,
        json=update_body
    )

    if resp.status_code == 200:
        return f"✅ Event updated: {event_id}"
    return f"❌ Failed to update event: {resp.status_code} - {resp.text[:200]}"


def outlook_calendar_delete_event(event_id: str) -> str:
    """Delete an event from Outlook Calendar."""
    headers = _outlook_headers()
    if not headers:
        return "❌ Outlook Calendar not authenticated."

    resp = requests.delete(
        f"https://graph.microsoft.com/v1.0/me/calendar/events/{event_id}",
        headers=headers
    )

    if resp.status_code == 200 or resp.status_code == 204:
        return f"✅ Event deleted: {event_id}"
    return f"❌ Failed to delete event: {resp.status_code} - {resp.text[:200]}"


def outlook_calendar_today() -> str:
    """Get today's events from Outlook Calendar."""
    return outlook_calendar_list_events(days=1, max_results=20)


def outlook_calendar_list_calendars() -> str:
    """List all calendars in Outlook."""
    headers = _outlook_headers()
    if not headers:
        return "❌ Outlook not authenticated."

    resp = requests.get(
        "https://graph.microsoft.com/v1.0/me/calendars",
        headers=headers,
        params={"$select": "id,name,isDefaultCalendar,color"}
    )

    if resp.status_code != 200:
        return f"❌ Failed to list calendars: {resp.status_code}"

    calendars = resp.json().get("value", [])
    output = f"📅 Your Calendars ({len(calendars)})\n"
    output += "=" * 40 + "\n\n"

    for cal in calendars:
        name = cal.get("name", "Unnamed")
        is_default = cal.get("isDefaultCalendar", False)
        marker = " ⭐" if is_default else ""
        cal_id = cal.get("id", "")
        output += f"{'⭐' if is_default else '  '} {name}\n"
        output += f"   🆔 {cal_id}\n\n"

    return output


# ═══════════════════════════════════════════════════════════════════
# Unified Provider-Agnostic Interface
# ═══════════════════════════════════════════════════════════════════

def calendar_list_events(provider: str = "auto", days: int = 7, max_results: int = 20) -> str:
    """List upcoming events from the configured calendar provider."""
    if provider == "auto":
        provider = _detect_provider()

    if provider == "gmail":
        return google_calendar_list_events(days=days, max_results=max_results)
    elif provider == "outlook":
        return outlook_calendar_list_events(days=days, max_results=max_results)
    else:
        return "❌ No calendar provider configured. Set up Gmail or Outlook in config/api_keys.json"


def calendar_create_event(
    provider: str = "auto",
    title: str = "", start: str = "", end: str = "",
    description: str = "", location: str = "", attendees: str = ""
) -> str:
    """Create an event on the configured calendar provider."""
    if provider == "auto":
        provider = _detect_provider()

    if not title or not start or not end:
        return "❌ Please provide title, start, and end times"

    if provider == "gmail":
        return google_calendar_create_event(
            title=title, start=start, end=end,
            description=description, location=location, attendees=attendees
        )
    elif provider == "outlook":
        return outlook_calendar_create_event(
            title=title, start=start, end=end,
            description=description, location=location, attendees=attendees
        )
    else:
        return "❌ No calendar provider configured"


def calendar_update_event(
    provider: str = "auto", event_id: str = "",
    title: str = "", start: str = "", end: str = "",
    description: str = "", location: str = ""
) -> str:
    """Update an existing event on the configured calendar provider."""
    if provider == "auto":
        provider = _detect_provider()

    if not event_id:
        return "❌ Please provide an event_id (from calendar_list_events)"

    if provider == "gmail":
        return google_calendar_update_event(
            event_id=event_id, title=title, start=start, end=end,
            description=description, location=location
        )
    elif provider == "outlook":
        return outlook_calendar_update_event(
            event_id=event_id, title=title, start=start, end=end,
            description=description, location=location
        )
    else:
        return "❌ No calendar provider configured"


def calendar_delete_event(provider: str = "auto", event_id: str = "") -> str:
    """Delete an event from the configured calendar provider."""
    if provider == "auto":
        provider = _detect_provider()

    if not event_id:
        return "❌ Please provide an event_id (from calendar_list_events)"

    if provider == "gmail":
        return google_calendar_delete_event(event_id)
    elif provider == "outlook":
        return outlook_calendar_delete_event(event_id)
    else:
        return "❌ No calendar provider configured"


def calendar_today(provider: str = "auto") -> str:
    """Get today's events from the configured provider."""
    if provider == "auto":
        provider = _detect_provider()

    if provider == "gmail":
        return google_calendar_today()
    elif provider == "outlook":
        return outlook_calendar_today()
    else:
        return "❌ No calendar provider configured"


def calendar_list_calendars(provider: str = "auto") -> str:
    """List all available calendars."""
    if provider == "auto":
        provider = _detect_provider()

    if provider == "outlook":
        return outlook_calendar_list_calendars()
    elif provider == "gmail":
        # Google Calendar list calendars
        headers = _gcal_headers()
        if not headers:
            return "❌ Google Calendar not authenticated."
        resp = requests.get(
            "https://www.googleapis.com/calendar/v3/users/me/calendarList",
            headers=headers,
            params={"maxResults": 50}
        )
        if resp.status_code != 200:
            return f"❌ Failed to list calendars: {resp.status_code}"
        cals = resp.json().get("items", [])
        output = f"📅 Your Calendars ({len(cals)})\n"
        output += "=" * 40 + "\n\n"
        for cal in cals:
            summary = cal.get("summary", "Unnamed")
            is_primary = cal.get("primary", False)
            cal_id = cal.get("id", "")
            output += f"{'⭐' if is_primary else '  '} {summary}\n"
            output += f"   🆔 {cal_id}\n\n"
        return output
    else:
        return "❌ No calendar provider configured"


# ═══════════════════════════════════════════════════════════════════
# Tool Definitions for Registration
# ═══════════════════════════════════════════════════════════════════

CALENDAR_TOOLS = [
    {
        "name": "calendar_list_events",
        "description": (
            "Lists upcoming calendar events from Google Calendar or Outlook. "
            "Shows event title, time, location, and attendees. "
            "Auto-detects configured provider."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "provider": {"type": "STRING", "description": "gmail | outlook | auto (default: auto)"},
                "days": {"type": "INTEGER", "description": "Number of days to look ahead (default: 7)"},
                "max_results": {"type": "INTEGER", "description": "Max events to show (default: 20)"}
            },
            "required": []
        }
    },
    {
        "name": "calendar_create_event",
        "description": (
            "Creates a new calendar event. "
            "Supports title, start/end times, description, location, and attendees. "
            "Times should be ISO format (e.g., 2024-01-15T10:00:00)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "provider": {"type": "STRING", "description": "gmail | outlook | auto (default: auto)"},
                "title": {"type": "STRING", "description": "Event title"},
                "start": {"type": "STRING", "description": "Start time in ISO format (e.g., 2024-01-15T10:00:00)"},
                "end": {"type": "STRING", "description": "End time in ISO format"},
                "description": {"type": "STRING", "description": "Event description (optional)"},
                "location": {"type": "STRING", "description": "Event location (optional)"},
                "attendees": {"type": "STRING", "description": "Comma-separated email addresses (optional)"}
            },
            "required": ["title", "start", "end"]
        }
    },
    {
        "name": "calendar_update_event",
        "description": (
            "Updates an existing calendar event. "
            "Use calendar_list_events to find event IDs."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "provider": {"type": "STRING", "description": "gmail | outlook | auto (default: auto)"},
                "event_id": {"type": "STRING", "description": "Event ID from calendar_list_events"},
                "title": {"type": "STRING", "description": "New title (optional)"},
                "start": {"type": "STRING", "description": "New start time (optional)"},
                "end": {"type": "STRING", "description": "New end time (optional)"},
                "description": {"type": "STRING", "description": "New description (optional)"},
                "location": {"type": "STRING", "description": "New location (optional)"}
            },
            "required": ["event_id"]
        }
    },
    {
        "name": "calendar_delete_event",
        "description": (
            "Deletes a calendar event. "
            "Use calendar_list_events to find event IDs."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "provider": {"type": "STRING", "description": "gmail | outlook | auto (default: auto)"},
                "event_id": {"type": "STRING", "description": "Event ID to delete"}
            },
            "required": ["event_id"]
        }
    },
    {
        "name": "calendar_today",
        "description": "Shows today's calendar events from the configured provider.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "provider": {"type": "STRING", "description": "gmail | outlook | auto (default: auto)"}
            },
            "required": []
        }
    },
    {
        "name": "calendar_list_calendars",
        "description": "Lists all available calendars (Google Calendar and Outlook).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "provider": {"type": "STRING", "description": "gmail | outlook | auto (default: auto)"}
            },
            "required": []
        }
    },
    {
        "name": "calendar_free_busy",
        "description": (
            "Checks free/busy status for a time range. "
            "Google Calendar only."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "start": {"type": "STRING", "description": "Start time in ISO format"},
                "end": {"type": "STRING", "description": "End time in ISO format"}
            },
            "required": ["start", "end"]
        }
    },
]


def handle_calendar_tool(tool_name: str, parameters: dict, speak=None) -> str:
    """Route calendar tool calls to appropriate functions."""
    try:
        if tool_name == "calendar_list_events":
            return calendar_list_events(
                provider=parameters.get("provider", "auto"),
                days=parameters.get("days", 7),
                max_results=parameters.get("max_results", 20)
            )
        elif tool_name == "calendar_create_event":
            return calendar_create_event(
                provider=parameters.get("provider", "auto"),
                title=parameters.get("title", ""),
                start=parameters.get("start", ""),
                end=parameters.get("end", ""),
                description=parameters.get("description", ""),
                location=parameters.get("location", ""),
                attendees=parameters.get("attendees", "")
            )
        elif tool_name == "calendar_update_event":
            return calendar_update_event(
                provider=parameters.get("provider", "auto"),
                event_id=parameters.get("event_id", ""),
                title=parameters.get("title", ""),
                start=parameters.get("start", ""),
                end=parameters.get("end", ""),
                description=parameters.get("description", ""),
                location=parameters.get("location", "")
            )
        elif tool_name == "calendar_delete_event":
            return calendar_delete_event(
                provider=parameters.get("provider", "auto"),
                event_id=parameters.get("event_id", "")
            )
        elif tool_name == "calendar_today":
            return calendar_today(
                provider=parameters.get("provider", "auto")
            )
        elif tool_name == "calendar_list_calendars":
            return calendar_list_calendars(
                provider=parameters.get("provider", "auto")
            )
        elif tool_name == "calendar_free_busy":
            return google_calendar_free_busy(
                start=parameters.get("start", ""),
                end=parameters.get("end", "")
            )
        else:
            return f"❌ Unknown calendar tool: {tool_name}"
    except Exception as e:
        return f"❌ Calendar tool error: {e}"
