"""
dashboard/server.py — REX Local HTTP Dashboard

Plain HTTP on port 8000 (no SSL warnings, no firewall issues).
Security at the application layer: AES-256-CBC with session-key-derived key.
CryptoJS is auto-downloaded once and served locally — no CDN needed after that.

Install deps:  pip install fastapi "uvicorn[standard]" cryptography
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import socket
import string
import time
from datetime import timedelta
from pathlib import Path
from core.error_handler import log_error
from core.repository import (
    ConnectionLease,
    ConversationMessage,
    ConversationRepository,
    DeviceClaim,
    MessageBatch,
    decode_session,
    encode_session,
    get_repository,
    get_store_path,
)

try:
    from livekit import api as _livekit_api
except ImportError:
    _livekit_api = None  # type: ignore[assignment]

REX_STORE_PATH = get_store_path()
REX_TOKEN_TTL_SECONDS = max(60, int(os.getenv("REX_TOKEN_TTL_SECONDS") or os.getenv("JARVIS_TOKEN_TTL_SECONDS") or "900"))
REX_ROOM_PREFIX = os.getenv("REX_ROOM_NAME") or os.getenv("JARVIS_ROOM_NAME") or "rex"

_DEPS_OK = False
try:
    from fastapi import Body, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
    import uvicorn
    _DEPS_OK = True
except ImportError:
    pass

# python-multipart is required for file uploads — optional dependency
_UPLOAD_OK = False
try:
    from fastapi import UploadFile, File as FastAPIFile
    _UPLOAD_OK = True
except Exception as _e:

    log_error(_e, context="dashboard.server", severity="error")

BASE_DIR    = Path(__file__).resolve().parent.parent
STATIC_DIR  = Path(__file__).parent / "static"
DISPATCH_DIR = STATIC_DIR / "dispatch"
HUD_DIR      = STATIC_DIR / "hud"
PORT        = 8000
MAX_UPLOAD_MB = 500
DISPATCH_STATE_FILE = BASE_DIR / "data" / "dispatch_state.json"


def _make_uploads_dir() -> Path:
    """Return (and create) the cross-platform uploads folder."""
    for candidate in [
        Path.home() / "Downloads" / "REX Uploads",
        Path.home() / "Documents" / "REX Uploads",
        BASE_DIR / "uploads",
    ]:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except Exception as _e:

            log_error(_e, context="dashboard.server", severity="error")
    return BASE_DIR / "uploads"


UPLOADS_DIR = _make_uploads_dir()

def _quiet_run(*args, **kwargs):
    import platform
    import subprocess

    if platform.system() == 'Windows':
        kwargs.setdefault('creationflags', getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return subprocess.run(*args, **kwargs)


def _get_gemini_key() -> str | None:
    try:
        import json as _json
        with open(BASE_DIR / "config" / "api_keys.json", "r", encoding="utf-8") as f:
            return _json.load(f).get("gemini_api_key")
    except Exception:
        return None

_KEY_CHARS = [c for c in (string.ascii_uppercase + string.digits)
              if c not in ('O', 'I', 'L', '0', '1')]

# ── AES-256-CBC ───────────────────────────────────────────────────────────────
_AES_SALT = b'REX-DASHBOARD-v1'


def _derive_key(session_key: str) -> bytes:
    """SHA-256(sessionKey‖salt) → 32-byte AES-256 key (microseconds, no PBKDF2 needed)."""
    return hashlib.sha256(session_key.encode('utf-8') + _AES_SALT).digest()


def _decrypt_cbc(aes_key: bytes, enc_b64: str) -> str:
    """Decrypt base64(IV[16] ‖ ciphertext) with AES-256-CBC + PKCS7."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_pad
    raw      = base64.b64decode(enc_b64)
    iv, ct   = raw[:16], raw[16:]
    dec      = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
    padded   = dec.update(ct) + dec.finalize()
    unpadder = sym_pad.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode('utf-8')


# ── CryptoJS (auto-download once, served locally) ─────────────────────────────
_CRYPTOJS_CDN  = ("https://cdnjs.cloudflare.com/ajax/libs/"
                  "crypto-js/4.2.0/crypto-js.min.js")
_CRYPTOJS_FILE = STATIC_DIR / "crypto-js.min.js"


