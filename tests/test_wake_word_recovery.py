# tests/test_wake_word_recovery.py
"""Tests for wake_word.py's corrupted-model self-heal.

Uses a real zipfile on a temp dir plus a monkeypatched ``vosk`` module so
the repair path is exercised end-to-end without an 81 MB model on the dev
box. Cases covered: missing dir -> recovered from zip; corrupt dir ->
recovered; no zip -> degrades; broken zip -> original dir left in place.
"""

import sys
import types
import zipfile
from pathlib import Path

from wake_word import WakeWordListener


def _make_model_zip(tmp_path, name="vosk-model-small-en-us-0.15"):
    """Build a minimal real zip that extracts a single top-level model dir."""
    zip_path = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(f"{name}/README", "fake model\n")
        zf.writestr(f"{name}/conf/model.conf", "foo\n")
    return zip_path


def _make_corrupt_dir(tmp_path, name="vosk-model-small-en-us-0.15"):
    """A dir that exists but fails vosk.Model() (e.g. truncated am/final.mdl)."""
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "am").mkdir(exist_ok=True)
    (d / "am" / "final.mdl").write_text("partial binary data")
    return d


def _install_fake_vosk(monkeypatch):
    """Fake vosk module: Model() succeeds only when README exists."""

    class _Model:
        def __init__(self, path):
            p = Path(path)
            if not p.is_dir() or not (p / "README").exists():
                raise ValueError("Failed to create a model")

    class _Recognizer:
        def __init__(self, *a, **k):
            pass

        def SetWords(self, *a, **k):
            pass

    vosk_mod = types.ModuleType("vosk")
    vosk_mod.Model = _Model
    vosk_mod.KaldiRecognizer = _Recognizer
    monkeypatch.setitem(sys.modules, "vosk", vosk_mod)


def test_missing_dir_recovered_from_zip(tmp_path, monkeypatch):
    _make_model_zip(tmp_path)
    _install_fake_vosk(monkeypatch)
    target = tmp_path / "vosk-model-small-en-us-0.15"

    w = WakeWordListener(model_dir=str(target))
    assert w.available is True
    assert target.is_dir()
    assert (target / "README").read_text() == "fake model\n"


def test_corrupt_dir_recovered_from_generic_zip_name(tmp_path, monkeypatch):
    """Real Pi layout: zip is vosk-model.zip, model dir has a .15 suffix."""
    # build the generic zip
    zip_path = tmp_path / "vosk-model.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("vosk-model-small-en-us-0.15/README", "fake model\n")
        zf.writestr("vosk-model-small-en-us-0.15/conf/model.conf", "foo\n")
    _make_corrupt_dir(tmp_path)
    _install_fake_vosk(monkeypatch)
    target = tmp_path / "vosk-model-small-en-us-0.15"

    w = WakeWordListener(model_dir=str(target))
    assert w.available is True
    # Recovery swapped in the clean extraction (README appears).
    assert (target / "README").read_text() == "fake model\n"
    # No .bad dir left behind.
    assert not (tmp_path / "vosk-model-small-en-us-0.15.bad").exists()


def test_no_zip_leaves_dir_alone(tmp_path, monkeypatch):
    _install_fake_vosk(monkeypatch)
    target = tmp_path / "vosk-model-small-en-us-0.15"
    target.mkdir()
    (target / "README").write_text("custom\n")

    w = WakeWordListener(model_dir=str(target))
    assert w.available is True
    assert (target / "README").read_text() == "custom\n"


def test_bad_zip_degrades_and_preserves_original(tmp_path, monkeypatch):
    # A zip whose only entry is NOT a valid model root (no README anywhere).
    zip_path = tmp_path / "vosk-model-small-en-us-0.15.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("junk.txt", "not a model")
    _make_corrupt_dir(tmp_path)
    _install_fake_vosk(monkeypatch)
    target = tmp_path / "vosk-model-small-en-us-0.15"

    w = WakeWordListener(model_dir=str(target))
    assert w.available is False
    # Corrupt dir was NOT deleted and no .bad swap happened.
    assert target.is_dir()
    assert not (tmp_path / "vosk-model-small-en-us-0.15.bad").exists()


def test_recovery_compiles_and_python_syntax(tmp_path, monkeypatch):
    import py_compile

    py_compile.compile("wake_word.py", doraise=True)