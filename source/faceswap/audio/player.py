"""Audio playback with pitch, volume and loop (FR-007).

Design notes
------------
* Decoding uses ``soundfile`` (libsndfile >= 1.1), which reads WAV, OGG and
  MP3. The whole file is loaded into memory as float32 — fine for typical music
  tracks and it makes variable-rate playback trivial.
* Playback uses a ``sounddevice`` output stream. Pitch is implemented as
  **varispeed resampling**: the read pointer advances by ``pitch`` samples per
  output sample with linear interpolation, so 2.0x is an octave up (and plays
  twice as fast), 0.5x an octave down. This is fully offline and real-time.
  Pitch that preserves tempo would need a phase vocoder / Rubber Band and can
  be added behind the same interface later.
* A capture tap records the exact samples sent to the device so the recorder
  can mux the audio that was actually heard (post pitch + volume) into the MP4.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

from ..logging_setup import get_logger

log = get_logger("audio")

try:
    import soundfile as sf
    import sounddevice as sd
except Exception as exc:  # pragma: no cover - environment dependent
    sf = None
    sd = None
    _IMPORT_ERROR: Optional[Exception] = exc
else:
    _IMPORT_ERROR = None

_SUPPORTED = {".wav", ".ogg", ".mp3", ".flac"}


class AudioPlayer:
    """In-memory music player with pitch/volume/loop and a recording tap."""

    def __init__(self) -> None:
        if sd is None or sf is None:
            raise RuntimeError(
                "Audio backend unavailable (need soundfile + sounddevice). "
                f"Original error: {_IMPORT_ERROR}"
            )
        self._lock = threading.Lock()
        self._data: Optional[np.ndarray] = None    # (frames, channels) float32
        self._sr: int = 44100
        self._channels: int = 2
        self._pos: float = 0.0                     # fractional read pointer
        self._path: Optional[Path] = None

        self._stream: Optional["sd.OutputStream"] = None
        self._playing = False
        self._paused = False

        self.volume: float = 1.0
        self.pitch: float = 1.0                    # 0.5 .. 2.0
        self.loop: bool = False

        self._capture: Optional[list] = None       # list of captured blocks

    # -- loading -------------------------------------------------------------
    def load(self, path: str | Path) -> None:
        path = Path(path)
        if path.suffix.lower() not in _SUPPORTED:
            raise ValueError(f"Unsupported audio type '{path.suffix}'. Use MP3, WAV or OGG.")
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        with self._lock:
            self._data = data
            self._sr = int(sr)
            self._channels = data.shape[1]
            self._pos = 0.0
            self._path = path
        log.info("Loaded audio '%s' (%.1fs, %d ch, %d Hz)",
                 path.name, len(data) / sr, self._channels, sr)

    @property
    def loaded(self) -> bool:
        return self._data is not None

    @property
    def path(self) -> Optional[Path]:
        return self._path

    @property
    def duration(self) -> float:
        with self._lock:
            return 0.0 if self._data is None else len(self._data) / self._sr

    # -- transport -----------------------------------------------------------
    def play(self) -> None:
        if self._data is None:
            raise RuntimeError("No audio loaded")
        if self._playing and self._paused:
            self._paused = False
            return
        if self._playing:
            return
        self._open_stream()
        self._paused = False
        self._playing = True
        self._stream.start()
        log.info("Audio play")

    def pause(self) -> None:
        if self._playing:
            self._paused = True
            log.info("Audio pause")

    def stop(self) -> None:
        self._playing = False
        self._paused = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # pragma: no cover
                pass
            self._stream = None
        with self._lock:
            self._pos = 0.0
        log.info("Audio stop")

    def set_volume(self, v: float) -> None:
        self.volume = float(np.clip(v, 0.0, 2.0))

    def set_pitch(self, p: float) -> None:
        self.pitch = float(np.clip(p, 0.5, 2.0))

    def set_loop(self, on: bool) -> None:
        self.loop = bool(on)

    @property
    def is_playing(self) -> bool:
        return self._playing and not self._paused

    # -- stream + callback ---------------------------------------------------
    def _open_stream(self) -> None:
        self._stream = sd.OutputStream(
            samplerate=self._sr,
            channels=self._channels,
            dtype="float32",
            blocksize=1024,
            callback=self._callback,
        )

    def _callback(self, outdata: np.ndarray, frames: int, time_info, status) -> None:
        if status:  # underflow/overflow — log once-ish, keep going
            log.debug("Audio stream status: %s", status)
        with self._lock:
            data = self._data
            sr = self._sr
            pos = self._pos
            pitch = self.pitch
            vol = self.volume
            loop = self.loop
            playing = self._playing and not self._paused

        if data is None or not playing:
            outdata.fill(0.0)
            # Keep the captured timeline aligned with wall-clock by recording
            # silence while paused/stopped during an active capture.
            with self._lock:
                if self._capture is not None:
                    self._capture.append(np.zeros((frames, self._channels), np.float32))
            return

        n = len(data)
        # Fractional sample indices for this block.
        idx = pos + np.arange(frames, dtype=np.float64) * pitch
        end = idx[-1] if frames else pos

        if not loop:
            valid = idx < (n - 1)
            safe = np.clip(idx, 0, n - 1)
            i0 = np.floor(safe).astype(np.int64)
            frac = (safe - i0)[:, None]
            i1 = np.minimum(i0 + 1, n - 1)
            block = data[i0] * (1 - frac) + data[i1] * frac
            block[~valid] = 0.0
            new_pos = end + pitch
            if end >= n - 1:
                # Reached the end this block — stop after emitting.
                self._playing = False
        else:
            wrapped = np.mod(idx, n)
            i0 = np.floor(wrapped).astype(np.int64)
            frac = (wrapped - i0)[:, None]
            i1 = np.mod(i0 + 1, n)
            block = data[i0] * (1 - frac) + data[i1] * frac
            new_pos = (end + pitch) % n

        block = (block * vol).astype(np.float32)
        outdata[:] = block

        with self._lock:
            self._pos = float(new_pos)
            if self._capture is not None:
                self._capture.append(block.copy())

    # -- recording tap -------------------------------------------------------
    def start_capture(self) -> int:
        """Begin capturing output samples for muxing. Returns the samplerate."""
        with self._lock:
            self._capture = []
            return self._sr

    def stop_capture(self) -> Tuple[Optional[np.ndarray], int]:
        """Return captured (samples, samplerate); ``None`` if nothing captured."""
        with self._lock:
            blocks = self._capture
            self._capture = None
            sr = self._sr
        if not blocks:
            return None, sr
        return np.concatenate(blocks, axis=0), sr

    def shutdown(self) -> None:
        self.stop()
