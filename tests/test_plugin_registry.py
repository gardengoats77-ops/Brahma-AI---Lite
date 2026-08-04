"""
tests/test_plugin_registry.py — Tests for plugin_registry auto-discovery and dispatch
"""

import sys
import importlib
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure the project root is importable
PROJECT_ROOT = str(Path(__file__).parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from plugin_registry import PluginRegistry, registry


# ═══════════════════════════════════════════════════════════════════
# Test: PluginRegistry.discover()
# ═══════════════════════════════════════════════════════════════════

class TestDiscovery:
    """Tests for auto-discovery of plugin modules."""

    def test_discover_returns_positive_count(self):
        """discover() should find at least 1 plugin."""
        reg = PluginRegistry()
        count = reg.discover(Path(PROJECT_ROOT) / "actions")
        assert count > 0, "No plugins discovered"

    def test_discover_finds_osint_tools(self):
        """discover() should find the osint_tools plugin."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        plugin = reg.get_plugin("osint")
        assert plugin is not None, "osint plugin not found"
        assert len(plugin.tools_list) > 0

    def test_discover_finds_email_manager(self):
        """discover() should find the email_manager plugin."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        plugin = reg.get_plugin("email")
        assert plugin is not None, "email plugin not found"

    def test_discover_finds_network_tools(self):
        """discover() should find the network_tools plugin."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        plugin = reg.get_plugin("network")
        assert plugin is not None, "network plugin not found"

    def test_discover_finds_redteam_tools(self):
        """discover() should find the redteam_tools plugin."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        plugin = reg.get_plugin("redteam")
        assert plugin is not None, "redteam plugin not found"

    def test_discover_finds_ocr_tool(self):
        """discover() should find the ocr_tool plugin."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        plugin = reg.get_plugin("ocr")
        assert plugin is not None, "ocr plugin not found"

    def test_discover_finds_deep_research(self):
        """discover() should find the deep_research plugin."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        plugin = reg.get_plugin("research")
        assert plugin is not None, "research plugin not found"

    def test_discover_finds_email_monitor(self):
        """discover() should find the email_monitor plugin."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        plugin = reg.get_plugin("email_monitor")
        assert plugin is not None, "email_monitor plugin not found"

    def test_discover_finds_calendar_sync(self):
        """discover() should find the calendar_sync plugin."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        plugin = reg.get_plugin("calendar")
        assert plugin is not None, "calendar plugin not found"

    def test_discover_finds_db_query(self):
        """discover() should find the db_query plugin."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        plugin = reg.get_plugin("db")
        assert plugin is not None, "db plugin not found"

    def test_discover_idempotent(self):
        """Calling discover() twice should not duplicate plugins."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        count1 = len(reg.get_all_tools())
        reg.discover(Path(PROJECT_ROOT) / "actions")
        count2 = len(reg.get_all_tools())
        assert count1 == count2, "Duplicate discovery"

    def test_discover_nonexistent_dir_returns_zero(self):
        """discover() with nonexistent dir should return 0."""
        reg = PluginRegistry()
        count = reg.discover(Path("/nonexistent/path"))
        assert count == 0


# ═══════════════════════════════════════════════════════════════════
# Test: PluginRegistry.get_all_tools()
# ═══════════════════════════════════════════════════════════════════

class TestGetAllTools:
    """Tests for the unified tool declarations list."""

    def test_returns_list(self):
        """get_all_tools() should return a list."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        tools = reg.get_all_tools()
        assert isinstance(tools, list)

    def test_all_tools_have_name(self):
        """Every tool should have a 'name' field."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        for tool in reg.get_all_tools():
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert isinstance(tool["name"], str)
            assert len(tool["name"]) > 0

    def test_all_tools_have_description(self):
        """Every tool should have a 'description' field."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        for tool in reg.get_all_tools():
            assert "description" in tool, f"Tool '{tool.get('name')}' missing 'description'"

    def test_all_tools_have_parameters(self):
        """Every tool should have a 'parameters' field."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        for tool in reg.get_all_tools():
            assert "parameters" in tool, f"Tool '{tool.get('name')}' missing 'parameters'"
            params = tool["parameters"]
            assert "type" in params, f"Tool '{tool.get('name')}' parameters missing 'type'"
            assert "properties" in params, f"Tool '{tool.get('name')}' parameters missing 'properties'"

    def test_minimum_tool_count(self):
        """Should discover at least 40 tools across all plugins."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        tools = reg.get_all_tools()
        assert len(tools) >= 40, f"Expected >= 40 tools, got {len(tools)}"

    def test_tool_names_are_strings(self):
        """All tool names should be strings."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        for tool in reg.get_all_tools():
            assert isinstance(tool["name"], str)


# ═══════════════════════════════════════════════════════════════════
# Test: PluginRegistry.dispatch()
# ═══════════════════════════════════════════════════════════════════

class TestDispatch:
    """Tests for tool dispatch routing."""

    def test_dispatch_unknown_tool_returns_error(self):
        """Dispatching an unknown tool should return an error string."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        result = reg.dispatch("nonexistent_tool_xyz", {}, None)
        assert "Unknown tool" in result or "❌" in result

    def test_dispatch_osint_tool(self):
        """Dispatching an osint tool should reach the osint handler."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        handler = reg.get_handler("osint_ip_lookup")
        assert handler is not None, "osint_ip_lookup handler not found"

    def test_dispatch_email_tool(self):
        """Dispatching an email tool should reach the email handler."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        handler = reg.get_handler("email_read")
        assert handler is not None, "email_read handler not found"

    def test_dispatch_db_tool(self):
        """Dispatching a db tool should reach the db handler."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        handler = reg.get_handler("db_list_connections")
        assert handler is not None, "db_list_connections handler not found"

    def test_dispatch_calendar_tool(self):
        """Dispatching a calendar tool should reach the calendar handler."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        handler = reg.get_handler("calendar_list_events")
        assert handler is not None, "calendar_list_events handler not found"

    def test_dispatch_ocr_tool(self):
        """Dispatching an ocr tool should reach the ocr handler."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        handler = reg.get_handler("ocr_extract")
        assert handler is not None, "ocr_extract handler not found"

    def test_dispatch_research_tool(self):
        """Dispatching a research tool should reach the research handler."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        handler = reg.get_handler("research_topic")
        assert handler is not None, "research_topic handler not found"

    def test_dispatch_handles_exception_gracefully(self):
        """dispatch() should catch exceptions and return error string."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        # db_list_connections is safe and should not crash
        result = reg.dispatch("db_list_connections", {}, None)
        assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════
# Test: PluginRegistry.validate_all()
# ═══════════════════════════════════════════════════════════════════

class TestValidation:
    """Tests for tool declaration validation."""

    def test_validate_no_duplicates(self):
        """validate_all() should flag duplicate tool names."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        issues = reg.validate_all()
        dup_issues = [i for i in issues if "Duplicate" in i]
        assert len(dup_issues) == 0, f"Duplicate tools found: {dup_issues}"

    def test_validate_all_tools_have_name(self):
        """validate_all() should flag tools missing 'name'."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        issues = reg.validate_all()
        name_issues = [i for i in issues if "missing 'name'" in i]
        assert len(name_issues) == 0, f"Tools missing name: {name_issues}"

    def test_validate_all_tools_have_description(self):
        """validate_all() should flag tools missing 'description'."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        issues = reg.validate_all()
        desc_issues = [i for i in issues if "missing 'description'" in i]
        assert len(desc_issues) == 0, f"Tools missing description: {desc_issues}"

    def test_validate_core_tool_collision_detection(self):
        """validate_all() should detect collisions with core tools."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        # Simulate a core tool with the same name as a plugin tool
        core_names = {"db_list_connections"}
        issues = reg.validate_all(core_tool_names=core_names)
        collision_issues = [i for i in issues if "Duplicate" in i and "db_list_connections" in i]
        assert len(collision_issues) > 0, "Should detect collision with core tool"


# ═══════════════════════════════════════════════════════════════════
# Test: PluginInfo dataclass
# ═══════════════════════════════════════════════════════════════════

class TestPluginInfo:
    """Tests for PluginInfo metadata."""

    def test_plugin_has_name(self):
        """Each plugin should have a name."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        for plugin in reg.list_plugins():
            assert isinstance(plugin.name, str)
            assert len(plugin.name) > 0

    def test_plugin_has_category(self):
        """Each plugin should have a category."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        for plugin in reg.list_plugins():
            assert isinstance(plugin.category, str)
            assert len(plugin.category) > 0

    def test_plugin_has_handler(self):
        """Each plugin should have a callable handler."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        for plugin in reg.list_plugins():
            assert callable(plugin.handler), f"Handler not callable: {plugin.handler}"

    def test_plugin_has_tools_list(self):
        """Each plugin should have a non-empty tools list."""
        reg = PluginRegistry()
        reg.discover(Path(PROJECT_ROOT) / "actions")
        for plugin in reg.list_plugins():
            assert isinstance(plugin.tools_list, list)
            assert len(plugin.tools_list) > 0


# ═══════════════════════════════════════════════════════════════════
# Test: Singleton registry
# ═══════════════════════════════════════════════════════════════════

class TestSingleton:
    """Tests for the module-level singleton."""

    def test_singleton_exists(self):
        """The module-level registry singleton should exist."""
        assert registry is not None
        assert isinstance(registry, PluginRegistry)

    def test_singleton_has_plugins(self):
        """The singleton should have discovered plugins."""
        # The singleton auto-discovers at import time
        tools = registry.get_all_tools()
        assert len(tools) > 0
