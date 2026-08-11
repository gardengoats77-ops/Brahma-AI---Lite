# pi/briefing.py
"""Morning briefing aggregation for REX-OMEGA Pi voice assistant (Phase 13.2).

Aggregates: calendar_today + weather + news + scheduler list_reminders
into a single voice-friendly string for the "Good morning" command.

Weather: OpenWeatherMap API (env WEATHER_API_KEY, WEATHER_CITY)
News: NewsAPI or RSS feed (env NEWS_API_KEY)

If no API keys are configured, sections are skipped gracefully:
    "Weather not configured" / "News not configured"

Config is read from ~/.config/rex-remote/briefing_config.json.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("brahma.pi.briefing")

# ── Configuration ─────────────────────────────────────────────────────────

DEFAULT_CONFIG_DIR = Path(os.path.expanduser("~/.config/rex-remote"))
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "briefing_config.json"

CONFIG_PATH = DEFAULT_CONFIG_PATH


def _load_config() -> dict:
    """Load briefing config from disk. Returns empty dict if not configured."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to read briefing config: %s", e)
        return {}


# ── Weather ───────────────────────────────────────────────────────────────

def _fetch_weather() -> Optional[str]:
    """Fetch current weather from OpenWeatherMap.

    Returns a voice-friendly string like "72°F and sunny" or None if
    WEATHER_API_KEY is not set.
    """
    api_key = os.environ.get("WEATHER_API_KEY")
    if not api_key:
        log.debug("WEATHER_API_KEY not set — skipping weather")
        return None

    city = os.environ.get("WEATHER_CITY", "San Francisco")
    units = os.environ.get("WEATHER_UNITS", "imperial")
    url = (
        f"https://api.openweathermap.org/data/2.5/weather"
        f"?q={city}&appid={api_key}&units={units}"
    )

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Brahma-AI-Lite/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        temp = data.get("main", {}).get("temp", 0)
        description = data.get("weather", [{}])[0].get("description", "unknown")
        city_name = data.get("name", city)

        unit_label = "°F" if units == "imperial" else "°C"
        result = f"{int(temp)}{unit_label} and {description} in {city_name}"
        log.info("Weather fetched: %s", result)
        return result
    except urllib.error.URLError as e:
        log.warning("Weather API unreachable: %s", e)
        return "Weather unavailable"
    except Exception as e:  # noqa: BLE001
        log.warning("Weather fetch failed: %s", e)
        return "Weather unavailable"


# ── News ──────────────────────────────────────────────────────────────────

def _fetch_news() -> List[str]:
    """Fetch top news headlines from NewsAPI or RSS feed.

    Returns a list of headline strings, or empty list if NEWS_API_KEY
    is not set and no RSS feed is configured.
    """
    api_key = os.environ.get("NEWS_API_KEY")
    if not api_key:
        log.debug("NEWS_API_KEY not set — trying RSS fallback")
        return _fetch_news_rss()

    url = (
        "https://newsapi.org/v2/top-headlines"
        "?country=us&pageSize=5"
    )
    headers = {
        "X-Api-Key": api_key,
        "User-Agent": "Brahma-AI-Lite/1.0",
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        articles = data.get("articles", [])[:5]
        headlines = [a.get("title", "") for a in articles if a.get("title")]
        log.info("News fetched: %d headlines", len(headlines))
        return headlines
    except urllib.error.HTTPError as e:
        log.warning("NewsAPI HTTP error %s — trying RSS fallback", e.code)
        return _fetch_news_rss()
    except urllib.error.URLError as e:
        log.warning("NewsAPI unreachable: %s — trying RSS fallback", e)
        return _fetch_news_rss()
    except Exception as e:  # noqa: BLE001
        log.warning("News fetch failed: %s", e)
        return []


def _fetch_news_rss() -> List[str]:
    """Fallback: fetch news from configured RSS feed."""
    config = _load_config()
    rss_url = config.get("news_rss_url", "")
    if not rss_url:
        return []

    try:
        req = urllib.request.Request(rss_url, headers={"User-Agent": "Brahma-AI-Lite/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8")

        # Simple XML parsing for <title> tags
        import re
        titles = re.findall(r"<title>(.*?)</title>", content)
        # Skip first title (usually the feed title)
        headlines = titles[1:6] if len(titles) > 1 else titles[:5]
        log.info("RSS news fetched: %d headlines", len(headlines))
        return headlines
    except Exception as e:  # noqa: BLE001
        log.warning("RSS news fetch failed: %s", e)
        return []


# ── Calendar (delegates to calendar_sync) ─────────────────────────────────

def _fetch_calendar_today() -> str:
    """Return a voice-friendly summary of today's calendar events."""
    from pi import calendar_sync

    return calendar_sync.calendar_today()


# ── Reminders (delegates to scheduler) ────────────────────────────────────

def _fetch_reminders() -> str:
    """Return a voice-friendly summary of scheduled reminders."""
    from pi import scheduler

    reminders = scheduler.list_reminders(status_filter="scheduled")
    if not reminders:
        return ""

    count = len(reminders)
    messages = [r.get("message", "") for r in reminders]
    if count == 1:
        return f"You have 1 reminder: {messages[0]}"
    joined = ", ".join(messages[:-1])
    return f"You have {count} reminders: {joined}, and {messages[-1]}"


# ── Main briefing ─────────────────────────────────────────────────────────

def morning_briefing() -> str:
    """Aggregate calendar + weather + news + reminders into a voice-friendly briefing.

    Returns a single string suitable for TTS, e.g.:
        "Good morning! Here's your briefing: weather is 72°F and sunny,
         you have 2 events today: 9am Standup, 2pm Team Meeting,
         top news: AI breakthrough, Space launch,
         and you have 1 reminder: check the build."
    """
    parts = ["Good morning! Here's your briefing:"]

    # ── Weather ──
    weather = _fetch_weather()
    if weather:
        parts.append(f"weather is {weather}")
    else:
        parts.append("weather not configured")

    # ── Calendar ──
    calendar_summary = _fetch_calendar_today()
    if calendar_summary == "Calendar not configured":
        parts.append("calendar not configured")
    elif "no events" in calendar_summary.lower():
        parts.append("no events today")
    else:
        # Extract the voice-friendly part
        parts.append(calendar_summary.lower())

    # ── News ──
    news = _fetch_news()
    if news:
        if len(news) == 1:
            parts.append(f"top news: {news[0]}")
        else:
            news_str = "; ".join(news[:3])
            parts.append(f"top news: {news_str}")
    else:
        parts.append("news not configured")

    # ── Reminders ──
    reminders = _fetch_reminders()
    if reminders:
        parts.append(reminders.lower())

    # Join into a single string
    briefing_text = " ".join(parts)
    log.info("Morning briefing generated (%d chars)", len(briefing_text))
    return briefing_text