"""
REX MCP Server
==============
Exposes all REX desktop assistant tools as MCP (Model Context Protocol) tools,
allowing any MCP-compatible LLM client to use REX's capabilities.

Usage:
    python -m mcp_server.server          # stdio transport (default)
    python -m mcp_server.server --http   # streamable HTTP on port 3100

Requires:  pip install mcp
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import traceback
from pathlib import Path
from typing import Any

# ── Ensure project root is importable ────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# ── Bootstrap REX dispatcher ─────────────────────────────────────────────────
from core.action_registry import register_all_actions  # noqa: E402
from core.dispatcher import dispatcher  # noqa: E402
from plugin_registry import registry  # noqa: E402

register_all_actions()

# ── Constants ────────────────────────────────────────────────────────────────
_TYPE_MAP = {
    "STRING": "string", "INTEGER": "integer", "NUMBER": "number",
    "BOOLEAN": "boolean", "ARRAY": "array", "OBJECT": "object",
}
_SKIPPED = {"shutdown_rex", "save_memory"}


# ── Build tool list ──────────────────────────────────────────────────────────

def _to_mcp_tool(name: str, decl: dict) -> types.Tool:
    params = decl.get("parameters", {})
    properties = params.get("properties", {})
    required = params.get("required", [])
    json_props = {}
    for pname, pdef in properties.items():
        raw_type = pdef.get("type", "STRING").upper()
        json_type = _TYPE_MAP.get(raw_type, "string")
        prop: dict[str, Any] = {"type": json_type, "description": pdef.get("description", "")}
        if raw_type == "ARRAY":
            items = pdef.get("items", {})
            prop["items"] = {"type": _TYPE_MAP.get(items.get("type", "STRING").upper(), "string")}
        json_props[pname] = prop
    return types.Tool(
        name=f"rex_{name}",
        description=decl.get("description", f"REX tool: {name}"),
        inputSchema={"type": "object", "properties": json_props, "required": required},
    )


def _build_tool_list() -> list[types.Tool]:
    tools: list[types.Tool] = []
    seen: set[str] = set()
    for name in dispatcher.names():
        if name in _SKIPPED:
            continue
        action = dispatcher.get(name)
        if action is not None:
            tools.append(_to_mcp_tool(name, action.declaration))
            seen.add(name)

    for decl in registry.get_all_tools():
        name = str(decl.get("name") or "")
        if name and name not in _SKIPPED and name not in seen:
            tools.append(_to_mcp_tool(name, decl))
            seen.add(name)

    # Utility tools
    tools.append(types.Tool(
        name="rex_list_tools",
        description="List all available REX tools with their descriptions.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ))
    tools.append(types.Tool(
        name="rex_system_info",
        description="Show REX system information: version, platform, Python version.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ))
    return tools


_TOOLS = _build_tool_list()


async def _dispatch_tool(name: str, args: dict) -> str:
    action_name = name.removeprefix("rex_")

    if action_name == "list_tools":
        lines = [f"• {t.name}: {(t.description or '')[:100]}" for t in _TOOLS
                 if t.name.startswith("rex_") and t.name not in ("rex_list_tools", "rex_system_info")]
        return "\n".join(lines)

    if action_name == "system_info":
        import platform
        return (f"REX AI Desktop Assistant\n"
                f"Platform: {platform.system()} {platform.release()}\n"
                f"Python: {platform.python_version()}\n"
                f"Tools registered: {len(dispatcher.names())}")

    action_def = dispatcher.get(action_name)
    smart_home_service = None
    if action_def and action_def.dispatch == "smart_home":
        from smart_home.service import SmartHomeService
        smart_home_service = SmartHomeService()

    try:
        result = await dispatcher.dispatch(
            action_name,
            args,
            loop=asyncio.get_running_loop(),
            smart_home_service=smart_home_service,
            plugin_registry=registry,
        )
        if result.startswith("Unknown action:"):
            return f"Unknown tool: {name}"
        return result
    except Exception as e:
        return f"Error executing {name}: {e}\n\n{traceback.format_exc()}"


# ── MCP server ───────────────────────────────────────────────────────────────

async def _on_list_tools(ctx, params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=_TOOLS)


async def _on_call_tool(ctx, params) -> types.CallToolResult:
    result_text = await _dispatch_tool(params.name, params.arguments or {})
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=result_text)],
    )


app = Server(
    name="rex-tools",
    description="REX Desktop Assistant tools for Windows automation, web, files, smart home, email, calendar, and more.",
    on_list_tools=_on_list_tools,
    on_call_tool=_on_call_tool,
)


# ── Entry point ──────────────────────────────────────────────────────────────

async def _run_stdio():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main():
    parser = argparse.ArgumentParser(description="REX MCP Server")
    parser.add_argument("--http", action="store_true", help="Use streamable HTTP transport")
    parser.add_argument("--port", type=int, default=3100, help="HTTP port (default: 3100)")
    args = parser.parse_args()

    if args.http:
        print("[REX MCP] HTTP transport not yet implemented — use stdio.")
        sys.exit(1)
    asyncio.run(_run_stdio())


if __name__ == "__main__":
    main()
