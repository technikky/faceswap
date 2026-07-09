"""USB camera capture (FR-001).

Provides device enumeration and a background-threaded capture loop that always
serves the most recent frame, so a slow consumer (detection + rendering) never
stalls the camera or the UI.

Backend handling: on Windows some cameras open only via Media Foundation (MSMF)
and others only via DirectShow (DSHOW), so we try both. A camera that opens but
delivers no frames (typically because another app holds it) is still reported so
the user can see/select it, and the capture loop keeps retrying so it recovers
once the device is free.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

from ..logging_setup import get_logger

log = get_logger("camera")

# Order matters: MSMF is the modern default and works with most UVC webcams;
# DSHOW is the fallback for devices MSMF cannot open.
_WIN_BACKENDS = [("MSMF", cv2.CAP_MSMF), ("DSHOW", cv2.CAP_DSHOW)]


@dataclass
class CameraDevice:
    index: int
    name: str

    def __str__(self) -> str:  # shown in the UI dropdown
        return f"Camera {self.index}: {self.name}"


def _backend_order(pref: str):
    if pref == "msmf":
        return [("MSMF", cv2.CAP_MSMF), ("DSHOW", cv2.CAP_DSHOW)]
    if pref == "dshow":
        return [("DSHOW", cv2.CAP_DSHOW), ("MSMF", cv2.CAP_MSMF)]
    if pref == "any":
        return [("ANY", cv2.CAP_ANY)]
    return _WIN_BACKENDS  # "auto"


def enumerate_cameras(max_probe: int = 8) -> List[CameraDevice]:
    """Return cameras that can be opened on any backend.

    A device is listed if it *opens*, even if the first frame read fails (it may
    be momentarily busy). OpenCV cannot read friendly names portably, so we label
    by index and the backend that opened it.
    """
    devices: List[CameraDevice] = []
    for index in range(max_probe):
        for name, be in _WIN_BACKENDS:
            cap = cv2.VideoCapture(index, be)
            opened = cap.isOpened()
            cap.release()
            if opened:
                devices.append(CameraDevice(index=index, name=f"USB Video Device ({name})"))
                break
    if not devices:
        log.warning("No cameras detected during enumeration")
    else:
        log.info("Detected %d camera(s): %s", len(devices), [d.index for d in devices])
    return devices


class CameraCapture:
    """Threaded camera reader. Latest-frame-wins to minimise latency."""

    def __init__(
        self,
        device_index: int = 0,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        mirror: bool = True,
        backend: str = "auto",
    ) -> None:
        self.device_index = device_index
        self.width = width
        self.height = height
        self.fps = fps
        self.mirror = mirror
        self.backend = backend

        self._cap: Optional[cv2.VideoCapture] = None
        self._backend_used: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._running = False
        self._last_error: Optional[str] = None
        self._frame_count = 0

    # -- lifecycle -----------------------------------------------------------
    def _configure(self, cap: cv2.VideoCapture, backend_name: str) -> None:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        # MJPG lets UVC cameras deliver high resolution at full FPS on DirectShow;
        # forcing a FOURCC on MSMF can break some cameras, so only set it there.
        if backend_name == "DSHOW":
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

    @staticmethod
    def _warmup(cap: cv2.VideoCapture, attempts: int = 15) -> bool:
        for _ in range(attempts):
            ok, frame = cap.read()
            if ok and frame is not None:
                return True
            time.sleep(0.05)
        return False

    def open(self) -> None:
        fallback: Optional[Tuple[cv2.VideoCapture, str]] = None
        for name, be in _backend_order(self.backend):
            cap = cv2.VideoCapture(self.device_index, be)
            if not cap.isOpened():
                cap.release()
                continue
            self._configure(cap, name)
            if self._warmup(cap):
                self._finalise(cap, name)
                return
            # Opened but no frames yet (device likely busy) - keep as a fallback.
            if fallback is None:
                fallback = (cap, name)
            else:
                cap.release()

        if fallback is not None:
            cap, name = fallback
            self._finalise(cap, name)
            log.warning(
                "Camera %d opened on %s but produced no frame during warm-up; "
                "another app may be using it. Will keep retrying.",
                self.device_index, name,
            )
            return

        raise RuntimeError(
            f"Cannot open camera {self.device_index} on MSMF or DirectShow. "
            f"Is it connected and not in use by another app (Teams, browser, Camera app)?"
        )

    def _finalise(self, cap: cv2.VideoCapture, backend_name: str) -> None:
        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = cap.get(cv2.CAP_PROP_FPS)
        log.info(
            "Camera %d opened via %s: requested %dx%d@%d, got %dx%d@%.0f",
            self.device_index, backend_name, self.width, self.height, self.fps,
            actual_w, actual_h, actual_fps,
        )
        if actual_w > 0 and actual_h > 0:
            self.width, self.height = actual_w, actual_h
        if actual_fps > 0:
            self.fps = int(round(actual_fps))
        self._cap = cap
        self._backend_used = backend_name

    def start(self) -> None:
        if self._cap is None:
            self.open()
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="camera", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        assert self._cap is not None
        fail_streak = 0
        while self._running:
            ok, frame = self._cap.read()
            if not ok or frame is None:
                fail_streak += 1
                self._last_error = "Camera read failed (in use by another app or disconnected?)"
                if fail_streak == 1:
                    log.error(self._last_error)
                time.sleep(0.05)
                if fail_streak > 200:  # ~10s of failures -> give up the loop
                    self._running = False
                continue
            if fail_streak:
                log.info("Camera %d recovered", self.device_index)
            fail_streak = 0
            self._last_error = None
            if self.mirror:
                frame = cv2.flip(frame, 1)
            with self._lock:
                self._frame = frame
                self._frame_count += 1

    def read(self) -> Optional[np.ndarray]:
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    # -- status --------------------------------------------------------------
    @property
    def is_healthy(self) -> bool:
        return self._running and self._last_error is None

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def resolution(self) -> Tuple[int, int]:
        return self.width, self.height

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        log.info("Camera %d stopped", self.device_index)
