# tests/test_dispatch_memory.py
"""Tests for pi/dispatch_memory.py — JSON-backed ring buffer of dispatches."""
import json
from pathlib import Path

import pytest


@pytest.fixture
def tmp_history(tmp_path):
    """Create a temporary dispatch_history.json and patch DISPATCH_HISTORY_FILE."""
    from pi import dispatch_memory
    original = dispatch_memory.DISPATCH_HISTORY_FILE
    hist_file = tmp_path / "dispatch_history.json"
    dispatch_memory.DISPATCH_HISTORY_FILE = hist_file
    yield hist_file
    dispatch_memory.DISPATCH_HISTORY_FILE = original


def test_recall_last_task(tmp_history):
    """Recall the most recent dispatch from history."""
    from pi.dispatch_memory import record_dispatch, recall_dispatch

    # Record a dispatch
    record_dispatch(
        task_id="task-001",
        assigned_agent="research",
        prompt="Find latest AI papers on RAG",
        result="Found 12 papers, top 3: RAGAS, ARES, TruLens",
    )

    # Recall "last" should return the most recent dispatch
    result = recall_dispatch("last")
    assert result is not None
    assert result["task_id"] == "task-001"
    assert result["assigned_agent"] == "research"
    assert result["prompt"] == "Find latest AI papers on RAG"
    assert "RAGAS" in result["result"]
    assert "timestamp" in result


def test_recall_by_task_id(tmp_history):
    """Recall a specific dispatch by task_id."""
    from pi.dispatch_memory import record_dispatch, recall_dispatch

    record_dispatch(
        task_id="task-abc",
        assigned_agent="coding",
        prompt="Fix the login bug",
        result="Fixed in auth.py line 42",
    )

    result = recall_dispatch("task-abc")
    assert result is not None
    assert result["task_id"] == "task-abc"
    assert result["assigned_agent"] == "coding"


def test_recall_last_by_agent(tmp_history):
    """Recall the last dispatch assigned to a specific agent."""
    from pi.dispatch_memory import record_dispatch, recall_dispatch

    record_dispatch(
        task_id="t1",
        assigned_agent="research",
        prompt="Research LLMs",
        result="LLMs are transformer-based",
    )
    record_dispatch(
        task_id="t2",
        assigned_agent="coding",
        prompt="Write tests",
        result="Tests written",
    )
    record_dispatch(
        task_id="t3",
        assigned_agent="research",
        prompt="Research RAG",
        result="RAG combines retrieval + generation",
    )

    # "last from research" should return the most recent research task (t3)
    result = recall_dispatch("last from research")
    assert result is not None
    assert result["task_id"] == "t3"
    assert result["assigned_agent"] == "research"

    # "last from coding" should return t2
    result = recall_dispatch("last from coding")
    assert result is not None
    assert result["task_id"] == "t2"


def test_recall_returns_none_when_empty(tmp_history):
    """Recall on empty history returns None."""
    from pi.dispatch_memory import recall_dispatch

    result = recall_dispatch("last")
    assert result is None


def test_recall_returns_none_for_unknown_task(tmp_history):
    """Recall for non-existent task_id returns None."""
    from pi.dispatch_memory import record_dispatch, recall_dispatch

    record_dispatch(
        task_id="known",
        assigned_agent="research",
        prompt="test",
        result="done",
    )

    result = recall_dispatch("unknown-task")
    assert result is None


def test_ring_buffer_capped_at_50(tmp_history):
    """History should be capped at 50 entries (ring buffer)."""
    from pi.dispatch_memory import record_dispatch, recall_dispatch, MAX_HISTORY

    # Record 60 dispatches (over the 50 limit)
    for i in range(60):
        record_dispatch(
            task_id=f"task-{i:03d}",
            assigned_agent="research",
            prompt=f"prompt {i}",
            result=f"result {i}",
        )

    # Recall "last" should be task-059
    result = recall_dispatch("last")
    assert result["task_id"] == "task-059"

    # task-000 through task-009 should have been evicted
    result = recall_dispatch("task-000")
    assert result is None

    # task-010 should be the oldest surviving entry
    result = recall_dispatch("task-010")
    assert result is not None


def test_recall_last_with_keyword_research(tmp_history):
    """'last research' returns the most recent research-agent dispatch."""
    from pi.dispatch_memory import record_dispatch, recall_dispatch

    record_dispatch(
        task_id="t1",
        assigned_agent="coding",
        prompt="Write code",
        result="Code done",
    )
    record_dispatch(
        task_id="t2",
        assigned_agent="research",
        prompt="Research topic",
        result="Research done",
    )
    record_dispatch(
        task_id="t3",
        assigned_agent="coding",
        prompt="More code",
        result="More code done",
    )

    # "last research" should return t2
    result = recall_dispatch("last research")
    assert result is not None
    assert result["task_id"] == "t2"
    assert result["assigned_agent"] == "research"