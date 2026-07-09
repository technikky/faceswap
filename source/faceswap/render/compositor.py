"""Shared 2D compositing helper.

Warps a source RGB image + alpha onto a destination frame so it is centred at
``(cx, cy)``, rotated by ``angle_deg`` and scaled so its width becomes
``target_w`` pixels, then alpha-blends with ``opacity``. Used by both the image
overlay and the 3D model renderer (which produces a rendered RGBA sprite).
"""
from __future__ import annotations

import cv2
import numpy as np


def warp_and_blend(
    frame_bgr: np.ndarray,
    src_bgr: np.ndarray,
    src_alpha: np.ndarray,
    cx: float,
    cy: float,
    target_w: float,
    angle_deg: float,
    opacity: float = 1.0,
) -> np.ndarray:
    """Return a new frame with ``src`` composited onto it."""
    fh, fw = frame_bgr.shape[:2]
    oh, ow = src_bgr.shape[:2]
    s = max(target_w, 1.0) / ow

    m = cv2.getRotationMatrix2D((ow / 2.0, oh / 2.0), -angle_deg, s)
    m[0, 2] += cx - ow / 2.0
    m[1, 2] += cy - oh / 2.0

    warped_bgr = cv2.warpAffine(src_bgr, m, (fw, fh), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    warped_a = cv2.warpAffine(src_alpha, m, (fw, fh), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    if warped_a.ndim == 2:
        warped_a = warped_a[:, :, None]

    a = warped_a * float(np.clip(opacity, 0.0, 1.0))
    out = frame_bgr.astype(np.float32) * (1.0 - a) + warped_bgr * a
    return np.clip(out, 0, 255).astype(np.uint8)
