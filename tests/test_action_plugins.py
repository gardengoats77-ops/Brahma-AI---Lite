"""
tests/test_action_plugins.py — Tests for all 9 REX action plugins
Verifies tool declarations, handler routing, and basic functionality.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = str(Path(__file__).parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ═══════════════════════════════════════════════════════════════════
# Helper: Plugin test base
# ═══════════════════════════════════════════════════════════════════

def _validate_tool_declarations(tools_list, category_name):
    """Validate a plugin's TOOL declarations structure."""
    assert isinstance(tools_list, list), f"{category_name}: TOOLS should be a list"
    assert len(tools_list) > 0, f"{category_name}: TOOLS should not be empty"
    for tool in tools_list:
        assert "name" in tool, f"{category_name}: tool missing 'name'"
        assert "description" in tool, f"{category_name}: tool '{tool.get('name')}' missing 'description'"
        assert "parameters" in tool, f"{category_name}: tool '{tool.get('name')}' missing 'parameters'"
        params = tool["parameters"]
        assert "type" in params, f"{category_name}: tool '{tool.get('name')}' params missing 'type'"
        assert "properties" in params, f"{category_name}: tool '{tool.get('name')}' params missing 'properties'"


def _validate_handler(handler, tools_list, category_name):
    """Validate a plugin's handler function."""
    assert callable(handler), f"{category_name}: handler should be callable"
    # Test that handler returns a string for unknown tool
    result = handler("nonexistent_tool_xyz", {}, None)
    assert isinstance(result, str), f"{category_name}: handler should return string"


# ═══════════════════════════════════════════════════════════════════
# Test: OSINT Tools
# ═══════════════════════════════════════════════════════════════════

class TestOSINTTools:
    """Tests for actions/osint_tools.py"""

    def test_tool_declarations_valid(self):
        from actions.osint_tools import OSINT_TOOLS
        _validate_tool_declarations(OSINT_TOOLS, "osint")

    def test_handler_callable(self):
        from actions.osint_tools import handle_osint_tool
        assert callable(handle_osint_tool)

    def test_handler_returns_string(self):
        from actions.osint_tools import handle_osint_tool
        result = handle_osint_tool("nonexistent_tool", {}, None)
        assert isinstance(result, str)

    def test_has_domain_recon(self):
        from actions.osint_tools import OSINT_TOOLS
        names = [t["name"] for t in OSINT_TOOLS]
        assert "osint_domain_recon" in names

    def test_has_ip_lookup(self):
        from actions.osint_tools import OSINT_TOOLS
        names = [t["name"] for t in OSINT_TOOLS]
        assert "osint_ip_lookup" in names

    def test_has_full_recon(self):
        from actions.osint_tools import OSINT_TOOLS
        names = [t["name"] for t in OSINT_TOOLS]
        assert "osint_full_recon" in names


# ═══════════════════════════════════════════════════════════════════
# Test: Network Tools
# ═══════════════════════════════════════════════════════════════════

class TestNetworkTools:
    """Tests for actions/network_tools.py"""

    def test_tool_declarations_valid(self):
        from actions.network_tools import NETWORK_TOOLS
        _validate_tool_declarations(NETWORK_TOOLS, "network")

    def test_handler_callable(self):
        from actions.network_tools import handle_network_tool
        assert callable(handle_network_tool)

    def test_handler_returns_string(self):
        from actions.network_tools import handle_network_tool
        result = handle_network_tool("nonexistent_tool", {}, None)
        assert isinstance(result, str)

    def test_has_ping(self):
        from actions.network_tools import NETWORK_TOOLS
        names = [t["name"] for t in NETWORK_TOOLS]
        assert "net_ping" in names

    def test_has_port_scan(self):
        from actions.network_tools import NETWORK_TOOLS
        names = [t["name"] for t in NETWORK_TOOLS]
        assert "net_port_scan" in names


# ═══════════════════════════════════════════════════════════════════
# Test: Red Team Tools
# ═══════════════════════════════════════════════════════════════════

