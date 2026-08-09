"""Linux/macOS compatibility: the app must import with Windows-only modules stubbed.

Almighty AI is Windows-first.  ``linux_shim`` pre-registers inert stubs
for the Windows-only modules (pyaudio, pycaw, comtypes, pywinauto,
win10toast) so the rest of the app can import and run its cross-platform
parts (UI, dashboard, TTS, ...) on Linux/macOS.

These tests verify:

  * every ``actions/*`` module, plus ``ui`` and ``main``, imports cleanly on
    non-Windows platforms;
  * the stubs remain the ``linux_shim`` stubs in ``sys.modules`` — no real
    Windows-only module leaks through;
  * ``winreg`` is deliberately NOT stubbed (regression guard: the stdlib
    ``mimetypes`` module probes ``import winreg`` and would crash on a fake
    module);
  * using a stub member raises ``RuntimeError`` so the app's existing
    ``try/except`` fallbacks take over.
"""

import importlib
import mimetypes
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Modules that exist only on Windows and are stubbed by linux_shim.
WINDOWS_ONLY_STUBS = (
    "pyaudio",
    "pycaw",
    "pycaw.pycaw",
    "comtypes",
    "pywinauto",
    "pywinauto.findwindows",
    "win10toast",
)

on_non_windows = pytest.mark.skipif(
    os.name == "nt",
    reason="linux_shim is a no-op on Windows",
)


def _require_linux_shim():
    """Import the shim (it self-installs stubs on import) and return it."""
    # Some app modules (ui/styles, ui/widgets, main) want a display.  When
    # running over SSH there is no DISPLAY env var even though an X server
    # exists on :0 — detect it from the X11 socket so the suite passes on
    # the headless-VPN Pi as well as the desktop box.
    if "DISPLAY" not in os.environ and os.path.exists("/tmp/.X11-unix/X0"):
        os.environ["DISPLAY"] = ":0"
    import linux_shim  # noqa: F401
    return sys.modules["linux_shim"]


def _all_app_modules() -> list[str]:
    """Every module the app wires together: ui, main, and all actions/*."""
    names = ["ui", "main"]
    for path in sorted((ROOT / "actions").glob("*.py")):
        if path.name.startswith("_"):
            continue
        names.append(f"actions.{path.stem}")
    return names


@on_non_windows
def test_every_app_module_imports_cleanly():
    """ui, main, and every actions/* module import with the shim active."""
    _require_linux_shim()
    for name in _all_app_modules():
        importlib.import_module(name)


@on_non_windows
def test_no_windows_only_module_leaks_through():
    """After importing the whole app, stubs are still the linux_shim stubs."""
    _require_linux_shim()
    for name in _all_app_modules():
        importlib.import_module(name)

    for name in WINDOWS_ONLY_STUBS:
        mod = sys.modules.get(name)
        assert mod is not None, f"{name} was not stubbed in sys.modules"
        doc = getattr(mod, "__doc__", "") or ""
        assert "linux_shim" in doc, (
            f"{name} is not the linux_shim stub — a real Windows-only "
            f"module leaked through: {doc!r}"
        )


@on_non_windows
def test_winreg_is_not_stubbed():
    """Regression guard: stubbing winreg crashes stdlib mimetypes."""
    _require_linux_shim()
    assert "winreg" not in sys.modules, (
        "winreg must not be stubbed — stdlib mimetypes probes it and would "
        "call into a fake module, crashing import."
    )
    with pytest.raises(ImportError):
        import winreg  # noqa: F401  (real module only exists on Windows)

    # The exact failure previously hit: mimetypes._read_windows_registry.
    guessed, _encoding = mimetypes.guess_type("notes.txt")
    assert guessed == "text/plain"


@on_non_windows
def test_stub_members_raise_for_fallbacks():
    """Stub members raise RuntimeError so the app's except blocks run."""
    _require_linux_shim()

    import pyaudio
    from comtypes import CLSCTX_ALL
    from pywinauto import Application
    from win10toast import ToastNotifier

    assert CLSCTX_ALL == 0x17  # real comtypes constant, kept for compatibility

    for ctor in (pyaudio.PyAudio, Application, ToastNotifier):
        with pytest.raises(RuntimeError):
            ctor()
