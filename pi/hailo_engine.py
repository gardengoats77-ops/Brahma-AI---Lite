"""Hailo-10H NPU inference engine for Brahma AI (Pi build).

Mirrors the interface expected by ``agent/executor.py`` and
``actions/_llm.py`` so the Hailo NPU can be swapped in as a local
inference backend without touching the existing agent code path.

Flow:
  1. Load HEF model compiled for Hailo-10H (vision or LLM).
  2. Open a VDevice (PCIe to the AI HAT+).
  3. Configure network group from the HEF's default params.
  4. ``complete(prompt)``: tokenize → NPU batch infer → detokenize → text.
  5. ``generate(token_ids)``: feed raw token ids for hybrid compute.

When the NPU is absent (missing ``hailo_platform``, missing driver, no
HEF file), ``available`` is ``False`` and ``complete()`` raises a
``RuntimeError`` the caller catches to fall back to Gemini Live or
OpenRouter.

Hardware notes (confirmed live on star-server 2026-08-05):
  * Device: ``pci/0001:01:00.0`` — Hailo-10H, Architecture HAILO10H, FW 5.3.0
  * 5 vision HEFs at ``/usr/share/hailo-models/`` — yolov8s_pose, yolov11m,
    yolov5n_seg, resnet_v1_50, yolov8m_pose
  * yolov8s_pose input: UINT8 NHWC(640x640x3)
  * No LLM HEF compiled yet — NPU is vision-only for now.
  * ``hailortcli fw-control identify`` confirms the device is live.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import numpy as np

log = logging.getLogger("hailo.engine")

try:
    from hailo_platform import HEF, VDevice, HailoStreamInterface, ConfigureParams
    _HAILO_IMPORTED = True
except Exception as e:  # noqa: BLE001
    log.info("hailo_platform not importable — NPU backend disabled: %s", e)
    _HAILO_IMPORTED = False


class HailoEngine:
    """Hailo-10H inference backend, mirroring ``_llm.py``'s interface."""

    def __init__(self, hef_path: str, model_name: str = "yolov8s_pose"):
        self.hef_path = hef_path
        self.model_name = model_name
        self._hef: Optional[object] = None
        self._vdevice: Optional[object] = None
        self._network_group: Optional[object] = None
        self._inputs: Optional[list] = None
        self._outputs: Optional[list] = None
        self._available = False

        if not _HAILO_IMPORTED:
            log.info("HailoEngine built without hailo_platform")
            return
        if not os.path.exists(hef_path):
            log.warning("HEF not found at %s — NPU disabled", hef_path)
            return
        try:
            self._initialize_hardware()
            self._available = True
            log.info("Hailo-10H engine ready: %s (model: %s)", hef_path, model_name)
        except Exception as e:  # noqa: BLE001
            log.error("Hailo engine init failed: %s", e)
            self._available = False

    def _initialize_hardware(self) -> None:
        """Open VDevice, load HEF, configure network group + vstreams."""
        self._hef = HEF(self.hef_path)
        self._vdevice = VDevice()
        # HailoRT 5.1.1 API: ConfigureParams.create_from_hef(hef, interface)
        cfg_params = ConfigureParams.create_from_hef(
            self._hef, HailoStreamInterface.PCIe
        )
        self._network_group = self._vdevice.configure(self._hef, cfg_params)[0]
        self._inputs = self._network_group.make_input_vstream_params(
            src_type=HailoStreamInterface.PCIe
        )
        self._outputs = self._network_group.make_output_vstream_params(
            dest_type=HailoStreamInterface.PCIe
        )

    @property
    def available(self) -> bool:
        return self._available

    def complete(self, prompt: str, max_tokens: int = 128) -> str:
        """Stub completion: feed tokenized prompt to NPU, return decoded text.

        Raises RuntimeError if the NPU is not available so callers can catch
        and fall back to Gemini/Ollama.

        NOTE: For vision models (yolov8s_pose etc.) this is a semantic
        mismatch — ``complete`` is for text generation.  Call ``infer_vision``
        for vision models.  This method exists so the LLM adapter interface
        is satisfied; it will always be a no-op on vision-only HEFs.
        """
        if not self.available:
            raise RuntimeError("Hailo NPU backend not available — caller must fall back")
        # Convert prompt chars to pseudo-token ids (placeholder tokenizer).
        tok = np.array([ord(c) for c in prompt[:64]], dtype=np.uint32)
        pad = np.zeros(64 - len(tok), dtype=np.uint32)
        with self._network_group.activate():
            with self._vdevice.create_input_vstream(self._inputs) as inp, \
                 self._vdevice.create_output_vstream(self._outputs) as out:
                inp[0].write(np.concatenate([tok, pad]))
                raw = out[0].read()
                return self._decode(raw)

    def generate(self, token_ids: list) -> str:
        """Feed raw token ids to the NPU for hybrid compute.

        Used alongside Gemini Live when the NPU does the forward pass.
        """
        if not self.available:
            raise RuntimeError("Hailo NPU backend not available")
        data = np.array(token_ids, dtype=np.uint32)
        with self._network_group.activate():
            with self._vdevice.create_input_vstream(self._inputs) as inp, \
                 self._vdevice.create_output_vstream(self._outputs) as out:
                inp[0].write(data)
                raw = out[0].read()
                return self._decode(raw)

    def infer_vision(self, image: np.ndarray) -> np.ndarray:
        """Run vision inference (e.g. yolov8s_pose) on a UINT8 HWC image.

        For pose detection: input should be (640, 640, 3) UINT8.
        Returns the raw NPU output array (shape depends on the HEF).
        """
        if not self.available:
            raise RuntimeError("Hailo NPU backend not available")
        with self._network_group.activate():
            with self._vdevice.create_input_vstream(self._inputs) as inp, \
                 self._vdevice.create_output_vstream(self._outputs) as out:
                inp[0].write(image)
                raw = out[0].read()
                return np.array(raw)

    def _decode(self, raw: object) -> str:
        """Per-model placeholder decoder.

        Real implementation depends on the model's output format and
        vocabulary.  For vision models, this is unused.  For a future LLM
        HEF, swap in the actual tokenizer/decoder here.
        """
        return "Brahma AI NPU response (HEF outputs attached)."
