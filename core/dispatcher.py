"""
REX Action Dispatcher
Centralizes action registration, TOOL_DECLARATIONS, and dispatch logic.
Actions register once; the dispatcher handles routing, executor wrapping,
and plugin fallback automatically.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Optional

from core.error_handler import log_error


# ---------------------------------------------------------------------------
# Action registration
# ---------------------------------------------------------------------------

class ActionDef:
    """One registered action: metadata + handler + dispatch strategy."""

    __slots__ = ("name", "declaration", "handler", "dispatch", "default_result",
                 "needs_speak", "thread")

    def __init__(
        self,
        name: str,
        declaration: dict,
        handler: Callable,
        dispatch: str = "executor",        # "executor" | "thread" | "smart_home" | "agent_task" | "custom"
        default_result: str = "Done.",
        needs_speak: bool = False,
        thread: bool = False,
    ):
        self.name = name
        self.declaration = declaration
        self.handler = handler
        self.dispatch = dispatch
        self.default_result = default_result
        self.needs_speak = needs_speak
        self.thread = thread


class ActionDispatcher:
    """Singleton registry that all action modules register into at import time."""

    def __init__(self):
        self._actions: dict[str, ActionDef] = {}

    # -- registration -------------------------------------------------------

    def register(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: Callable,
        *,
        dispatch: str = "executor",
        default_result: str = "Done.",
        needs_speak: bool = False,
    ) -> None:
        declaration = {
            "name": name,
            "description": description,
            "parameters": parameters,
        }
        self._actions[name] = ActionDef(
            name=name,
            declaration=declaration,
            handler=handler,
            dispatch=dispatch,
            default_result=default_result,
            needs_speak=needs_speak,
        )

    # -- queries ------------------------------------------------------------

    def get_declarations(self) -> list[dict]:
        """Return TOOL_DECLARATION list for the Gemini API."""
        return [a.declaration for a in self._actions.values()]

    def has(self, name: str) -> bool:
        return name in self._actions

    def get(self, name: str) -> Optional[ActionDef]:
        return self._actions.get(name)

    def names(self) -> list[str]:
        return list(self._actions.keys())

    # -- dispatch ------------------------------------------------------------

    async def dispatch(
        self,
        name: str,
        args: dict,
        *,
        ui=None,
        speak: Optional[Callable] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        smart_home_service=None,
        plugin_registry=None,
        extra_context: Optional[dict] = None,
    ) -> str:
        """
        Dispatch an action by name. Returns the result string.

        Special dispatch modes:
        - "executor":  run_in_executor (default)
        - "thread":    background daemon thread
        - "smart_home": delegate to smart_home_service.execute_command
        - "agent_task": submit to agent task queue
        - "custom":    call handler(args, ui=ui, speak=speak) directly (for plugin fallback)
        """
        extra = extra_context or {}
        loop = loop or asyncio.get_event_loop()

        action = self._actions.get(name)

        # ── Special: smart_home_control ──────────────────────────────────
        if action and action.dispatch == "smart_home":
            command_text = str(args.get("command") or "").strip()
            r = await loop.run_in_executor(
                None, lambda: smart_home_service.execute_command(command_text)
            )
            return str((r or {}).get("detail") or "Smart-home command completed.")

        # ── Special: agent_task ──────────────────────────────────────────
        if action and action.dispatch == "agent_task":
            from agent.task_queue import get_queue, TaskPriority
            priority_map = {
                "low": TaskPriority.LOW,
                "normal": TaskPriority.NORMAL,
                "high": TaskPriority.HIGH,
            }
            priority = priority_map.get(
                args.get("priority", "normal").lower(), TaskPriority.NORMAL
            )
            task_id = get_queue().submit(
                goal=args.get("goal", ""), priority=priority, speak=speak
            )
            return f"Task started (ID: {task_id})."

        # ── Special: shutdown_rex ────────────────────────────────────────
        if name == "shutdown_rex":
            if ui:
                ui.write_log("SYS: Shutdown requested.")
            if speak:
                speak("Goodbye, sir.")

            def _shutdown():
                import time, os
                time.sleep(1)
                os._exit(0)

            threading.Thread(target=_shutdown, daemon=True).start()
            return "Shutting down."

        # ── Plugin fallback ─────────────────────────────────────────────
        if action is None and plugin_registry and plugin_registry.get_handler(name):
            r = await loop.run_in_executor(
                None, lambda: plugin_registry.dispatch(name, args, speak)
            )
            return r or "Done."

        if action is None:
            return f"Unknown action: {name}"

        # Plugin-shaped registrations intentionally keep their implementation
        # in PluginRegistry; resolve them through the same fallback used by
        # undiscovered action names instead of inspecting a None handler.
        if action.dispatch == "plugin":
            if plugin_registry and plugin_registry.get_handler(name):
                result = await loop.run_in_executor(
                    None, lambda: plugin_registry.dispatch(name, args, speak)
                )
                return result or action.default_result
            return f"Action '{name}' is unavailable: plugin handler not loaded."

        # ── Pre-dispatch mutations (file_processor, word_document) ───────
        if name == "file_processor" and ui:
            if not args.get("file_path") and getattr(ui, "current_file", None):
                args["file_path"] = ui.current_file
        if name == "word_document" and ui:
            if not args.get("file_path") and getattr(ui, "current_file", None):
                from pathlib import Path as _P
                if _P(ui.current_file).suffix.lower() == ".docx":
                    args["file_path"] = ui.current_file

        # ── Thread dispatch ─────────────────────────────────────────────
        if action.thread:
            threading.Thread(
                target=action.handler,
                kwargs={"parameters": args, "response": None, "player": ui,
                        "session_memory": None},
                daemon=True,
            ).start()
            return action.default_result

        # ── Executor dispatch (default) ─────────────────────────────────
        call_kwargs = {"parameters": args, "player": ui}
        if action.needs_speak:
            call_kwargs["speak"] = speak

        # Some handlers accept extra kwargs (response, session_memory)
        # We pass them only if the handler's signature expects them
        import inspect
        sig = inspect.signature(action.handler)
        if "response" in sig.parameters:
            call_kwargs["response"] = None
        if "session_memory" in sig.parameters:
            call_kwargs["session_memory"] = None

        r = await loop.run_in_executor(
            None, lambda: action.handler(**call_kwargs)
        )
        return r or action.default_result


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

dispatcher = ActionDispatcher()
