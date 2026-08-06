#!/usr/bin/env python3
"""
start-rex.py -- Unified REX launcher

Boots the dashboard, root backend, and optional agent worker from a single
command with port conflict resolution and a --dev / --prod mode flag.

Usage:
    python start-rex.py                  # dev:  dashboard :8080  +  backend :8000
    python start-rex.py --prod           # prod: dashboard :8000  (self-contained)
    python start-rex.py --with-agent     # also spawn the LiveKit agent worker
    python start-rex.py --port 9000      # custom dashboard port
    python start-rex.py --backend-port 9001  # custom backend port
    python start-rex.py --dev --port 8080 --backend-port 8000 --with-agent

Modes
-----
dev (default)
    Dashboard on a high port (default 8080) with the BLACKOPS HUD, dispatch
    PWA, file sharing, and a one-time-key login.  The root backend runs
    alongside on port 8000 for LiveKit token issuance and raw context
    endpoints.  Two processes -- full compatibility.

prod
    Dashboard only on port 8000 (self-contained -- all 9 context/token/device
    endpoints are already built into the dashboard since Phase 2).  Faster
    startup, single process.  The root backend is NOT started.

--with-agent
    Spawns ``python agent.py start`` as a child process.  Requires provider
    credentials (LiveKit Cloud, Deepgram, OpenAI, ElevenLabs) in the .env
    file.  If credentials are missing the worker starts but logs a warning.

Port conflict resolution
------------------------
Before binding, the launcher checks whether the target port is already in
use.  If it is, the port is auto-incremented (+1) up to 10 times.
Successful and adjusted ports are printed in cyan/amber so they stand out
in the terminal.
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# ── terminal colours (no dependencies) ────────────────────────────────────────
_CYAN = "\033[96m"
_GREEN = "\033[92m"
_AMBER = "\033[93m"
_RED = "\033[91m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _colour(col: str, text: str) -> str:
    return f"{col}{text}{_RESET}"


# ── port helpers ──────────────────────────────────────────────────────────────


def _port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    """Return True when nothing is listening on *host:port*.

    Does NOT set SO_REUSEADDR -- on Windows that flag allows a second bind
    to succeed even when another socket already owns the port, which defeats
    the point of the availability check.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _resolve_port(desired: int, host: str = "127.0.0.1", label: str = "port") -> int:
    """Return *desired*, or the next free port if *desired* is busy (up to +10).

    Note: there is an inherent TOCTOU race between this check and the actual
    server bind.  For a dev launcher this is acceptable; a production deploy
    should have the server itself retry on ``OSError: address in use``.
    """
    if _port_is_free(desired, host):
        return desired
    for offset in range(1, 11):
        candidate = desired + offset
        if _port_is_free(candidate, host):
            print(
                f"  {_colour(_AMBER, '[!]')} {label} {desired} is busy "
                f"-> using {_colour(_CYAN, str(candidate))}"
            )
            return candidate
    print(f"  {_colour(_RED, '[X]')} No free {label} in range {desired}-{desired + 10}")
    sys.exit(1)


# ── process management ────────────────────────────────────────────────────────

_children: list[subprocess.Popen] = []
_shutting_down = False
# Log file for child process stderr -- written to the launcher's own directory
# so startup errors from dashboard/backend/agent are never silently lost.
_CHILD_LOG = BASE_DIR / "logs" / "rex-launcher.log"


def _spawn(
    args: list[str],
    label: str,
    env: dict[str, str] | None = None,
) -> subprocess.Popen:
    """Spawn a detached child process and track it for clean shutdown."""
    merged_env = os.environ.copy()
    if env:
        for key, value in env.items():
            existing = merged_env.get(key, "")
            # Extend PATH-like variables (PYTHONPATH) instead of replacing
            if key in ("PYTHONPATH", "PATH", "Path") and existing:
                merged_env[key] = f"{value}{os.pathsep}{existing}"
            else:
                merged_env[key] = value
    _CHILD_LOG.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(str(_CHILD_LOG), "a", encoding="utf-8")  # noqa: SIM115 -- kept open for child lifespan
    popen_kwargs: dict = {
        "args": args,
        "env": merged_env,
        "stdout": log_fh,
        "stderr": subprocess.STDOUT,
    }
    # CREATE_NEW_PROCESS_GROUP on Windows lets us send Ctrl-Break
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    proc = subprocess.Popen(**popen_kwargs)  # type: ignore[arg-type]
    _children.append(proc)
    print(f"  {_colour(_GREEN, '[+]')} {label}  pid={proc.pid}")
    return proc


