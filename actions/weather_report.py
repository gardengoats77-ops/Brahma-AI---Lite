# actions/weather_report.py

import webbrowser
from urllib.parse import quote_plus

from actions.daily_briefing import fetch_weather_info


def weather_action(
    parameters: dict,
    player=None,
    session_memory=None
):
    """
    Weather report action.
    Fetches live weather from wttr.in and speaks a real summary.
    Opens a Google weather search only as a degraded fallback when
    the live fetch fails.
    """

    city = parameters.get("city")
    if not city or not isinstance(city, str):
        msg = "Sir, the city is missing for the weather report."
        _speak_and_log(msg, player)
        return msg
    city = city.strip()

    report = fetch_weather_info(city)
    if report:
        _speak_and_log(report, player)
        if session_memory:
            try:
                session_memory.set_last_search(
                    query=f"weather in {city}",
                    response=report
                )
            except Exception:
                pass
        return report

    # Live fetch failed — degraded fallback: open the live page
    url = f"https://www.google.com/search?q={quote_plus(f'weather in {city}')}"
    try:
        webbrowser.open(url)
    except Exception:
        msg = f"Sir, I couldn't fetch live weather for {city} or open the browser."
        _speak_and_log(msg, player)
        return msg

    msg = f"I couldn't fetch live data, so I opened the weather page for {city}, sir."
    _speak_and_log(msg, player)
    return msg


def _speak_and_log(message: str, player=None):
    if player:
        try:
            player.write_log(f"Rex: {message}")
        except Exception:
            pass
