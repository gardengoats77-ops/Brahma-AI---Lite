"""
tests/test_memory.py — Tests for memory/memory_manager.py
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# Fixtures are in conftest.py: temp_memory, sample_memory


# ---------------------------------------------------------------------------
# Test: load_memory
# ---------------------------------------------------------------------------

class TestLoadMemory:
    """Tests for load_memory()."""

    def test_load_empty_when_no_file(self, temp_memory):
        """Should return empty memory when file doesn't exist."""
        from memory.memory_manager import load_memory
        result = load_memory()
        assert isinstance(result, dict)
        assert "identity" in result
        assert "preferences" in result
        assert "projects" in result
        assert "relationships" in result
        assert "wishes" in result
        assert "notes" in result

    def test_load_returns_valid_structure(self, temp_memory, sample_memory):
        """Should load and return the saved memory structure."""
        from memory.memory_manager import load_memory, save_memory
        save_memory(sample_memory)
        result = load_memory()
        assert result["identity"]["name"]["value"] == "chuckee"
        assert result["preferences"]["favorite_color"]["value"] == "gold"

    def test_load_handles_corrupted_json(self, temp_memory):
        """Should return empty memory when file contains invalid JSON."""
        from memory.memory_manager import load_memory
        temp_memory.write_text("not valid json {{{", encoding="utf-8")
        result = load_memory()
        assert isinstance(result, dict)
        assert "identity" in result


# ---------------------------------------------------------------------------
# Test: save_memory
# ---------------------------------------------------------------------------

class TestSaveMemory:
    """Tests for save_memory()."""

    def test_save_creates_file(self, temp_memory, sample_memory):
        """Should create the memory file when saving."""
        from memory.memory_manager import save_memory
        save_memory(sample_memory)
        assert temp_memory.exists()

    def test_save_writes_valid_json(self, temp_memory, sample_memory):
        """Should write valid JSON that can be parsed back."""
        from memory.memory_manager import save_memory
        save_memory(sample_memory)
        data = json.loads(temp_memory.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert data["identity"]["name"]["value"] == "chuckee"

    def test_save_roundtrip(self, temp_memory, sample_memory):
        """Save then load should return the same data."""
        from memory.memory_manager import save_memory, load_memory
        save_memory(sample_memory)
        loaded = load_memory()
        assert loaded == sample_memory


# ---------------------------------------------------------------------------
# Test: update_memory
# ---------------------------------------------------------------------------

class TestUpdateMemory:
    """Tests for update_memory()."""

    def test_update_adds_new_entry(self, temp_memory):
        """Should add a new entry to memory."""
        from memory.memory_manager import update_memory, load_memory
        update_memory({"notes": {"new_thing": {"value": "something"}}})
        memory = load_memory()
        assert memory["notes"]["new_thing"]["value"] == "something"

    def test_update_existing_entry(self, temp_memory, sample_memory):
        """Should update an existing entry's value."""
        from memory.memory_manager import update_memory, save_memory, load_memory
        save_memory(sample_memory)
        update_memory({"identity": {"name": {"value": "new_name"}}})
        memory = load_memory()
        assert memory["identity"]["name"]["value"] == "new_name"

    def test_update_preserves_other_entries(self, temp_memory, sample_memory):
        """Should not overwrite other entries when updating one."""
        from memory.memory_manager import update_memory, save_memory, load_memory
        save_memory(sample_memory)
        update_memory({"identity": {"city": {"value": "Delhi"}}})
        memory = load_memory()
        assert memory["identity"]["name"]["value"] == "chuckee"
        assert memory["identity"]["city"]["value"] == "Delhi"

    def test_update_empty_dict_noop(self, temp_memory, sample_memory):
        """Empty update should not change anything."""
        from memory.memory_manager import update_memory, save_memory, load_memory
        save_memory(sample_memory)
        update_memory({})
        memory = load_memory()
        assert memory == sample_memory


# ---------------------------------------------------------------------------
# Test: forget
# ---------------------------------------------------------------------------

class TestForget:
    """Tests for forget()."""

    def test_forget_removes_entry(self, temp_memory, sample_memory):
        """Should remove an entry from memory."""
        from memory.memory_manager import forget, save_memory, load_memory
        save_memory(sample_memory)
        result = forget("name", "identity")
        memory = load_memory()
        assert "name" not in memory["identity"]
        assert "Forgotten" in result

    def test_forget_nonexistent_returns_not_found(self, temp_memory):
        """Should return 'Not found' for nonexistent entry."""
        from memory.memory_manager import forget
        result = forget("nonexistent_key", "notes")
        assert "Not found" in result

    def test_forget_preserves_other_categories(self, temp_memory, sample_memory):
        """Should not affect other categories."""
        from memory.memory_manager import forget, save_memory, load_memory
        save_memory(sample_memory)
        forget("name", "identity")
        memory = load_memory()
        assert memory["preferences"]["favorite_color"]["value"] == "gold"


# ---------------------------------------------------------------------------
# Test: format_memory_for_prompt
# ---------------------------------------------------------------------------

class TestFormatMemoryForPrompt:
    """Tests for format_memory_for_prompt()."""

    def test_empty_memory_returns_empty(self):
        """Empty memory should return empty string."""
        from memory.memory_manager import format_memory_for_prompt
        result = format_memory_for_prompt({})
        assert result == ""

    def test_none_returns_empty(self):
        """None input should return empty string."""
        from memory.memory_manager import format_memory_for_prompt
        result = format_memory_for_prompt(None)
        assert result == ""

    def test_includes_identity_fields(self, sample_memory):
        """Should include identity fields in the output."""
        from memory.memory_manager import format_memory_for_prompt
        result = format_memory_for_prompt(sample_memory)
        assert "chuckee" in result

    def test_includes_preferences(self, sample_memory):
        """Should include preferences in the output."""
        from memory.memory_manager import format_memory_for_prompt
        result = format_memory_for_prompt(sample_memory)
        assert "gold" in result

    def test_has_header(self, sample_memory):
        """Should start with the memory header."""
        from memory.memory_manager import format_memory_for_prompt
        result = format_memory_for_prompt(sample_memory)
        assert "WHAT YOU KNOW ABOUT THIS PERSON" in result
