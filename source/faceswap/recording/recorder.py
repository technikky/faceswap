"""Video recording (FR-008) and snapshots (FR-009).

Records the composited output to an H.264 MP4. Prefers piping raw frames to
FFmpeg (libx264) for true H.264; the FFmpeg binary bundled by
``imageio-ffmpeg`` is used when present so no system install is required
(offline-friendly). Falls back to OpenCV's ``mp4v`` writer if FFmpeg cannot be
located.

Audio is intentionally out of scope for this MVP (video-only recording).
"""
from __future__ import annotations

import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from ..logging_setup import get_logger
from ..paths import ROOT, ensure_dir

log = get_logger("recording")


def _find_ffmpeg() -> Optional[str]:
    """Locate an FFmpeg executable: bundled wheel, then PATH, then offline-sdk."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:
        pass
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    for candidate in (ROOT / "offline-sdk" / "FFmpeg").rglob("ffmpeg.exe"):
        return str(candidate)
    return None


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def mux_audio(video_path: Path, audio_path: Path, out_path: Path) -> Optional[Path]:
    """Mux an audio file into a video file (copy video, encode AAC audio).

    Returns the muxed output path, or ``None`` if FFmpeg is unavailable (in
    which case the caller should keep the video-only file).
    """
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        log.warning("FFmpeg not found; cannot mux audio. Keeping video-only file.")
        return None
    cmd = [
        ffmpeg, "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out_path),
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        log.error("Audio mux failed: %s", exc)
        return None
    log.info("Muxed audio -> %s", out_path)
    return out_path


def save_snapshot(frame_bgr: np.ndarray, out_dir: Path, fmt: str = "png") -> Path:
    """Write a still image (FR-009). Returns the output path."""
    ensure_dir(out_dir)
    fmt = fmt.lower().lstrip(".")
    if fmt not in ("png", "jpg", "jpeg"):
        fmt = "png"
    path = out_dir / f"snapshot_{_timestamp()}.{fmt}"
    ext = ".jpg" if fmt in ("jpg", "jpeg") else ".png"
    ok, buf = cv2.imencode(ext, frame_bgr)
    if not ok:
        raise RuntimeError("Failed to encode snapshot")
    buf.tofile(str(path))  # tofile handles unicode paths on Windows
    log.info("Snapshot saved -> %s", path)
    return path


class VideoRecorder:
    """Records composited frames to an MP4 file."""

    def __init__(
        self,
        width: int,
        height: int,
        fps: int = 30,
        bitrate: str = "8M",
        output_dir: str | Path = "recordings",
    ) -> None:
        # libx264 with yuv420p requires even dimensions.
        self.width = width - (width % 2)
        self.height = height - (height % 2)
        self.fps = max(1, int(fps))
        self.bitrate = bitrate
        self.output_dir = ensure_dir(Path(output_dir) if Path(output_dir).is_absolute()
                                     else ROOT / output_dir)
        self.output_path: Optional[Path] = None

        self._proc: Optional[subprocess.Popen] = None
        self._writer: Optional[cv2.VideoWriter] = None
        self._frames = 0
        self._using_ffmpeg = False
        self._t0: Optional[float] = None  # wall-clock start of the first frame

    def start(self) -> Path:
        self._t0 = None
        self.output_path = self.output_dir / f"recording_{_timestamp()}.mp4"
        ffmpeg = _find_ffmpeg()
        if ffmpeg:
            self._start_ffmpeg(ffmpeg)
        else:
            log.warning("FFmpeg not found; falling back to OpenCV mp4v (not H.264).")
            self._start_opencv()
        log.info("Recording started -> %s (%s)", self.output_path,
                 "H.264/ffmpeg" if self._using_ffmpeg else "mp4v/opencv")
        return self.output_path

    def _start_ffmpeg(self, ffmpeg: str) -> None:
        cmd = [
            ffmpeg, "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{self.width}x{self.height}", "-r", str(self.fps),
            "-i", "-",
            "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-b:v", self.bitrate,
            str(self.output_path),
        ]
        self._proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._using_ffmpeg = True

    def _start_opencv(self) -> None:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self._writer = cv2.VideoWriter(
            str(self.output_path), fourcc, self.fps, (self.width, self.height)
        )
        if not self._writer.isOpened():
            raise RuntimeError("Could not open OpenCV VideoWriter for recording")
        self._using_ffmpeg = False

    def _write_raw(self, frame_bgr: np.ndarray) -> None:
        if self._using_ffmpeg and self._proc and self._proc.stdin:
            try:
                self._proc.stdin.write(frame_bgr.tobytes())
            except (BrokenPipeError, OSError) as exc:
                log.error("FFmpeg pipe broken during recording: %s", exc)
                self._using_ffmpeg = False
        elif self._writer is not None:
            self._writer.write(frame_bgr)
        self._frames += 1

    def write(self, frame_bgr: np.ndarray) -> None:
        """Write a frame, paced to real time.

        The processing pipeline runs at a variable rate (often slower than the
        target FPS), so we emit exactly ``fps`` frames per real second: the
        latest frame is duplicated when we are behind and dropped when we are
        ahead. This keeps the recorded timeline equal to real elapsed time, so
        playback speed is correct and audio stays in sync.
        """
        if frame_bgr.shape[1] != self.width or frame_bgr.shape[0] != self.height:
            frame_bgr = cv2.resize(frame_bgr, (self.width, self.height))

        now = time.perf_counter()
        if self._t0 is None:
            self._t0 = now
        # How many frames the timeline should contain by now (+1 so the very
        # first frame is emitted immediately).
        target = int((now - self._t0) * self.fps) + 1
        reps = target - self._frames
        if reps <= 0:
            return  # ahead of schedule -> drop to hold real-time pace
        for _ in range(reps):
            self._write_raw(frame_bgr)

    @property
    def frame_count(self) -> int:
        return self._frames

    def stop(self) -> Optional[Path]:
        if self._proc is not None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.wait(timeout=15)
            except Exception as exc:  # pragma: no cover
                log.error("Error finalising ffmpeg: %s", exc)
                self._proc.kill()
            self._proc = None
        if self._writer is not None:
            self._writer.release()
            self._writer = None
        log.info("Recording stopped: %d frames -> %s", self._frames, self.output_path)
        return self.output_path
