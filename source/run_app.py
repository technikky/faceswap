"""Convenience launcher so you can run the app without setting PYTHONPATH.

    python source/run_app.py
"""
import os
import sys

# Ensure the package on <root>/source is importable.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from faceswap.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
