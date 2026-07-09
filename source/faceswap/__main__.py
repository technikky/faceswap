"""Application entry point.

Run with:  python -m faceswap      (from the ``source/`` directory)
       or:  python source/run_app.py
"""
from __future__ import annotations

import sys

from .config import Config
from .logging_setup import setup_logging


def main() -> int:
    log = setup_logging()
    log.info("Starting Offline Face Replacement (MVP)")

    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:
        log.error("PySide6 is required to run the UI: %s", exc)
        print("ERROR: PySide6 is not installed. Run: pip install -r requirements.txt")
        return 2

    from .ui import MainWindow

    config = Config.load()
    app = QApplication(sys.argv)
    window = MainWindow(config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