def _ensure_network_access(port: int) -> None:
    """Cross-platform, best-effort: open port in the OS firewall for LAN access.

    Runs in a background thread — never blocks uvicorn startup.

    Windows : writes a .bat file, runs it elevated via Windows ShellExecuteW
              (native UAC dialog, guaranteed to appear). One-time setup.
    macOS   : osascript admin dialog if the Application Firewall is on.
    Linux   : pkexec GUI → sudo -n → prints manual command as fallback.
    """
    import sys, subprocess, os, tempfile, threading

    # ── Windows ──────────────────────────────────────────────────────────────
    if sys.platform == "win32":
        import ctypes, time

        port_rule = f"REX Dashboard Port {port}"
        prog_rule  = "REX Dashboard Python"
        py_exe     = sys.executable

        def _netsh_rule_exists(name: str) -> bool:
            try:
                r = _quiet_run(
                    ["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
                    capture_output=True, text=True, timeout=5,
                )
                return r.returncode == 0 and "No rules match" not in r.stdout
            except Exception:
                return False

        def _network_is_public() -> bool:
            try:
                r = _quiet_run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "(Get-NetConnectionProfile | "
                     "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                     "Measure-Object).Count"],
                    capture_output=True, text=True, timeout=6,
                )
                return r.stdout.strip() not in ("", "0")
            except Exception:
                return False

        need_port    = not _netsh_rule_exists(port_rule)
        need_prog    = not _netsh_rule_exists(prog_rule)
        need_private = _network_is_public()

        if not need_port and not need_prog and not need_private:
            return  # already fully configured

        # Build a .bat file — netsh + powershell, runs fast when elevated
        bat_lines = ["@echo off"]
        if need_private:
            bat_lines.append(
                'powershell -NoProfile -NonInteractive -Command "'
                'Get-NetConnectionProfile | '
                "Where-Object {$_.NetworkCategory -eq 'Public'} | "
                'Set-NetConnectionProfile -NetworkCategory Private"'
            )
        if need_port:
            bat_lines.append(
                f'netsh advfirewall firewall add rule '
                f'name="{port_rule}" protocol=TCP dir=in '
                f'localport={port} action=allow'
            )
        if need_prog:
            bat_lines.append(
                f'netsh advfirewall firewall add rule '
                f'name="{prog_rule}" dir=in action=allow '
                f'program="{py_exe}" enable=yes'
            )

        bat_body = "\r\n".join(bat_lines) + "\r\n"
        fd, bat_path = tempfile.mkstemp(suffix=".bat", prefix="rex_fw_")
        try:
            os.write(fd, bat_body.encode("mbcs"))   # Windows cmd.exe expects ANSI
            os.close(fd)
        except Exception:
            try:
                os.close(fd)
            except Exception as _e:

                log_error(_e, context="dashboard.server", severity="error")
            return

        # ── Try running directly (succeeds when already admin) ────────────────
        try:
            r = _quiet_run(
                [bat_path], capture_output=True, timeout=8, shell=True
            )
            if r.returncode == 0:
                print(f"[Dashboard] Firewall configured for port {port}.")
                try:
                    os.unlink(bat_path)
                except Exception as _e:

                    log_error(_e, context="dashboard.server", severity="error")
                return
        except Exception as _e:

            log_error(_e, context="dashboard.server", severity="error")

        # ── ShellExecuteW: native UAC elevation (most reliable on Windows) ────
        # ShellExecuteW with verb "runas" always shows the UAC dialog regardless
        # of UAC level settings. Non-blocking — uvicorn is already running.
        print("[Dashboard] One-time network setup required.")
        print("[Dashboard] >>> A Windows security dialog will appear — click 'Yes' <<<")
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(
                None,       # hwnd  (no parent window)
                "runas",    # verb  (request elevation)
                bat_path,   # file  (our .bat)
                None,       # params
                None,       # working dir
                0,          # SW_HIDE (run without a visible cmd window)
            )
            if int(ret) > 32:
                # ShellExecuteW returns immediately; bat finishes in ~1 second.
                # Sleep briefly so the rules are in place before the first retry.
                time.sleep(2)
                print(f"[Dashboard] Network setup complete — port {port} is open.")
                print("[Dashboard] Refresh your phone browser to connect.")
            else:
                print("[Dashboard] Setup was not allowed.")
                print("[Dashboard] Phone connections may fail until REX is run as Administrator.")
        except Exception as e:
            print(f"[Dashboard] Firewall setup error: {e}")
        finally:
            # Cleanup after the bat has had time to run
            def _cleanup(path: str) -> None:
                time.sleep(5)
                try:
                    os.unlink(path)
                except Exception as _e:

                    log_error(_e, context="dashboard.server", severity="error")
            threading.Thread(target=_cleanup, args=(bat_path,), daemon=True).start()
        return

    # ── macOS ─────────────────────────────────────────────────────────────────
    if sys.platform == "darwin":
        fw_ctl = "/usr/libexec/ApplicationFirewall/socketfilterfw"
        try:
            r = _quiet_run(
                [fw_ctl, "--getglobalstate"], capture_output=True, text=True, timeout=5,
            )
            if "disabled" in r.stdout.lower():
                return  # firewall off — nothing to do

            py = sys.executable
            listed = _quiet_run(
                [fw_ctl, "--listapps"], capture_output=True, text=True, timeout=5,
            )
            if py in listed.stdout:
                return  # already allowed

            print("[Dashboard] One-time network setup — enter your password in the macOS dialog.")
            _quiet_run(
                ["osascript", "-e",
                 f'do shell script "{fw_ctl} --add {py} && {fw_ctl} --unblockapp {py}"'
                 f' with administrator privileges'],
                timeout=60,
            )
        except Exception as _e:

            log_error(_e, context="dashboard.server", severity="error")  # macOS firewall is off by default — silent failure is fine
        return

    # ── Linux ─────────────────────────────────────────────────────────────────
    def _privileged(cmd: list[str]) -> bool:
        for prefix in (["pkexec"], ["sudo", "-n"]):
            try:
                r = _quiet_run(prefix + cmd, capture_output=True, timeout=30)
                if r.returncode == 0:
                    return True
            except Exception as _e:

                log_error(_e, context="dashboard.server", severity="error")
        return False

    try:  # ufw
        r = _quiet_run(["ufw", "status"], capture_output=True, text=True, timeout=5)
        if "active" in r.stdout.lower():
            if _privileged(["ufw", "allow", f"{port}/tcp"]):
                print(f"[Dashboard] ufw: port {port} allowed.")
            else:
                print(f"[Dashboard] Run manually:  sudo ufw allow {port}/tcp")
            return
    except FileNotFoundError:
        pass

    try:  # firewalld
        r = _quiet_run(
            ["firewall-cmd", "--state"], capture_output=True, text=True, timeout=5,
        )
        if "running" in r.stdout.lower():
            ok = (_privileged(["firewall-cmd", "--add-port", f"{port}/tcp", "--permanent"])
                  and _privileged(["firewall-cmd", "--reload"]))
            if ok:
                print(f"[Dashboard] firewalld: port {port} allowed.")
            else:
                print(f"[Dashboard] Run manually:  sudo firewall-cmd --add-port={port}/tcp --permanent && sudo firewall-cmd --reload")
            return
    except FileNotFoundError:
        pass

    try:  # iptables (not persistent but works until reboot)
        r = _quiet_run(["iptables", "-L", "INPUT", "-n"], capture_output=True, timeout=5)
        if r.returncode == 0:
            if _privileged(["iptables", "-A", "INPUT", "-p", "tcp", "--dport", str(port), "-j", "ACCEPT"]):
                print(f"[Dashboard] iptables: port {port} opened.")
            else:
                print(f"[Dashboard] Run manually:  sudo iptables -A INPUT -p tcp --dport {port} -j ACCEPT")
    except FileNotFoundError:
        pass  # no iptables means firewall is probably off — nothing to do


