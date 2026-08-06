<p align="center">
  <img src="assets/Almighty_AI_Logo.png" alt="Almighty AI" width="260" />
</p>

<h1 align="center">Almighty AI</h1>

<p align="center">
  <strong>Open-source Windows desktop AI assistant</strong> for voice, automation, productivity, and intelligent workflows.
</p>

<p align="center">
  <a href="#overview"><img src="https://img.shields.io/badge/experience-open%20source-blue?style=for-the-badge" alt="Open Source Experience" /></a>
  <a href="#getting-started"><img src="https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey?style=for-the-badge" alt="Windows" /></a>
  <a href="#how-it-works"><img src="https://img.shields.io/badge/ai-voice%20%2B%20automation-green?style=for-the-badge" alt="AI + Automation" /></a>
</p>

---

## Overview

Almighty AI is an open-source desktop assistant designed for Windows power users. It unites voice and text input with intelligent automation, productivity workflows, document generation, and adaptive screen-aware actions.

- Live voice and text interaction via Gemini with OpenRouter fallback
- Automatic daily briefing with interruption-aware audio playback
- Desktop automation for apps, windows, files, and browser workflows
- Office content generation for PowerPoint, Word, spreadsheets, and PDF
- Built-in website and workspace creation through Almighty's local workspace generator
- Discord collaboration, reminders, meeting assistant, and notifications

## Why It Stands Out

- Responsive UI with rich task/workspace feedback
- Seamless AI and voice integration for desktop productivity
- Modular tool-driven architecture for clean extension
- Robust fallback handling to keep the assistant available
- Local-first configuration with secure credentials storage

## Features

### Intelligent Assistant

- Unified voice + typed conversation experience
- Startup daily briefing with Edge TTS delivery
- Instant briefing interruption when a new message arrives
- Gemini-first live AI with OpenRouter fallback support

### Automation & Productivity

- Open and control Windows applications and system actions
- Browser automation with Playwright for web workflows
- Screen inspection and contextual content extraction
- File and document automation for fast productivity

### Office & Content Tools

- Generate PowerPoint decks and presentation content
- Create spreadsheets and Word documents quickly
- Export polished reports as PDF files
- Build landing pages and websites from within the app

### Integrations

- Discord bridge for remote commands and chat
- Local credential management for Gemini and OpenRouter
- Configurable voice, notifications, startup, and UI preferences

## How It Works

Almighty AI is built on a layered desktop architecture that separates UI, AI session management, and tool execution.

- `main.py` initializes the application, launches the UI, and manages the AI runtime.
- `AlmightyLive` owns the live AI session, audio queues, and command routing.
- `actions/` contains modular tools for automation, document generation, notifications, meetings, and search.
- `AttentionMonitor` captures external events and notification text, then speaks alerts using Edge TTS.
- `daily_briefing.py` constructs the morning briefing text and triggers playback after startup.
- `ui.py` provides a polished Qt-based interface with command entry, workspace cards, and status feedback.
- `or_client.py` provides OpenRouter fallback support when Gemini is unavailable or rate-limited.

## Getting Started

### Prerequisites

- Windows 10 or Windows 11
- Python 3.11 or 3.12
- Git installed
- Gemini API key
- OpenRouter API key (optional, recommended for fallback resilience)

### 1. Clone the repository

```powershell
git clone https://github.com/titechprabhasolutions/Brahma-AI---Lite.git
cd "Brahma-AI---Lite"
```

