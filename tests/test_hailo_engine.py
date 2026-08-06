# tests/test_hailo_engine.py
"""Tests for the Hailo-10H NPU inference engine.

When the Hailo NPU is absent (missing hailo_platform, no HEF file, or
hardware not enumerating), the engine must report available=False and
complete() must raise a catchable RuntimeError so the caller can fall
back to Gemini Live or Ollama.
"""
import importlib
import sys
from unittest import mock

import pi.hailo_engine  # noqa: F401 — ensure module is importable before reloads


def _reload_engine_under_mocked_hailo(hailo_platform_value):
    """Reload pi.hailo_engine with ``hailo_platform`` stubbed in sys.modules.

    The Engine reads ``hailo_platform`` at import time (try/except), so to test
    branch coverage we have to invalidate and re-execute the module body. A
    plain ``from pi.hailo_engine import HailoEngine`` inside the patch context
    returns the cached module and bypasses the hailo_platform try/except;
    ``importlib.reload`` runs the body again under the patched sys.modules.

    The double-numpy ImportError that used to hit these tests when run in
    isolation was caused by re-importing without proper invalidation.
    """
    with mock.patch.dict("sys.modules", {"hailo_platform": hailo_platform_value}):
        # Drop any cached pi.hailo_engine so the reload executes the body
        # fresh under the patched sys.modules.
        sys.modules.pop("pi.hailo_engine", None)
        mod = importlib.import_module("pi.hailo_engine")
        return mod.HailoEngine


def test_hailo_engine_falls_back_without_hardware():
    """When hailo_platform cannot be imported, engine must construct and report unavailable."""
    HailoEngine = _reload_engine_under_mocked_hailo(None)
    eng = HailoEngine(hef_path="/nonexistent/brahma.hef", model_name="yolov8s_pose")
    assert eng.available is False


def test_hailo_engine_unavailable_when_hef_missing():
    """When hailo_platform imports but HEF file doesn't exist, must report unavailable."""
    fake_hailo = mock.MagicMock()
    fake_hailo.HEF = mock.MagicMock()
    fake_hailo.VDevice = mock.MagicMock()
    fake_hailo.HailoStreamInterface = mock.MagicMock()
    fake_hailo.ConfigureParams = mock.MagicMock()
    HailoEngine = _reload_engine_under_mocked_hailo(fake_hailo)
    eng = HailoEngine(hef_path="/nonexistent/model.hef", model_name="test")
    assert eng.available is False


def test_hailo_engine_complete_raises_when_unavailable():
    """complete() must raise RuntimeError when NPU is not available."""
    HailoEngine = _reload_engine_under_mocked_hailo(None)
    eng = HailoEngine(hef_path="/nonexistent/model.hef", model_name="test")
    try:
        eng.complete("hello")
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "not available" in str(e).lower() or "npu" in str(e).lower()


def test_hailo_engine_generate_raises_when_unavailable():
    """generate() must raise RuntimeError when NPU is not available."""
    HailoEngine = _reload_engine_under_mocked_hailo(None)
    eng = HailoEngine(hef_path="/nonexistent/model.hef", model_name="test")
    try:
        eng.generate([1, 2, 3])
        assert False, "Should have raised RuntimeError"
    except RuntimeError:
        pass