def _ensure_crypto_js() -> None:
    if _CRYPTOJS_FILE.exists():
        return
    try:
        import urllib.request
        print("[Dashboard] Downloading CryptoJS (one-time setup)…")
        urllib.request.urlretrieve(_CRYPTOJS_CDN, str(_CRYPTOJS_FILE))
        print("[Dashboard] CryptoJS cached — will serve locally from now on.")
    except Exception as e:
        print(f"[Dashboard] CryptoJS download failed: {e}")
        print(f"[Dashboard] Encryption will fall back to CDN load on client.")


_ensure_crypto_js()


# ── helpers ───────────────────────────────────────────────────────────────────

def _is_private_ipv4(ip: str) -> bool:
    try:
        parts = [int(part) for part in ip.split('.')]
        if len(parts) != 4:
            return False
        a, b, c, d = parts
        if a == 10:
            return True
        if a == 172 and 16 <= b <= 31:
            return True
        if a == 192 and b == 168:
            return True
        return False
    except Exception:
        return False


def _local_ip() -> str:
    """Return the best LAN-facing IPv4 address, preferring a private network IP.
    Uses socket UDP probe (works on Linux/macOS/Windows) as primary method.
    Falls back to gethostbyname_ex and Windows ipconfig only if needed.
    """
    import platform
    preferred_private: list[str] = []
    private_candidates: list[str] = []
    other_candidates: list[str] = []

    def _add_candidate(ip: str, preferred: bool = False) -> None:
        if not ip or ip in ('127.0.0.1', '0.0.0.0'):
            return
        if ip.startswith('127.') or ip.startswith('169.254.'):
            return
        if _is_private_ipv4(ip):
            if preferred:
                preferred_private.append(ip)
            else:
                private_candidates.append(ip)
        else:
            other_candidates.append(ip)

    # Primary: UDP socket probe to public DNS (works on Linux/macOS/Windows)
    for probe in ('8.8.8.8', '1.1.1.1', '1.1.1.2'):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect((probe, 80))
            _add_candidate(s.getsockname()[0], preferred=True)
            s.close()
        except Exception:
            pass

    # Secondary: gethostbyname_ex (may return multiple IPs)
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            _add_candidate(ip)
    except Exception:
        pass

    # Tertiary (Linux): ip route get 8.8.8.8
    try:
        import subprocess
        r = subprocess.run(['ip', 'route', 'get', '8.8.8.8'], capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            for m in re.finditer(r'src\s+([0-9.]+)', r.stdout):
                _add_candidate(m.group(1), preferred=True)
    except Exception:
        pass

    # Fallback (Windows only): ipconfig
    if platform.system() == 'Windows':
        try:
            import subprocess
            r = _quiet_run(['ipconfig'], capture_output=True, text=True, timeout=8)
            for ip in re.findall(r'IPv4[^:]*:\\s*([0-9]+(?:\\.[0-9]+){3})', r.stdout):
                _add_candidate(ip)
        except Exception:
            pass

    for ip in preferred_private:
        if _is_private_ipv4(ip):
            return ip
    for ip in private_candidates:
        if _is_private_ipv4(ip):
            return ip
    for ip in other_candidates:
        if ip:
            return ip
    return '127.0.0.1'


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


# ── Dispatch unlock page ──────────────────────────────────────────────────────
# Served instead of the dispatch PWA until the visitor proves they hold a valid
# REX one-time key (same pool as Mobile Connect).
_DISPATCH_LOCK_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>REX — Dispatch Unlock</title>
<style>
  :root { --bg:#050608; --red:#ff4545; --text:#f2f4f7; --muted:rgba(242,244,247,.58); }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--text); font-family:'Segoe UI',system-ui,sans-serif;
         min-height:100vh; display:flex; align-items:center; justify-content:center; padding:20px; }
  .card { width:100%; max-width:360px; background:rgba(255,255,255,.03);
          border:1px solid rgba(255,255,255,.1); border-radius:18px; padding:28px; text-align:center; }
  h1 { font-size:18px; letter-spacing:.14em; text-transform:uppercase; margin-bottom:6px; }
  h1 em { color:var(--red); font-style:normal; text-shadow:0 0 16px rgba(255,69,69,.5); }
  p { color:var(--muted); font-size:13px; line-height:1.55; margin:8px 0 18px; }
  input { width:100%; padding:13px 14px; border-radius:10px; border:1px solid rgba(255,255,255,.14);
          background:rgba(255,255,255,.05); color:var(--text); font-size:22px; text-align:center;
          letter-spacing:.4em; text-transform:uppercase; outline:none; }
  input:focus { border-color:var(--red); box-shadow:0 0 0 3px rgba(255,69,69,.12); }
  button { width:100%; margin-top:12px; padding:12px; border-radius:10px; border:none;
           background:var(--red); color:#fff; font-weight:700; font-size:14px; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  .err { color:#f87171; font-size:12px; margin-top:10px; min-height:16px; }
  .hint { font-size:11px; color:var(--muted); margin-top:14px; line-height:1.6; }
</style>
</head>
<body>
<div class="card">
  <h1><em>REX</em> Dispatch</h1>
  <p>Enter the 6-character key from REX's <strong>Mobile Connect</strong> to unlock your dispatch data.</p>
  <input id="pin" maxlength="6" autocomplete="off" inputmode="text" placeholder="\u2022\u2022\u2022\u2022\u2022\u2022" autofocus>
  <button id="go">Unlock</button>
  <div class="err" id="err"></div>
  <div class="hint">Press <strong>Mobile Connect</strong> in the REX desktop app to get a key.<br>Already paired? This unlocks automatically.</div>
</div>
<script>
const $ = id => document.getElementById(id);
function setCookie(v){ document.cookie = 'rex_token=' + encodeURIComponent(v) + '; path=/; SameSite=Lax'; }
function go(token){ setCookie(token); location.href = '/dispatch'; }
$('go').addEventListener('click', async () => {
  const pin = $('pin').value.trim().toUpperCase();
  if (pin.length < 6) { $('err').textContent = 'Enter the 6-character key.'; return; }
  $('go').disabled = true; $('err').textContent = 'Unlocking\u2026';
  try {
    const r = await fetch('/api/dispatch/pair', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({pin})});
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || 'Invalid or expired key');
    localStorage.setItem('jarvis_device_token', d.device_token);
    go(d.token);
  } catch (e) { $('err').textContent = e.message; $('go').disabled = false; }
});
$('pin').addEventListener('keydown', e => { if (e.key === 'Enter') $('go').click(); });
(async () => {
  const dev = localStorage.getItem('jarvis_device_token');
  if (!dev) return;
  try {
    const r = await fetch('/api/device-login', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({device_token: dev})});
    const d = await r.json();
    if (d.ok && d.token) { go(d.token); }
  } catch (e) {}
})();
</script>
</body>
</html>"""


def _ensure_ssl_certs() -> bool:
    """Create local self-signed certs when missing so phones can use HTTPS."""
    certs = BASE_DIR / "config" / "certs"
    key_path = certs / "jarvis.key"
    cert_path = certs / "jarvis.crt"
    if key_path.exists() and cert_path.exists():
        return True
    try:
        import datetime
        import ipaddress
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        certs.mkdir(parents=True, exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "REX Local Remote"),
        ])
        alt_names = [
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ]
        try:
            alt_names.append(x509.IPAddress(ipaddress.ip_address(_local_ip())))
        except Exception as _e:

            log_error(_e, context="dashboard.server", severity="error")
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow() - datetime.timedelta(days=1))
            .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
            .sign(key, hashes.SHA256())
        )
        key_path.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return True
    except Exception as exc:
        print(f"[Dashboard] Could not create HTTPS certificate: {exc}")
        return False


# ── DashboardServer ───────────────────────────────────────────────────────────

class DashboardServer:

    def __init__(self):
        self._ip                          = _local_ip()
        self._tokens: set[str]            = set()
        self._token_keys: dict[str, str]  = {}   # auth_token → session_key
        self._aes_cache:  dict[str, bytes]= {}   # session_key → AES bytes
        self._clients: set[WebSocket]     = set()
        self._history: list[dict]         = []
        self._command_queue               = asyncio.Queue()
        self._wake_callback               = None
        self._connect_callback            = None
        self._pending_keys: dict[str, float] = {}
        self._device_sessions: dict[str, dict] = {}  # device_token → {session_key}
        self._phone_audio_queue: asyncio.Queue    = asyncio.Queue(maxsize=200)
        self._uploads_dir                 = UPLOADS_DIR
        self._login_html                  = _read("login.html")
        self._app_html                    = _read("app.html")
        self._repo: ConversationRepository = get_repository()
        self.app                          = self._build_app()

    # ── one-time key management ───────────────────────────────────────────

    def new_key(self, expiry_secs: int = 600) -> str:
        now = time.time()
        self._pending_keys = {k: v for k, v in self._pending_keys.items() if v > now}
        key = ''.join(secrets.choice(_KEY_CHARS) for _ in range(6))
        self._pending_keys[key] = now + expiry_secs
        return key

    # ── dispatch data persistence (cross-device sync) ────────────────────

    def load_dispatch_state(self) -> dict:
        """Load the synced dispatch state (properties + completion history)."""
        try:
            return json.loads(DISPATCH_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_dispatch_state(self, state: dict) -> None:
        """Persist dispatch state to disk so it survives browser cache clears."""
        try:
            DISPATCH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            DISPATCH_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception as _e:
            log_error(_e, context="dashboard.server.save_dispatch_state", severity="warning")
    def get_url(self) -> str:
        return f"http://{self._ip}:{PORT}"

    def get_manual_url(self) -> str:
        """URL for manual browser entry on the local network."""
        return f"http://{self._ip}:{PORT}"

    def _aes_key(self, session_key: str) -> bytes:
        if session_key not in self._aes_cache:
            self._aes_cache[session_key] = _derive_key(session_key)
        return self._aes_cache[session_key]

    def _decrypt(self, token: str, enc_b64: str) -> str | None:
        sk = self._token_keys.get(token)
        if not sk:
            return None
        try:
            return _decrypt_cbc(self._aes_key(sk), enc_b64)
        except Exception:
            return None

    # ── callbacks ────────────────────────────────────────────────────────

    def set_wake_callback(self, fn) -> None:
        self._wake_callback = fn

    def set_connect_callback(self, fn) -> None:
        self._connect_callback = fn

    # ── broadcast ────────────────────────────────────────────────────────

    async def broadcast(self, msg: dict) -> None:
        self._history.append(msg)
        if len(self._history) > 300:
            self._history = self._history[-300:]
        dead: set[WebSocket] = set()
        for ws in list(self._clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    # ── FastAPI app ───────────────────────────────────────────────────────

    def _build_app(self) -> "FastAPI":
        app = FastAPI(docs_url=None, redoc_url=None)

        def _auth(req: Request) -> bool:
            tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            return bool(tok) and tok in self._tokens

        # serve CryptoJS from local cache, fallback to CDN redirect
        @app.get("/static/crypto.js")
        async def serve_crypto():
            if _CRYPTOJS_FILE.exists():
                return FileResponse(str(_CRYPTOJS_FILE),
                                    media_type="application/javascript")
            from fastapi.responses import RedirectResponse
            return RedirectResponse(_CRYPTOJS_CDN)

        @app.get("/login", response_class=HTMLResponse)
        async def login_page():
            return HTMLResponse(self._login_html)

        # ── Garden Goats Dispatch (embedded PWA) ───────────────────────────
        # Served from the same origin as the dashboard so phone + desktop
        # share one URL (http://<ip>:8000/dispatch). The app is PIN-protected
        # with the same one-time-key pool as Mobile Connect: /dispatch serves a
        # lock page until the visitor proves they hold a valid key, then sets
        # the rex_token cookie. Data sync (/api/dispatch/*) reuses the same
        # token for auth and persists state server-side so jobs survive browser
        # cache clears and are shared across devices.
        #
        # The files live at dashboard/static/dispatch/ — a static copy of the
        # standalone goats-dispatch/ app. To refresh after changing the source:
        #   cp goats-dispatch/index.html goats-dispatch/sw.js goats-dispatch/manifest.json \
        #      projects/REX-AI/dashboard/static/dispatch/
        # or run scripts/sync_dispatch.py (one-shot or --watch).
        # Do NOT run goats-dispatch/server.py while REX is up — it also binds
        # port 8000 and would conflict with the dashboard.

        def _dispatch_auth(req: Request) -> bool:
            """Accept the rex_token cookie (set by the lock page) or a Bearer header."""
            tok = req.cookies.get("rex_token", "") or req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            return bool(tok) and tok in self._tokens

        @app.get("/dispatch", response_class=HTMLResponse)
        async def dispatch_page(req: Request):
            """No trailing slash: enforce the PIN gate, then redirect to /dispatch/
            so the PWA's relative assets (./sw.js, ./manifest.json) resolve under
            the /dispatch/ mount instead of the server root."""
            if not _dispatch_auth(req):
                return HTMLResponse(_DISPATCH_LOCK_HTML)
            from fastapi.responses import RedirectResponse
            return RedirectResponse("/dispatch/")

        @app.get("/dispatch/", response_class=HTMLResponse)
        async def dispatch_page_slash(req: Request):
            if not _dispatch_auth(req):
                return HTMLResponse(_DISPATCH_LOCK_HTML)
            try:
                return HTMLResponse((DISPATCH_DIR / "index.html").read_text(encoding="utf-8"))
            except Exception:
                return HTMLResponse("<!DOCTYPE html><html><body><h2>Dispatch app not found</h2></body></html>", status_code=404)

        @app.get("/dispatch/index.html", response_class=HTMLResponse)
        async def dispatch_index(req: Request):
            """Explicit index.html so the service worker's precache entry works."""
            if not _dispatch_auth(req):
                return HTMLResponse(_DISPATCH_LOCK_HTML)
            try:
                return HTMLResponse((DISPATCH_DIR / "index.html").read_text(encoding="utf-8"))
            except Exception:
                return HTMLResponse("<!DOCTYPE html><html><body><h2>Dispatch app not found</h2></body></html>", status_code=404)

        @app.get("/dispatch/sw.js")
        async def dispatch_sw():
            try:
                return FileResponse(str(DISPATCH_DIR / "sw.js"), media_type="application/javascript")
            except Exception:
                return JSONResponse({"error": "Not found"}, status_code=404)

        @app.get("/dispatch/manifest.json")
        async def dispatch_manifest():
            try:
                return FileResponse(str(DISPATCH_DIR / "manifest.json"), media_type="application/manifest+json")
            except Exception:
                return JSONResponse({"error": "Not found"}, status_code=404)

        @app.post("/api/dispatch/pair")
        async def dispatch_pair(req: Request):
            """Validate a one-time REX key and return an auth token + device token."""
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"ok": False, "error": "Bad request"}, status_code=400)
            entered = str(body.get("pin", "")).strip().upper()
            now = time.time()
            if entered in self._pending_keys and self._pending_keys[entered] > now:
                del self._pending_keys[entered]          # one-time use
                tok = secrets.token_urlsafe(32)
                dev_tok = secrets.token_urlsafe(32)
                self._tokens.add(tok)
                self._token_keys[tok] = entered
                self._device_sessions[dev_tok] = {"session_key": entered}
                self._aes_key(entered)
                if self._connect_callback:
                    self._connect_callback()
                asyncio.create_task(self.broadcast(
                    {"type": "sys", "text": "Dispatch app unlocked."}
                ))
                return JSONResponse({"ok": True, "token": tok, "device_token": dev_tok})
            return JSONResponse({"ok": False, "error": "Invalid or expired key"}, status_code=401)

        @app.get("/api/dispatch/load")
        async def dispatch_load(req: Request):
            """Fetch the server-side copy of dispatch state for cross-device sync."""
            if not _dispatch_auth(req):
                return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
            return JSONResponse({"ok": True, "state": self.load_dispatch_state()})

        @app.post("/api/dispatch/save")
        async def dispatch_save(req: Request):
            """Persist dispatch state server-side (properties + completion history)."""
            if not _dispatch_auth(req):
                return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"ok": False, "error": "Bad request"}, status_code=400)
            self.save_dispatch_state(body.get("state") or {})
            return JSONResponse({"ok": True})

        # ── BLACKOPS HUD (embedded tactical frontend) ──────────────────────

        from fastapi.staticfiles import StaticFiles

        @app.get("/hud", response_class=HTMLResponse)
        @app.get("/hud/", response_class=HTMLResponse)
        async def hud_page():
            try:
                return HTMLResponse((HUD_DIR / "index.html").read_text(encoding="utf-8"))
            except Exception:
                return HTMLResponse("<!DOCTYPE html><html><body><h2>BLACKOPS HUD not found</h2></body></html>", status_code=404)

        if (HUD_DIR / "assets").is_dir():
            app.mount("/hud/assets", StaticFiles(directory=str(HUD_DIR / "assets")), name="hud_assets")

        @app.get("/", response_class=HTMLResponse)
        async def index():
            # Auth is handled client-side via sessionStorage bearer token.
            # Server-side header auth can't work here because browser navigations
            # don't send custom headers (location.href doesn't carry Authorization).
            html = (self._app_html
                    .replace("__IP__", self._ip)
                    .replace("__PORT__", str(PORT)))
            return HTMLResponse(html)

        @app.post("/login")
        async def login(req: Request):
            body    = await req.json()
            entered = str(body.get("pin", "")).strip().upper()
            now     = time.time()
            if entered in self._pending_keys and self._pending_keys[entered] > now:
                del self._pending_keys[entered]          # one-time use
                tok = secrets.token_urlsafe(32)
                self._tokens.add(tok)
                self._token_keys[tok] = entered
                self._aes_key(entered)                   # pre-derive & cache
                if self._connect_callback:
                    self._connect_callback()
                asyncio.create_task(self.broadcast(
                    {"type": "sys", "text": "Remote connection established."}
                ))
                # Bearer token in response body — no cookies needed (works on any browser/HTTP)
                return JSONResponse({"ok": True, "token": tok})
            return JSONResponse({"ok": False, "error": "Invalid or expired key"},
                                status_code=401)

        @app.get("/auto-login")
        async def auto_login(key: str = ""):
            """QR code target — validates one-time key, creates session, redirects phone."""
            now = time.time()
            if not key or key not in self._pending_keys or self._pending_keys[key] <= now:
                return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<style>
  body{background:#07090f;color:#dde3ed;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}
  h2{color:#f87171;margin-bottom:12px}p{color:#5e6a7e;font-size:14px}
</style></head>
<body><div><h2>Link Expired</h2>
<p>Press <strong style="color:#dde3ed">Mobile Connect</strong> in REX to get a new QR code.</p>
</div></body></html>""")

            del self._pending_keys[key]
            tok     = secrets.token_urlsafe(32)
            dev_tok = secrets.token_urlsafe(32)
            self._tokens.add(tok)
            self._token_keys[tok] = key
            self._aes_key(key)
            self._device_sessions[dev_tok] = {"session_key": key}

            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Remote connection established via QR code."}
            ))

            return HTMLResponse(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width">
<style>
  body{{background:#07090f;color:#dde3ed;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}}
  p{{color:#5e6a7e;font-size:14px}}
</style></head>
<body>
<script>
  sessionStorage.setItem('jarvis_token','{tok}');
  sessionStorage.setItem('jarvis_key','{key}');
  localStorage.setItem('jarvis_device_token','{dev_tok}');
  setTimeout(function(){{location.replace('/')}},400);
</script>
<p>Connecting to REX…</p>
</body></html>""")

        @app.post("/api/device-login")
        async def device_login_ep(req: Request):
            """Return a fresh auth token for a previously paired device token."""
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"ok": False}, status_code=400)
            dev_tok = (body.get("device_token") or "").strip()
            if not dev_tok or dev_tok not in self._device_sessions:
                return JSONResponse({"ok": False}, status_code=401)
            session_key = self._device_sessions[dev_tok]["session_key"]
            tok = secrets.token_urlsafe(32)
            self._tokens.add(tok)
            self._token_keys[tok] = session_key
            self._aes_key(session_key)
            if self._connect_callback:
                self._connect_callback()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Known device reconnected automatically."}
            ))
            return JSONResponse({"ok": True, "token": tok, "key": session_key})

        @app.post("/api/revoke-devices")
        async def revoke_devices(req: Request):
            """Invalidate all persistent device tokens (admin action)."""
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            count = len(self._device_sessions)
            self._device_sessions.clear()
            return JSONResponse({"ok": True, "revoked": count})

        @app.post("/api/command")
        async def command(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body  = await req.json()
            token = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
            enc   = body.get("enc", "")
            if enc:
                text = self._decrypt(token, enc)
                if text is None:
                    return JSONResponse({"error": "Decryption failed"}, status_code=400)
            else:
                text = (body.get("text") or "").strip()
            if text:
                await self._command_queue.put(text)
                if self._wake_callback:
                    self._wake_callback()
            return JSONResponse({"ok": True})

        @app.post("/api/wake")
        async def wake_ep(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if self._wake_callback:
                self._wake_callback()
            return JSONResponse({"ok": True})

        # ── Phone mic real-time audio → Gemini Live ──────────────────────────

        @app.websocket("/ws/phone-audio")
        async def phone_audio_ws(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            asyncio.create_task(self.broadcast(
                {"type": "sys", "text": "Phone microphone live."}
            ))
            try:
                while True:
                    data = await websocket.receive_bytes()
                    try:
                        self._phone_audio_queue.put_nowait(
                            {"data": data, "mime_type": "audio/pcm"}
                        )
                    except asyncio.QueueFull:
                        pass  # drop frame rather than block
            except WebSocketDisconnect:
                pass
            finally:
                asyncio.create_task(self.broadcast(
                    {"type": "sys", "text": "Phone microphone stopped."}
                ))

        # ── File sharing ──────────────────────────────────────────────────────

        def _safe_filename(raw: str) -> str:
            name = Path(raw).name                          # strip path components
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(". ")
            return name or "upload"

        if _UPLOAD_OK:
            @app.post("/api/upload")
            async def upload_file(req: Request, file: UploadFile = FastAPIFile(...)):
                if not _auth(req):
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)

                safe = _safe_filename(file.filename or "upload")
                dest = self._uploads_dir / safe
                stem, suffix = Path(safe).stem, Path(safe).suffix
                counter = 1
                while dest.exists():
                    dest = self._uploads_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

                size = 0
                max_bytes = MAX_UPLOAD_MB * 1024 * 1024
                try:
                    with open(dest, "wb") as fout:
                        while True:
                            chunk = await file.read(65536)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > max_bytes:
                                fout.close()
                                dest.unlink(missing_ok=True)
                                return JSONResponse(
                                    {"error": f"File too large (max {MAX_UPLOAD_MB} MB)"},
                                    status_code=413,
                                )
                            fout.write(chunk)
                except Exception as exc:
                    try:
                        dest.unlink(missing_ok=True)
                    except Exception as _e:

                        log_error(_e, context="dashboard.server", severity="error")
                    return JSONResponse({"error": str(exc)}, status_code=500)

                asyncio.create_task(self.broadcast({
                    "type": "file_received",
                    "name": dest.name,
                    "size": size,
                    "saved_to": str(self._uploads_dir),
                }))
                return JSONResponse({"ok": True, "name": dest.name, "size": size})
        else:
            @app.post("/api/upload")
            async def upload_unavailable(req: Request):
                return JSONResponse(
                    {"error": "File uploads require: pip install python-multipart"},
                    status_code=503,
                )

        @app.get("/api/files")
        async def list_files(req: Request):
            if not _auth(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            files = []
            try:
                for f in sorted(
                    (p for p in self._uploads_dir.iterdir() if p.is_file()),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                ):
                    files.append({"name": f.name, "size": f.stat().st_size})
            except Exception as _e:

                log_error(_e, context="dashboard.server", severity="error")
            return JSONResponse({"files": files})

        @app.get("/uploads/{filename}")
        async def download_file(filename: str, token: str = ""):
            # Auth via query param — browser <a download> can't send custom headers
            tok = token.strip()
            if not tok or tok not in self._tokens:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            safe = re.sub(r'[/\\]', '', filename)
            path = self._uploads_dir / safe
            if not path.exists() or not path.is_file():
                return JSONResponse({"error": "Not found"}, status_code=404)
            return FileResponse(str(path), filename=safe)

        # ── BLACKOPS HUD syndication bridge ───────────────────────────────
        # The HUD's useWebSocket.tsx connects here (no auth — LAN-local) and
        # fans agent/alert/log events to every other connected client (cross-
        # tab / cross-device live syndication). Messages are opaque JSON
        # relays; the HUD's own broadcastLog/broadcastAgent already applied
        # the change locally, so the sender is excluded from the fan-out.

        import threading

        # ── REX conversation context + device endpoints ─────────────────────
        # Shared SQLite-backed persistence (WAL mode) — the same store the
        # agent worker writes to. These endpoints replace the proxy bridge;
        # the dashboard is now self-contained without the root backend.

        @app.post("/api/session")
        async def create_session(
            device_id: str = Query(..., min_length=1, max_length=128),
            user_id: str | None = Query(default=None, max_length=128),
        ):
            safe_device = self._repo._check_identifier(device_id, "device_id")
            resolved_user = self._repo.resolve_user(f"session-{safe_device}", safe_device, user_id)
            return {"session_token": encode_session(safe_device, resolved_user), "device_id": safe_device, "user_id": resolved_user}

        @app.get("/api/token")
        async def token(
            identity: str | None = Query(default=None, max_length=128),
            device_id: str | None = Query(default=None, max_length=128),
            user_id: str | None = Query(default=None, max_length=128),
            room: str | None = Query(default=None, max_length=128),
            authorization: str | None = Header(default=None),
        ):
            if _livekit_api is None:
                raise HTTPException(status_code=503, detail="LiveKit server SDK is not installed in this REX deployment")
            if not os.getenv("LIVEKIT_API_KEY") or not os.getenv("LIVEKIT_API_SECRET"):
                raise HTTPException(status_code=503, detail="LiveKit credentials are not configured")
            participant_identity = identity or f"rex-{secrets.token_urlsafe(9)}"
            participant_identity = self._repo._check_identifier(participant_identity, "identity")
            resolved_device = self._repo._check_identifier(device_id or participant_identity, "device_id")
            session = decode_session(authorization, resolved_device, user_id)
            resolved_user = self._repo.resolve_user(participant_identity, resolved_device, session["user_id"])
            if user_id and resolved_user != user_id:
                raise HTTPException(status_code=403, detail="Session user does not match requested user")
            room_name = room or f"{REX_ROOM_PREFIX}-{resolved_user}-{resolved_device}"
            room_name = self._repo._check_identifier(room_name, "room")
            import json as _json
            metadata = _json.dumps({"device_id": resolved_device, "user_id": resolved_user, "identity": participant_identity}, separators=(",", ":"))
            access_token = (
                _livekit_api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
                .with_identity(participant_identity)
                .with_name("REX user")
                .with_metadata(metadata)
                .with_ttl(timedelta(seconds=REX_TOKEN_TTL_SECONDS))
                .with_grants(_livekit_api.VideoGrants(room_join=True, room=room_name, can_publish=True, can_subscribe=True, can_publish_data=True))
                .to_jwt()
            )
            return {"token": access_token, "url": os.environ.get("LIVEKIT_URL", ""), "room": room_name, "identity": participant_identity, "device_id": resolved_device, "user_id": resolved_user, "expires_in": REX_TOKEN_TTL_SECONDS}

        @app.get("/api/context/{identity}")
        async def get_context(
            identity: str,
            device_id: str | None = Query(default=None, max_length=128),
            user_id: str | None = Query(default=None, max_length=128),
            authorization: str | None = Header(default=None),
        ):
            safe_device = self._repo._check_identifier(device_id or identity, "device_id")
            session = decode_session(authorization, safe_device, user_id)
            return self._repo.history(identity, safe_device, session["user_id"])

        @app.get("/api/context/user/{user_id}")
        async def get_user_context(user_id: str, device_id: str = Query(..., max_length=128), authorization: str | None = Header(default=None)):
            safe_user_id = self._repo._check_identifier(user_id, "user_id")
            safe_device = self._repo._check_identifier(device_id, "device_id")
            decode_session(authorization, safe_device, safe_user_id)
            return self._repo.user_history(safe_user_id)

        @app.post("/api/context/{identity}/messages")
        async def append_context(
            identity: str,
            batch: MessageBatch = Body(...),
            device_id: str | None = Query(default=None, max_length=128),
            user_id: str | None = Query(default=None, max_length=128),
            authorization: str | None = Header(default=None),
        ):
            safe_device = self._repo._check_identifier(device_id or identity, "device_id")
            session = decode_session(authorization, safe_device, user_id)
            return self._repo.add_messages(identity, safe_device, session["user_id"], batch.messages)

        @app.post("/api/device/{device_id}/claim")
        async def claim_device(device_id: str, claim: DeviceClaim, authorization: str | None = Header(default=None)):
            safe_device = self._repo._check_identifier(device_id, "device_id")
            decode_session(authorization, safe_device)
            self._repo.claim_device(safe_device, claim.user_id)
            return {"device_id": safe_device, "user_id": claim.user_id, "session_token": encode_session(safe_device, claim.user_id)}

        @app.post("/api/device/{device_id}/connection")
        async def activate_connection(device_id: str, lease: ConnectionLease, authorization: str | None = Header(default=None)):
            safe_device = self._repo._check_identifier(device_id, "device_id")
            decode_session(authorization, safe_device)
            if lease.device_id != safe_device:
                raise HTTPException(status_code=400, detail="device_id mismatch")
            return self._repo.activate_connection(lease)

        @app.get("/api/device/{device_id}/connection")
        async def get_connection(device_id: str, authorization: str | None = Header(default=None)):
            safe_device = self._repo._check_identifier(device_id, "device_id")
            decode_session(authorization, safe_device)
            return {"device_id": safe_device, "active": self._repo.get_active_connection(safe_device)}

        @app.delete("/api/device/{device_id}/connection/{connection_id}")
        async def release_connection(device_id: str, connection_id: str, authorization: str | None = Header(default=None)):
            safe_device = self._repo._check_identifier(device_id, "device_id")
            decode_session(authorization, safe_device)
            return {"released": self._repo.release_connection(safe_device, connection_id)}

        # ── BLACKOPS HUD syndication bridge ───────────────────────────────
        # The HUD's useWebSocket.tsx connects here (no auth — LAN-local) and
        # fans agent/alert/log events to every other connected client (cross-
        # tab / cross-device live syndication). Messages are opaque JSON
        # relays; the HUD's own broadcastLog/broadcastAgent already applied
        # the change locally, so the sender is excluded from the fan-out.

        import threading

        _hud_clients: set[WebSocket] = set()
        _hud_lock = threading.Lock()

        async def _hud_relay(message: str, exclude: WebSocket | None = None) -> None:
            with _hud_lock:
                clients = list(_hud_clients)
            for client in clients:
                if client is exclude:
                    continue
                try:
                    await client.send_text(message)
                except Exception:
                    with _hud_lock:
                        _hud_clients.discard(client)

        @app.websocket("/api/ws")
        async def hud_ws_ep(websocket: WebSocket) -> None:
            await websocket.accept()
            with _hud_lock:
                _hud_clients.add(websocket)
            try:
                while True:
                    payload = await websocket.receive_text()
                    await _hud_relay(payload, exclude=websocket)
            except WebSocketDisconnect:
                pass
            except Exception:
                pass
            finally:
                with _hud_lock:
                    _hud_clients.discard(websocket)

        @app.websocket("/ws")
        async def ws_ep(websocket: WebSocket, token: str = ""):
            tok = token.strip()
            if not tok or tok not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            self._clients.add(websocket)
            for entry in self._history[-50:]:
                try:
                    await websocket.send_json(entry)
                except Exception:
                    break
            try:
                while True:
                    data = await websocket.receive_json()
                    if data.get("type") == "command":
                        enc = data.get("enc", "")
                        t   = self._decrypt(tok, enc) if enc else (data.get("text") or "").strip()
                        if t:
                            await self._command_queue.put(t)
                            if self._wake_callback:
                                self._wake_callback()
            except WebSocketDisconnect:
                pass
            finally:
                self._clients.discard(websocket)

        return app

    # ── serve ─────────────────────────────────────────────────────────────
    async def _serve_alias(self) -> None:
        """Legacy HTTPS alias server kept for compatibility, but not used for QR pairing."""
        ssl_key  = BASE_DIR / "config" / "certs" / "jarvis.key"
        ssl_cert = BASE_DIR / "config" / "certs" / "jarvis.crt"
        asyncio.get_event_loop().run_in_executor(None, _ensure_network_access, PORT + 1)
        cfg = uvicorn.Config(
            self.app, host="0.0.0.0", port=PORT + 1, log_level="warning",
            ssl_keyfile=str(ssl_key), ssl_certfile=str(ssl_cert),
            log_config=None, access_log=False,
        )
        print(f"[Dashboard] Manual entry:  {self._ip}:{PORT + 1}  (legacy HTTPS alias)")
        await uvicorn.Server(cfg).serve()
    async def serve(self) -> None:
        if not _DEPS_OK:
            print("[Dashboard] fastapi/uvicorn not installed - dashboard disabled.")
            print("[Dashboard] Run:  pip install fastapi 'uvicorn[standard]' cryptography")
            return

        # Firewall setup runs in a thread - uvicorn starts immediately,
        # no waiting for UAC dialogs or subprocess timeouts.
        asyncio.get_event_loop().run_in_executor(None, _ensure_network_access, PORT)

        cfg = uvicorn.Config(
            self.app, host="0.0.0.0", port=PORT, log_level="warning",
            log_config=None, access_log=False,
        )

        print(f"[Dashboard] http://{self._ip}:{PORT}")
        print("[Dashboard] Press 'Mobile Connect' in REX UI to get the QR code.")
        await uvicorn.Server(cfg).serve()


# Module-level app instance for uvicorn discovery (uvicorn dashboard.server:app)
try:
    _server_instance = DashboardServer()
    app = _server_instance.app
except Exception:
    app = None
