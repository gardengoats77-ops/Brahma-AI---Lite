# pi/error_recovery.py
"""Error recovery for Pi: circuit breaker, retry with exponential backoff.

Provides graceful degradation for every network/external call in pi_main.py:
  - CircuitBreaker: CLOSED -> OPEN -> HALF_OPEN state machine
  - retry_with_backoff: decorator with exponential backoff
  - with_error_recovery: decorator that catches exceptions, returns fallback,
    and announces failures via TTS

Voice announcement on failure: "Network error, switching to offline mode"
"""
from __future__ import annotations

import functools
import logging
import random
import time
from typing import Any, Callable, Optional, TypeVar

log = logging.getLogger("brahma.error_recovery")

F = TypeVar("F", bound=Callable[..., Any])


class CircuitOpenError(Exception):
    """Raised when a call is attempted through an OPEN circuit breaker."""


class CircuitBreaker:
    """Circuit breaker pattern for network/external calls.

    States:
      CLOSED   — normal operation, requests flow through
      OPEN     — too many failures, requests blocked
      HALF_OPEN — cooldown elapsed, testing with next request

    After N consecutive failures, circuit opens.
    After cooldown period, circuit transitions to HALF_OPEN.
    If half-open request succeeds, circuit closes.
    If half-open request fails, circuit re-opens.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown: float = 30.0,
    ):
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self._failures = 0
        self._state = "CLOSED"
        self._opened_at: float = 0.0

    @property
    def state(self) -> str:
        """Current state, auto-transitioning from OPEN to HALF_OPEN."""
        if self._state == "OPEN":
            if time.monotonic() - self._opened_at >= self.cooldown:
                self._state = "HALF_OPEN"
        return self._state

    def allow_request(self) -> bool:
        """Check if a request should be allowed through."""
        return self.state != "OPEN"

    def record_failure(self) -> None:
        """Record a call failure. Opens circuit if threshold reached."""
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._state = "OPEN"
            self._opened_at = time.monotonic()
            log.warning(
                "Circuit OPEN after %d failures (cooldown=%.1fs)",
                self._failures, self.cooldown,
            )

    def record_success(self) -> None:
        """Record a call success. Resets failure count and closes circuit."""
        self._failures = 0
        self._state = "CLOSED"

    def __call__(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Call func through the circuit breaker.

        Raises CircuitOpenError if the circuit is OPEN.
        Records success/failure automatically.
        """
        if not self.allow_request():
            raise CircuitOpenError(
                f"Circuit OPEN — blocking call to {func.__name__}"
            )
        try:
            result = func(*args, **kwargs)
            self.record_success()
            return result
        except Exception:
            self.record_failure()
            raise


def retry_with_backoff(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    exponential_base: float = 2.0,
    max_delay: float = 30.0,
    jitter: bool = True,
) -> Callable[[F], F]:
    """Decorator: retry a failing call with exponential backoff.

    Args:
        max_attempts: maximum number of attempts
        base_delay: initial delay in seconds
        exponential_base: multiplier for each retry
        max_delay: cap on delay between retries
        jitter: add randomness to avoid thundering herd
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Optional[Exception] = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        delay = min(
                            base_delay * (exponential_base ** attempt),
                            max_delay,
                        )
                        if jitter:
                            delay = delay * (0.5 + random.random())
                        log.warning(
                            "Attempt %d/%d for %s failed: %s — retrying in %.2fs",
                            attempt + 1, max_attempts, func.__name__, e, delay,
                        )
                        time.sleep(delay)
                    else:
                        log.error(
                            "All %d attempts for %s failed: %s",
                            max_attempts, func.__name__, e,
                        )
            raise last_exception  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]
    return decorator


def with_error_recovery(
    fallback: Any = None,
    announce: Any = None,
    message: str = "Network error, switching to offline mode",
) -> Callable[[F], F]:
    """Decorator: catch exceptions, return fallback, announce via TTS.

    Args:
        fallback: value to return on failure
        announce: TTS object (must have .speak()) or None
        message: voice announcement text on failure
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                log.warning(
                    "Error in %s: %s — returning fallback=%s",
                    func.__name__, e, fallback,
                )
                if announce is not None and hasattr(announce, "speak"):
                    announce.speak(message)
                return fallback
        return wrapper  # type: ignore[return-value]
    return decorator


def async_retry_with_backoff(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    exponential_base: float = 2.0,
    max_delay: float = 30.0,
    jitter: bool = True,
) -> Callable[[F], F]:
    """Decorator: async retry with exponential backoff."""
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            import asyncio
            last_exception: Optional[Exception] = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        delay = min(
                            base_delay * (exponential_base ** attempt),
                            max_delay,
                        )
                        if jitter:
                            delay = delay * (0.5 + random.random())
                        log.warning(
                            "Async attempt %d/%d for %s failed: %s — retrying in %.2fs",
                            attempt + 1, max_attempts, func.__name__, e, delay,
                        )
                        await asyncio.sleep(delay)
                    else:
                        log.error(
                            "All %d async attempts for %s failed: %s",
                            max_attempts, func.__name__, e,
                        )
            raise last_exception  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]
    return decorator


def async_with_error_recovery(
    fallback: Any = None,
    announce: Any = None,
    message: str = "Network error, switching to offline mode",
) -> Callable[[F], F]:
    """Decorator: async catch exceptions, return fallback, announce via TTS."""
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                log.warning(
                    "Async error in %s: %s — returning fallback=%s",
                    func.__name__, e, fallback,
                )
                if announce is not None and hasattr(announce, "speak"):
                    announce.speak(message)
                return fallback
        return wrapper  # type: ignore[return-value]
    return decorator