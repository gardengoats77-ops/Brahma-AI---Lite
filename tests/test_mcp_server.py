"""Regression tests for the MCP-to-dispatcher execution seam."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from mcp_server import server


def test_mcp_routes_core_action_through_dispatcher() -> None:
    """MCP must use dispatcher policy for a normal registered action."""
    dispatch = AsyncMock(return_value="canonical result")

    with patch.object(server.dispatcher, "dispatch", dispatch):
        result = asyncio.run(server._dispatch_tool("rex_web_search", {"query": "REX"}))

    assert result == "canonical result"
    dispatch.assert_awaited_once()
    assert dispatch.await_args.kwargs["plugin_registry"] is server.registry


def test_mcp_routes_plugin_action_through_dispatcher_fallback() -> None:
    """MCP must not reject an action that exists only in PluginRegistry."""
    dispatch = AsyncMock(return_value="plugin result")

    with patch.object(server.dispatcher, "dispatch", dispatch):
        result = asyncio.run(server._dispatch_tool("rex_osint_ip_lookup", {"ip": "127.0.0.1"}))

    assert result == "plugin result"
    dispatch.assert_awaited_once()
    assert dispatch.await_args.args == ("osint_ip_lookup", {"ip": "127.0.0.1"})


def test_mcp_converts_dispatcher_failure_to_safe_text() -> None:
    """A dispatcher exception must become an MCP-safe string result."""
    dispatch = AsyncMock(side_effect=RuntimeError("backend unavailable"))

    with patch.object(server.dispatcher, "dispatch", dispatch):
        result = asyncio.run(server._dispatch_tool("rex_web_search", {"query": "REX"}))

    assert "Error executing rex_web_search" in result
    assert "backend unavailable" in result


def test_mcp_advertises_plugin_registry_tools() -> None:
    """MCP clients must be able to discover plugin-shaped actions."""
    tools = {tool.name: tool for tool in server._build_tool_list()}
    assert "rex_db_list_connections" in tools
    schema = tools["rex_db_list_connections"].input_schema
    assert schema["type"] == "object"
    assert "properties" in schema


def test_mcp_executes_plugin_action_through_real_dispatcher(tmp_path) -> None:
    """The real dispatcher must execute an action-shaped plugin fallback."""
    isolated_config = tmp_path / "database_connections.json"
    isolated_config.write_text("{}", encoding="utf-8")
    from actions import db_query

    with patch.object(db_query, "DB_CONFIG_PATH", isolated_config):
        result = asyncio.run(server._dispatch_tool("rex_db_list_connections", {}))

    assert result == "📭 No database connections configured. Use db_add_connection to add one."


def test_mcp_does_not_construct_smart_home_for_normal_action() -> None:
    """Unrelated MCP actions must not depend on smart-home initialization."""
    dispatch = AsyncMock(return_value="canonical result")
    with patch.object(server.dispatcher, "dispatch", dispatch), patch(
        "smart_home.service.SmartHomeService", side_effect=AssertionError("unexpected smart-home init")
    ):
        result = asyncio.run(server._dispatch_tool("rex_web_search", {"query": "REX"}))

    assert result == "canonical result"
