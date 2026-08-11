# tests/test_gesture_control.py
"""Tests for Phase 9.1: Pose Detection → Voice Trigger.

The GestureController uses the HailoEngine (YOLO pose HEF) to detect
hand/body gestures and fire callbacks that trigger voice commands.

Tests cover the gesture-detection logic in isolation by feeding
COCO-format keypoints directly — no Hailo NPU hardware required.
"""
import sys

import numpy as np


# ── Keypoint helpers ──────────────────────────────────────────────────────
# COCO 17-keypoint layout (YOLOv8-pose output order):
#   0=nose 1=left_eye 2=right_eye 3=left_ear 4=right_ear
#   5=left_shoulder 6=right_shoulder
#   7=left_elbow 8=right_elbow
#   9=left_wrist 10=right_wrist
#   11=left_hip 12=right_hip
#   13=left_knee 14=right_knee
#   15=left_ankle 16=right_ankle
# Each keypoint is [x, y, confidence] with normalized (0..1) coordinates.


def _base_pose() -> np.ndarray:
    """Return a neutral standing pose with all keypoints populated."""
    return np.array([
        [0.50, 0.50, 0.9],  # 0  nose
        [0.45, 0.45, 0.9],  # 1  left_eye
        [0.55, 0.45, 0.9],  # 2  right_eye
        [0.40, 0.45, 0.9],  # 3  left_ear
        [0.60, 0.45, 0.9],  # 4  right_ear
        [0.35, 0.60, 0.9],  # 5  left_shoulder
        [0.65, 0.60, 0.9],  # 6  right_shoulder
        [0.30, 0.70, 0.9],  # 7  left_elbow
        [0.70, 0.70, 0.9],  # 8  right_elbow
        [0.25, 0.80, 0.9],  # 9  left_wrist
        [0.75, 0.80, 0.9],  # 10 right_wrist
        [0.40, 0.80, 0.9],  # 11 left_hip
        [0.60, 0.80, 0.9],  # 12 right_hip
        [0.35, 0.95, 0.9],  # 13 left_knee
        [0.65, 0.95, 0.9],  # 14 right_knee
        [0.35, 1.00, 0.9],  # 15 left_ankle
        [0.65, 1.00, 0.9],  # 16 right_ankle
    ], dtype=np.float32)


def _with_right_arm(pose, elbow_y: float, wrist_y: float) -> np.ndarray:
    """Return a copy of pose with the right arm at the given positions."""
    p = pose.copy()
    p[8] = [0.70, elbow_y, 0.9]   # right_elbow
    p[10] = [0.70, wrist_y, 0.9]  # right_wrist
    return p


def _with_left_arm(pose, elbow_y: float, wrist_y: float) -> np.ndarray:
    """Return a copy of pose with the left arm at the given positions."""
    p = pose.copy()
    p[7] = [0.30, elbow_y, 0.9]   # left_elbow
    p[9] = [0.30, wrist_y, 0.9]   # left_wrist
    return p


def _with_both_arms(pose, left_elbow_y, left_wrist_y, right_elbow_y, right_wrist_y):
    """Return a copy with both arms positioned."""
    p = pose.copy()
    p[7] = [0.30, left_elbow_y, 0.9]
    p[9] = [0.30, left_wrist_y, 0.9]
    p[8] = [0.70, right_elbow_y, 0.9]
    p[10] = [0.70, right_wrist_y, 0.9]
    return p


# ── Import helper ───────────────────────────────────────────────────────────

def _import_gesture_control():
    """Import pi.gesture_control, reloading if already cached."""
    sys.modules.pop("pi.gesture_control", None)
    import pi.gesture_control as gc
    return gc


# ── Tests ───────────────────────────────────────────────────────────────────

def test_wave_triggers_action():
    """When a wave gesture is detected from pose keypoints, the callback fires."""
    gc = _import_gesture_control()

    fired = []

    def on_wave(gesture_name):
        fired.append(gesture_name)

    controller = gc.GestureController(
        engine=None,  # No NPU needed for this unit test
        on_wave=on_wave,
    )

    # Right wrist (y=0.25) well above right shoulder (y=0.6) → wave
    pose = _with_right_arm(_base_pose(), elbow_y=0.45, wrist_y=0.25)
    controller._detect_gesture(pose)

    assert len(fired) >= 1, f"Wave callback should have fired, got {fired}"
    assert fired[0] == "wave"


def test_thumbs_up_triggers_action():
    """A thumbs-up (wrist just above elbow on one side) fires the callback."""
    gc = _import_gesture_control()

    fired = []
    controller = gc.GestureController(
        engine=None,
        on_thumbs_up=lambda g: fired.append(g),
    )

    # Right wrist slightly above elbow → thumbs up
    pose = _with_right_arm(_base_pose(), elbow_y=0.70, wrist_y=0.55)
    controller._detect_gesture(pose)

    assert len(fired) >= 1, f"Thumbs-up callback should have fired, got {fired}"
    assert fired[0] == "thumbs_up"


def test_stop_triggers_action():
    """Both wrists raised above shoulders fires the stop callback."""
    gc = _import_gesture_control()

    fired = []
    controller = gc.GestureController(
        engine=None,
        on_stop=lambda g: fired.append(g),
    )

    # Both wrists well above both shoulders → stop
    pose = _with_both_arms(
        _base_pose(),
        left_elbow_y=0.45, left_wrist_y=0.25,
        right_elbow_y=0.45, right_wrist_y=0.25,
    )
    controller._detect_gesture(pose)

    assert len(fired) >= 1, f"Stop callback should have fired, got {fired}"
    assert fired[0] == "stop"


def test_neutral_pose_no_gesture():
    """A neutral standing pose should not trigger any gesture callback."""
    gc = _import_gesture_control()

    wave_fired = []
    thumbs_fired = []
    stop_fired = []

    controller = gc.GestureController(
        engine=None,
        on_wave=lambda g: wave_fired.append(g),
        on_thumbs_up=lambda g: thumbs_fired.append(g),
        on_stop=lambda g: stop_fired.append(g),
    )

    controller._detect_gesture(_base_pose())

    assert len(wave_fired) == 0
    assert len(thumbs_fired) == 0
    assert len(stop_fired) == 0


def test_controller_unavailable_without_engine():
    """When no engine is provided, the controller reports unavailable."""
    gc = _import_gesture_control()
    controller = gc.GestureController(engine=None)
    assert controller.available is False


def test_left_hand_wave_also_triggers():
    """A wave with the left hand should also fire the callback."""
    gc = _import_gesture_control()

    fired = []
    controller = gc.GestureController(
        engine=None,
        on_wave=lambda g: fired.append(g),
    )

    # Left wrist (y=0.25) well above left shoulder (y=0.6) → wave
    pose = _with_left_arm(_base_pose(), elbow_y=0.45, wrist_y=0.25)
    controller._detect_gesture(pose)

    assert len(fired) >= 1, f"Left-hand wave should trigger callback, got {fired}"
    assert fired[0] == "wave"