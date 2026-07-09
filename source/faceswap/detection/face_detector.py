"""Real-time face detection and tracking (FR-002, FR-003).

Built on the MediaPipe Tasks ``FaceLandmarker`` (the legacy ``solutions`` API was
removed in recent MediaPipe). Produces a smoothed :class:`FacePose` describing
centre, scale and in-plane rotation for the 2D image overlay. Tracking is
stabilised with an exponential moving average and recovers automatically after
brief losses.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from ..logging_setup import get_logger
from .mp_common import (
    face_geometry, landmarks_xy, make_face_landmarker, to_mp_image,
)

log = get_logger("detection")

# VIDEO running mode needs a monotonically increasing timestamp (ms). We use a
# synthetic per-frame increment so it never depends on wall-clock behaviour.
_TS_STEP_MS = 33


@dataclass
class FacePose:
    """Face geometry in pixel coordinates of the processed frame."""

    cx: float
    cy: float
    width: float
    height: float
    roll_deg: float
    confidence: float

    @property
    def center(self) -> Tuple[float, float]:
        return self.cx, self.cy


def _ema(prev: float, new: float, alpha: float) -> float:
    return alpha * prev + (1.0 - alpha) * new


class FaceDetector:
    """Detects and tracks a single primary face for 2D overlay alignment."""

    def __init__(
        self,
        model_selection: int = 0,          # kept for config compatibility (unused)
        min_detection_confidence: float = 0.5,
        smoothing: float = 0.6,
        lost_frames_before_reset: int = 15,
    ) -> None:
        self.smoothing = float(np.clip(smoothing, 0.0, 0.95))
        self.lost_frames_before_reset = lost_frames_before_reset
        self._landmarker = make_face_landmarker(
            want_transform=False, min_conf=min_detection_confidence
        )
        self._smoothed: Optional[FacePose] = None
        self._lost_frames = 0
        self._ts = 0

    def process(self, frame_bgr: np.ndarray) -> Optional[FacePose]:
        """Return the smoothed pose of the primary face, or ``None`` if lost."""
        h, w = frame_bgr.shape[:2]
        self._ts += _TS_STEP_MS
        result = self._landmarker.detect_for_video(to_mp_image(frame_bgr), self._ts)

        faces = getattr(result, "face_landmarks", None)
        if not faces:
            self._lost_frames += 1
            if self._lost_frames >= self.lost_frames_before_reset:
                self._smoothed = None
            return self._smoothed

        self._lost_frames = 0
        pts = landmarks_xy(faces[0], w, h)
        cx, cy, bw, bh, roll = face_geometry(pts)
        raw = FacePose(cx=cx, cy=cy, width=bw, height=bh, roll_deg=roll, confidence=1.0)
        self._smoothed = self._smooth(raw)
        return self._smoothed

    def _smooth(self, raw: FacePose) -> FacePose:
        prev = self._smoothed
        if prev is None:
            return raw
        a = self.smoothing
        prev_rad, new_rad = math.radians(prev.roll_deg), math.radians(raw.roll_deg)
        sx = _ema(math.sin(prev_rad), math.sin(new_rad), a)
        cx_ang = _ema(math.cos(prev_rad), math.cos(new_rad), a)
        roll = math.degrees(math.atan2(sx, cx_ang))
        return FacePose(
            cx=_ema(prev.cx, raw.cx, a),
            cy=_ema(prev.cy, raw.cy, a),
            width=_ema(prev.width, raw.width, a),
            height=_ema(prev.height, raw.height, a),
            roll_deg=roll,
            confidence=raw.confidence,
        )

    def close(self) -> None:
        try:
            self._landmarker.close()
        except Exception:  # pragma: no cover
            pass
