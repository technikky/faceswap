"""Background processing thread.

Runs the engine's per-frame pipeline off the Qt GUI thread and emits the
composited frame plus a lightweight status string for the UI to display.
"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np
from PySide6.QtCore import QThread, Signal

from ..engine import ProcessingEngine
from ..logging_setup import get_logger

log = get_logger("ui.worker")


class ProcessingWorker(QThread):
    frame_ready = Signal(np.ndarray)      # composited BGR frame
    status = Signal(str)                  # human-readable status line
    error = Signal(str)                   # recoverable error message

    def __init__(self, engine: ProcessingEngine) -> None:
        super().__init__()
        self._engine = engine
        self._running = False
        self._last_frame: Optional[np.ndarray] = None

    @property
    def last_frame(self) -> Optional[np.ndarray]:
        return self._last_frame

    def run(self) -> None:  # noqa: D401 - QThread entry point
        self._running = True
        fps_t0 = time.perf_counter()
        frames = 0
        fps = 0.0
        reported_error = False

        while self._running:
            try:
                result = self._engine.process_once()
            except Exception as exc:  # keep the app alive (NFR reliability)
                if not reported_error:
                    log.exception("Processing error")
                    self.error.emit(str(exc))
                    reported_error = True
                time.sleep(0.05)
                continue

            cam = self._engine.camera
            if cam is not None and not cam.is_healthy and cam.last_error:
                if not reported_error:
                    self.error.emit(cam.last_error)
                    reported_error = True
                time.sleep(0.05)
                continue
            reported_error = False

            if result is None:
                time.sleep(0.005)
                continue

            self._last_frame = result.frame_bgr
            self.frame_ready.emit(result.frame_bgr)

            frames += 1
            now = time.perf_counter()
            if now - fps_t0 >= 0.5:
                fps = frames / (now - fps_t0)
                frames = 0
                fps_t0 = now

            face = "face: tracked" if result.pose is not None else "face: --"
            rec = f" | REC {result.rec_frames}f" if result.recording else ""
            self.status.emit(f"{fps:4.1f} FPS | {face}{rec}")

        log.info("Processing worker stopped")

    def stop(self) -> None:
        self._running = False
        self.wait(2000)