### 2. Create a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```powershell
pip install -r requirements.txt
playwright install
```

### 4. Configure API keys

The app loads keys from `config/api_keys.json`. `setup.py` writes the complete skeleton (including `os_system`, which the API-key gate requires); if you create the file by hand, use the same shape:

```json
{
  "gemini_api_key": "YOUR_GEMINI_API_KEY",
  "openrouter_api_key": "YOUR_OPENROUTER_API_KEY",
  "anthropic_api_key": "",
  "os_system": "Windows"
}
```

#### Gemini API Key

- Create a Google Cloud or Gemini account
- Enable Gemini API access for your project
- Generate an API key and add it under `gemini_api_key`

#### OpenRouter API Key (recommended)

- Register at https://openrouter.ai
- Generate an `sk-or-` API key
- Add it under `openrouter_api_key`

### 5. Optional: Configure Discord integration

To enable Discord bridging, populate `config/discord_bot.json` with your bot credentials and connection settings.

### 6. Start the app

```powershell
python main.py
```

For a cleaner Windows launch without console output:

```powershell
scripts\start_almighty.vbs
```

## Linux / macOS (partial support)

Almighty is Windows-first, but the cross-platform parts (UI, local dashboard, TTS, office/document tools, file & web tools) also run on Linux and macOS thanks to `linux_shim.py`.

### What works / what doesn't

| Area | On Linux/macOS |
|---|---|
| UI (`ui.py`), gesture HUD, dashboard (`dashboard/server.py`) | ✅ |
| Edge TTS (`edge-tts`) | ✅ |
| Office, PDF, doc, file, web-search, YouTube, smart-home, Discord tools | ✅ |
| Gemini / OpenRouter chat | ✅ |
| Voice capture (`sounddevice`) | ✅ (needs a working audio device) |
| Registry, Steam/Epic updater, Windows volume/pycaw, toasts, pywinauto automation | ❌ (stubbed — degrades gracefully, returns "not installed"-style messages) |

### 1. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

### 2. Install the Linux-compatible dependencies

```bash
pip install -r requirements-linux.txt
```

> `requirements-linux.txt` is the Linux/macOS-installable subset of `requirements.txt` with pinned, verified versions. The Windows-only packages (`pyaudio`, `pycaw`, `comtypes`, `pywinauto`, `win10toast`) are intentionally omitted — they cannot build on Linux.

Optional — browser automation:

```bash
playwright install chromium
```

### 3. Configure API keys

Same as on Windows — create `config/api_keys.json` (the app's setup page also writes `os_system` automatically; it is required for the API-key gate):

```json
{
  "gemini_api_key": "YOUR_GEMINI_API_KEY",
  "openrouter_api_key": "YOUR_OPENROUTER_API_KEY",
  "anthropic_api_key": "",
  "os_system": "Linux"
}
```

### 4. Run headless (no display / server environments)

```bash
QT_QPA_PLATFORM=offscreen python main.py
```

The local dashboard (phone remote / QR pairing) serves on `http://127.0.0.1:8000`.

The Gemini Live voice channel needs raw-PCM rates (16 kHz in / 24 kHz out) that many HDA codecs reject. `linux_shim.configure_audio_devices()` is called before every stream open and automatically routes audio through an ALSA plug layer (`pipewire` / `sysdefault`) that converts sample rates. To force specific devices, set `ALMIGHTY_AUDIO_IN` / `ALMIGHTY_AUDIO_OUT` to a device index or name substring, e.g.:

```bash
ALMIGHTY_AUDIO_IN=11 ALMIGHTY_AUDIO_OUT=11 QT_QPA_PLATFORM=offscreen python main.py
```

**Persisting the audio fix across reboots.** PipeWire may come up with a muted HDMI sink as default and hide the motherboard analog output (jack detection marks it "not available", so `pactl set-card-profile` silently fails). `scripts/setup-audio.sh` installs a WirePlumber override that un-hides the ALC897 analog output, then unmutes and raises the volume on every sink and pins the default (analog first, HDMI fallback). Install it as a login service once:

```bash
bash scripts/setup-audio.sh --install   # apply now + enable at every login
bash scripts/setup-audio.sh --status    # show cards / sinks / default
```

### 5. Run the test suite

```bash
.venv/bin/python -m pytest tests/
```

The suite includes `tests/test_linux_shim.py`, which imports every `actions/*` module plus `ui` and `main` on non-Windows and asserts that no real Windows-only module leaks through the stubs.

