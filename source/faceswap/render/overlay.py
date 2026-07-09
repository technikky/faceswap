"""2D image overlay compositor (FR-004, FR-005, FR-006).

Loads a PNG (with alpha) or JPG image and composites it onto the live frame so
that it tracks the detected face: aligned to the face centre, rotated with the
head roll, and scaled to the face size. Supports opacity plus manual scale,
rotation and translation offsets.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from ..detection.face_detector import FacePose
from ..logging_setup import get_logger

log = get_logger("overlay")

_SUPPORTED = {".png", ".jpg", ".jpeg"}


class ImageOverlay:
    """Holds the active overlay image and composites it onto frames."""

    def __init__(self) -> None:
        self._bgr: Optional[np.ndarray] = None      # HxWx3 float32
        self._alpha: Optional[np.ndarray] = None    # HxWx1 float32 in [0,1]
        self._path: Optional[Path] = None

    # -- loading -------------------------------------------------------------
    def load(self, path: str | Path) -> None:
        path = Path(path)
        if path.suffix.lower() not in _SUPPORTED:
            raise ValueError(f"Unsupported image type '{path.suffix}'. Use PNG or JPG.")
        # np.fromfile handles non-ASCII paths that cv2.imread mishandles on Windows.
        data = np.fromfile(str(path), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ValueError(f"Could not decode image '{path}' (corrupted or unsupported).")

        if img.ndim == 2:  # grayscale
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        if img.shape[2] == 4:
            bgr = img[:, :, :3].astype(np.float32)
            alpha = (img[:, :, 3:4].astype(np.float32)) / 255.0
        else:
            bgr = img[:, :, :3].astype(np.float32)
            alpha = np.ones((img.shape[0], img.shape[1], 1), dtype=np.float32)

        self._bgr = bgr
        self._alpha = alpha
        self._path = path
        log.info("Loaded overlay '%s' (%dx%d, alpha=%s)",
                 path.name, img.shape[1], img.shape[0], img.shape[2] == 4)

    @property
    def loaded(self) -> bool:
        return self._bgr is not None

    @property
    def path(self) -> Optional[Path]:
        return self._path

    def clear(self) -> None:
        self._bgr = self._alpha = self._path = None

    # -- compositing ---------------------------------------------------------
    def composite(
        self,
        frame_bgr: np.ndarray,
        pose: FacePose,
        *,
        opacity: float = 1.0,
        scale: float = 1.0,
        rotation_offset_deg: float = 0.0,
        offset_x: float = 0.0,
        offset_y: float = 0.0,
        size_reference: str = "bbox",
        size_factor: float = 1.15,
        follow_rotation: bool = True,
    ) -> np.ndarray:
        """Return a new frame with the overlay composited onto the face.

        ``offset_x``/``offset_y`` are expressed as fractions of the face width
        so manual offsets stay consistent as the subject moves nearer/further.
        """
        if self._bgr is None or self._alpha is None:
            return frame_bgr

        fh, fw = frame_bgr.shape[:2]
        oh, ow = self._bgr.shape[:2]

        # Target on-screen width of the overlay.
        ref = pose.width if size_reference == "bbox" else max(pose.width, 1.0)
        target_w = max(ref * size_factor * scale, 1.0)
        s = target_w / ow

        angle = (pose.roll_deg if follow_rotation else 0.0) + rotation_offset_deg
        cx = pose.cx + offset_x * pose.width
        cy = pose.cy + offset_y * pose.width

        # Rotate+scale about the overlay centre, then translate that centre to
        # the target face centre. getRotationMatrix2D uses counter-clockwise
        # positive angles; negate so a positive roll rotates with the head.
        m = cv2.getRotationMatrix2D((ow / 2.0, oh / 2.0), -angle, s)
        m[0, 2] += cx - ow / 2.0
        m[1, 2] += cy - oh / 2.0

        warped_bgr = cv2.warpAffine(
            self._bgr, m, (fw, fh),
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0,
        )
        warped_alpha = cv2.warpAffine(
            self._alpha, m, (fw, fh),
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0,
        )
        if warped_alpha.ndim == 2:
            warped_alpha = warped_alpha[:, :, None]

        a = warped_alpha * float(np.clip(opacity, 0.0, 1.0))
        base = frame_bgr.astype(np.float32)
        out = base * (1.0 - a) + warped_bgr * a
        return np.clip(out, 0, 255).astype(np.uint8)
