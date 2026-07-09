"""Human-readable JSON configuration with persistence (PRS section 9).

Each config file under ``config/`` maps to one section. Values are loaded on
startup and written back on change so settings persist between sessions.
Unknown keys are preserved; missing files fall back to the built-in defaults.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict

from .logging_setup import get_logger
from .paths import CONFIG_DIR, ensure_dir

log = get_logger("config")

_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "camera": {
        "device_index": 0,
        "width": 1280,
        "height": 720,
        "fps": 30,
        "mirror": True,
        "backend": "auto",
    },
    "render": {
        "mode": "image",  # "image" (PNG/JPG) or "model" (3D FBX/OBJ/glTF)
        "overlay_image": "",
        "enabled": True,
        "opacity": 1.0,
        "scale": 1.0,
        "rotation_offset_deg": 0.0,
        "offset_x": 0.0,
        "offset_y": 0.0,
        "size_reference": "bbox",
        "size_factor": 1.15,
        "follow_rotation": True,
        "smoothing": 0.5,
    },
    "model": {
        "model_path": "",
        "opacity": 1.0,
        "scale": 1.0,
        "size_factor": 1.6,
        "offset_x": 0.0,
        "offset_y": 0.0,
        "rot_x": 0.0,
        "rot_y": 0.0,
        "rot_z": 0.0,
        "render_size": 512,
    },
    "audio": {
        "last_file": "",
        "volume": 1.0,
        "pitch": 1.0,
        "loop": False,
    },
    "recording": {
        "output_dir": "recordings",
        "codec": "h264",
        "fps": 30,
        "bitrate": "8M",
        "container": "mp4",
        "snapshot_dir": "snapshots",
        "snapshot_format": "png",
    },
    "detection": {
        "model_selection": 0,
        "min_detection_confidence": 0.5,
        "tracking_smoothing": 0.6,
        "lost_frames_before_reset": 15,
    },
}


@dataclass
class Config:
    """Holds all configuration sections and persists them to ``config/``."""

    sections: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Config":
        ensure_dir(CONFIG_DIR)
        sections: Dict[str, Dict[str, Any]] = {}
        for name, defaults in _DEFAULTS.items():
            data = dict(defaults)
            path = CONFIG_DIR / f"{name}.json"
            if path.exists():
                try:
                    on_disk = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(on_disk, dict):
                        data.update(on_disk)
                except (json.JSONDecodeError, OSError) as exc:
                    log.warning("Could not read %s (%s); using defaults", path, exc)
            sections[name] = data
        return cls(sections=sections)

    def section(self, name: str) -> Dict[str, Any]:
        return self.sections.setdefault(name, dict(_DEFAULTS.get(name, {})))

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self.section(section).get(key, default)

    def set(self, section: str, key: str, value: Any) -> None:
        self.section(section)[key] = value

    def save(self, section: str | None = None) -> None:
        ensure_dir(CONFIG_DIR)
        names = [section] if section else list(self.sections)
        for name in names:
            path = CONFIG_DIR / f"{name}.json"
            try:
                path.write_text(
                    json.dumps(self.sections[name], indent=2) + "\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                log.error("Could not write %s: %s", path, exc)
