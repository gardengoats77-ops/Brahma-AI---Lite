"""
Plugin Registry for REX
Auto-discovers action modules that follow the convention:
  - Module exports a list named <CATEGORY>_TOOLS (list of tool declaration dicts)
  - Module exports a function named handle_<category>_tool(tool_name, parameters, speak)

Usage:
    from plugin_registry import registry
    all_tools = registry.get_all_tools()
    result = registry.dispatch(tool_name, parameters, speak_fn)
"""

import importlib
import pkgutil
import sys
from pathlib import Path
from typing import Callable, Optional
from dataclasses import dataclass, field


@dataclass
class PluginInfo:
    """Metadata for a discovered plugin."""
    name: str                   # e.g. "osint_tools"
    category: str               # e.g. "osint"  (prefix for tool names)
    tools_list: list            # the TOOLS list (e.g. OSINT_TOOLS)
    handler: Callable           # handle function (e.g. handle_osint_tool)
    module: object = None       # the imported module


class PluginRegistry:
    """
    Scans the actions/ directory for plugin modules and builds:
    - A unified TOOL_DECLARATIONS list
    - A dispatch function that routes tool_name → correct handler
    """

    # Convention: modules whose name ends with these skip auto-registration
    # (they are helpers, not tool providers)
    _SKIP_MODULES = {"__pycache__"}

    # Convention: tool list vars end with _TOOLS, handler funcs start with handle_
    _TOOLS_SUFFIX = "_TOOLS"
    _HANDLER_PREFIX = "handle_"
    _HANDLER_SUFFIX = "_tool"

    def __init__(self):
        self._plugins: dict[str, PluginInfo] = {}
        self._all_tools: list[dict] = []
        self._dispatch_map: dict[str, Callable] = {}
        self._loaded = False

    def discover(self, actions_dir: str | Path = None) -> int:
        """
        Scan actions/ for plugin modules and register them.
        Returns the number of plugins discovered.
        """
        if self._loaded:
            return len(self._plugins)

        if actions_dir is None:
            actions_dir = Path(__file__).parent / "actions"
        else:
            actions_dir = Path(actions_dir)

        if not actions_dir.exists():
            print(f"[PluginRegistry] actions/ directory not found: {actions_dir}")
            return 0

        # Ensure actions/ is importable
        actions_str = str(actions_dir.parent)
        if actions_str not in sys.path:
            sys.path.insert(0, actions_str)

        discovered = 0
        for module_info in pkgutil.iter_modules([str(actions_dir)]):
            module_name = module_info.name

            # Skip __pycache__ and private modules
            if module_name.startswith("_") or module_name in self._SKIP_MODULES:
                continue

            try:
                full_name = f"actions.{module_name}"
                module = importlib.import_module(full_name)
                self._register_module(module_name, module)
                discovered += 1
            except ImportError as e:
                print(f"[PluginRegistry] Missing dependency for actions.{module_name}: {e}")
            except Exception as e:
                print(f"[PluginRegistry] Failed to load actions.{module_name}: {type(e).__name__}: {e}")

        self._loaded = True
        print(f"[PluginRegistry] Discovered {discovered} plugins with {len(self._all_tools)} tools")
        return discovered

    def _register_module(self, module_name: str, module: object) -> None:
        """Inspect a module for *_TOOLS and handle_*_tool exports."""
        # Find the TOOLS list
        tools_list = None
        category = None

        for attr_name in dir(module):
            if attr_name.endswith(self._TOOLS_SUFFIX) and attr_name.startswith("_"):
                continue
            if attr_name.endswith(self._TOOLS_SUFFIX):
                val = getattr(module, attr_name, None)
                if isinstance(val, list) and len(val) > 0:
                    # Check it's a list of dicts with 'name' keys
                    if all(isinstance(t, dict) and "name" in t for t in val):
                        tools_list = val
                        # Derive category: "OSINT_TOOLS" → "osint"
                        category = attr_name.replace(self._TOOLS_SUFFIX, "").lower()
                        break

        if tools_list is None or category is None:
            return  # Not a tool-providing module

        # Find the handler function
        handler_name = f"handle_{category}{self._HANDLER_SUFFIX}"
        handler = getattr(module, handler_name, None)
        if handler is None:
            # Try alternative: handle_<module_name>_tool
            handler_name = f"handle_{module_name}{self._HANDLER_SUFFIX}"
            handler = getattr(module, handler_name, None)

        if handler is None or not callable(handler):
            print(f"[PluginRegistry] Warning: {module_name} has {attr_name} but no handler ({handler_name})")
            return

        # Register
        plugin = PluginInfo(
            name=module_name,
            category=category,
            tools_list=tools_list,
            handler=handler,
            module=module,
        )
        self._plugins[category] = plugin
        self._all_tools.extend(tools_list)

        # Build dispatch map: tool_name → handler
        for tool in tools_list:
            tool_name = tool.get("name", "")
            if tool_name:
                self._dispatch_map[tool_name] = handler

        print(f"[PluginRegistry] Registered: {category} ({len(tools_list)} tools)")

    def get_all_tools(self) -> list[dict]:
        """Return the combined TOOL_DECLARATIONS list."""
        if not self._loaded:
            self.discover()
        return self._all_tools

    def dispatch(self, tool_name: str, parameters: dict, speak: Optional[Callable] = None) -> str:
        """
        Route a tool call to the correct handler.
        Returns the handler's string result, or an error message.
        """
        handler = self._dispatch_map.get(tool_name)
        if handler is None:
            return f"❌ Unknown tool: {tool_name}"
        try:
            return handler(tool_name, parameters, speak)
        except Exception as e:
            return f"❌ Plugin error ({tool_name}): {e}"

    def get_plugin(self, category: str) -> Optional[PluginInfo]:
        """Get a specific plugin by category name."""
        return self._plugins.get(category)

    def list_plugins(self) -> list[PluginInfo]:
        """List all registered plugins."""
        if not self._loaded:
            self.discover()
        return list(self._plugins.values())

    def get_handler(self, tool_name: str) -> Optional[Callable]:
        """Get the handler for a specific tool name."""
        return self._dispatch_map.get(tool_name)

    def validate_all(self, core_tool_names: set = None) -> list[str]:
        """
        Validate all registered tool declarations.
        Returns a list of warning/error messages (empty = all valid).
        """
        issues = []
        seen_names = set(core_tool_names or set())

        for plugin in self._plugins.values():
            for tool in plugin.tools_list:
                name = tool.get("name", "")

                # Check for duplicate names (including core tools)
                if name in seen_names:
                    issues.append(f"Duplicate tool name: {name} (in {plugin.name})")
                seen_names.add(name)

                # Check required fields
                if not name:
                    issues.append(f"Tool in {plugin.name} missing 'name' field")
                    continue
                if "description" not in tool:
                    issues.append(f"Tool '{name}' missing 'description'")
                if "parameters" not in tool:
                    issues.append(f"Tool '{name}' missing 'parameters'")
                else:
                    params = tool["parameters"]
                    if "type" not in params:
                        issues.append(f"Tool '{name}' parameters missing 'type'")
                    if "properties" not in params:
                        issues.append(f"Tool '{name}' parameters missing 'properties'")

                    # Check required fields exist in properties
                    required = params.get("required", [])
                    properties = params.get("properties", {})
                    for req_field in required:
                        if req_field not in properties:
                            issues.append(f"Tool '{name}' required field '{req_field}' not in properties")

        return issues


# Singleton instance
registry = PluginRegistry()
