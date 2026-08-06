"""Adapter that lets the Hailo NPU satisfy the ``actions/_llm.py`` interface.

The existing code path calls:
    model = gemini("model_name")
    response = model.generate_content(contents)

and expects a response object with ``.text`` or ``.candidates``.

This adapter provides the same ``.generate_content(contents)`` interface
backed by HailoEngine. It takes the same ``contents`` argument (string
or multimodal list), flattens it to a prompt string, calls
``HailoEngine.complete``, and wraps the result in a response object with
a ``.text`` attribute — the shape most call sites actually use.

Usage in ``actions/_llm.py``::

    def gemini(model_name, system_instruction=None):
        # Try Hailo NPU first (if available + hef configured)
        hef = os.environ.get("BRAHMA_HEF_PATH")
        if hef and os.path.exists(hef):
            from pi.llm_adapter import HailoLLMAdapter
            adapter = HailoLLMAdapter(hef_path=hef, model=model_name)
            if adapter.is_available():
                return adapter
        # Fall through to Gemini Live / OpenRouter as before
        ...
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from pi.hailo_engine import HailoEngine

log = logging.getLogger("hailo.adapter")


@dataclass
class _NPUResponse:
    """Minimal response object mirroring Gemini's response.text shape."""
    text: str
    # candidates left as None — most call sites only read .text


class HailoLLMAdapter:
    """Wraps HailoEngine in the _llm.gemini() -> generate_content() shape."""

    def __init__(self, hef_path: str, model: str = "yolov8s_pose"):
        self._engine = HailoEngine(hef_path=hef_path, model_name=model)

    def is_available(self) -> bool:
        return self._engine.available

    def generate_content(self, contents, **kwargs) -> _NPUResponse:
        """Mimic google-genai's generate_content interface.

        ``contents`` can be:
          - a string (most common)
          - a list of parts (multimodal) — we extract text parts

        Returns a response object with ``.text`` — the shape every call
        site in actions/* reads via ``response.text``.
        """
        if not self.is_available():
            raise RuntimeError("Hailo NPU not available")

        # Flatten contents to a prompt string.
        if isinstance(contents, str):
            prompt = contents
        elif isinstance(contents, list):
            parts = []
            for item in contents:
                if isinstance(item, str):
                    parts.append(item)
                elif hasattr(item, "text"):
                    parts.append(item.text)
                elif isinstance(item, dict) and "text" in item:
                    parts.append(item["text"])
            prompt = "\n".join(parts)
        else:
            prompt = str(contents)

        result = self._engine.complete(prompt)
        return _NPUResponse(text=result if isinstance(result, str) else str(result))