class TestRedTeamTools:
    """Tests for actions/redteam_tools.py"""

    def test_tool_declarations_valid(self):
        from actions.redteam_tools import REDTEAM_TOOLS
        _validate_tool_declarations(REDTEAM_TOOLS, "redteam")

    def test_handler_callable(self):
        from actions.redteam_tools import handle_redteam_tool
        assert callable(handle_redteam_tool)

    def test_handler_returns_string(self):
        from actions.redteam_tools import handle_redteam_tool
        result = handle_redteam_tool("nonexistent_tool", {}, None)
        assert isinstance(result, str)

    def test_has_password_check(self):
        from actions.redteam_tools import REDTEAM_TOOLS
        names = [t["name"] for t in REDTEAM_TOOLS]
        assert "red_password_check" in names


# ═══════════════════════════════════════════════════════════════════
# Test: Email Manager
# ═══════════════════════════════════════════════════════════════════

class TestEmailManager:
    """Tests for actions/email_manager.py"""

    def test_tool_declarations_valid(self):
        from actions.email_manager import EMAIL_TOOLS
        _validate_tool_declarations(EMAIL_TOOLS, "email")

    def test_handler_callable(self):
        from actions.email_manager import handle_email_tool
        assert callable(handle_email_tool)

    def test_handler_returns_string(self):
        from actions.email_manager import handle_email_tool
        result = handle_email_tool("nonexistent_tool", {}, None)
        assert isinstance(result, str)

    def test_has_email_read(self):
        from actions.email_manager import EMAIL_TOOLS
        names = [t["name"] for t in EMAIL_TOOLS]
        assert "email_read" in names

    def test_has_email_compose(self):
        from actions.email_manager import EMAIL_TOOLS
        names = [t["name"] for t in EMAIL_TOOLS]
        assert "email_compose" in names

    def test_has_email_auth(self):
        from actions.email_manager import EMAIL_TOOLS
        names = [t["name"] for t in EMAIL_TOOLS]
        assert "email_auth" in names

    def test_detect_provider_returns_string(self):
        from actions.email_manager import _detect_provider
        result = _detect_provider()
        assert isinstance(result, str)
        assert result in ("gmail", "outlook", "none")


# ═══════════════════════════════════════════════════════════════════
# Test: OCR Tool
# ═══════════════════════════════════════════════════════════════════

class TestOCRCool:
    """Tests for actions/ocr_tool.py"""

    def test_tool_declarations_valid(self):
        from actions.ocr_tool import OCR_TOOLS
        _validate_tool_declarations(OCR_TOOLS, "ocr")

    def test_handler_callable(self):
        from actions.ocr_tool import handle_ocr_tool
        assert callable(handle_ocr_tool)

    def test_handler_returns_string(self):
        from actions.ocr_tool import handle_ocr_tool
        result = handle_ocr_tool("nonexistent_tool", {}, None)
        assert isinstance(result, str)

    def test_has_ocr_extract(self):
        from actions.ocr_tool import OCR_TOOLS
        names = [t["name"] for t in OCR_TOOLS]
        assert "ocr_extract" in names


# ═══════════════════════════════════════════════════════════════════
# Test: Deep Research
# ═══════════════════════════════════════════════════════════════════

class TestDeepResearch:
    """Tests for actions/deep_research.py"""

    def test_tool_declarations_valid(self):
        from actions.deep_research import RESEARCH_TOOLS
        _validate_tool_declarations(RESEARCH_TOOLS, "research")

    def test_handler_callable(self):
        from actions.deep_research import handle_research_tool
        assert callable(handle_research_tool)

    def test_handler_returns_string(self):
        from actions.deep_research import handle_research_tool
        result = handle_research_tool("nonexistent_tool", {}, None)
        assert isinstance(result, str)

    def test_has_research_topic(self):
        from actions.deep_research import RESEARCH_TOOLS
        names = [t["name"] for t in RESEARCH_TOOLS]
        assert "research_topic" in names

    def test_has_research_cache(self):
        from actions.deep_research import RESEARCH_TOOLS
        names = [t["name"] for t in RESEARCH_TOOLS]
        assert "research_cache" in names


# ═══════════════════════════════════════════════════════════════════
# Test: Email Monitor
# ═══════════════════════════════════════════════════════════════════

