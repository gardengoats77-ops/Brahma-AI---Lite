# tests/test_error_recovery.py
"""Tests for Pi error recovery: circuit breaker and retry with exponential backoff.

Verifies:
  - Circuit breaker opens after N consecutive failures
  - Circuit breaker transitions to HALF_OPEN after cooldown
  - Half-open success closes the circuit
  - Half-open failure re-opens the circuit
  - Retry with exponential backoff gives up after max attempts
  - Voice announcement on failure via TTS
"""
import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from pi.error_recovery import CircuitBreaker, retry_with_backoff, with_error_recovery


class TestCircuitBreakerStates:
    """Circuit breaker state machine transitions."""

    def test_initial_state_is_closed(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown=1.0)
        assert cb.state == "CLOSED"

    def test_opens_after_n_failures(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown=1.0)
        cb.record_failure()
        assert cb.state == "CLOSED"
        cb.record_failure()
        assert cb.state == "CLOSED"
        cb.record_failure()
        assert cb.state == "OPEN"

    def test_does_not_open_before_threshold(self):
        cb = CircuitBreaker(failure_threshold=5, cooldown=1.0)
        for _ in range(4):
            cb.record_failure()
        assert cb.state == "CLOSED"

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown=1.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        # Only 2 consecutive failures (success reset the counter)
        assert cb.state == "CLOSED"


class TestCircuitBreakerOpenState:
    """Behavior when circuit is OPEN."""

    def test_blocks_calls_when_open(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown=10.0)
        cb.record_failure()
        assert cb.state == "OPEN"
        assert cb.allow_request() is False

    def test_transitions_to_half_open_after_cooldown(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown=0.1)
        cb.record_failure()
        assert cb.state == "OPEN"
        time.sleep(0.15)
        assert cb.allow_request() is True
        assert cb.state == "HALF_OPEN"

    def test_half_open_success_closes_circuit(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown=0.1)
        cb.record_failure()
        time.sleep(0.15)
        assert cb.state == "HALF_OPEN"
        cb.record_success()
        assert cb.state == "CLOSED"

    def test_half_open_failure_reopens_circuit(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown=0.1)
        cb.record_failure()
        time.sleep(0.15)
        assert cb.state == "HALF_OPEN"
        cb.record_failure()
        assert cb.state == "OPEN"


class TestCircuitBreakerCallable:
    """CircuitBreaker as a callable decorator/wrapper."""

    def test_calls_function_when_closed(self):
        cb = CircuitBreaker(failure_threshold=3, cooldown=1.0)
        result = cb(lambda: "success")
        assert result == "success"

    def test_raises_when_open(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown=10.0)
        cb.record_failure()
        with pytest.raises(Exception):
            cb(lambda: "should not run")

    def test_records_failure_on_exception(self):
        cb = CircuitBreaker(failure_threshold=2, cooldown=1.0)
        with pytest.raises(ValueError):
            cb(lambda: (_ for _ in ()).throw(ValueError("fail")))
        assert cb._failures == 1

    def test_threshold_failure_raises_circuit_open(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown=10.0)
        with pytest.raises(Exception):
            cb(lambda: (_ for _ in ()).throw(ValueError("fail")))
        # Next call should raise CircuitOpenError
        with pytest.raises(Exception):
            cb(lambda: "should not run")


class TestRetryWithBackoff:
    """retry_with_backoff decorator."""

    def test_succeeds_first_try(self):
        call_count = 0

        @retry_with_backoff(max_attempts=3, base_delay=0.01)
        def flaky():
            nonlocal call_count
            call_count += 1
            return "ok"

        assert flaky() == "ok"
        assert call_count == 1

    def test_retries_on_failure(self):
        call_count = 0

        @retry_with_backoff(max_attempts=3, base_delay=0.01)
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("network down")
            return "ok"

        assert flaky() == "ok"
        assert call_count == 3

    def test_gives_up_after_max_attempts(self):
        call_count = 0

        @retry_with_backoff(max_attempts=3, base_delay=0.01)
        def always_fails():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("network down")

        with pytest.raises(ConnectionError):
            always_fails()
        assert call_count == 3

    def test_exponential_delay(self):
        delays = []

        def mock_sleep(t):
            delays.append(t)

        call_count = 0

        @retry_with_backoff(max_attempts=4, base_delay=0.1, exponential_base=2.0)
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                raise ConnectionError("network down")
            return "ok"

        # Patch random to return 0.5 so delay = base * 2^n (no jitter variance)
        with patch("time.sleep", side_effect=mock_sleep), patch("pi.error_recovery.random.random", return_value=0.5):
            flaky()

        # Should have 3 delays: 0.1, 0.2, 0.4
        assert len(delays) == 3
        assert delays[0] == pytest.approx(0.1, abs=0.01)
        assert delays[1] == pytest.approx(0.2, abs=0.01)
        assert delays[2] == pytest.approx(0.4, abs=0.01)


class TestWithErrorRecovery:
    """with_error_recovery decorator for wrapping external calls."""

    def test_success_returns_value(self):
        @with_error_recovery(fallback="offline", announce=None)
        def good_call():
            return "online"

        assert good_call() == "online"

    def test_failure_returns_fallback(self):
        @with_error_recovery(fallback="offline", announce=None)
        def bad_call():
            raise ConnectionError("network down")

        assert bad_call() == "offline"

    def test_failure_announces_via_tts(self):
        mock_tts = MagicMock()
        call_count = 0

        @with_error_recovery(fallback="offline", announce=mock_tts)
        def bad_call():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("network down")

        result = bad_call()
        assert result == "offline"
        mock_tts.speak.assert_called_once()
        spoken = mock_tts.speak.call_args[0][0]
        assert "offline" in spoken.lower() or "error" in spoken.lower() or "network" in spoken.lower()

    def test_no_tts_does_not_crash(self):
        @with_error_recovery(fallback="offline", announce=None)
        def bad_call():
            raise ConnectionError("network down")

        # announce=None should not crash
        assert bad_call() == "offline"


class TestCircuitBreakerOpensAfterNFailures:
    """Primary acceptance test: circuit breaker opens after N failures."""

    def test_circuit_breaker_opens_after_n_failures_instance(self):
        """After N consecutive failures, circuit transitions to OPEN state."""
        cb = CircuitBreaker(failure_threshold=3, cooldown=5.0)
        assert cb.state == "CLOSED"

        # First 2 failures: still CLOSED
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "CLOSED"

        # 3rd failure: transitions to OPEN
        cb.record_failure()
        assert cb.state == "OPEN"

        # Circuit is now OPEN — requests blocked
        assert cb.allow_request() is False


def test_circuit_breaker_opens_after_n_failures():
    """After N consecutive failures, circuit transitions to OPEN state.

    Primary acceptance test for Phase 11.1: Comprehensive Error Recovery.
    """
    cb = CircuitBreaker(failure_threshold=3, cooldown=5.0)
    assert cb.state == "CLOSED"

    # First 2 failures: still CLOSED
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "CLOSED"

    # 3rd failure: transitions to OPEN
    cb.record_failure()
    assert cb.state == "OPEN"

    # Circuit is now OPEN — requests blocked
    assert cb.allow_request() is False