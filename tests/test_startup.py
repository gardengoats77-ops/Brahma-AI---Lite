"""Regression tests for startup bootstrap behavior."""

from __future__ import annotations

from unittest.mock import Mock, patch

import main


def test_configure_stdio_logs_reconfigure_failures_without_raising() -> None:
    """A stream reconfigure failure must not mask startup with NameError."""
    stdout = Mock()
    stderr = Mock()
    stdout.reconfigure.side_effect = OSError("unsupported stream")
    stderr.reconfigure.side_effect = OSError("unsupported stream")

    with patch.object(main, "log_error") as log_error:
        main._configure_stdio(stdout, stderr)

    assert stdout.reconfigure.call_count == 1
    assert stderr.reconfigure.call_count == 1
    assert log_error.call_count == 2


def test_configure_stdio_survives_logger_failure() -> None:
    """A broken logger must not prevent the application from starting."""
    stdout = Mock()
    stdout.reconfigure.side_effect = OSError("unsupported stream")

    with patch.object(main, "log_error", side_effect=RuntimeError("logger unavailable")):
        main._configure_stdio(stdout, Mock())
