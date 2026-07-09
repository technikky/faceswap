"""Project path resolution.

Everything is resolved relative to the project root so the app runs from any
working directory. The project root is the folder that contains ``config/``.
"""
from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    """Return the project root (the folder containing ``config/``).

    This file lives at ``<root>/source/faceswap/paths.py``, so the root is two
    parents up from the ``faceswap`` package directory.
    """
    return Path(__file__).resolve().parents[2]


ROOT = project_root()
CONFIG_DIR = ROOT / "config"
ASSETS_DIR = ROOT / "assets"
TEST_DIR = ROOT / "test"
LOGS_DIR = ROOT / "logs"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
