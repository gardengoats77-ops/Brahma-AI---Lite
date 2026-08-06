"""linux_shim.py — Linux/macOS compatibility shim for Windows-only imports.

Almighty AI is a Windows desktop app.  On non-Windows platforms the
following modules are unavailable and would crash at import time:

    winreg        (module-scope import in actions/game_updater.py)
    pyaudio       (requirements-only today, stubbed for safety)
    pycaw/pycaw.pycaw, comtypes   (volume control — used only on Windows)
    pywinauto     (UI automation — used only on Windows)
    win10toast    (toast notifications — used only on Windows)

This module pre-registers inert stand-ins in ``sys.modules`` so the rest of
the codebase imports cleanly.  Every stub member raises a descriptive
``RuntimeError`` when actually *used*, so the existing ``try/except``
fallbacks in the app take over gracefully instead of hard-crashing.

Import it once as early as possible in the entrypoint::

    import linux_shim   # noqa: F401

It is a no-op on Windows.

It also provides :func:`configure_audio_devices`, which routes the Gemini
Live voice channel through a sample-rate-converting ALSA device (pipewire /
sysdefault) so the app's non-native rates (16 kHz in / 24 kHz out) work on
Linux without depending on the exact HDA codec.
"""

from __future__ import annotations

import os
import sys
import types

_WINDOWS = os.name == "nt"

# ── helpers ──────────────────────────────────────────────────────────────────

def _stub_module(name: str, attrs: dict | None = None) -> types.ModuleType:
    """Create a ``sys.modules`` entry for a fake module."""
    mod = types.ModuleType(name)
    mod.__doc__ = f"Inert {__name__} stub for the Windows-only module '{name}'."
    for key, value in (attrs or {}).items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    return mod


def _unavailable(what: str):
    """Return a callable that raises a clear 'Windows-only' error."""
    def _raise(*args, **kwargs):
        raise RuntimeError(
            f"'{what}' is Windows-only and is stubbed out by {__name__} "
            f"on platform '{os.name}'. This capability is unavailable on this OS."
        )
    _raise.__name__ = what.rsplit(".", 1)[-1]
    return _raise


def _stub_class(what: str):
    """Return a class whose constructor raises (members are the real API)."""
    class _Stub:
        def __init__(self, *args, **kwargs):
            _unavailable(what)()

    _Stub.__name__ = what.rsplit(".", 1)[-1]
    _Stub.__qualname__ = _Stub.__name__
    _Stub.__doc__ = f"Stub for Windows-only '{what}'."
    return _Stub


def install() -> None:
    """Register Windows-only stubs in ``sys.modules`` (no-op on Windows)."""
    if _WINDOWS:
        return

    # NOTE: `winreg` is deliberately NOT stubbed in sys.modules. Python's own
    # stdlib (mimetypes) probes `import winreg` to detect Windows; a fake
    # module would make it call into the stub and crash. Instead the one
    # module-scope importer (actions/game_updater.py) guards its own import
    # and treats a missing winreg as 'no registry on this OS'.

    # ── pyaudio — audio capture (requirements-only; stub defensively) ────────
    _stub_module("pyaudio", {"PyAudio": _stub_class("pyaudio.PyAudio")})

    # ── comtypes + pycaw — Windows Core Audio volume control ─────────────────
    _stub_module("comtypes", {
        "CLSCTX_ALL": 0x17,  # real value; constant-only usage on Windows
        "CoInitialize": _unavailable("comtypes.CoInitialize"),
        "CoUninitialize": _unavailable("comtypes.CoUninitialize"),
    })

    pycaw_mod = _stub_module("pycaw", {})
    _stub_module("pycaw.pycaw", {
        "AudioUtilities": _stub_class("pycaw.pycaw.AudioUtilities"),
        "IAudioEndpointVolume": _stub_class("pycaw.pycaw.IAudioEndpointVolume"),
    })
    pycaw_mod.pycaw = sys.modules["pycaw.pycaw"]  # allow `from pycaw import pycaw`

    # ── pywinauto — Windows UI automation ────────────────────────────────────
    findwindows_mod = _stub_module("pywinauto.findwindows", {
        "find_windows": _unavailable("pywinauto.findwindows.find_windows"),
        "find_window":  _unavailable("pywinauto.findwindows.find_window"),
        "find_elements": _unavailable("pywinauto.findwindows.find_elements"),
        "find_elements_android": _unavailable("pywinauto.findwindows.find_elements_android"),
    })
    _stub_module("pywinauto", {
        "Application": _stub_class("pywinauto.Application"),
        "Desktop":     _stub_class("pywinauto.Desktop"),
        "findwindows": findwindows_mod,
    })

    # ── win10toast — Windows toast notifications ─────────────────────────────
    _stub_module("win10toast", {"ToastNotifier": _stub_class("win10toast.ToastNotifier")})

    print(f"[{__name__}] Installed stubs for Windows-only modules (running on {os.name}).")


# Auto-install on import.
install()


# ── Audio device selection (Gemini Live voice channel) ──────────────────────

_AUDIO_CONFIGURED = False
_AUDIO_PAIR = (-1, -1)