class TestEmailMonitor:
    """Tests for actions/email_monitor.py"""

    def test_tool_declarations_valid(self):
        from actions.email_monitor import EMAIL_MONITOR_TOOLS
        _validate_tool_declarations(EMAIL_MONITOR_TOOLS, "email_monitor")

    def test_handler_callable(self):
        from actions.email_monitor import handle_email_monitor_tool
        assert callable(handle_email_monitor_tool)

    def test_handler_returns_string(self):
        from actions.email_monitor import handle_email_monitor_tool
        result = handle_email_monitor_tool("nonexistent_tool", {}, None)
        assert isinstance(result, str)

    def test_has_monitor_start(self):
        from actions.email_monitor import EMAIL_MONITOR_TOOLS
        names = [t["name"] for t in EMAIL_MONITOR_TOOLS]
        assert "email_monitor_start" in names

    def test_has_monitor_stop(self):
        from actions.email_monitor import EMAIL_MONITOR_TOOLS
        names = [t["name"] for t in EMAIL_MONITOR_TOOLS]
        assert "email_monitor_stop" in names

    def test_has_monitor_status(self):
        from actions.email_monitor import EMAIL_MONITOR_TOOLS
        names = [t["name"] for t in EMAIL_MONITOR_TOOLS]
        assert "email_monitor_status" in names

    def test_get_email_monitor_returns_instance(self):
        from actions.email_monitor import get_email_monitor
        monitor = get_email_monitor()
        assert monitor is not None
        assert hasattr(monitor, "config")
        assert hasattr(monitor, "start")
        assert hasattr(monitor, "stop")

    def test_monitor_config_has_defaults(self):
        from actions.email_monitor import get_email_monitor
        monitor = get_email_monitor()
        assert "interval_minutes" in monitor.config
        assert "quiet_hours_start" in monitor.config
        assert "voice_alerts" in monitor.config


# ═══════════════════════════════════════════════════════════════════
# Test: Calendar Sync
# ═══════════════════════════════════════════════════════════════════

class TestCalendarSync:
    """Tests for actions/calendar_sync.py"""

    def test_tool_declarations_valid(self):
        from actions.calendar_sync import CALENDAR_TOOLS
        _validate_tool_declarations(CALENDAR_TOOLS, "calendar")

    def test_handler_callable(self):
        from actions.calendar_sync import handle_calendar_tool
        assert callable(handle_calendar_tool)

    def test_handler_returns_string(self):
        from actions.calendar_sync import handle_calendar_tool
        result = handle_calendar_tool("nonexistent_tool", {}, None)
        assert isinstance(result, str)

    def test_has_calendar_list_events(self):
        from actions.calendar_sync import CALENDAR_TOOLS
        names = [t["name"] for t in CALENDAR_TOOLS]
        assert "calendar_list_events" in names

    def test_has_calendar_create_event(self):
        from actions.calendar_sync import CALENDAR_TOOLS
        names = [t["name"] for t in CALENDAR_TOOLS]
        assert "calendar_create_event" in names

    def test_has_calendar_today(self):
        from actions.calendar_sync import CALENDAR_TOOLS
        names = [t["name"] for t in CALENDAR_TOOLS]
        assert "calendar_today" in names

    def test_calendar_list_events_no_provider(self):
        """Should return error when no provider configured."""
        from actions.calendar_sync import calendar_list_events
        with patch("actions.calendar_sync._detect_provider", return_value="none"):
            result = calendar_list_events()
            assert "❌" in result or "No calendar" in result


# ═══════════════════════════════════════════════════════════════════
# Test: Database Query
# ═══════════════════════════════════════════════════════════════════

