"""
dashboard/dev_login.py — REX dashboard bootstrap with a pre-minted session key.

The /login flow validates against DashboardServer._pending_keys, an in-memory
dict populated only when the desktop app calls new_key() (Mobile Connect).
When the desktop app isn't running, uvicorn's standalone app has an empty key
store and every PIN is rejected. This bootstrap mints a key in the SAME process
that serves, prints it to stdout (visible in the preview log), then serves the
normal FastAPI app on 127.0.0.1:8080.

Run:
    .venv/Scripts/python.exe dashboard/dev_login.py
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dashboard.server import DashboardServer, PORT  # noqa: E402

HOST = "127.0.0.1"
HOST_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080


def main() -> None:
    server = DashboardServer()
    key = server.new_key(expiry_secs=1800)  # 30-minute one-time key
    print(f"[dev_login] SESSION KEY: {key}", flush=True)
    print(f"[dev_login] Serving on http://{HOST}:{HOST_PORT}/login", flush=True)

    import uvicorn

    cfg = uvicorn.Config(
        server.app,
        host=HOST,
        port=HOST_PORT,
        log_level="warning",
        log_config=None,
        access_log=False,
    )
    uvicorn.Server(cfg).run()


if __name__ == "__main__":
    main()
