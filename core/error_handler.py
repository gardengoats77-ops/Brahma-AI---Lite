"""
core/error_handler.py — Centralized error handling for REX

Replaces the 146+ silent 'except Exception: pass' blocks throughout the codebase
with structured, logged, recoverable error handling.

Usage:
    from core.error_handler import safe_execute, log_error, get_logger

    # Quick wrapper for non-critical code
    result = safe_execute(my_function, arg1, arg2, default=None, context="fetching weather")

    # Manual logging
    logger = get_logger("my_module")
    try:
        do_something()
    except Exception as e:
        log_error(e, context="my_module.do_something", severity="warning")

    # Decorator for entire functions
    @handle_errors(context="daily_briefing", default_return="")
    def get_greeting():
        ...
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FORMAT = "%(asctime)s | %(name)-22s | %(levelname)-7s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Minimum severity to show in console (DEBUG/INFO/WARNING/ERROR/CRITICAL)
CONSOLE_LEVEL = logging.WARNING

# Minimum severity to write to file
FILE_LEVEL = logging.DEBUG

# Error categories for classification
class ErrorCategory:
    NETWORK = "network"
    API = "api"
    FILE_SYSTEM = "filesystem"
    PROCESS = "process"
    INPUT = "input"
    CONFIG = "config"
    UI = "ui"
    UNKNOWN = "unknown"


def _classify_error(exc: Exception) -> str:
    """Classify an exception into a category for structured logging."""
    name = type(exc).__name__
    msg = str(exc).lower()

    if any(k in name for k in ("Connection", "Timeout", "URLError", "HTTPError", "Socket")):
        return ErrorCategory.NETWORK
    if any(k in name for k in ("API", "Auth", "Permission", "RateLimit")):
        return ErrorCategory.API
    if any(k in name for k in ("File", "IO", "OS", "Permission")) or "file" in msg:
        return ErrorCategory.FILE_SYSTEM
    if any(k in name for k in ("Process", "Subprocess", "Popen")):
        return ErrorCategory.PROCESS
    if any(k in name for k in ("Value", "Type", "Index", "Key", "Attribute", "Unicode")):
        return ErrorCategory.INPUT
    if "config" in msg or "setting" in msg:
        return ErrorCategory.CONFIG
    if any(k in name for k in ("Q", "Qt", "Widget")):
        return ErrorCategory.UI
    return ErrorCategory.UNKNOWN


# ---------------------------------------------------------------------------
# Logger factory
# ---------------------------------------------------------------------------

_initialized = False


def _setup_logging():
    """Initialize the logging system once."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    # Create log directory
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Root REX logger
    root_logger = logging.getLogger("rex")
    root_logger.setLevel(logging.DEBUG)

    # Console handler — only WARNING and above
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(CONSOLE_LEVEL)
    console.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    root_logger.addHandler(console)

    # File handler — everything DEBUG and above
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = LOG_DIR / f"rex-{today}.log"
    file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
    file_handler.setLevel(FILE_LEVEL)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    root_logger.addHandler(file_handler)

    # Rotate: keep only last 7 log files
    _rotate_logs()


def _rotate_logs():
    """Delete log files older than 7 days."""
    try:
        cutoff = datetime.now().timestamp() - (7 * 86400)
        for f in LOG_DIR.glob("rex-*.log"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except Exception:
        pass  # Rotation failure is non-critical


def get_logger(name: str) -> logging.Logger:
    """
    Get a namespaced logger under the 'rex' root.

    Args:
        name: Module name (e.g., 'daily_briefing', 'browser_control')

    Returns:
        A logging.Logger instance with the rex prefix.
    """
    _setup_logging()
    return logging.getLogger(f"rex.{name}")


# ---------------------------------------------------------------------------
# Core error functions
# ---------------------------------------------------------------------------

def log_error(
    exc: Exception,
    context: str = "",
    severity: str = "error",
    extra_data: dict | None = None,
) -> dict:
    """
    Log an exception with structured metadata.

    Args:
        exc: The caught exception
        context: Where the error occurred (e.g., 'daily_briefing.fetch_weather')
        severity: 'debug', 'info', 'warning', 'error', 'critical'
        extra_data: Additional context to include in the log

    Returns:
        A dict with error metadata for programmatic use.
    """
    _setup_logging()

    category = _classify_error(exc)
    module = context.split(".")[0] if context else "unknown"
    logger = logging.getLogger(f"rex.{module}")

    # Build structured error record
    error_record = {
        "timestamp": datetime.now().isoformat(),
        "category": category,
        "context": context,
        "exception_type": type(exc).__name__,
        "message": str(exc)[:500],
        "severity": severity,
    }
    if extra_data:
        error_record["extra"] = extra_data

    # Log with appropriate severity
    log_func = getattr(logger, severity, logger.error)
    log_msg = f"[{category.upper()}] {context}: {type(exc).__name__}: {exc}"
    log_func(log_msg, exc_info=True)

    return error_record


def safe_execute(
    func: Callable,
    *args,
    default: Any = None,
    context: str = "",
    severity: str = "warning",
    **kwargs,
) -> Any:
    """
    Execute a function safely, catching and logging any exceptions.

    Args:
        func: The function to call
        *args: Positional arguments for the function
        default: Value to return on failure
        context: Description for logging (e.g., 'fetch_weather')
        severity: Logging severity on failure
        **kwargs: Keyword arguments for the function

    Returns:
        The function's return value, or `default` on failure.
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        ctx = context or f"{func.__module__}.{func.__qualname__}"
        log_error(e, context=ctx, severity=severity)
        return default


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "get_logger",
    "log_error",
    "safe_execute",
    "handle_errors",
    "safe_import",
    "safe_json_load",
    "ErrorCategory",
]


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

F = TypeVar("F", bound=Callable[..., Any])


def handle_errors(
    func: F | None = None,
    *,
    context: str = "",
    default_return: Any = None,
    severity: str = "error",
    reraise: bool = False,
) -> Callable:
    """
    Decorator that wraps a function with error handling.

    Usage:
        @handle_errors(context="daily_briefing", default_return="")
        def get_greeting():
            ...

        @handle_errors(severity="critical", reraise=True)
        def critical_operation():
            ...
    """
    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                ctx = context or f"{fn.__module__}.{fn.__qualname__}"
                log_error(e, context=ctx, severity=severity)
                if reraise:
                    raise
                return default_return
        return wrapper  # type: ignore

    if func is not None:
        return decorator(func)
    return decorator


# ---------------------------------------------------------------------------
# Convenience helpers for common patterns in the REX codebase
# ---------------------------------------------------------------------------

def safe_import(module_name: str, pip_name: str | None = None) -> Any:
    """
    Safely import a module, returning None on failure.

    Args:
        module_name: The import path (e.g., 'google.generativeai')
        pip_name: Pip package name if different from module

    Returns:
        The imported module, or None if import fails.
    """
    try:
        import importlib
        return importlib.import_module(module_name)
    except ImportError:
        logger = get_logger("imports")
        pkg = pip_name or module_name.split(".")[0]
        logger.warning(f"Optional dependency '{pkg}' not installed. "
                       f"Install with: pip install {pkg}")
        return None


def safe_json_load(path: str | Path, default: Any = None) -> Any:
    """
    Safely load a JSON file, returning default on failure.

    Args:
        path: Path to the JSON file
        default: Value to return on any failure

    Returns:
        Parsed JSON data, or default.
    """
    import json
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log_error(e, context=f"safe_json_load({path})", severity="warning")
        return default

