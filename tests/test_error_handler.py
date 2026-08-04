"""
tests/test_error_handler.py — Tests for core/error_handler.py
"""

import logging
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Test: ErrorCategory classification
# ---------------------------------------------------------------------------

class TestErrorCategory:
    """Tests for error classification."""

    def test_network_errors(self):
        """Connection/Timeout/HTTP errors should be classified as NETWORK."""
        from core.error_handler import _classify_error, ErrorCategory
        for exc_cls in [ConnectionError, TimeoutError]:
            assert _classify_error(exc_cls("test")) == ErrorCategory.NETWORK

    def test_file_errors(self):
        """File/IO errors should be classified as FILE_SYSTEM."""
        from core.error_handler import _classify_error, ErrorCategory
        assert _classify_error(FileNotFoundError("not found")) == ErrorCategory.FILE_SYSTEM
        assert _classify_error(IOError("io error")) == ErrorCategory.FILE_SYSTEM

    def test_value_errors(self):
        """Value/Type/Index errors should be classified as INPUT."""
        from core.error_handler import _classify_error, ErrorCategory
        assert _classify_error(ValueError("bad value")) == ErrorCategory.INPUT
        assert _classify_error(TypeError("bad type")) == ErrorCategory.INPUT
        assert _classify_error(IndexError("out of range")) == ErrorCategory.INPUT
        assert _classify_error(KeyError("missing key")) == ErrorCategory.INPUT

    def test_unknown_errors(self):
        """Generic exceptions should be classified as UNKNOWN."""
        from core.error_handler import _classify_error, ErrorCategory
        assert _classify_error(RuntimeError("something")) == ErrorCategory.UNKNOWN


# ---------------------------------------------------------------------------
# Test: get_logger
# ---------------------------------------------------------------------------

class TestGetLogger:
    """Tests for get_logger() factory."""

    def test_returns_logger(self):
        """get_logger should return a logging.Logger instance."""
        from core.error_handler import get_logger
        logger = get_logger("test_module")
        assert isinstance(logger, logging.Logger)

    def test_logger_has_rex_prefix(self):
        """Logger name should be prefixed with 'rex.'."""
        from core.error_handler import get_logger
        logger = get_logger("my_module")
        assert logger.name == "rex.my_module"

    def test_logger_has_handler(self):
        """Logger should have at least one handler after setup."""
        from core.error_handler import get_logger
        logger = get_logger("test_handlers")
        assert len(logger.handlers) > 0 or len(logger.parent.handlers) > 0


# ---------------------------------------------------------------------------
# Test: log_error
# ---------------------------------------------------------------------------

class TestLogError:
    """Tests for log_error() function."""

    def test_returns_metadata_dict(self):
        """log_error should return a dict with error metadata."""
        from core.error_handler import log_error
        result = log_error(ValueError("test error"), context="test.module")
        assert isinstance(result, dict)
        assert result["category"] == "input"
        assert result["context"] == "test.module"
        assert result["exception_type"] == "ValueError"
        assert "test error" in result["message"]

    def test_includes_timestamp(self):
        """Metadata should include an ISO timestamp."""
        from core.error_handler import log_error
        result = log_error(RuntimeError("test"), context="test")
        assert "timestamp" in result
        assert "T" in result["timestamp"]  # ISO format has T separator

    def test_extra_data_included(self):
        """Extra data should be included in the metadata."""
        from core.error_handler import log_error
        result = log_error(
            ValueError("test"),
            context="test",
            extra_data={"attempt": 3, "model": "gemini"}
        )
        assert result["extra"]["attempt"] == 3
        assert result["extra"]["model"] == "gemini"


# ---------------------------------------------------------------------------
# Test: safe_execute
# ---------------------------------------------------------------------------