### How the shim works

`linux_shim.py` (imported first by `main.py`) pre-registers inert stubs in `sys.modules` for `pyaudio`, `pycaw`, `comtypes`, `pywinauto`, and `win10toast` on non-Windows platforms. Stub members raise a descriptive `RuntimeError` when used, so the app's existing `try/except` fallbacks take over instead of crashing.

Two important design notes baked into the code:

- **`winreg` is never stubbed.** Python's own stdlib (`mimetypes`) probes `import winreg` to detect Windows; a fake module makes it crash. `actions/game_updater.py` instead guards its own import (`winreg = None`) and skips registry lookups.
- The shim is a **no-op on Windows**, so Windows behavior is completely unchanged.

## Configuration

- `config/api_keys.json` — Gemini and OpenRouter credentials
- `config/app_settings.json` — voice, UI, startup, and automation settings
- `config/discord_bot.json` — Discord bridge settings

## Plugin System

You can extend Almighty AI with lightweight Python plugins placed in the `plugins/` folder. Plugins are simple `*.py` files that export one or more hook functions:

- `on_almighty_created(almighty)` — called when the `AlmightyLive` instance is created
- `on_startup(almighty)` — called once during app startup after plugins are registered
- `on_text_command(text, source, almighty=None)` — called for each incoming text command; return `True` to mark the command handled and stop further processing

Example: `plugins/example_plugin.py` demonstrates the hooks.

To enable plugins, simply drop your plugin file into the `plugins/` folder and restart the app. The launcher and `main.py` load plugins automatically.

### Skills (markdown instruction sets)

Skills are small markdown instruction sets the agent can discover and load on demand — the same pattern used by Claude Code skills. Drop a skill into the `skills/` folder as `skills/<name>.md` or `skills/<name>/SKILL.md`:

```markdown
---
name: business-plan
description: Build a complete business plan — deck, written plan, and financial sheet.
---

Follow these steps when the user asks for a business plan...
```

- `list_skills` / `load_skill` are exposed to the live chat and the agent planner, so the assistant finds a matching skill and reads its instructions before acting.
- No frontmatter required — the file name becomes the skill name and the first line the description.
- Plugins can contribute skills too by exporting `get_skills()` returning `[{"name": ..., "description": ..., "content": ...}]`.

Example skills ship in `skills/` (`instagram-content`, `business-plan`).

### MCP servers (Model Context Protocol)

Almighty AI can connect to any MCP server — the open standard for agent tools — and expose its tools to the live chat and the planner/executor pipeline. Configure servers in `config/mcp_servers.json` (copy from `config/mcp_servers.example.json`; the real file is gitignored because it may hold tokens):

```json
{
  "servers": [
    {"name": "filesystem", "command": "npx",
     "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/you/Documents"]},
    {"name": "github", "command": "npx",
     "args": ["-y", "@modelcontextprotocol/server-github"],
     "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "..."}},
    {"name": "cloud-db", "transport": "http",
     "url": "https://mcp.example.com/mcp",
     "token": "optional-bearer-token"}
  ]
}
```

**Local servers** (default) spawn a process: `command`/`args`/`env` are passed to the SDK's stdio transport. **Remote servers** use the modern streamable-HTTP transport: set `"transport": "http"` (aliases `"streamable-http"` / `"sse"` are accepted) with a `url` (http/https) plus optional `token` (sent as `Authorization: Bearer …`) and optional `headers`.

Servers start lazily on first use, each on its own background thread. `mcp_list` shows connected tools and the planner/live chat treat them like native tools. The Settings → System & Connect page lists every configured server with live status (connected / failed / not started), tool counts, and a per-server **Test Connection** button. Requires the `mcp` package (in `requirements.txt`). A failed remote handshake (wrong token, unreachable host) is contained per server and reported as a start failure.

### Commercial licensing (Almighty Pro)

