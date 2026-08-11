"""
dashboard/mobile.py — FastAPI HTMX Mobile Dashboard

Mobile-friendly web UI on :8000 for fleet status, dispatch history,
voice logs, and one-tap dispatch. Uses Jinja2 templates with
Alpine.js + Tailwind CSS via CDN and HTMX for dynamic updates.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

log = logging.getLogger("brahma.mobile")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"

# ── In-memory state (populated by pi_main.py or background pollers) ─────────
_STATE = {
    "status": "online",
    "hardware": "Raspberry Pi 5 + Hailo-10H + Whisplay HAT",
    "mic_available": False,
    "speaker_available": False,
    "display_available": False,
    "hailo_available": False,
    "voice_state": "IDLE",
    "last_wake": None,
    "dispatch_history": [],
    "voice_log": [],
    "fleet_devices": [
        {"name": "pi", "type": "edge", "reachable": True, "ip": "127.0.0.1"},
        {"name": "desktop", "type": "desktop", "reachable": False, "ip": ""},
    ],
}


def update_state(**kwargs) -> None:
    """Thread-safe state update from external callers."""
    _STATE.update(kwargs)


# ── FastAPI app ──────────────────────────────────────────────────────────────
app = FastAPI(title="Brahma AI Lite — Mobile Dashboard")

# Mount static files if directory exists
_static_dir = BASE_DIR / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main dashboard page — fleet status overview."""
    return templates.TemplateResponse(request, "index.html", {
        "state": _STATE,
        "active_tab": "status",
    })


@app.get("/dispatch", response_class=HTMLResponse)
async def dispatch_page(request: Request):
    """Dispatch form + history page."""
    return templates.TemplateResponse(request, "index.html", {
        "state": _STATE,
        "active_tab": "dispatch",
    })


@app.get("/fleet", response_class=HTMLResponse)
async def fleet_page(request: Request):
    """Fleet device cards page."""
    return templates.TemplateResponse(request, "index.html", {
        "state": _STATE,
        "active_tab": "fleet",
    })


@app.get("/voice", response_class=HTMLResponse)
async def voice_page(request: Request):
    """Voice log stream page."""
    return templates.TemplateResponse(request, "index.html", {
        "state": _STATE,
        "active_tab": "voice",
    })


@app.get("/api/health")
async def health():
    """Health/status JSON endpoint for polling."""
    return JSONResponse({
        "status": _STATE.get("status", "unknown"),
        "hardware": _STATE.get("hardware", "unknown"),
        "mic_available": _STATE.get("mic_available", False),
        "speaker_available": _STATE.get("speaker_available", False),
        "display_available": _STATE.get("display_available", False),
        "hailo_available": _STATE.get("hailo_available", False),
        "voice_state": _STATE.get("voice_state", "IDLE"),
        "last_wake": _STATE.get("last_wake"),
        "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
    })


@app.get("/api/fleet")
async def fleet_status():
    """Return fleet device list as JSON."""
    return JSONResponse({"devices": _STATE.get("fleet_devices", [])})


@app.get("/api/dispatch/history")
async def dispatch_history():
    """Return dispatch history as JSON."""
    return JSONResponse({"history": _STATE.get("dispatch_history", [])})


@app.get("/api/voice/log")
async def voice_log():
    """Return voice log entries as JSON."""
    return JSONResponse({"log": _STATE.get("voice_log", [])})


@app.post("/api/dispatch")
async def dispatch_command(request: Request):
    """Submit a dispatch command."""
    body = await request.json()
    prompt = body.get("prompt", "").strip()
    if not prompt:
        return JSONResponse({"error": "prompt required"}, status_code=400)
    entry = {
        "task_id": f"dispatch-{int(datetime.now(timezone.utc).timestamp())}",
        "prompt": prompt,
        "status": "queued",
        "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
    }
    _STATE["dispatch_history"].insert(0, entry)
    # Keep only last 50 entries
    _STATE["dispatch_history"] = _STATE["dispatch_history"][:50]
    return JSONResponse({"ok": True, "entry": entry})


# ── Background server starter ────────────────────────────────────────────────

_server_thread: Optional[threading.Thread] = None


def start_dashboard(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Start the mobile dashboard in a background daemon thread.

    Safe to call multiple times — only one thread is started.
    """
    global _server_thread
    if _server_thread is not None and _server_thread.is_alive():
        return

    def _run():
        import uvicorn
        uvicorn.run(
            "dashboard.mobile:app",
            host=host,
            port=port,
            log_level="warning",
        )

    _server_thread = threading.Thread(target=_run, daemon=True)
    _server_thread.start()
    log.info("Mobile dashboard started on %s:%d", host, port)


def stop_dashboard() -> None:
    """Signal the dashboard thread to stop (best-effort for daemon thread)."""
    # Daemon threads die with the process; nothing to do here.
    pass