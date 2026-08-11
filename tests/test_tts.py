# tests/test_tts.py
"""Tests for voice confirmation TTS after command execution.

Verifies that after _remote_execute completes a brain_dispatch or fleet_open_app,
the TTS engine is called with a brief, human-readable confirmation string.
"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest

# Ensure pi.remote_control is importable as an attribute of pi for patch()
import pi.remote_control  # noqa: F401

from pi_main import _remote_execute


def test_voice_confirmation_on_dispatch():
    """After brain_dispatch completes, TTS should speak a confirmation with agent + task_id."""
    mock_tts = MagicMock()

    fc = MagicMock()
    fc.name = "brain_dispatch"
    fc.args = {"prompt": "research quantum computing"}

    with patch("pi.remote_control") as mock_rc:
        # Mock the streaming path: dispatch_stream yields deltas, accumulate_and_speak
        # splits them into sentences for TTS.
        mock_rc.dispatch_stream = MagicMock(
            return_value=iter(["Dispatched ", "to ", "rex-code ", "task ", "abc123."])
        )
        mock_rc.accumulate_and_speak = MagicMock(
            side_effect=lambda chunks: list(chunks)
        )
        mock_rc.dispatch.return_value = {
            "ok": True,
            "assigned_agent": "rex-code",
            "task_id": "abc123",
        }

        result = asyncio.run(_remote_execute(fc, tts=mock_tts))

    # TTS should have been called for each sentence from accumulate_and_speak
    assert mock_tts.speak.call_count >= 1
    # Collect all spoken text
    spoken_all = " ".join(str(c[0][0]) for c in mock_tts.speak.call_args_list)
    assert "rex-code" in spoken_all
    assert "abc123" in spoken_all


def test_voice_confirmation_on_open_app():
    """After fleet_open_app completes, TTS should speak 'opened <app> on <device>'."""
    mock_tts = MagicMock()

    fc = MagicMock()
    fc.name = "fleet_open_app"
    fc.args = {"device": "desktop", "app": "browser"}

    with patch("pi.remote_control") as mock_rc:
        mock_rc.open_app.return_value = {"ok": True}

        result = asyncio.run(_remote_execute(fc, tts=mock_tts))

    assert result["result"] == "opened browser on desktop"
    mock_tts.speak.assert_called_once()
    spoken = mock_tts.speak.call_args[0][0]
    assert "browser" in spoken
    assert "desktop" in spoken


def test_no_tts_on_fleet_status():
    """fleet_status is read-only — no TTS confirmation needed."""
    mock_tts = MagicMock()

    fc = MagicMock()
    fc.name = "fleet_status"
    fc.args = {}

    with patch("pi.remote_control") as mock_rc:
        mock_rc.fleet_status.return_value = [
            {"name": "desktop", "reachable": True},
        ]

        result = asyncio.run(_remote_execute(fc, tts=mock_tts))

    assert "desktop" in result["result"]
    mock_tts.speak.assert_not_called()


def test_no_tts_param_does_not_crash():
    """If tts is None (default), _remote_execute should still work without error."""
    fc = MagicMock()
    fc.name = "fleet_status"
    fc.args = {}

    with patch("pi.remote_control") as mock_rc:
        mock_rc.fleet_status.return_value = []

        result = asyncio.run(_remote_execute(fc))

    assert result["result"] == "no devices"