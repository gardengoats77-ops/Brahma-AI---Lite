# tests/test_llm_adapter.py
"""Tests for the Hailo NPU -> _llm.py interface adapter.

The adapter wraps HailoEngine in the shape expected by actions/_llm.py:
``model = gemini("model_name")`` -> ``response = model.generate_content(prompt)``.

When the NPU is unavailable (missing hailo_platform, no HEF), the adapter
must report unavailable and generate_content must raise so callers fall
back to Gemini or Ollama.

NOTE: We do NOT mock sys.modules['hailo_platform'] here because doing so
corrupts numpy's module state on some platforms. Instead we rely on the
fact that on the dev machine hailo_platform is not installed, so
HailoEngine naturally reports unavailable=True. On the Pi, hailo_platform
IS installed and the .hef path determines availability.
"""
from pi.llm_adapter import HailoLLMAdapter


def test_adapter_reports_unavailable_without_hef():
    """When the HEF file doesn't exist, adapter.is_available() must be False."""
    adapter = HailoLLMAdapter(hef_path="/nonexistent/x.hef", model="yolov8s_pose")
    assert adapter.is_available() is False


def test_adapter_generate_content_raises_when_unavailable():
    """generate_content must raise RuntimeError when NPU is not available."""
    adapter = HailoLLMAdapter(hef_path="/nonexistent/x.hef", model="yolov8s_pose")
    assert adapter.is_available() is False
    try:
        adapter.generate_content("hello")
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "not available" in str(e).lower() or "npu" in str(e).lower()


def test_adapter_generate_content_returns_text_when_available():
    """When the NPU is available, generate_content must return a response with .text."""
    adapter = HailoLLMAdapter(hef_path="/nonexistent/x.hef", model="test")

    # Force available=True via a mock engine
    class FakeEngine:
        available = True
        def complete(self, prompt: str, max_tokens: int = 128) -> str:
            return "NPU says: " + prompt

    adapter._engine = FakeEngine()
    assert adapter.is_available() is True
    response = adapter.generate_content("hello world")
    assert isinstance(response.text, str)
    assert "NPU says" in response.text


def test_adapter_handles_list_contents():
    """generate_content should flatten a list of text parts into a prompt."""
    adapter = HailoLLMAdapter(hef_path="/nonexistent/x.hef", model="test")

    class FakeEngine:
        available = True
        def complete(self, prompt: str, max_tokens: int = 128) -> str:
            return f"processed: {prompt}"

    adapter._engine = FakeEngine()
    response = adapter.generate_content(["hello", "world"])
    assert "processed" in response.text
    assert "hello" in response.text
    assert "world" in response.text
