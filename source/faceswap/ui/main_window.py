"""Main application window (FR-010).

Layout: camera preview (left) and a scrollable control panel (right) with
camera settings, an object browser (2D image or 3D model), alignment
adjustments, audio controls, and recording controls, plus a status bar.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPushButton, QScrollArea, QSlider, QVBoxLayout,
    QWidget,
)

from ..capture import enumerate_cameras
from ..config import Config
from ..engine import ProcessingEngine
from ..logging_setup import get_logger
from ..paths import ASSETS_DIR, TEST_DIR
from .worker import ProcessingWorker

log = get_logger("ui.window")

_RESOLUTIONS = [(640, 480), (1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)]
_FPS_CHOICES = [30, 60]


class MainWindow(QMainWindow):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.engine = ProcessingEngine(config)
        self.worker: Optional[ProcessingWorker] = None
        self._last_frame: Optional[np.ndarray] = None
        self._rgb_ref: Optional[np.ndarray] = None

        self.setWindowTitle("Offline Face Replacement — MVP")
        self.resize(1240, 820)
        self._build_ui()
        self._start()

    # -- construction --------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)

        self.preview = QLabel("Starting camera…")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(800, 600)
        self.preview.setStyleSheet("background:#101014; color:#8888aa;")
        root.addWidget(self.preview, stretch=3)

        panel = QVBoxLayout()
        panel.addWidget(self._camera_group())
        panel.addWidget(self._object_group())
        panel.addWidget(self._image_group())
        panel.addWidget(self._model_group())
        panel.addWidget(self._audio_group())
        panel.addWidget(self._record_group())
        panel.addStretch(1)

        holder = QWidget()
        holder.setLayout(panel)
        scroll = QScrollArea()
        scroll.setWidget(holder)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(360)
        root.addWidget(scroll, stretch=1)

        self.setCentralWidget(central)
        self.status = self.statusBar()
        self.status.showMessage("Ready")
        self._update_mode_ui()

    # -- reusable slider -----------------------------------------------------
    def _add_slider(
        self, layout: QVBoxLayout, label: str, section: str, key: str,
        lo: int, hi: int, to_cfg: Callable[[int], float], from_cfg: Callable[[float], float],
    ) -> QSlider:
        layout.addWidget(QLabel(label))
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(int(from_cfg(float(self.config.get(section, key)))))
        s.valueChanged.connect(lambda v: self.config.set(section, key, to_cfg(v)))
        s.sliderReleased.connect(lambda: self.config.save(section))
        layout.addWidget(s)
        return s

    def _camera_group(self) -> QGroupBox:
        box = QGroupBox("Camera")
        v = QVBoxLayout(box)
        self.device_combo = QComboBox()
        self.devices = enumerate_cameras()
        if self.devices:
            for d in self.devices:
                self.device_combo.addItem(str(d), d.index)
        else:
            self.device_combo.addItem("No camera detected", 0)
        v.addWidget(self.device_combo)

        self.res_combo = QComboBox()
        for w, h in _RESOLUTIONS:
            self.res_combo.addItem(f"{w} x {h}", (w, h))
        cur = (int(self.config.get("camera", "width")), int(self.config.get("camera", "height")))
        if cur in _RESOLUTIONS:
            self.res_combo.setCurrentIndex(_RESOLUTIONS.index(cur))
        v.addWidget(self.res_combo)

        self.fps_combo = QComboBox()
        for f in _FPS_CHOICES:
            self.fps_combo.addItem(f"{f} FPS", f)
        if int(self.config.get("camera", "fps")) in _FPS_CHOICES:
            self.fps_combo.setCurrentIndex(_FPS_CHOICES.index(int(self.config.get("camera", "fps"))))
        v.addWidget(self.fps_combo)

        apply_btn = QPushButton("Apply camera settings")
        apply_btn.clicked.connect(self._apply_camera)
        v.addWidget(apply_btn)
        return box

    def _object_group(self) -> QGroupBox:
        box = QGroupBox("Replacement Object")
        v = QVBoxLayout(box)

        v.addWidget(QLabel("Mode"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("2D image (PNG/JPG)", "image")
        self.mode_combo.addItem("3D model (FBX/OBJ/glTF)", "model")
        self.mode_combo.setCurrentIndex(0 if self.engine.mode == "image" else 1)
        self.mode_combo.currentIndexChanged.connect(self._change_mode)
        v.addWidget(self.mode_combo)

        self.enable_chk = QCheckBox("Enable replacement")
        self.enable_chk.setChecked(bool(self.config.get("render", "enabled")))
        self.enable_chk.toggled.connect(lambda s: self._set_cfg("render", "enabled", bool(s)))
        v.addWidget(self.enable_chk)
        return box

    def _image_group(self) -> QGroupBox:
        self.image_box = QGroupBox("2D Image")
        v = QVBoxLayout(self.image_box)
        self.overlay_label = QLabel(self._overlay_name())
        self.overlay_label.setWordWrap(True)
        v.addWidget(self.overlay_label)

        row = QHBoxLayout()
        load = QPushButton("Load PNG/JPG…")
        load.clicked.connect(self._load_overlay)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear_overlay)
        row.addWidget(load)
        row.addWidget(clear)
        v.addLayout(row)

        self.follow_chk = QCheckBox("Follow head rotation")
        self.follow_chk.setChecked(bool(self.config.get("render", "follow_rotation")))
        self.follow_chk.toggled.connect(lambda s: self._set_cfg("render", "follow_rotation", bool(s)))
        v.addWidget(self.follow_chk)

        self._add_slider(v, "Opacity", "render", "opacity", 0, 100, lambda x: x/100, lambda v: v*100)
        self._add_slider(v, "Size (fit to face)", "render", "size_factor", 50, 300, lambda x: x/100, lambda v: v*100)
        self._add_slider(v, "Scale", "render", "scale", 25, 300, lambda x: x/100, lambda v: v*100)
        self._add_slider(v, "Rotation offset", "render", "rotation_offset_deg", -180, 180, float, lambda v: v)
        self._add_slider(v, "Offset X", "render", "offset_x", -100, 100, lambda x: x/100, lambda v: v*100)
        self._add_slider(v, "Offset Y", "render", "offset_y", -100, 100, lambda x: x/100, lambda v: v*100)
        return self.image_box

    def _model_group(self) -> QGroupBox:
        self.model_box = QGroupBox("3D Model")
        v = QVBoxLayout(self.model_box)
        self.model_label = QLabel(self._model_name())
        self.model_label.setWordWrap(True)
        v.addWidget(self.model_label)

        row = QHBoxLayout()
        load = QPushButton("Load FBX/OBJ/glTF…")
        load.clicked.connect(self._load_model)
        row.addWidget(load)
        v.addLayout(row)

        self._add_slider(v, "Opacity", "model", "opacity", 0, 100, lambda x: x/100, lambda v: v*100)
        self._add_slider(v, "Size (fit to face)", "model", "size_factor", 50, 400, lambda x: x/100, lambda v: v*100)
        self._add_slider(v, "Scale", "model", "scale", 25, 300, lambda x: x/100, lambda v: v*100)
        self._add_slider(v, "Rotate X (pitch)", "model", "rot_x", -180, 180, float, lambda v: v)
        self._add_slider(v, "Rotate Y (yaw)", "model", "rot_y", -180, 180, float, lambda v: v)
        self._add_slider(v, "Rotate Z (roll)", "model", "rot_z", -180, 180, float, lambda v: v)
        self._add_slider(v, "Offset X", "model", "offset_x", -100, 100, lambda x: x/100, lambda v: v*100)
        self._add_slider(v, "Offset Y", "model", "offset_y", -100, 100, lambda x: x/100, lambda v: v*100)
        return self.model_box

    def _audio_group(self) -> QGroupBox:
        box = QGroupBox("Audio (music)")
        v = QVBoxLayout(box)
        self.audio_label = QLabel("No track loaded")
        self.audio_label.setWordWrap(True)
        v.addWidget(self.audio_label)

        load = QPushButton("Load MP3/WAV/OGG…")
        load.clicked.connect(self._load_audio)
        v.addWidget(load)

        row = QHBoxLayout()
        self.play_btn = QPushButton("▶ Play")
        self.play_btn.clicked.connect(self._audio_play)
        pause = QPushButton("⏸ Pause")
        pause.clicked.connect(self._audio_pause)
        stop = QPushButton("⏹ Stop")
        stop.clicked.connect(self._audio_stop)
        for b in (self.play_btn, pause, stop):
            row.addWidget(b)
        v.addLayout(row)

        self.loop_chk = QCheckBox("Loop")
        self.loop_chk.setChecked(bool(self.config.get("audio", "loop")))
        self.loop_chk.toggled.connect(self._audio_loop)
        v.addWidget(self.loop_chk)

        v.addWidget(QLabel("Volume"))
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 200)
        self.volume_slider.setValue(int(float(self.config.get("audio", "volume")) * 100))
        self.volume_slider.valueChanged.connect(self._audio_volume)
        v.addWidget(self.volume_slider)

        v.addWidget(QLabel("Pitch (0.5x – 2.0x)"))
        self.pitch_slider = QSlider(Qt.Horizontal)
        self.pitch_slider.setRange(50, 200)
        self.pitch_slider.setValue(int(float(self.config.get("audio", "pitch")) * 100))
        self.pitch_slider.valueChanged.connect(self._audio_pitch)
        v.addWidget(self.pitch_slider)
        return box

    def _record_group(self) -> QGroupBox:
        box = QGroupBox("Recording")
        v = QVBoxLayout(box)
        self.record_btn = QPushButton("● Start Recording")
        self.record_btn.clicked.connect(self._toggle_record)
        v.addWidget(self.record_btn)
        snap = QPushButton("Snapshot (PNG)")
        snap.clicked.connect(self._snapshot)
        v.addWidget(snap)
        return box

    # -- engine lifecycle ----------------------------------------------------
    def _start(self) -> None:
        try:
            self.engine.start_camera()
        except Exception as exc:
            QMessageBox.critical(self, "Camera error", str(exc))
            log.error("Camera start failed: %s", exc)
        self.worker = ProcessingWorker(self.engine)
        self.worker.frame_ready.connect(self._on_frame)
        self.worker.status.connect(self._on_status)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _apply_camera(self) -> None:
        idx = self.device_combo.currentData()
        w, h = self.res_combo.currentData()
        fps = self.fps_combo.currentData()
        try:
            self.engine.restart_camera(int(idx), int(w), int(h), int(fps))
            self.status.showMessage(f"Camera set to {w}x{h}@{fps}", 4000)
        except Exception as exc:
            QMessageBox.warning(self, "Camera error", str(exc))

    # -- mode ----------------------------------------------------------------
    def _change_mode(self) -> None:
        mode = self.mode_combo.currentData()
        self.engine.set_mode(mode)
        self._update_mode_ui()

    def _update_mode_ui(self) -> None:
        is_image = self.engine.mode == "image"
        self.image_box.setVisible(is_image)
        self.model_box.setVisible(not is_image)

    # -- image handlers ------------------------------------------------------
    def _overlay_name(self) -> str:
        p = self.engine.overlay.path
        return f"Loaded: {p.name}" if p else "No image loaded"

    def _model_name(self) -> str:
        p = self.config.get("model", "model_path", "")
        return f"Loaded: {Path(p).name}" if p else "No model loaded"

    def _default_dir(self) -> str:
        for d in (ASSETS_DIR / "images", TEST_DIR, ASSETS_DIR):
            if d.exists():
                return str(d)
        return ""

    def _load_overlay(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select overlay image", self._default_dir(), "Images (*.png *.jpg *.jpeg)")
        if not path:
            return
        try:
            self.engine.load_overlay(path)
            self.overlay_label.setText(self._overlay_name())
            self.status.showMessage(f"Loaded {Path(path).name}", 4000)
        except Exception as exc:
            QMessageBox.warning(self, "Could not load image", str(exc))

    def _clear_overlay(self) -> None:
        self.engine.overlay.clear()
        self._set_cfg("render", "overlay_image", "", save=True)
        self.overlay_label.setText(self._overlay_name())

    def _load_model(self) -> None:
        start_dir = str(ASSETS_DIR / "models") if (ASSETS_DIR / "models").exists() else self._default_dir()
        path, _ = QFileDialog.getOpenFileName(
            self, "Select 3D model", start_dir,
            "3D models (*.obj *.fbx *.gltf *.glb *.ply *.stl)")
        if not path:
            return
        try:
            self.engine.load_model(path)
            self.model_label.setText(self._model_name())
            self.status.showMessage(f"Loaded {Path(path).name}", 4000)
        except Exception as exc:
            QMessageBox.warning(self, "Could not load model", str(exc))

    # -- audio handlers ------------------------------------------------------
    def _load_audio(self) -> None:
        start = str(ASSETS_DIR / "music") if (ASSETS_DIR / "music").exists() else self._default_dir()
        path, _ = QFileDialog.getOpenFileName(
            self, "Select audio track", start, "Audio (*.mp3 *.wav *.ogg *.flac)")
        if not path:
            return
        try:
            audio = self.engine.ensure_audio()
            audio.load(path)
            self.audio_label.setText(f"Loaded: {Path(path).name}  ({audio.duration:.1f}s)")
            self._set_cfg("audio", "last_file", path, save=True)
        except Exception as exc:
            QMessageBox.warning(self, "Audio error", str(exc))

    def _audio_play(self) -> None:
        try:
            self.engine.ensure_audio().play()
        except Exception as exc:
            QMessageBox.warning(self, "Audio error", str(exc))

    def _audio_pause(self) -> None:
        if self.engine.audio is not None:
            self.engine.audio.pause()

    def _audio_stop(self) -> None:
        if self.engine.audio is not None:
            self.engine.audio.stop()

    def _audio_loop(self, on: bool) -> None:
        self._set_cfg("audio", "loop", bool(on), save=True)
        if self.engine.audio is not None:
            self.engine.audio.set_loop(bool(on))

    def _audio_volume(self, val: int) -> None:
        self._set_cfg("audio", "volume", val / 100.0)
        if self.engine.audio is not None:
            self.engine.audio.set_volume(val / 100.0)

    def _audio_pitch(self, val: int) -> None:
        self._set_cfg("audio", "pitch", val / 100.0)
        if self.engine.audio is not None:
            self.engine.audio.set_pitch(val / 100.0)

    # -- recording handlers --------------------------------------------------
    def _toggle_record(self) -> None:
        if self.engine.is_recording:
            path = self.engine.stop_recording()
            self.record_btn.setText("● Start Recording")
            self.record_btn.setStyleSheet("")
            if path:
                self.status.showMessage(f"Saved {path}", 6000)
                QMessageBox.information(self, "Recording saved", f"Saved to:\n{path}")
        else:
            try:
                path = self.engine.start_recording()
                self.record_btn.setText("■ Stop Recording")
                self.record_btn.setStyleSheet("background:#a02020; color:white;")
                self.status.showMessage(f"Recording -> {path}", 4000)
            except Exception as exc:
                QMessageBox.warning(self, "Recording error", str(exc))

    def _snapshot(self) -> None:
        if self._last_frame is None:
            self.status.showMessage("No frame to snapshot yet", 3000)
            return
        try:
            path = self.engine.snapshot(self._last_frame)
            self.status.showMessage(f"Snapshot saved: {path}", 5000)
        except Exception as exc:
            QMessageBox.warning(self, "Snapshot error", str(exc))

    # -- config helper -------------------------------------------------------
    def _set_cfg(self, section: str, key: str, value, save: bool = False) -> None:
        self.config.set(section, key, value)
        if save:
            self.config.save(section)

    # -- worker signals ------------------------------------------------------
    def _on_frame(self, frame_bgr: np.ndarray) -> None:
        self._last_frame = frame_bgr
        rgb = np.ascontiguousarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        h, w = rgb.shape[:2]
        self._rgb_ref = rgb  # keep buffer alive for the QImage
        image = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(image).scaled(
            self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview.setPixmap(pix)

    def _on_status(self, text: str) -> None:
        self.status.showMessage(text)

    def _on_error(self, text: str) -> None:
        self.status.showMessage(f"⚠ {text}", 5000)

    # -- shutdown ------------------------------------------------------------
    def closeEvent(self, event) -> None:
        log.info("Shutting down")
        if self.worker is not None:
            self.worker.stop()
        self.engine.shutdown()
        self.config.save()
        super().closeEvent(event)
