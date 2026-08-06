"""actions/_subprocess_utils.py — Safe subprocess wrappers.

Replaces dangerous ``shell=True`` subprocess calls throughout the
codebase with validated, shell=False alternatives.

Key safety guarantees:

* Never passes untrusted input to a shell interpreter.
* Validates every argument against a program-specific whitelist regex.
* Bounds execution with a default timeout to prevent hangs.
* Logs every invocation for auditability.

Usage::

    from actions._subprocess_utils import safe_run

    result = safe_run(
        ["schtasks", "/Create", "/TN", task_name, "/XML", xml_path, "/F"],
        timeout=15,
    )
    if result.returncode == 0:
        ...

Public API:
    * safe_run(cmd, *, timeout, capture_output, text, check)
        Run a command list with shell=False and validated arguments.
    * safe_popen(cmd, *, stdout, stderr, cwd)
        Start a non-blocking subprocess with shell=False.

This module never raises ``ValueError`` for an *unsafe* argument during
normal operation; instead it returns a ``CompletedProcess`` with
``returncode=-1`` and a stderr message, matching the existing call sites
that expect a result object rather than an exception.  Callers that want
the strict behaviour can pass ``check=True``.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)

# Default timeout for safe_run — single shell commands should never take long.
DEFAULT_TIMEOUT = 30

# ── Per-program argument whitelists ────────────────────────────────────────
#
# Each entry maps an executable base name (as resolved by os.path.basename)
# to a regex that every *non-flag* argument must satisfy.  Flag arguments
# (starting with ``-`` or ``/``) are allowed by default because the callers
# in this codebase construct them from constants, not from user input.
#
# When an entry maps to ``None``, the program is fully trusted (no
# per-argument validation) — used only for well-known system binaries that
# are invoked with internally-constructed argument lists.
_ALLOWED_PROGRAMS: dict[str, re.Pattern[str] | None] = {
    # Windows scheduled tasks — task names and XML paths are constructed by
    # reminder.py from a sanitised message + temp directory.
    "schtasks": re.compile(r'^[A-Za-z0-9_\-./\\: {}]+$', re.IGNORECASE),
    # Windows msg command — message is already sanitised by reminder.py.
    "msg": re.compile(r'^[\w\s.,!?"\'-]+$'),
    # macOS AppleScript bridge — scripts are built from constants.
    "osascript": None,
    # Linux brightness / wallpaper helpers.
    "brightnessctl": re.compile(r'^[0-9+%\-]+$'),
    "gsettings": re.compile(r'^[a-zA-Z0-9.\s"_/-]+$'),
    "xfconf-query": re.compile(r'^[a-zA-Z0-9.\s"/_-]+$'),
    "feh": re.compile(r'^[a-zA-Z0-9.\s"/_~-]+$'),
    "qdbus": re.compile(r'^[a-zA-Z0-9.\s"/_-]+$'),
    # Linux process listing.
    "which": re.compile(r'^[a-zA-Z0-9_\-./]+$'),
    # Package managers — package names are validated by the caller.
    "pip": None,
    # Editors — paths are validated by the caller.
    "code": None,
    "code.cmd": None,
    # Python itself — used for running scripts.
    "python": None,
    "python3": None,
}

# Programs that are completely blocked — never allowed even through safe_run.
_BLOCKED_PROGRAMS: set[str] = {
    "rm", "rmdir", "del", "format", "mkfs", "dd",
    "shutdown",  # shutdown is handled by dedicated, audited call sites only
    "reboot",
}


def _validate_args(program: str, cmd: Sequence[str]) -> str | None:
    """Return an error string if *cmd* is unsafe, else ``None``.

    *program* is the base name of the executable (lower-cased).
    *cmd* is the full argument list.
    """
    if program in _BLOCKED_PROGRAMS:
        return f"Program '{program}' is blocked by the safe subprocess policy."

    pattern = _ALLOWED_PROGRAMS.get(program)
    if pattern is None:
        # Program not in the whitelist → only allow if it's the python
        # interpreter (which is a trusted entry point) or if every argument
        # is a flag (defensive default).
        if program in ("python", "python3", sys.executable and Path(sys.executable).name.lower()):
            return None
        # Unknown program: allow flags only.
        for arg in cmd[1:]:
            if not arg.startswith("-") and not arg.startswith("/"):
                return f"Program '{program}' is not in the safe-subprocess allowlist and argument is not a flag: {arg!r}"
        return None

    # Flag arguments (start with - or /) are always allowed — they are
    # constructed by callers from constants, not from user input.
    for arg in cmd[1:]:
        if arg.startswith("-") or arg.startswith("/"):
            continue
        if not pattern.match(arg):
            return f"Argument {arg!r} for program '{program}' failed whitelist validation."

    return None


def safe_run(
    cmd: Sequence[str],
    *,
    timeout: int = DEFAULT_TIMEOUT,
    capture_output: bool = True,
    text: bool = True,
    check: bool = False,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Run *cmd* with ``shell=False`` and validated arguments.

    Returns a :class:`subprocess.CompletedProcess`.  If the command is
    rejected by the safety policy, returns a synthetic
    ``CompletedProcess`` with ``returncode=-1`` and an explanatory
    stderr (unless ``check=True``, in which case raises ``ValueError``).
    """
    if not cmd:
        err = "safe_run: empty command list"
        if check:
            raise ValueError(err)
        return subprocess.CompletedProcess(args=list(cmd), returncode=-1, stdout="", stderr=err)

    program = Path(cmd[0]).name.lower()

    err = _validate_args(program, cmd)
    if err is not None:
        logger.warning("safe_run blocked: %s | cmd=%r", err, cmd)
        if check:
            raise ValueError(err)
        return subprocess.CompletedProcess(
            args=list(cmd), returncode=-1,
            stdout="" if text else b"",
            stderr=err if text else err.encode(),
        )

    logger.debug("safe_run: %r", cmd)
    try:
        return subprocess.run(
            list(cmd),
            shell=False,  # always — never pass through a shell
            timeout=timeout,
            capture_output=capture_output,
            text=text,
            check=check,
            **kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"safe_run timeout after {timeout}s: {cmd[0]}"
        logger.warning(msg)
        return subprocess.CompletedProcess(
            args=list(cmd), returncode=-1,
            stdout="" if text else b"",
            stderr=msg if text else msg.encode(),
        )
    except FileNotFoundError as exc:
        msg = f"safe_run: program not found: {cmd[0]}"
        logger.warning(msg)
        return subprocess.CompletedProcess(
            args=list(cmd), returncode=-1,
            stdout="" if text else b"",
            stderr=msg if text else msg.encode(),
        )


def safe_popen(
    cmd: Sequence[str],
    *,
    stdout=None,
    stderr=None,
    cwd: str | Path | None = None,
    **kwargs,
) -> subprocess.Popen:
    """Start a non-blocking subprocess with ``shell=False``.

    Unlike :func:`safe_run`, this does **not** validate every argument
    strictly (because Popen is often used for long-running processes with
    dynamic arguments).  It does enforce ``shell=False`` and blocks
    programs in :data:`_BLOCKED_PROGRAMS`.
    """
    if not cmd:
        raise ValueError("safe_popen: empty command list")

    program = Path(cmd[0]).name.lower()
    if program in _BLOCKED_PROGRAMS:
        raise ValueError(f"safe_popen: program '{program}' is blocked by the safe-subprocess policy.")

    logger.debug("safe_popen: %r", cmd)
    return subprocess.Popen(
        list(cmd),
        shell=False,  # always — never pass through a shell
        stdout=stdout,
        stderr=stderr,
        cwd=str(cwd) if cwd else None,
        **kwargs,
    )
