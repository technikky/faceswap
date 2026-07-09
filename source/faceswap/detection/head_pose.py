"""6DoF head-pose estimation for 3D model alignment (FR-003, FR-005).

Built on the MediaPipe Tasks ``FaceLandmarker`` with facial transformation
matrix output. The 4x4 transform gives head rotation directly (no solvePnP);
screen position and scale come from the landmark bounding box. The 3D renderer
uses the rotation to orient the model and the 2D box to place/scale it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..logging_setup import get_logger
from .mp_common import (
    face_geometry, landmarks_xy, make_face_landmarker, to_mp_image,
)

log = get_logger("headpose")

_TS_STEP_MS = 33


@dataclass
class HeadPose:
    cx: float
    cy: float
    width: float
    height: float
    roll_deg: float
    yaw_deg: float
    pitch_deg: float
    rotation_matrix: np.ndarray = field(default_factory=lambda: np.eye(3))
    confidence: float = 1.0

    @property
    def center(self):
        return self.cx, self.cy


def _ema(a, b, alpha):
    return alpha * a + (1 - alpha) * b


def _ema_angle(prev_deg, new_deg, alpha):
    p, n = math.radians(prev_deg), math.radians(new_deg)
    s = _ema(math.sin(p), math.sin(n), alpha)
    c = _ema(math.cos(p), math.cos(n), alpha)
    return math.degrees(math.atan2(s, c))


def _euler(R: np.ndarray):
    """Return (yaw, pitch, roll) degrees from a rotation matrix (for display)."""
    sy = math.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)
    if sy > 1e-6:
        x = math.atan2(R[2, 1], R[2, 2])
        y = math.atan2(-R[2, 0], sy)
        z = math.atan2(R[1, 0], R[0, 0])
    else:
        x = math.atan2(-R[1, 2], R[1, 1])
        y = math.atan2(-R[2, 0], sy)
        z = 0.0
    return math.degrees(y), math.degrees(x), math.degrees(z)


class HeadPoseEstimator:
    def __init__(
        self,
        min_detection_confidence: float = 0.5,
        smoothing: float = 0.6,
        lost_frames_before_reset: int = 15,
    ) -> None:
        self.smoothing = float(np.clip(smoothing, 0.0, 0.95))
        self.lost_frames_before_reset = lost_frames_before_reset
        self._landmarker = make_face_landmarker(
            want_transform=True, min_conf=min_detection_confidence
        )
        self._smoothed: Optional[HeadPose] = None
        self._lost = 0
        self._ts = 0

    def process(self, frame_bgr: np.ndarray) -> Optional[HeadPose]:
        h, w = frame_bgr.shape[:2]
        self._ts += _TS_STEP_MS
        result = self._landmarker.detect_for_video(to_mp_image(frame_bgr), self._ts)

        faces = getattr(result, "face_landmarks", None)
        if not faces:
            self._lost += 1
            if self._lost >= self.lost_frames_before_reset:
                self._smoothed = None
            return self._smoothed
        self._lost = 0

        pts = landmarks_xy(faces[0], w, h)
        cx, cy, bw, bh, roll2d = face_geometry(pts)

        rot = np.eye(3)
        yaw = pitch = 0.0
        roll = roll2d
        mats = getattr(result, "facial_transformation_matrixes", None)
        if mats:
            m = np.asarray(mats[0], dtype=np.float64).reshape(4, 4)
            r = m[:3, :3].copy()
            # Remove any scale so we pass a pure rotation to the renderer.
            for i in range(3):
                n = np.linalg.norm(r[:, i])
                if n > 1e-9:
                    r[:, i] /= n
            rot = r
            yaw, pitch, roll = _euler(rot)

        raw = HeadPose(cx=cx, cy=cy, width=bw, height=bh,
                       roll_deg=roll, yaw_deg=yaw, pitch_deg=pitch,
                       rotation_matrix=rot)
        self._smoothed = self._smooth(raw)
        return self._smoothed

    def _smooth(self, raw: HeadPose) -> HeadPose:
        prev = self._smoothed
        if prev is None:
            return raw
        a = self.smoothing
        return HeadPose(
            cx=_ema(prev.cx, raw.cx, a),
            cy=_ema(prev.cy, raw.cy, a),
            width=_ema(prev.width, raw.width, a),
            height=_ema(prev.height, raw.height, a),
            roll_deg=_ema_angle(prev.roll_deg, raw.roll_deg, a),
            yaw_deg=_ema_angle(prev.yaw_deg, raw.yaw_deg, a),
            pitch_deg=_ema_angle(prev.pitch_deg, raw.pitch_deg, a),
            rotation_matrix=raw.rotation_matrix,
            confidence=raw.confidence,
        )

    def close(self) -> None:
        try:
            self._landmarker.close()
        except Exception:  # pragma: no cover
            pass
