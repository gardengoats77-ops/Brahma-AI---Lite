"""Live-chat persona regression: the agent must be logged as "Rex".

Branding split: the *system* is "Almighty AI" (window title, product name),
while the *agent* that replies is "Rex".  A future edit could silently
revert the UI-log prefix (`ui.write_log(f"Rex: {reply}")`) or the system
prompts back to "Almighty AI:" or "Brahma AI".  These tests drive the app's
own text-command path (`AlmightyLive._on_text_command` → `_fallback_reply`)
with a stub UI that captures the log lines, and assert the persona contract:

  * the reply appears in the UI log prefixed "Rex:" — never "Almighty AI:"
    or "Brahma AI:";
  * the loaded system prompt (core/prompt.txt) introduces the agent as
    "Rex" and the system as "Almighty AI".

By default the model call is mocked so the suite stays offline, deterministic,
and free of API cost.  The real Gemini round-trip test runs automatically
whenever a valid key is present in ``config/api_keys.json``; set
``ALMIGHTY_SKIP_LIVE=1`` to force-skip it (it is billed and needs network) —
e.g. in CI or when offline.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Importing `main` pulls in PyQt6 (via ui.py); keep tests headless-friendly.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _require_live():
    """Import the app modules (linux_shim self-installs stubs on Linux)."""
    import linux_shim  # noqa: F401
    import main  # noqa: F401
    return main


class StubUI:
    """Minimal stand-in for AlmightyUI that records what the app writes."""

    def __init__(self):
        self.logs: list[str] = []
        self.states: list[str] = []
        self.muted = False
        self.on_text_command = None
        self.on_attention_action = None
        self.on_remote_clicked = None

    def write_log(self, text) -> None:
        self.logs.append(str(text))

    def set_state(self, state: str) -> None:
        self.states.append(state)

    def _load_app_settings(self) -> dict:
        return {}

    def begin_task_workspace(self, *args, **kwargs) -> None:
        pass

    def update_task_workspace(self, *args, **kwargs) -> None:
        pass

    def finish_task_workspace(self, *args, **kwargs) -> None:
        pass


def _wait_for_reply(ui: StubUI, timeout: float = 15.0) -> str | None:
    """Poll the captured log for the first 'Rex:' reply line."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for line in ui.logs:
            if line.startswith("Rex:"):
                return line
        time.sleep(0.1)
    return None


@pytest.fixture
def live_chat():
    """A constructed AlmightyLive wired to a capturing StubUI."""
    main = _require_live()
    ui = StubUI()
    live = main.AlmightyLive(ui, enable_dashboard=False)
    return main, ui, live


def test_system_prompts_use_rex_persona(live_chat):
    """The agent is 'Rex' inside the Almighty AI system in the prompt files."""
    main, _ui, _live = live_chat

    prompt = main._load_system_prompt()
    assert prompt.startswith("You are Rex"), prompt[:120]
    assert "Almighty AI" in prompt, "system name must remain Almighty AI"
    assert "Brahma AI" not in prompt

    on_disk = main.PROMPT_PATH.read_text(encoding="utf-8")
    assert "You are Rex" in on_disk
    assert "Almighty AI" in on_disk
    assert "Brahma" not in on_disk


def test_live_chat_reply_uses_rex_persona(live_chat, monkeypatch):
    """A text command produces a 'Rex:' reply — never 'Almighty AI:'/'Brahma AI:'.

    The model call is mocked so the assertion targets the app's own reply
    formatting: the UI log line the user sees must carry the Rex persona.
    """
    main, ui, live = live_chat

    canned = "I am Rex, your desktop assistant. How can I help you today?"
    monkeypatch.setattr(main, "_gemini_text_reply", lambda _text: canned)
    monkeypatch.setattr(main.openrouter_client, "chat", lambda *_a, **_k: "")

    live._on_text_command("What are you called?", source="local")

    reply_line = _wait_for_reply(ui)
    assert reply_line is not None, (
        "No 'Rex:' reply appeared in the UI log; log was:\n"
        + "\n".join(ui.logs)
    )
    assert reply_line.startswith("Rex:"), reply_line
    assert "Rex" in reply_line
    assert not any("Almighty AI:" in line for line in ui.logs), (
        "Stale 'Almighty AI:' agent prefix leaked into the UI log:\n"
        + "\n".join(ui.logs)
    )
    assert not any("Brahma AI:" in line for line in ui.logs), (
        "Stale 'Brahma AI:' branding leaked into the UI log:\n"
        + "\n".join(ui.logs)
    )
    assert "THINKING" in ui.states and "LISTENING" in ui.states


@pytest.mark.skipif(
    os.environ.get("ALMIGHTY_SKIP_LIVE", "").lower() in {"1", "true", "yes"},
    reason="Set ALMIGHTY_SKIP_LIVE=1 to skip the real (billed) Gemini round-trip.",
)
def test_live_chat_real_gemini_reply():
    """Real Gemini reply through the same path (no mocking).

    Runs by default when a valid key exists in config/api_keys.json;
    skipped only when ALMIGHTY_SKIP_LIVE=1 or no key is present.
    """
    main = _require_live()

    key_path = main.API_CONFIG_PATH
    has_key = False
    try:
        import json

        has_key = bool(json.loads(key_path.read_text(encoding="utf-8")).get("gemini_api_key"))
    except Exception:
        has_key = False
    if not has_key:
        pytest.skip("No gemini_api_key in config/api_keys.json")

    ui = StubUI()
    live = main.AlmightyLive(ui, enable_dashboard=False)
    live._on_text_command(
        "What are you called? Answer with only your name.", source="local"
    )
    reply_line = _wait_for_reply(ui, timeout=60.0)
    assert reply_line is not None, (
        "Real Gemini reply never arrived; log was:\n" + "\n".join(ui.logs)
    )
    assert reply_line.startswith("Rex:"), reply_line
    assert not any("Almighty AI:" in line for line in ui.logs)
    assert not any("Brahma AI:" in line for line in ui.logs)
