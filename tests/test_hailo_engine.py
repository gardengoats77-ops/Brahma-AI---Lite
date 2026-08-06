# tests/test_hailo_engine.py
"""Tests for the Hailo-10H NPU inference engine.

When the Hailo NPU is absent (missing hailo_platform, no HEF file, or
hardware not enumerating), the engine must report available=False and
complete() must raise a catchable RuntimeError so the caller can fall
back to Gemini Live or Ollama.
"""
import os
from unittest import mock


def test_hailo_engine_falls_back_without_hardware():
    """When hailo_platform cannot be imported, engine must construct and report unavailable."""
    with mock.patch.dict("sys.modules", {"hailo_platform": None}):
        from pi.hailo_engine import HailoEngine
        eng = HailoEngine(hef_path="/nonexistent/brahma.hef", model_name="yolov8s_pose")
        assert eng.available is False


def test_hailo_engine_unavailable_when_hef_missing():
    """When hailo_platform imports but HEF file doesn't exist, must report unavailable."""
    fake_hailo = mock.MagicMock()
    fake_hailo.HEF = mock.MagicMock()
    fake_hailo.VDevice = mock.MagicMock()
    fake_hailo.HailoStreamInterface = mock.MagicMock()
    fake_hailo.ConfigureContext = mock.MagicMock()
    with mock.patch.dict("sys.modules", {"hailo_platform": fake_hailo}):
        from pi.hailo_engine import HailoEngine
        eng = HailoEngine(hef_path="/nonexistent/model.hef", model_name="test")
        assert eng.available is False


def test_hailo_engine_complete_raises_when_unavailable():
    """complete() must raise RuntimeError when NPU is not available."""
    with mock.patch.dict("sys.modules", {"hailo_platform": None}):
        from pi.hailo_engine import HailoEngine
        eng = HailoEngine(hef_path="/nonexistent/model.hef", model_name="test")
        try:
            eng.complete("hello")
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "not available" in str(e).lower() or "npu" in str(e).lower()


def test_hailo_engine_generate_raises_when_unavailable():
    """generate() must raise RuntimeError when NPU is not available."""
    with mock.patch.dict("sys.modules", {"hailo_platform": None}):
        from pi.hailo_engine import HailoEngine
        eng = HailoEngine(hef_path="/nonexistent/model.hef", model_name="test")
        try:
            eng.generate([1, 2, 3])
            assert False, "Should have raised RuntimeError"
        except RuntimeError:
            pass
