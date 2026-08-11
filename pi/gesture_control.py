"""Phase 9.1: Pose Detection → Voice Trigger.

Uses the Hailo-10H NPU (via HailoEngine) to run YOLOv8-pose inference
and detect hand/body gestures. Detected gestures fire callbacks that
trigger voice commands — gesture control without camera streaming.

Architecture:
  1. HailoEngine runs yolov8s_pose HEF on a single frame (no stream).
  2. Raw NPU output is parsed into COCO 17-keypoint poses.
  3. GestureController classifies poses into named gestures.
  4. Callbacks fire to trigger voice commands ("Hey Rex, wave detected").

Gestures detected:
  - wave:       wrist raised well above shoulder (arm extended up)
  - thumbs_up:  wrist raised near shoulder height, arm bent
  - stop:       both wrists raised above shoulders, palms forward

When the NPU is absent (no hailo_platform, no HEF, or hardware not
enumerating), the controller reports available=False and callbacks
are never fired — graceful degradation.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

import numpy as np

log = logging.getLogger("hailo.gesture")

# ── COCO 17-keypoint indices ────────────────────────────────────────────────
# YOLOv8-pose outputs keypoints in COCO order:
#   0=nose 1=L_eye 2=R_eye 3=L_ear 4=R_ear
#   5=L_shoulder 6=R_shoulder
#   7=L_elbow 8=R_elbow
#   9=L_wrist 10=R_wrist
#   11=L_hip 12=R_hip
#   13=L_knee 14=R_knee
#   15=L_ankle 16=R_ankle
NOSE = 0
L_SHOULDER = 5
R_SHOULDER = 6
L_ELBOW = 7
R_ELBOW = 8
L_WRIST = 9
R_WRIST = 10

# ── Detection thresholds (normalized 0..1 image coordinates) ───────────────
# In image space y=0 is top, y=1 is bottom → smaller y = higher in frame.
WAVE_THRESHOLD = 0.15        # wrist must be ≥0.15 above shoulder to count as wave
THUMBS_UP_MIN = 0.03         # wrist just above shoulder (but below wave threshold)
STOP_THRESHOLD = 0.10        # both wrists must be ≥0.10 above their shoulders
CONFIDENCE_THRESHOLD = 0.3   # minimum keypoint confidence to trust a joint


class GestureController:
    """Detect gestures from YOLO-pose keypoints and fire voice-command callbacks.

    The controller can be used in two modes:

    1. **Direct keypoint mode** (unit testing, external pose providers):
       Call ``_detect_gesture(keypoints)`` with a (17, 3) COCO keypoint array.

    2. **NPU inference mode** (production on Pi with Hailo-10H):
       Call ``detect_from_image(image)`` with a UINT8 HWC frame. The
       controller feeds the frame through the HailoEngine and parses
       the output into keypoints internally.

    Args:
        engine: HailoEngine instance for NPU inference. If None, the
            controller reports ``available=False`` and inference is
            disabled, but ``_detect_gesture`` still works for testing.
        on_wave: Callback fired with gesture name when wave is detected.
        on_thumbs_up: Callback fired with gesture name when thumbs-up detected.
        on_stop: Callback fired with gesture name when stop detected.
    """

    def __init__(
        self,
        engine: Optional[object] = None,
        on_wave: Optional[Callable[[str], None]] = None,
        on_thumbs_up: Optional[Callable[[str], None]] = None,
        on_stop: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._engine = engine
        self._on_wave = on_wave
        self._on_thumbs_up = on_thumbs_up
        self._on_stop = on_stop

    @property
    def available(self) -> bool:
        """True if the NPU engine is ready for inference."""
        return self._engine is not None and getattr(self._engine, "available", False)

    # ── Public API ──────────────────────────────────────────────────────────

    def detect_from_image(self, image: np.ndarray) -> Optional[str]:
        """Run pose inference on an image frame and detect gesture.

        Args:
            image: UINT8 HWC image (640x640x3) for yolov8s_pose.

        Returns:
            Gesture name ("wave", "thumbs_up", "stop") or None.
        """
        if not self.available:
            return None

        try:
            raw = self._engine.infer_vision(image)
            keypoints = self._parse_yolo_pose_output(raw)
            if keypoints is None:
                return None
            return self._detect_gesture(keypoints)
        except Exception as e:  # noqa: BLE001
            log.debug("Gesture inference failed: %s", e)
            return None

    # ── Internal: keypoint parsing ─────────────────────────────────────────

    @staticmethod
    def _parse_yolo_pose_output(raw: np.ndarray) -> Optional[np.ndarray]:
        """Parse raw YOLOv8-pose NPU output into (17, 3) COCO keypoints.

        YOLOv8-pose output format (per detection):
          [x_center, y_center, width, height, object_conf, class_id,
           kpt0_x, kpt0_y, kpt0_conf, kpt1_x, kpt1_y, kpt1_conf, ...]

        We take the detection with the highest object confidence and
        extract its 17 keypoints. Returns None if no valid detection.
        """
        try:
            dets = np.asarray(raw)
            # Flatten to (N, 5+3*17) = (N, 56) — handle various output shapes
            if dets.ndim == 3:
                dets = dets.reshape(-1, dets.shape[-1])
            elif dets.ndim == 1:
                dets = dets.reshape(1, -1)

            if dets.shape[1] < 5 + 3 * 17:
                log.debug("YOLO-pose output too narrow: %s", dets.shape)
                return None

            # Column layout: [0..4] = bbox+conf+class, [5..55] = 17 keypoints * 3
            obj_confs = dets[:, 4]
            best_idx = int(np.argmax(obj_confs))
            best = dets[best_idx]

            keypoints = np.zeros((17, 3), dtype=np.float32)
            for i in range(17):
                col = 5 + i * 3
                keypoints[i, 0] = best[col]      # x
                keypoints[i, 1] = best[col + 1]  # y
                keypoints[i, 2] = best[col + 2]  # confidence
            return keypoints
        except Exception as e:  # noqa: BLE001
            log.debug("Failed to parse YOLO-pose output: %s", e)
            return None

    # ── Internal: gesture classification ────────────────────────────────────

    def _detect_gesture(self, keypoints: np.ndarray) -> Optional[str]:
        """Classify a COCO 17-keypoint pose into a named gesture.

        Args:
            keypoints: Array of shape (17, 3) with [x, y, confidence] per joint.

        Returns:
            Gesture name ("wave", "thumbs_up", "stop") or None if no gesture.
        """
        if keypoints is None or len(keypoints) < 17:
            return None

        kp = keypoints

        def _conf(idx: int) -> float:
            return float(kp[idx][2])

        def _y(idx: int) -> float:
            return float(kp[idx][1])

        # ── Check which wrists are raised above their shoulders ─────────────
        l_shoulder_conf = _conf(L_SHOULDER)
        r_shoulder_conf = _conf(R_SHOULDER)
        l_wrist_conf = _conf(L_WRIST)
        r_wrist_conf = _conf(R_WRIST)

        l_raised = (
            l_shoulder_conf > CONFIDENCE_THRESHOLD
            and l_wrist_conf > CONFIDENCE_THRESHOLD
            and _y(L_WRIST) < _y(L_SHOULDER) - STOP_THRESHOLD
        )
        r_raised = (
            r_shoulder_conf > CONFIDENCE_THRESHOLD
            and r_wrist_conf > CONFIDENCE_THRESHOLD
            and _y(R_WRIST) < _y(R_SHOULDER) - STOP_THRESHOLD
        )

        # ── Stop: both wrists raised above shoulders ────────────────────────
        if l_raised and r_raised:
            if self._on_stop:
                self._on_stop("stop")
            return "stop"

        # ── Check individual arms for wave / thumbs_up ──────────────────────
        # Right arm
        if r_shoulder_conf > CONFIDENCE_THRESHOLD and r_wrist_conf > CONFIDENCE_THRESHOLD:
            shoulder_y = _y(R_SHOULDER)
            wrist_y = _y(R_WRIST)
            diff = shoulder_y - wrist_y

            if diff > WAVE_THRESHOLD:
                if self._on_wave:
                    self._on_wave("wave")
                return "wave"
            elif diff > THUMBS_UP_MIN:
                # Thumbs up: wrist above elbow (arm bent, hand near shoulder)
                elbow_conf = _conf(R_ELBOW)
                if elbow_conf > CONFIDENCE_THRESHOLD and wrist_y < _y(R_ELBOW):
                    if self._on_thumbs_up:
                        self._on_thumbs_up("thumbs_up")
                    return "thumbs_up"

        # Left arm
        if l_shoulder_conf > CONFIDENCE_THRESHOLD and l_wrist_conf > CONFIDENCE_THRESHOLD:
            shoulder_y = _y(L_SHOULDER)
            wrist_y = _y(L_WRIST)
            diff = shoulder_y - wrist_y

            if diff > WAVE_THRESHOLD:
                if self._on_wave:
                    self._on_wave("wave")
                return "wave"
            elif diff > THUMBS_UP_MIN:
                elbow_conf = _conf(L_ELBOW)
                if elbow_conf > CONFIDENCE_THRESHOLD and wrist_y < _y(L_ELBOW):
                    if self._on_thumbs_up:
                        self._on_thumbs_up("thumbs_up")
                    return "thumbs_up"

        return None