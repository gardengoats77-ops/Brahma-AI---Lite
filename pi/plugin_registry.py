"""
pi/plugin_registry.py — Voice Command Plugin Registry

Provides @voice_command decorator that auto-registers voice handlers
into a thread-safe registry. Plugins/*.py are auto-discovered at boot.

Usage:
    from pi.plugin_registry import voice_command

    @voice_command("turn on the lights", description="Turn on lights")
    def handle_lights(command_text: str, **kwargs) -> str:
        return "Lights on"

Discovery at boot:
    from pi.plugin_registry import registry
    registry.discover()  # scans plugins/*.py
    tools = registry.get_tool_declarations()  # Live-compatible
"""

import importlib
import importlib.util
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple


@dataclass
class VoicePluginInfo:
    """Metadata for a registered voice command handler."""

    name: str                        # function __name__ (e.g. "handle_lights")
    description: str                 # human-readable description
    pattern: str                     # regex or literal string pattern
    handler: Callable                # the decorated function
    module: Optional[object] = None  # source module (set during discovery)


class VoiceCommandRegistry:
    """
    Thread-safe registry for voice command plugins.

    Supports:
      - @reg.register("pattern") decorator
      - reg.discover(dir) for scanning plugins/*.py at boot
      - reg.get_tool_declarations() for Live API tool declarations
      - reg.match_command(text) for routing transcribed speech → handler
    """

    def __init__(self):
        self._plugins: list[VoicePluginInfo] = []
        self._lock = threading.Lock()
        self._import_counter: int = 0

    # ── Registration ─────────────────────────────────────────────────────

    def register(self, pattern: str, description: str = "") -> Callable:
        """
        Decorator factory: ``@reg.register("pattern", description="...")``

        Registers the decorated function as a voice command handler.
        Thread-safe: concurrent registrations are serialized via lock.
        """

        def decorator(func: Callable) -> Callable:
            with self._lock:
                # Replace any prior registration from the same function object
                self._plugins = [p for p in self._plugins if p.handler is not func]
                self._plugins.append(
                    VoicePluginInfo(
                        name=func.__name__,
                        description=description or func.__doc__ or "",
                        pattern=pattern,
                        handler=func,
                    )
                )
            # Mark the function so discover() can find it in imported modules
            func._voice_command_pattern = pattern  # type: ignore[attr-defined]
            func._voice_command_description = description or func.__doc__ or ""  # type: ignore[attr-defined]
            return func

        return decorator

    # ── Introspection ───────────────────────────────────────────────────

    def get_plugins(self) -> list[VoicePluginInfo]:
        """Return a snapshot copy of all registered plugins."""
        with self._lock:
            return list(self._plugins)

    def get_tool_declarations(self) -> list[dict]:
        """
        Build Gemini Live-compatible ``function_declarations`` for every
        registered plugin.  Each declaration exposes a ``command_text``
        string parameter so the model can pass the raw transcription.
        """
        tools = []
        for plugin in self.get_plugins():
            tools.append(
                {
                    "name": plugin.name,
                    "description": f"{plugin.description} (voice: {plugin.pattern})",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command_text": {
                                "type": "string",
                                "description": "The full transcribed voice command",
                            },
                        },
                        "required": ["command_text"],
                    },
                }
            )
        return tools

    # ── Matching / dispatch ─────────────────────────────────────────────

    def match_command(self, text: str) -> Optional[Tuple[VoicePluginInfo, object]]:
        """
        Return the first plugin whose pattern matches *text*.

        Matching strategy:
          - If the pattern contains regex meta-characters, use ``re.search``.
          - Otherwise, case-insensitive substring search.

        Returns ``(plugin, match_object)`` or ``None``.
        """
        if not text:
            return None

        text_lower = text.lower()
        for plugin in self.get_plugins():
            pattern = plugin.pattern
            if _looks_like_regex(pattern):
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    return (plugin, match)
            elif pattern.lower() in text_lower:
                return (plugin, None)
        return None

    # ── Discovery ───────────────────────────────────────────────────────

    def discover(self, plugins_dir: str | Path = None) -> int:
        """
        Scan *plugins_dir* for ``*.py`` files and import them.

        Importing triggers any ``@voice_command`` decorators in those
        modules, which auto-register into the singleton (``registry``) or
        into *this* instance if it differs.

        Returns the number of **new** plugins discovered.
        """
        if plugins_dir is None:
            plugins_dir = Path(__file__).resolve().parent.parent / "plugins"
        else:
            plugins_dir = Path(plugins_dir)

        if not plugins_dir.exists():
            return 0

        before = {p.name for p in self.get_plugins()}

        for py_file in sorted(plugins_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue  # skip __init__.py, private helpers
            self._import_plugin_file(py_file)

        after = {p.name for p in self.get_plugins()}
        return len(after - before)

    def _import_plugin_file(self, path: Path) -> None:
        """Import a single plugin file so its decorators run."""
        self._import_counter += 1
        module_name = f"_voice_plugin_{self._import_counter}_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, str(path))
            if spec is None or spec.loader is None:
                return
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as e:  # noqa: BLE001
            print(f"[VoiceCommandRegistry] Failed to import {path.name}: {e}")
        finally:
            # Prevent cache pollution across multiple discover() calls
            sys.modules.pop(module_name, None)

    # ── Lifecycle ───────────────────────────────────────────────────────

    def clear(self) -> None:
        """Remove all registered plugins (useful for testing)."""
        with self._lock:
            self._plugins.clear()


# ── Helpers ───────────────────────────────────────────────────────────────

_REGEX_META = set(r".*+?^$()[]{}|\"'")


def _looks_like_regex(pattern: str) -> bool:
    """Heuristic: does *pattern* contain regex meta-characters?"""
    return bool(_REGEX_META.intersection(pattern))


# ── Module-level convenience decorator & singleton ─────────────────────────

registry = VoiceCommandRegistry()


def voice_command(pattern: str, description: str = "") -> Callable:
    """
    Module-level decorator — shorthand for ``registry.register(...)``.

    Usage::

        @voice_command("play music", description="Play music")
        def handle_music(command_text: str, **kwargs) -> str:
            return "Playing music"
    """
    return registry.register(pattern, description=description)