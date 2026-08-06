"""Almighty AI — one-shot setup helper.

Installs the requirements and Playwright browsers, then writes the complete
``config/api_keys.json`` skeleton (with ``os_system`` pre-filled) so first-run
setup is paste-and-go: drop in your keys and launch ``python main.py``.

The app's API-key gate (``ui._check_config``) requires BOTH a non-empty
``gemini_api_key`` AND a non-empty ``os_system``.  Old setup flows only
created the two key fields, so the app kept showing the setup screen even
after keys were pasted.  This helper writes the exact shape the app reads
(``ui._load_api_defaults`` / ``ui._on_setup_done``) and never overwrites keys
you have already configured.
"""

import json
import platform
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
API_FILE = CONFIG_DIR / "api_keys.json"

# The canonical shape the app reads.  `os_system` is pre-filled with the
# detected OS so the API-key gate passes the moment a Gemini key is pasted.
DEFAULTS = {
    "gemini_api_key": "",
    "openrouter_api_key": "",
    "anthropic_api_key": "",
    "os_system": platform.system(),
}


def write_config_skeleton() -> Path:
    """Create/refresh ``config/api_keys.json``, preserving existing values.

    Existing non-empty values (real keys, a previously set ``os_system``,
    any future fields) are kept; only missing fields are filled from
    :data:`DEFAULTS`.  Never raises.
    """
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        existing: dict = {}
        if API_FILE.exists():
            try:
                existing = json.loads(API_FILE.read_text(encoding="utf-8"))
                if not isinstance(existing, dict):
                    existing = {}
            except Exception:
                existing = {}

        merged = dict(DEFAULTS)
        for key, value in existing.items():
            if value:  # keep any present, non-empty value
                merged[key] = value

        API_FILE.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"⚠️ Could not write config skeleton: {exc}")
    return API_FILE


def _install(what: str, args: list[str]) -> None:
    """Run an install step; warn instead of aborting so setup still finishes."""
    try:
        subprocess.run([sys.executable, *args], check=True)
    except Exception as exc:
        print(f"⚠️ Could not install {what}: {exc}")
        print("   Continue manually — e.g. on Linux use requirements-linux.txt.")


def main() -> None:
    # Write the config skeleton first so it always lands, even if a
    # dependency step below fails (e.g. Windows-only packages on Linux).
    api_file = write_config_skeleton()

    print("Installing requirements...")
    _install("requirements", ["-m", "pip", "install", "-r", "requirements.txt"])

    print("Installing Playwright browsers...")
    _install("Playwright browsers", ["-m", "playwright", "install"])

    try:
        data = json.loads(api_file.read_text(encoding="utf-8"))
    except Exception:
        data = {}

    print()
    print(r"✅ Setup complete! Run 'python main.py' or scripts\start_almighty.bat to start Almighty AI.")
    print(f"Config ready: {api_file}  (os_system={data.get('os_system', '?')})")
    if data.get("gemini_api_key"):
        print("   Gemini key already present — you're good to go.")
    else:
        print("   Paste your keys into config/api_keys.json, e.g.:")
        print('   {\n     "gemini_api_key": "AIza...",\n     "openrouter_api_key": "sk-or-..."\n   }')
        print("   (the UI's setup page also accepts them and writes this same file)")


if __name__ == "__main__":
    main()