class TestSafeExecute:
    """Tests for safe_execute() wrapper."""

    def test_returns_function_result(self):
        """Should return the function's return value on success."""
        from core.error_handler import safe_execute
        result = safe_execute(lambda: 42, context="test")
        assert result == 42

    def test_returns_default_on_failure(self):
        """Should return default value when function raises."""
        from core.error_handler import safe_execute
        def failing():
            raise ValueError("boom")
        result = safe_execute(failing, default="fallback", context="test")
        assert result == "fallback"

    def test_returns_none_by_default(self):
        """Default return value should be None if not specified."""
        from core.error_handler import safe_execute
        def failing():
            raise RuntimeError("fail")
        result = safe_execute(failing, context="test")
        assert result is None

    def test_passes_args_to_function(self):
        """Arguments should be forwarded to the wrapped function."""
        from core.error_handler import safe_execute
        def add(a, b):
            return a + b
        result = safe_execute(add, 3, 4, context="test")
        assert result == 7

    def test_passes_kwargs_to_function(self):
        """Keyword arguments should be forwarded to the wrapped function."""
        from core.error_handler import safe_execute
        def greet(name="world"):
            return f"hello {name}"
        result = safe_execute(greet, name="chuckee", context="test")
        assert result == "hello chuckee"


# ---------------------------------------------------------------------------
# Test: handle_errors decorator
# ---------------------------------------------------------------------------

class TestHandleErrors:
    """Tests for @handle_errors decorator."""

    def test_passes_through_on_success(self):
        """Decorator should not interfere with normal execution."""
        from core.error_handler import handle_errors

        @handle_errors(context="test", default_return="fallback")
        def good_func():
            return "success"

        assert good_func() == "success"

    def test_returns_default_on_failure(self):
        """Decorator should return default on exception."""
        from core.error_handler import handle_errors

        @handle_errors(context="test", default_return="fallback")
        def bad_func():
            raise ValueError("boom")

        assert bad_func() == "fallback"

    def test_preserves_function_name(self):
        """Decorator should preserve the original function name."""
        from core.error_handler import handle_errors

        @handle_errors(context="test")
        def my_special_function():
            return True

        assert my_special_function.__name__ == "my_special_function"

    def test_reraise_option(self):
        """With reraise=True, exceptions should propagate."""
        from core.error_handler import handle_errors

        @handle_errors(context="test", reraise=True)
        def bad_func():
            raise RuntimeError("reraise me")

        with pytest.raises(RuntimeError, match="reraise me"):
            bad_func()


# ---------------------------------------------------------------------------
# Test: safe_import
# ---------------------------------------------------------------------------

class TestSafeImport:
    """Tests for safe_import() helper."""

    def test_imports_existing_module(self):
        """Should return the module for valid imports."""
        from core.error_handler import safe_import
        result = safe_import("json")
        assert result is not None
        assert hasattr(result, "loads")

    def test_returns_none_for_missing_module(self):
        """Should return None for missing optional dependencies."""
        from core.error_handler import safe_import
        result = safe_import("nonexistent_package_xyz_123")
        assert result is None


# ---------------------------------------------------------------------------
# Test: safe_json_load
# ---------------------------------------------------------------------------

class TestSafeJsonLoad:
    """Tests for safe_json_load() helper."""

    def test_loads_valid_json(self, tmp_path):
        """Should parse valid JSON files."""
        from core.error_handler import safe_json_load
        f = tmp_path / "test.json"
        f.write_text('{"key": "value"}', encoding="utf-8")
        result = safe_json_load(f)
        assert result == {"key": "value"}

    def test_returns_default_on_missing_file(self, tmp_path):
        """Should return default for missing files."""
        from core.error_handler import safe_json_load
        result = safe_json_load(tmp_path / "missing.json", default={"fallback": True})
        assert result == {"fallback": True}

    def test_returns_default_on_invalid_json(self, tmp_path):
        """Should return default for invalid JSON."""
        from core.error_handler import safe_json_load
        f = tmp_path / "bad.json"
        f.write_text("not json", encoding="utf-8")
        result = safe_json_load(f, default=[])
        assert result == []