class TestDBQuery:
    """Tests for actions/db_query.py"""

    def test_tool_declarations_valid(self):
        from actions.db_query import DB_TOOLS
        _validate_tool_declarations(DB_TOOLS, "db")

    def test_handler_callable(self):
        from actions.db_query import handle_db_tool
        assert callable(handle_db_tool)

    def test_handler_returns_string(self):
        from actions.db_query import handle_db_tool
        result = handle_db_tool("nonexistent_tool", {}, None)
        assert isinstance(result, str)

    def test_has_db_query(self):
        from actions.db_query import DB_TOOLS
        names = [t["name"] for t in DB_TOOLS]
        assert "db_query" in names

    def test_has_db_add_connection(self):
        from actions.db_query import DB_TOOLS
        names = [t["name"] for t in DB_TOOLS]
        assert "db_add_connection" in names

    def test_has_db_list_connections(self):
        from actions.db_query import DB_TOOLS
        names = [t["name"] for t in DB_TOOLS]
        assert "db_list_connections" in names

    def test_has_db_execute_sql(self):
        from actions.db_query import DB_TOOLS
        names = [t["name"] for t in DB_TOOLS]
        assert "db_execute_sql" in names

    def test_is_safe_sql_blocks_drop(self):
        from actions.db_query import _is_safe_sql
        assert not _is_safe_sql("DROP TABLE users")

    def test_is_safe_sql_blocks_delete(self):
        from actions.db_query import _is_safe_sql
        assert not _is_safe_sql("DELETE FROM users")

    def test_is_safe_sql_blocks_semicolon(self):
        from actions.db_query import _is_safe_sql
        assert not _is_safe_sql("SELECT * FROM users; DROP TABLE users")

    def test_is_safe_sql_allows_select(self):
        from actions.db_query import _is_safe_sql
        assert _is_safe_sql("SELECT * FROM users")

    def test_list_connections_returns_string(self):
        from actions.db_query import db_list_connections
        result = db_list_connections()
        assert isinstance(result, str)

    def test_add_and_remove_connection(self):
        """Test adding and removing a SQLite connection."""
        from actions.db_query import db_add_connection, db_remove_connection, db_list_connections
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            result = db_add_connection("test_sqlite", "sqlite", path=db_path)
            assert "✅" in result
            result = db_remove_connection("test_sqlite")
            assert "✅" in result

    def test_sqlite_connection_works(self):
        """Test a basic SQLite query."""
        from actions.db_query import db_add_connection, db_execute_sql, db_remove_connection
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            # Create a test database
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE test (id INTEGER, name TEXT)")
            conn.execute("INSERT INTO test VALUES (1, 'hello')")
            conn.commit()
            conn.close()

            db_add_connection("test_sqlite_q", "sqlite", path=db_path)
            result = db_execute_sql("test_sqlite_q", "SELECT * FROM test")
            assert "hello" in result
            db_remove_connection("test_sqlite_q")


# ═══════════════════════════════════════════════════════════════════
# Test: Cross-plugin consistency
# ═══════════════════════════════════════════════════════════════════

class TestCrossPluginConsistency:
    """Tests that verify consistency across all plugins."""

    ALL_PLUGINS = [
        ("osint_tools", "OSINT_TOOLS", "handle_osint_tool"),
        ("network_tools", "NETWORK_TOOLS", "handle_network_tool"),
        ("redteam_tools", "REDTEAM_TOOLS", "handle_redteam_tool"),
        ("email_manager", "EMAIL_TOOLS", "handle_email_tool"),
        ("ocr_tool", "OCR_TOOLS", "handle_ocr_tool"),
        ("deep_research", "RESEARCH_TOOLS", "handle_research_tool"),
        ("email_monitor", "EMAIL_MONITOR_TOOLS", "handle_email_monitor_tool"),
        ("calendar_sync", "CALENDAR_TOOLS", "handle_calendar_tool"),
        ("db_query", "DB_TOOLS", "handle_db_tool"),
    ]

    @pytest.mark.parametrize("category,tools_var,handler_var", ALL_PLUGINS)
    def test_tools_list_exists(self, category, tools_var, handler_var):
        """Each plugin should export its TOOLS list."""
        module = __import__(f"actions.{category}", fromlist=[tools_var])
        tools = getattr(module, tools_var)
        assert isinstance(tools, list)
        assert len(tools) > 0

    @pytest.mark.parametrize("category,tools_var,handler_var", ALL_PLUGINS)
    def test_handler_exists(self, category, tools_var, handler_var):
        """Each plugin should export its handler function."""
        module = __import__(f"actions.{category}", fromlist=[handler_var])
        handler = getattr(module, handler_var)
        assert callable(handler)

    @pytest.mark.parametrize("category,tools_var,handler_var", ALL_PLUGINS)
    def test_all_tools_have_unique_names(self, category, tools_var, handler_var):
        """No plugin should have duplicate tool names within itself."""
        module = __import__(f"actions.{category}", fromlist=[tools_var])
        tools = getattr(module, tools_var)
        names = [t["name"] for t in tools]
        assert len(names) == len(set(names)), f"Duplicate names in {category}: {names}"