def _shutdown(signum: int | None = None, frame: object = None) -> None:
    """Graceful shutdown: terminate children, wait, then exit."""
    global _shutting_down  # noqa: PLW0603
    if _shutting_down:
        return
    _shutting_down = True
    print(f"\n{_colour(_AMBER, '[!]')} Shutting down REX...")
    for proc in _children:
        try:
            if sys.platform == "win32":
                proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                proc.terminate()
        except Exception:
            pass
    deadline = time.time() + 5
    for proc in _children:
        timeout = max(0.1, deadline - time.time())
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
    print(f"  {_colour(_GREEN, '[OK]')} All REX processes stopped.")
    sys.exit(0)


signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)
if sys.platform == "win32":
    try:
        signal.signal(signal.SIGBREAK, _shutdown)  # type: ignore[attr-defined]
    except AttributeError:
        pass  # SIGBREAK not available on all Windows Python builds


# ── main ──────────────────────────────────────────────────────────────────────


def _check_venv() -> str:
    """Return the Python executable inside the project's .venv."""
    candidates = [
        BASE_DIR / ".venv" / "Scripts" / "python.exe",         # Windows
        BASE_DIR / ".venv" / "bin" / "python3",                # Linux/macOS
        BASE_DIR / ".venv" / "bin" / "python",                 # macOS fallback
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    # Fall back to the current interpreter -- works if deps are already
    # installed in the active environment.
    return sys.executable


def _ensure_dotenv() -> None:
    """Copy .env.example -> .env if no .env exists yet."""
    env_path = BASE_DIR / ".env"
    example_path = BASE_DIR / ".env.example"
    if env_path.exists():
        return
    if example_path.exists():
        env_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  {_colour(_AMBER, '[!]')} Created .env from .env.example -- edit with your provider keys")
    else:
        env_path.write_text(
            "# REX provider credentials\n"
            "LIVEKIT_URL=\n"
            "LIVEKIT_API_KEY=\n"
            "LIVEKIT_API_SECRET=\n"
            "OPENAI_API_KEY=\n"
            "DEEPGRAM_API_KEY=\n"
            "ELEVENLABS_API_KEY=\n"
            "ELEVENLABS_VOICE_ID=EXAVITQu4vr4xnSDxMaL\n"
            "GOOGLE_CLOUD_TTS_API_KEY=\n",
            encoding="utf-8",
        )
        print(f"  {_colour(_AMBER, '[!]')} Created .env template -- edit with your provider keys")


def _agent_ready() -> bool:
    """Return True when at least the LiveKit endpoint URL is configured."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return False
    try:
        from dotenv import dotenv_values
        values = dotenv_values(str(env_path))
    except ImportError:
        return False  # python-dotenv not installed -- assume not ready
    return bool((values.get("LIVEKIT_URL") or "").strip())


def main() -> NoReturn:
    parser = argparse.ArgumentParser(
        description="REX unified launcher -- dashboard + backend + agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python start-rex.py                        # dev mode (dashboard :8080 + backend :8000)
  python start-rex.py --prod                 # prod mode (dashboard :8000 only)
  python start-rex.py --with-agent           # dev + agent worker
  python start-rex.py --dev --port 9000      # custom dashboard port
""",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dev", action="store_true", default=True, help="Dev mode: dashboard :8080 + backend :8000 (default)")
    mode.add_argument("--prod", action="store_true", help="Prod mode: dashboard :8000 only (self-contained, single process)")
    parser.add_argument("--with-agent", action="store_true", help="Also spawn the LiveKit agent worker")
    parser.add_argument("--port", type=int, default=None, help="Dashboard port (dev default 8080, prod default 8000)")
    parser.add_argument("--backend-port", type=int, default=None, help="Root backend port (dev default 8000; ignored in --prod)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
    args = parser.parse_args()

    python = _check_venv()
    _ensure_dotenv()

    is_prod = args.prod
    dash_port = args.port or (8000 if is_prod else 8080)
    backend_port = args.backend_port or 8000
    host = args.host

    # ── header ────────────────────────────────────────────────────────────
    print()
    print(f"  {_colour(_BOLD + _CYAN, 'REX')}  {_colour(_BOLD, 'Unified Launcher')}")
    mode_tag = _colour(_RED, "PROD") if is_prod else _colour(_GREEN, "DEV")
    print(f"  mode={mode_tag}  python={python}")
    print()

    # ── resolve ports ─────────────────────────────────────────────────────
    dash_port = _resolve_port(dash_port, host, "dashboard port")
    if not is_prod and dash_port == backend_port:
        # Dashboard grabbed the backend's port -> shift backend up
        backend_port = _resolve_port(backend_port + 1, host, "backend port")
        if backend_port == dash_port:
            backend_port = _resolve_port(dash_port + 1, host, "backend port")
    elif not is_prod:
        backend_port = _resolve_port(backend_port, host, "backend port")

    # ── dashboard (always) ────────────────────────────────────────────────
    dash_args = [
        python,
        str(BASE_DIR / "dashboard" / "dev_login.py"),
        str(dash_port),
    ]
    _spawn(dash_args, f"dashboard  http://{host}:{dash_port}")

    # ── root backend (dev only) ───────────────────────────────────────────
    if not is_prod:
        # The root server.py lives at C:/Users/garde/server.py -- two levels
        # above this launcher (projects/REX-AI/start-rex.py).  We run it from
        # that directory so Uvicorn's 'server:app' import resolves, and add
        # projects/REX-AI to PYTHONPATH so server.py's `from core.repository`
        # import works.
        root_dir = str(BASE_DIR.parent.parent)
        rex_ai_path = str(BASE_DIR)
        backend_args = [
            python,
            "-c",
            (
                "import sys; "
                f"sys.path.insert(0, {rex_ai_path!r}); "
                "import uvicorn; "
                f"uvicorn.run('server:app', host='{host}', port={backend_port}, "
                "log_level='warning', log_config=None, access_log=False)"
            ),
        ]
        _spawn(
            backend_args,
            f"backend    http://{host}:{backend_port}",
            env={"PYTHONPATH": f"{rex_ai_path};{root_dir}"},
        )

    # ── agent worker (optional) ───────────────────────────────────────────
    if args.with_agent:
        if _agent_ready():
            agent_args = [
                python,
                str(BASE_DIR / "agent.py"),
                "start",
            ]
            _spawn(agent_args, "agent      LiveKit voice worker")
        else:
            print(
                f"  {_colour(_AMBER, '[!]')} Agent worker skipped -- "
                f"LIVEKIT_URL not set in .env"
            )

    # ── summary ───────────────────────────────────────────────────────────
    print()
    print(f"  {_colour(_BOLD, 'Services')}")
    print(f"  dashboard   {_colour(_CYAN, f'http://{host}:{dash_port}')}")
    if not is_prod:
        print(f"  backend     {_colour(_CYAN, f'http://{host}:{backend_port}')}")
    if args.with_agent and _agent_ready():
        print(f"  agent       LiveKit voice worker (check agent logs)")
    print()
    print(f"  {_colour(_GREEN, '[OK]')} REX is running. Press {_colour(_BOLD, 'Ctrl+C')} to stop all services.")
    print()

    # ── block until interrupted ───────────────────────────────────────────
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown()


if __name__ == "__main__":
    main()