def configure_audio_devices(send_rate: int = 16000, recv_rate: int = 24000) -> tuple:
    """Pick sounddevice I/O devices that accept the Live voice channel rates.

    The Gemini Live session streams raw PCM at ``send_rate`` Hz in and
    ``recv_rate`` Hz out.  Many HDA codecs (e.g. ALC897) reject non-native
    rates with ``paInvalidSampleRate``, so on Linux we route the streams
    through an ALSA plug layer (``sysdefault`` preferred, then ``pipewire``)
    that performs automatic rate conversion.

    Override explicitly with the ``ALMIGHTY_AUDIO_IN`` / ``ALMIGHTY_AUDIO_OUT``
    environment variables (a device index, or a substring of the device name).

    Idempotent and best-effort — safe to call before every stream open and it
    never raises.  Sets ``sounddevice.default.device`` and returns the chosen
    ``(input, output)`` device pair.
    """
    global _AUDIO_CONFIGURED, _AUDIO_PAIR
    try:
        import sounddevice as sd
    except Exception:
        return _AUDIO_PAIR

    if _AUDIO_CONFIGURED:
        return _AUDIO_PAIR

    # Headless Raspberry Pi guard: no PipeWire/PulseAudio on a Pi with
    # the Whisplay HAT. The WM8960 I2S codec is a plain ALSA device
    # discovered at runtime by pi.whisplay_audio — nothing to reconfigure
    # here. Bail out before the desktop-oriented pactl/probe logic tries
    # to talk to PipeWire (which doesn't exist on headless Pi).
    import platform as _p
    if _p.machine().startswith(("arm", "aarch64")):
        try:
            import subprocess as _sp
            _sp.run(["pactl", "info"], capture_output=True, timeout=1)
        except Exception:
            # No pipewire on this Pi — expected for headless setup.
            print("[linux_shim] configure_audio_devices: headless Pi detected, skipping")
            _AUDIO_CONFIGURED = True
            return _AUDIO_PAIR

    # Probe in daemon worker threads so timeouts work from any thread
    # (signal.alarm only works on the main interpreter thread, and this runs
    # inside the app's runner thread at boot).
    import threading

    try:
        def _probe(dev_idx: int, want_input: bool, sample_rate: int) -> bool:
            result: dict = {}

            def _open():
                try:
                    if want_input:
                        with sd.InputStream(device=dev_idx, samplerate=sample_rate,
                                            channels=1, dtype="int16", blocksize=1024):
                            pass
                    else:
                        with sd.OutputStream(device=dev_idx, samplerate=sample_rate,
                                             channels=1, dtype="int16", blocksize=1024):
                            pass
                    result["ok"] = True
                except Exception:
                    result["ok"] = False

            worker = threading.Thread(target=_open, daemon=True)
            worker.start()
            worker.join(timeout=3)
            return bool(result.get("ok"))

        devs = sd.query_devices()

        def _resolve(spec: str):
            if not spec:
                return None
            if spec.isdigit():
                idx = int(spec)
                return idx if 0 <= idx < len(devs) else None
            low = spec.lower()
            for i, d in enumerate(devs):
                if low in (d.get("name") or "").lower():
                    return i
            return None

        default = sd.default.device
        default_in = default[0] if isinstance(default, (tuple, list)) else default
        default_out = default[1] if isinstance(default, (tuple, list)) else default

        chosen_in = _resolve(os.environ.get("ALMIGHTY_AUDIO_IN", "").strip())
        chosen_out = _resolve(os.environ.get("ALMIGHTY_AUDIO_OUT", "").strip())

        def _best(want_input: bool):
            rate = send_rate if want_input else recv_rate
            default_idx = default_in if want_input else default_out
            cands = []
            for i, d in enumerate(devs):
                chans = d.get("max_input_channels" if want_input else "max_output_channels", 0)
                if chans < 1 or not _probe(i, want_input, rate):
                    continue
                name = (d.get("name") or "").lower()
                # Prefer the ALSA plug layer (sysdefault/pipewire): it does
                # automatic rate conversion AND routes through the PipeWire
                # graph to whatever sink the user has active (volume/mute
                # respected). Raw hw: devices (e.g. the NVidia HDMI card)
                # reject the Live rates with paInvalidSampleRate.
                if "sysdefault" in name:
                    score = 100
                elif "pipewire" in name:
                    score = 90
                else:
                    score = 0
                    if i == default_idx:
                        score += 50  # fallback only if no plug layer works
                cands.append((score, i, d.get("name", "")))
            return cands

        if chosen_in is None:
            cands = _best(True)
            chosen_in = max(cands)[1] if cands else default_in
        if chosen_out is None:
            cands = _best(False)
            chosen_out = max(cands)[1] if cands else default_out

        if chosen_in is not None and chosen_out is not None:
            _AUDIO_PAIR = (chosen_in, chosen_out)
            sd.default.device = _AUDIO_PAIR
            print(f"[{__name__}] audio devices -> in={chosen_in} ({devs[chosen_in]['name']}) "
                  f"out={chosen_out} ({devs[chosen_out]['name']})")
    except Exception as exc:
        print(f"[{__name__}] audio device auto-config skipped: {exc}")
    finally:
        _AUDIO_CONFIGURED = True

    return _AUDIO_PAIR
