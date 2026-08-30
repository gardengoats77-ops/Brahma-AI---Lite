# REX-AI — Agent Guide

REX is an open-source Windows desktop AI assistant (Python 3.11–3.12, venv at `.venv`).
Voice + text interaction via Gemini with OpenRouter fallback, desktop automation, office
document generation, browser control, smart home, email, calendar, Discord, and an MCP server.

Deep-dive references: `README.md` (features/overview), `knowledge.md` (architecture detail,
gotchas, history). This file is the conventions an agent needs to work here without breaking things.

## Commands

```bash
# Setup (first time)
.venv/Scripts/python.exe -m pip install -r requirements.txt
playwright install

# Run the desktop app
python main.py                # or: start_rex.bat
.venv/Scripts/python.exe start_rex.py --dev        # dashboard :8080 + backend :8000
.venv/Scripts/python.exe start_rex.py --prod       # self-contained dashboard :8000
.venv/Scripts/python.exe start_rex.py --with-agent # also spawns the LiveKit agent worker

# Tests (do NOT break the ~199 existing tests)
.venv/Scripts/python.exe -m pytest tests/ -x -q

# MCP server (exposes REX tools to LLM clients)
python -m mcp_server.server

# Dashboard only (FastAPI)
uvicorn dashboard.server:app --host 127.0.0.1 --port 8000
```

## Architecture map

| Path | Purpose |
|------|---------|
| `main.py` | Entry point. `RexLive` class orchestrates the AI session, voice, dispatch, smart-home parsing, memory, dashboard startup. |
| `core/dispatcher.py` | `ActionDispatcher` — central singleton tool registry + routing. **The canonical place for tools.** |
| `core/action_registry.py` | `register_all_actions()` — registers built-in tools with the dispatcher. Add new core tools here. |
| `core/error_handler.py` | `log_error(...)` — use `context` and `severity` params. |
| `actions/` | 36 action modules. Two shapes: dispatcher-registered (`core/action_registry.py`) or PluginRegistry-shaped (`*_TOOLS` + `handle_*_tool`). |
| `plugin_registry.py` | `PluginRegistry` — auto-discovers `actions/*` modules exporting `*_TOOLS` lists + `handle_*_tool` handlers. Fallback path for tools. |
| `plugin_manager.py` | `PluginManager` — loads **user** plugins from `plugins/*.py` (lifecycle hooks only, NOT tools). |
| `plugins/` | User plugins (`plugin` object + hooks: `on_rex_created`, `on_text_command`, `on_startup`, `dispatch`). |
| `mcp_server/server.py` | MCP server wrapping dispatcher tools (MCP SDK v2). |
| `dashboard/server.py` + `dashboard/static/` | FastAPI dashboard :8000 (mobile connect, dispatch PWA, HUD). |
| `ui/` | Qt GUI: `main_window.py`, `widgets.py`, `styles.py`, `gesture_canvas.py`. |
| `smart_home/` | Smart home service, device manager, providers (Kasa, Atomberg), SQLite storage. |
| `memory/` | Long-term user memory, JSON persistence (`memory_manager.py`, `config_manager.py`). |
| `agent/` | Planner/executor/task-queue sub-system (`planner.py`, `executor.py`, `task_queue.py`). |
| `config/` | API keys, app settings, Firebase config — **git-ignored secrets**, never commit. |
| `tests/` | pytest suite with shared fixtures in `conftest.py`. |

## ⚠️ Four overlapping tool systems (the #1 gotcha)

1. **ActionDispatcher** (`core/dispatcher.py`) — 28 core tools registered by `action_registry.py`.
   Dispatch modes: `executor` (default) | `thread` | `smart_home` | `agent_task` | `custom` | `plugin`.
2. **PluginRegistry** (`plugin_registry.py`) — auto-discovers `actions/` modules exporting
   `<CATEGORY>_TOOLS` + `handle_<category>_tool`. Used as the dispatcher's fallback.
3. **PluginManager** (`plugin_manager.py`) — user plugins in `plugins/`, lifecycle hooks only. **NOT for tools.**
4. **MCP Server** — wraps dispatcher tools for MCP clients. Currently duplicates dispatch logic;
   prefer refactoring toward `dispatcher.dispatch()` rather than adding new parallel paths.

**Rule of thumb for new capabilities:** register with the **ActionDispatcher** unless the tool is
plugin-shaped (a `*_TOOLS` list module). Do not extend PluginManager with tool handlers.

## Adding a new action (dispatcher pattern — preferred)

1. Create `actions/your_thing.py` with a handler. Handler signature convention:
   `def your_thing(parameters: dict, player=None, speak=None, response=None, session_memory=None) -> str`
2. Register it in `core/action_registry.py`:

```python
from actions.your_thing import your_thing
dispatcher.register(
    name="your_thing",
    description="What this does and WHEN to call it (agent-facing).",
    parameters={
        "type": "OBJECT",
        "properties": {"key": {"type": "STRING", "description": "..."}},
        "required": ["key"],
    },
    handler=your_thing,
    dispatch="executor",        # or thread / smart_home / agent_task
    default_result="Done.",
    needs_speak=False,
)
```

- Parameter types are custom: `STRING | INTEGER | BOOLEAN | ARRAY | OBJECT` — **not** JSON Schema.
- `default_result` is what the agent hears if the handler returns no text.

## PluginRegistry-shaped action (fallback path)

Module exports a list named `<CATEGORY>_TOOLS` (list of dicts each with a `name` key) plus a
handler `handle_<category>_tool(tool_name, parameters)` (fallback name `handle_<module>_tool`).
Category derives from the list name: `OSINT_TOOLS` → category `osint`. See `actions/osint_tools.py`.

## Adding a user plugin (lifecycle only)

`plugins/<name>.py` exports a `plugin` object (or just module-level functions) with optional hooks:
`on_rex_created(rex)`, `on_text_command(text, ...)`, `on_startup()`, `dispatch(hook, *args, **kwargs)`.
Loaded by `PluginManager`; **cannot register tools** — use the dispatcher for that.

## Conventions

- **Logging:** `from core.error_handler import log_error`; use `log_error(msg, context=..., severity=...)`.
- **Tool params** use the custom types above, never JSON Schema.
- **Wake words:** "rex", "hey rex", "hi rex", "hello rex".
- **Windows-first** (this is a Windows app): `win32`/`comtypes`/`pywinauto`/`pyautogui` are fair game;
  keep cross-platform paths cheap when they don't add complexity.
- **Secrets:** `config/*.json`, `.env`, `.freebuff/`, `.jarvis-data/`, `memory/long_term.json` are git-ignored.
  Never commit them or read real API keys into tests.
- **Tests use fixtures, not real services:** `temp_config`, `temp_memory`, `sample_memory` in
  `tests/conftest.py` patch config/memory paths. Test modules insert project root into `sys.path`.

## Known gotchas (read before refactoring)

- `email_manager`, `ocr_tool`, `calendar_sync` are registered with `handler=None, dispatch="plugin"`
  and only work through the PluginRegistry fallback — not the core dispatcher.
- The dispatcher's `plugin_registry` parameter must receive a `PluginRegistry`, **not** a
  `PluginManager` (interface mismatch caused a bug, fixed in commit 66333eb; `PluginManager.get_handler`
  exists only to make this safe).
- MCP server duplicates dispatch logic — prefer routing through `dispatcher.dispatch()`.
- `knowledge.md` is the canonical architecture-notes file; update it when you make structural changes.
