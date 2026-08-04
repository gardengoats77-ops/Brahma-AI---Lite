"""
tests/conftest.py — Shared fixtures for the REX test suite
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def temp_config(tmp_path):
    """Create a temporary api_keys.json and patch _CONFIG_PATH."""
    config_file = tmp_path / "api_keys.json"
    config_file.write_text(json.dumps({
        "gemini_api_key": "test-gemini-key",
        "openrouter_api_key": "test-openrouter-key",
        "anthropic_api_key": "",
    }), encoding="utf-8")

    with patch("config._CONFIG_PATH", config_file):
        yield config_file


@pytest.fixture
def temp_memory(tmp_path):
    """Create a temporary long_term.json and patch MEMORY_PATH."""
    mem_file = tmp_path / "long_term.json"
    mem_file.parent.mkdir(parents=True, exist_ok=True)

    with patch("memory.memory_manager.MEMORY_PATH", mem_file):
        yield mem_file


@pytest.fixture
def sample_memory():
    """Return a sample memory dict."""
    return {
        "identity": {
            "name": {"value": "chuckee", "updated": "2026-08-02"},
            "city": {"value": "Mumbai", "updated": "2026-08-01"},
        },
        "preferences": {
            "favorite_color": {"value": "gold", "updated": "2026-08-02"},
        },
        "projects": {},
        "relationships": {},
        "wishes": {},
        "notes": {},
    }
