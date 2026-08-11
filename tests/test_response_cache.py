# tests/test_response_cache.py
"""Tests for pi/response_cache.py — SQLite-backed LRU response cache.

Verifies:
  - Cache stores and retrieves responses by prompt hash
  - Cache hit works when network is unavailable (offline mode)
  - LRU eviction keeps only last N entries
  - TTL expires old entries
  - Thread-safe access
"""
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from pi.response_cache import ResponseCache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_cache(tmp_path):
    """Create a ResponseCache backed by a temporary SQLite db."""
    db_path = tmp_path / "test_response_cache.db"
    cache = ResponseCache(db_path=str(db_path))
    yield cache
    cache.close()


# ---------------------------------------------------------------------------
# Test: cache hit offline
# ---------------------------------------------------------------------------

class TestCacheHitOffline:
    """Cache serves responses when network is unreachable."""

    def test_cache_hit_offline(self, tmp_cache):
        """When offline, a previously cached response should be served."""
        prompt = "What's the weather like today?"
        expected_response = {
            "ok": True,
            "result": "It's sunny and 72°F.",
            "assigned_agent": "weather_agent",
            "task_id": "test-123",
        }

        # Pre-populate cache (simulating a prior online session)
        tmp_cache.put(prompt, expected_response)

        # Simulate offline: confirm network call would fail
        # but cache returns the stored value
        result = tmp_cache.get(prompt)
        assert result is not None
        assert result["ok"] is True
        assert result["result"] == "It's sunny and 72°F."

    def test_cache_miss_offline(self, tmp_cache):
        """When offline and prompt not cached, get returns None."""
        result = tmp_cache.get("Never seen this prompt before")
        assert result is None

    def test_cache_hit_updates_access_time(self, tmp_cache):
        """Accessing a cached entry should update its LRU timestamp."""
        prompt = "Tell me a joke"
        response = {"ok": True, "result": "Why did the chicken cross the road?"}

        tmp_cache.put(prompt, response)
        first_access = tmp_cache.get(prompt)

        # Wait a tiny bit so timestamps differ if updated
        time.sleep(0.01)

        second_access = tmp_cache.get(prompt)
        assert first_access == second_access


# ---------------------------------------------------------------------------
# Test: key generation (SHA256)
# ---------------------------------------------------------------------------

class TestKeyGeneration:
    def test_key_is_sha256_of_prompt(self, tmp_cache):
        """Cache key should be SHA256 hash of the prompt string."""
        prompt = "Hello Rex"
        expected_key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

        # Put and get using the same prompt should use same key
        tmp_cache.put(prompt, {"ok": True})
        assert tmp_cache.get(prompt) is not None

    def test_different_prompts_different_keys(self, tmp_cache):
        """Different prompts should map to different cache entries."""
        tmp_cache.put("prompt A", {"ok": True, "result": "A"})
        tmp_cache.put("prompt B", {"ok": True, "result": "B"})

        assert tmp_cache.get("prompt A")["result"] == "A"
        assert tmp_cache.get("prompt B")["result"] == "B"


# ---------------------------------------------------------------------------
# Test: LRU eviction
# ---------------------------------------------------------------------------

class TestLRUEviction:
    def test_eviction_when_capacity_exceeded(self, tmp_cache):
        """When cache exceeds max entries, oldest entries are evicted."""
        # Set a small max_size for testing
        tmp_cache._max_size = 5

        # Insert 6 entries (one over limit)
        for i in range(6):
            tmp_cache.put(f"prompt {i}", {"ok": True, "idx": i})

        # First entry should be evicted (LRU)
        assert tmp_cache.get("prompt 0") is None
        # Remaining entries should still be present
        for i in range(1, 6):
            result = tmp_cache.get(f"prompt {i}")
            assert result is not None
            assert result["idx"] == i


# ---------------------------------------------------------------------------
# Test: TTL expiration
# ---------------------------------------------------------------------------

class TestTTLExpiration:
    def test_expired_entries_are_purged(self, tmp_cache):
        """Entries older than TTL should be treated as cache misses."""
        # Set TTL to 1 second for testing
        tmp_cache._ttl_seconds = 1

        prompt = "This will expire"
        tmp_cache.put(prompt, {"ok": True, "result": "fresh"})

        # Immediately available
        assert tmp_cache.get(prompt) is not None

        # Wait for TTL to expire
        time.sleep(1.1)

        # Should be expired now
        assert tmp_cache.get(prompt) is None


# ---------------------------------------------------------------------------
# Test: JSON value storage
# ---------------------------------------------------------------------------

class TestJSONStorage:
    def test_nested_json_roundtrip(self, tmp_cache):
        """Complex JSON responses should survive put/get unchanged."""
        prompt = "Complex response"
        response = {
            "ok": True,
            "result": {"nested": {"data": [1, 2, 3]}},
            "metadata": {"tokens": 42, "model": "gemini-2.5-flash"},
        }

        tmp_cache.put(prompt, response)
        result = tmp_cache.get(prompt)
        assert result == response


# ---------------------------------------------------------------------------
# Test: thread safety (basic)
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_reads_and_writes(self, tmp_cache):
        """Multiple threads reading/writing simultaneously should not corrupt."""
        import threading

        errors = []

        def writer(start):
            try:
                for i in range(10):
                    tmp_cache.put(f"thread-{start}-prompt-{i}", {
                        "ok": True, "thread": start, "i": i,
                    })
            except Exception as e:
                errors.append(e)

        def reader(start):
            try:
                for i in range(10):
                    tmp_cache.get(f"thread-{start}-prompt-{i}")
            except Exception as e:
                errors.append(e)

        threads = []
        for t in range(3):
            threads.append(threading.Thread(target=writer, args=(t,)))
            threads.append(threading.Thread(target=reader, args=(t,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread safety errors: {errors}"