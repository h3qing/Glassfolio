"""Where bundled resources live: the repository in development, the unpacked
bundle when frozen into the desktop app's sidecar (PyInstaller)."""

import sys
from pathlib import Path


def resource_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[2]