MCP servers and skills are **Pro features**, consistent with the commercial terms in `LICENSE` §3. The Community Edition keeps every other capability free. The Settings → System & Connect page has a **Licensing** card to paste a key, and an `ALMIGHTY_LICENSE_KEY` environment variable works for ephemeral activation.

Pro keys are **offline, Ed25519-signed strings**: `base64url(payload).base64url(signature)` where the payload carries `licensee`, `tier: "pro"`, `issued`, and an optional `expires`. The app embeds only the public key (`config/profile.py`); there is no phone-home. Keys are issued by the copyright holder:

```bash
python scripts/make_license.py --issue "Acme Corp" --days 365   # prints a key
python scripts/make_license.py --genkey                          # rotate the keypair
```

The private key lives in `config/license_private.pem` (gitignored — never commit, ship, or embed it). Tampered, expired, or forged keys are rejected with a clear reason in the UI.

## Project Structure

- `main.py` — core runtime, AI session orchestration, and startup flow
- `ui.py` — polished Qt interface, workspace cards, and controls
- `actions/` — modular tools for automation and AI workflows
- `config/` — local settings, API keys, and runtime configuration
- `tests/` — validation and integration tests for core features

## Recent Updates

### 2026-07-19

- Restored automatic daily briefing playback at startup.
- Unified local TTS output to the same Edge voice.
- Added briefing interruption support for immediate user response.
- Redesigned gesture HUD for hand landmark control.
- Improved cursor mapping for better desktop reach and direction.
- Added idle speech prompts for proactive engagement.
- Tightened the developer workflow so Almighty uses the local workspace generator for websites and keeps coding tasks in the main app flow.

## Community

- Discord: https://discord.gg/gEYmJKKtq3

## License

This project is licensed under a custom source-available license. See `LICENSE` for full terms and `TRADEMARK.md` for branding details.

## Maintained by

- Suryaansh Tiwari

Please preserve attribution and keep credentials secure when building on top of Almighty AI.

---

## Raspberry Pi 5 Embedded Build (Brahma AI Lite)

Headless voice assistant on a **Raspberry Pi 5** with the **Hailo-10H**
NPU (40 TOPS) and **PiSugar Whisplay HAT** (WM8960 I2S mic + speaker +
SPI TFT display). Reuses Almighty's voice pipeline (Gemini Live, Vosk
wake word, action tools, agent executor) without the PyQt6 desktop UI.

### Hardware

- Raspberry Pi 5 (8 GB recommended)
- Hailo AI HAT+ with Hailo-10H NPU (PCIe)
- PiSugar Whisplay HAT (WM8960 codec for I2S audio + SPI TFT display)

### Pi-side Setup

```bash
# On the Pi (Debian Trixie, 64-bit):
git clone <repo> && cd Brahma-AI---Lite
scripts/setup-pi.sh       # installs deps + Vosk model + checks NPU
```

### Hailo NPU Driver

If `hailortcli scan` reports no devices, rebuild the driver:

```bash
cd /usr/src/hailort-pcie-driver/linux/pcie
sudo make clean && sudo make all
sudo cp hailo1x_pci.ko /lib/modules/$(uname -r)/kernel/drivers/misc/
sudo depmod -a && sudo modprobe hailo1x_pci
echo "hailo1x_pci" | sudo tee /etc/modules-load.d/hailo.conf
hailortcli scan  # should report the Hailo-10H
```

### Whisplay Audio

Add to `/boot/firmware/config.txt` under the Whisplay section:

```
dtoverlay=wm8960-soundcard
```

Reboot. Verify with `arecord -l` (should show `wm8960-soundcard`).

### Run

```bash
.venv/bin/python pi_main.py   # foreground
bash scripts/install-pi-service.sh  # systemd user service (survives reboot)
journalctl --user -u brahma-pi -f    # tail logs
```

### Mobile Access

The dashboard FastAPI starts automatically on port 8000. Access via
Tailscale from any device on the same network:
`http://100.94.30.18:8000/api/health`
