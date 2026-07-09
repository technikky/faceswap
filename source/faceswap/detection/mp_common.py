"""Shared MediaPipe Tasks API helpers (mediapipe >= 0.10, Tasks vision).

The legacy ``mediapipe.solutions`` API (face_detection / face_mesh) was removed
in newer MediaPipe releases, so detection is built on the Tasks API's
``FaceLandmarker``. It provides 478 face landmarks (for 2D placement) and, when
requested, a 4x4 facial transformation matrix (for 3D head pose).

The model file ``face_landmarker.task`` must be present offline under
``assets/models/mediapipe/`` (bundled with the app).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import List, Tuple

import numpy as np

from ..logging_setup import get_logger
from ..paths import ASSETS_DIR, ROOT

log = get_logger("mp")

try:
    import mediapipe as mp
    from mediapipe.tasks.python import vision
    from mediapipe.tasks.python.core.base_options import BaseOptions
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - environment dependent
    mp = None
    vision = None
    BaseOptions = None
    _IMPORT_ERROR = exc

FACE_LANDMARKER_MODEL = "face_landmarker.task"

# Face-mesh landmark indices used for geometry.
RIGHT_EYE_OUTER = 33
LEFT_EYE_OUTER = 263


def find_model(name: str) -> Path:
    """Locate a bundled MediaPipe model file, or raise with guidance."""
    candidates = [
        ASSETS_DIR / "models" / "mediapipe" / name,
        ROOT / "offline-sdk" / "mediapipe-models" / name,
    ]
    for p in candidates:
        if p.exists():
            return p
    raise RuntimeError(
        f"MediaPipe model '{name}' not found. Expected at "
        f"assets/models/mediapipe/{name}. Run installer\\bundle_offline_sdk.ps1 "
        f"(which fetches it) or download it manually."
    )


def require_mediapipe() -> None:
    if vision is None:
        raise RuntimeError(
            "MediaPipe Tasks API is unavailable. Install mediapipe (see "
            f"requirements.txt). Original error: {_IMPORT_ERROR}"
        )


def make_face_landmarker(want_transform: bool, min_conf: float):
    """Create a FaceLandmarker in VIDEO running mode for a single face."""
    require_mediapipe()
    model_path = str(find_model(FACE_LANDMARKER_MODEL))
    options = vision.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=model_path),
        running_mode=vision.RunningMode.VIDEO,
        num_faces=1,
        min_face_detection_confidence=float(min_conf),
        min_face_presence_confidence=float(min_conf),
        min_tracking_confidence=float(min_conf),
        output_face_blendshapes=False,
        output_facial_transformation_matrixes=bool(want_transform),
    )
    return vision.FaceLandmarker.create_from_options(options)


def to_mp_image(frame_bgr: np.ndarray):
    """Wrap a BGR frame as an sRGB mp.Image (Tasks API input)."""
    rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])
    return mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)


def landmarks_xy(landmarks, w: int, h: int) -> np.ndarray:
    """Return an (N,2) array of pixel coordinates for normalized landmarks."""
    return np.array([(p.x * w, p.y * h) for p in landmarks], dtype=np.float64)


def face_geometry(pts: np.ndarray) -> Tuple[float, float, float, float, float]:
    """Return (cx, cy, width, height, roll_deg) from landmark pixels."""
    x0, y0 = pts[:, 0].min(), pts[:, 1].min()
    x1, y1 = pts[:, 0].max(), pts[:, 1].max()
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    width, height = (x1 - x0), (y1 - y0)
    re, le = pts[RIGHT_EYE_OUTER], pts[LEFT_EYE_OUTER]
    roll = math.degrees(math.atan2(le[1] - re[1], le[0] - re[0]))
    return cx, cy, width, height, roll
