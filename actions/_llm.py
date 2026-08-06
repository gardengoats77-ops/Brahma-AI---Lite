# actions/_llm.py — google-genai adapter for legacy-style call sites.
#
# The Lite app historically used the deprecated `google-generativeai` SDK
# (genai.configure + genai.GenerativeModel(...).generate_content(...)). This
# adapter exposes the same `model.generate_content(contents)` shape backed by
# the current `google-genai` package (client.models.generate_content), so the
# ~20 call sites across agent/ and actions/ needed zero changes.

from __future__ import annotations

import json
import sys
from pathlib import Path

from google import genai
from google.genai import types

_client = None


def _api_key() -> str:
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).parent
    else:
        root = Path(__file__).resolve().parent.parent
    with open(root / "config" / "api_keys.json", "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def gemini(model_name: str, system_instruction: str | None = None):
    """Return an object with .generate_content(contents) on google-genai.

    Mirrors the legacy GenerativeModel construction: model name at creation,
    optional system_instruction folded into the per-call config.
    """
    global _client
    # ponytail: module-level singleton — an API-key change in api_keys.json
    # needs an app restart to take effect (config edits already broadly do).
    if _client is None:
        _client = genai.Client(api_key=_api_key())

    config = None
    if system_instruction:
        config = types.GenerateContentConfig(system_instruction=system_instruction)

    class _Model:
        def generate_content(self, contents, **kwargs):
            return _client.models.generate_content(
                model=model_name, contents=contents, config=config, **kwargs
            )

    return _Model()
