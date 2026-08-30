# REX — Project Knowledge

## What this is

REX is an open-source Windows desktop AI assistant (Python). Voice + text interaction via Gemini AI with OpenRouter fallback. Desktop automation, office document generation, browser control, smart home, email, calendar, and more.

Maintained by Suryaansh Tiwari. License is source-available (see `LICENSE`).

## Commands

```bash
# Setup
pip install -r requirements.txt
playwright install

# Run the app
python main.py
start_rex.bat          # cleaner Windows launch

# Tests
.venv/Scripts/python.exe -m pytest tests/ -x -q

# MCP server (exposes REX tools to LLM clients)
python -m mcp_server.server

# Dashboard preview
uvicorn dashboard.server:app --host 127.0.0.1 --port 8000
```

## Key directories & files

| Path | Purpose |
|------|---------|
| `main.py` | Entry point, `RexLive` class, AI session orchestration (1600+ lines) |
| `core/dispatcher.py` | `ActionDispatcher` — central tool registry + routing (executor/thread/smart_home/agent_task) |
| `core/action_registry.py` | `register_all_actions()` — registers 28 built-in tools with the dispatcher |
| `plugin_registry.py` | `PluginRegistry` — auto-discovers tool modules in `actions/` directory |
| `plugin_manager.py` | `PluginManager` — loads user plugins from `plugins/` folder (lifecycle hooks only) |
| `mcp_server/server.py` | MCP server wrapping dispatcher tools (28 tools) using MCP SDK v2.0.0 |
| `actions/` | 35+ action modules (open_app, browser_control, file_controller, deep_research, etc.) |
| `dashboard/server.py` | FastAPI dashboard on port 8000 (mobile connect, dispatch PWA, HUD) |
| `dashboard/static/` | HTML frontends: `app.html`, `login.html`, `dispatch/`, `hud/` |
| `ui/` | Qt-based GUI: `main_window.py`, `widgets.py`, `styles.py`, `gesture_canvas.py` |
| `smart_home/` | Smart home service, device manager, providers (Kasa, Atomberg) |
| `memory/` | Long-term user memory (JSON file persistence) |
| `tests/` | pytest suite — 199 tests across `test_action_plugins.py`, `test_plugin_registry.py`, `test_memory.py`, etc. |
| `config/` | API keys, app settings, Firebase config (git-ignored) |

## Architecture gotchas

### Four overlapping tool systems

1. **ActionDispatcher** — 28 core tools, registered by `action_registry.py`. Dispatch modes: `executor` (default), `thread`, `smart_home`, `agent_task`, `custom`, `plugin`.
2. **PluginRegistry** — auto-discovers modules in `actions/` that export `*_TOOLS` lists + `handle_*_tool` functions. Used as fallback by dispatcher.
3. **PluginManager** — user-plugins in `plugins/` with lifecycle hooks (`on_rex_created`, `on_text_command`, `on_startup`). NOT for tools.
4. **MCP Server** — wraps dispatcher tools for MCP clients. Currently **duplicates** dispatch logic — should be refactored to call `dispatcher.dispatch()` directly.

### Critical bug fixed (66333eb)

The dispatcher's `plugin_registry` parameter was receiving a `PluginManager` instead of a `PluginRegistry`. Added `get_handler()` to PluginManager and pass the correct singleton from `plugin_registry.py`.

### Tools with handler=None need attention

`email_manager`, `ocr_tool`, `calendar_sync` are registered with `handler=None, dispatch="plugin"` — they only work via the plugin_registry fallback, not via the core dispatcher.

### Python version

Targets Python 3.11-3.12. Uses `.venv` virtual environment.

## Conventions

- Action handlers accept `parameters` dict + `player` (UI), `speak`, `response`, `session_memory`
- Tool declarations use custom types (`STRING`, `INTEGER`, `BOOLEAN`, `ARRAY`, `OBJECT`) — not JSON Schema
- Error logging: `from core.error_handler import log_error` — use `context` and `severity` params
- Wake words: "rex", "hey rex", "hi rex", "hello rex"
- Config files in `config/` are git-ignored for security
- `.env`, `.freebuff/`, `.jarvis-data/` are git-ignored
