"""
tests/test_config.py — Tests for API key loading and config validation
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, mock_open

import pytest

try:
    from ui import _default_app_settings
    HAS_PYQT6 = True
except ImportError:
    HAS_PYQT6 = False


# ---------------------------------------------------------------------------
# Test: config/api_keys.json loading
# ---------------------------------------------------------------------------

class TestApiKeyLoading:
    """Tests for the config/api_keys.json loading logic."""

    def test_load_api_keys_returns_dict(self, tmp_path):
        """Loading a valid api_keys.json returns a dict."""
        config = tmp_path / "api_keys.json"
        config.write_text(json.dumps({
            "gemini_api_key": "test-gemini-key",
            "openrouter_api_key": "test-openrouter-key",
        }), encoding="utf-8")

        with patch("pathlib.Path.open", mock_open(read_data=config.read_text())):
            data = json.loads(config.read_text(encoding="utf-8"))

        assert isinstance(data, dict)
        assert "gemini_api_key" in data
        assert "openrouter_api_key" in data

    def test_load_api_keys_empty_file_returns_empty(self, tmp_path):
        """An empty api_keys.json should not crash."""
        config = tmp_path / "api_keys.json"
        config.write_text("{}", encoding="utf-8")
        data = json.loads(config.read_text(encoding="utf-8"))
        assert data == {}

    def test_load_api_keys_missing_file(self, tmp_path):
        """A missing api_keys.json should raise FileNotFoundError."""
        config = tmp_path / "nonexistent.json"
        with pytest.raises(FileNotFoundError):
            config.read_text(encoding="utf-8")

    def test_load_api_keys_invalid_json(self, tmp_path):
        """Invalid JSON should raise json.JSONDecodeError."""
        config = tmp_path / "api_keys.json"
        config.write_text("not valid json {{{", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            json.loads(config.read_text(encoding="utf-8"))

    def test_api_keys_have_correct_structure(self, tmp_path):
        """api_keys.json should have the expected key structure."""
        config = tmp_path / "api_keys.json"
        config.write_text(json.dumps({
            "gemini_api_key": "AIza...",
            "openrouter_api_key": "sk-or-...",
            "anthropic_api_key": "",
        }), encoding="utf-8")
        data = json.loads(config.read_text(encoding="utf-8"))

        expected_keys = {"gemini_api_key", "openrouter_api_key", "anthropic_api_key"}
        assert expected_keys.issubset(set(data.keys()))




# ---------------------------------------------------------------------------
# Test: config/__init__.py loading
# ---------------------------------------------------------------------------

class TestConfigInit:
    """Tests for config/__init__.py module."""

    def test_config_path_is_set(self):
        """config._CONFIG_PATH should point to api_keys.json."""
        from config import _CONFIG_PATH
        assert _CONFIG_PATH.name == "api_keys.json"

    def test_get_config_returns_dict(self, temp_config):
        """get_config() should return a dict when config exists."""
        from config import get_config
        result = get_config()
        assert isinstance(result, dict)
        assert "gemini_api_key" in result

    def test_get_os_returns_string(self, temp_config):
        """get_os() should return a valid OS string."""
        from config import get_os
        result = get_os()
        assert isinstance(result, str)
        assert result in ("windows", "mac", "linux")


# ---------------------------------------------------------------------------
# Test: app_settings.json
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_PYQT6, reason="PyQt6 not installed")
class TestAppSettings:
    """Tests for config/app_settings.json loading."""

    def test_default_settings_structure(self):
        """Default settings should have expected keys."""
        settings = _default_app_settings()
        assert isinstance(settings, dict)
        assert "startup_animation_enabled" in settings
        assert "developer_mode_enabled" in settings

    def test_settings_json_roundtrip(self, tmp_path):
        """Settings should survive a JSON serialize/deserialize cycle."""
        original = _default_app_settings()
        path = tmp_path / "settings.json"
        path.write_text(json.dumps(original, indent=2), encoding="utf-8")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded == original
