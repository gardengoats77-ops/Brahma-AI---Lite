"""One runnable check for the real-data weather parsing logic.

Mocks the wttr.in HTTP response so no network is needed.
"""
import json
import urllib.request

import pytest

from actions.daily_briefing import fetch_weather_info


def _fake_urlopen(data: dict):
    """Return a callable that masquerades as urllib.request.urlopen."""
    class _Resp:
        def read(self) -> bytes:
            return json.dumps(data).encode("utf-8")
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
    return lambda req, timeout: _Resp()


FIXTURE = {
    "current_condition": [{"temp_C": "22", "weatherDesc": [{"value": "  Cloudy  "}]}],
    "weather": [{
        "maxtempC": "30",
        "hourly": [
            {"chanceofrain": "20", "weatherDesc": [{"value": "Sunny"}]},
            {"chanceofrain": "0",  "weatherDesc": [{"value": "Clear"}]},
        ],
    }],
}


@pytest.fixture(autouse=True)
def _mock_wttr(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(FIXTURE))


def test_fetch_weather_uses_live_conditions():
    out = fetch_weather_info("Paris")
    assert out is not None
    assert "Today's weather in Paris is 22 degrees" in out   # current temp_C
    assert "cloudy" in out                                    # stripped desc
    assert "20 percent chance of rain" in out                 # max chanceofrain


def test_fetch_weather_none_on_failure(monkeypatch):
    # Distinct city: the module-level cache persists across tests.
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout: (_ for _ in ()).throw(OSError("no network")),
    )
    assert fetch_weather_info("Berlin") is None


def test_fetch_weather_cached(monkeypatch):
    calls = []

    def counting_urlopen(req, timeout):
        calls.append(1)
        return _fake_urlopen(FIXTURE)(req, timeout)

    monkeypatch.setattr(urllib.request, "urlopen", counting_urlopen)
    fetch_weather_info("Madrid")
    fetch_weather_info("Madrid")
    assert len(calls) == 1  # second call served from cache, no network
