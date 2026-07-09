"""Processing engine: the capture -> detect -> composite -> record pipeline.

Supports two replacement modes:
  * "image" — a PNG/JPG overlay aligned via MediaPipe Face Detection (FR-004 2D)
  * "model" — an FBX/OBJ/glTF model rendered via head pose (FR-004 3D)

Audio playback (FR-007) is driven here too, and when recording is active the
audio actually heard is captured and muxed into the MP4 (FR-008).

The engine is UI-agnostic; the UI drives it from a worker thread.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import numpy as np

from .capture import CameraCapture
from .config import Config
from .detection import FaceDetector, FacePose, HeadPoseEstimator, HeadPose
from .logging_setup import get_logger
from .paths import ROOT
from .recording import VideoRecorder, save_snapshot
from .recording.recorder import mux_audio
from .render import ImageOverlay, ModelAsset, ModelRenderer

log = get_logger("engine")

Pose = Union[FacePose, HeadPose]


@dataclass
class FrameResult:
    frame_bgr: np.ndarray
    pose: Optional[Pose]
    recording: bool
    rec_frames: int


class ProcessingEngine:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.camera: Optional[CameraCapture] = None

        # 2D path
        self.detector: Optional[FaceDetector] = None
        self.overlay = ImageOverlay()

        # 3D path (lazy — creating a GL context / FaceMesh is expensive)
        self.head_estimator: Optional[HeadPoseEstimator] = None
        self.model_renderer: Optional[ModelRenderer] = None

        # Audio (lazy — backend import may be unavailable)
        self.audio = None
        self._audio_error: Optional[str] = None

        self.recorder: Optional[VideoRecorder] = None
        self._rec_video_path: Optional[Path] = None
        self._rec_audio_active = False
        self._lock = threading.Lock()

        self._restore_saved_assets()

    def _restore_saved_assets(self) -> None:
        img = self.config.get("render", "overlay_image", "")
        if img:
            try:
                self.overlay.load(img)
            except Exception as exc:
                log.warning("Could not load saved overlay '%s': %s", img, exc)
        model_path = self.config.get("model", "model_path", "")
        if model_path:
            try:
                self.load_model(model_path)
            except Exception as exc:
                log.warning("Could not load saved model '%s': %s", model_path, exc)

    # -- device lifecycle ----------------------------------------------------
    def start_camera(self) -> None:
        cam = self.config.section("camera")
        self.camera = CameraCapture(
            device_index=int(cam["device_index"]),
            width=int(cam["width"]),
            height=int(cam["height"]),
            fps=int(cam["fps"]),
            mirror=bool(cam["mirror"]),
            backend=str(cam["backend"]),
        )
        self.camera.start()
        self._ensure_detector()

    def _ensure_detector(self) -> None:
        if self.detector is None:
            det = self.config.section("detection")
            self.detector = FaceDetector(
                model_selection=int(det["model_selection"]),
                min_detection_confidence=float(det["min_detection_confidence"]),
                smoothing=float(det["tracking_smoothing"]),
                lost_frames_before_reset=int(det["lost_frames_before_reset"]),
            )

    def _ensure_head_estimator(self) -> None:
        if self.head_estimator is None:
            det = self.config.section("detection")
            self.head_estimator = HeadPoseEstimator(
                min_detection_confidence=float(det["min_detection_confidence"]),
                smoothing=float(det["tracking_smoothing"]),
                lost_frames_before_reset=int(det["lost_frames_before_reset"]),
            )

    def restart_camera(self, device_index: int, width: int, height: int, fps: int) -> None:
        self.stop_camera()
        self.config.set("camera", "device_index", device_index)
        self.config.set("camera", "width", width)
        self.config.set("camera", "height", height)
        self.config.set("camera", "fps", fps)
        self.config.save("camera")
        self.start_camera()

    def stop_camera(self) -> None:
        if self.camera is not None:
            self.camera.stop()
            self.camera = None

    # -- replacement asset management ----------------------------------------
    def set_mode(self, mode: str) -> None:
        self.config.set("render", "mode", mode)
        self.config.save("render")

    @property
    def mode(self) -> str:
        return self.config.get("render", "mode", "image")

    def load_overlay(self, path: str | Path) -> None:
        self.overlay.load(path)
        self.config.set("render", "overlay_image", str(path))
        self.config.save("render")

    def load_model(self, path: str | Path) -> None:
        asset = ModelAsset.load(path)
        if self.model_renderer is None:
            self.model_renderer = ModelRenderer(
                render_size=int(self.config.get("model", "render_size", 512))
            )
        self.model_renderer.set_model(asset)
        self.config.set("model", "model_path", str(path))
        self.config.save("model")

    # -- audio ---------------------------------------------------------------
    def ensure_audio(self):
        if self.audio is None and self._audio_error is None:
            try:
                from .audio import AudioPlayer
                self.audio = AudioPlayer()
                a = self.config.section("audio")
                self.audio.set_volume(float(a["volume"]))
                self.audio.set_pitch(float(a["pitch"]))
                self.audio.set_loop(bool(a["loop"]))
            except Exception as exc:
                self._audio_error = str(exc)
                log.error("Audio unavailable: %s", exc)
        if self.audio is None:
            raise RuntimeError(self._audio_error or "Audio backend unavailable")
        return self.audio

    # -- per-frame processing ------------------------------------------------
    def process_once(self) -> Optional[FrameResult]:
        if self.camera is None:
            return None
        frame = self.camera.read()
        if frame is None:
            return None

        render = self.config.section("render")
        pose: Optional[Pose] = None

        if render.get("enabled", True):
            if self.mode == "model" and self.model_renderer is not None \
                    and self.model_renderer.has_model:
                pose = self._process_model(frame, render)
                if pose is not None:
                    frame = self._composited_model(frame, pose)
            else:
                pose = self._process_image(frame)
                if pose is not None and self.overlay.loaded:
                    frame = self._composited_image(frame, pose, render)

        rec_frames = 0
        with self._lock:
            if self.recorder is not None:
                self.recorder.write(frame)
                rec_frames = self.recorder.frame_count

        return FrameResult(
            frame_bgr=frame, pose=pose,
            recording=self.recorder is not None, rec_frames=rec_frames,
        )

    def _process_image(self, frame) -> Optional[FacePose]:
        self._ensure_detector()
        return self.detector.process(frame)

    def _composited_image(self, frame, pose, render):
        return self.overlay.composite(
            frame, pose,
            opacity=float(render["opacity"]),
            scale=float(render["scale"]),
            rotation_offset_deg=float(render["rotation_offset_deg"]),
            offset_x=float(render["offset_x"]),
            offset_y=float(render["offset_y"]),
            size_reference=str(render["size_reference"]),
            size_factor=float(render["size_factor"]),
            follow_rotation=bool(render["follow_rotation"]),
        )

    def _process_model(self, frame, render) -> Optional[HeadPose]:
        self._ensure_head_estimator()
        return self.head_estimator.process(frame)

    def _composited_model(self, frame, pose):
        m = self.config.section("model")
        return self.model_renderer.composite(
            frame, pose,
            opacity=float(m["opacity"]),
            scale=float(m["scale"]),
            size_factor=float(m["size_factor"]),
            offset_x=float(m["offset_x"]),
            offset_y=float(m["offset_y"]),
            rot_offset=(float(m["rot_x"]), float(m["rot_y"]), float(m["rot_z"])),
        )

    # -- recording / snapshot -----------------------------------------------
    def start_recording(self) -> Path:
        if self.camera is None:
            raise RuntimeError("Camera is not running")
        rec = self.config.section("recording")
        w, h = self.camera.resolution
        with self._lock:
            self.recorder = VideoRecorder(
                width=w, height=h,
                fps=int(rec["fps"]) or self.camera.fps,
                bitrate=str(rec["bitrate"]),
                output_dir=str(rec["output_dir"]),
            )
            path = self.recorder.start()
            self._rec_video_path = path
        # Arm audio capture if a track is loaded, so it can be muxed in.
        self._rec_audio_active = False
        if self.audio is not None and self.audio.loaded:
            self.audio.start_capture()
            self._rec_audio_active = True
        return path

    def stop_recording(self) -> Optional[Path]:
        with self._lock:
            if self.recorder is None:
                return None
            video_path = self.recorder.stop()
            self.recorder = None

        if not self._rec_audio_active or self.audio is None:
            return video_path

        samples, sr = self.audio.stop_capture()
        self._rec_audio_active = False
        if samples is None or len(samples) == 0:
            return video_path
        return self._mux_recording(video_path, samples, sr)

    def _mux_recording(self, video_path: Path, samples: np.ndarray, sr: int) -> Path:
        try:
            import soundfile as sf
        except Exception as exc:
            log.warning("soundfile missing; cannot mux audio: %s", exc)
            return video_path
        wav_path = video_path.with_suffix(".wav")
        try:
            sf.write(str(wav_path), samples, sr)
        except Exception as exc:
            log.error("Could not write captured audio: %s", exc)
            return video_path

        final = video_path.with_name(video_path.stem + "_av.mp4")
        muxed = mux_audio(video_path, wav_path, final)
        try:
            wav_path.unlink(missing_ok=True)
            if muxed is not None:
                video_path.unlink(missing_ok=True)
        except OSError:
            pass
        return muxed or video_path

    @property
    def is_recording(self) -> bool:
        return self.recorder is not None

    def snapshot(self, frame_bgr: np.ndarray) -> Path:
        rec = self.config.section("recording")
        out_dir = Path(rec["snapshot_dir"])
        if not out_dir.is_absolute():
            out_dir = ROOT / out_dir
        return save_snapshot(frame_bgr, out_dir, str(rec["snapshot_format"]))

    # -- shutdown ------------------------------------------------------------
    def shutdown(self) -> None:
        self.stop_recording()
        self.stop_camera()
        if self.detector is not None:
            self.detector.close()
            self.detector = None
        if self.head_estimator is not None:
            self.head_estimator.close()
            self.head_estimator = None
        if self.model_renderer is not None:
            self.model_renderer.shutdown()
            self.model_renderer = None
        if self.audio is not None:
            self.audio.shutdown()
            self.audio = None
