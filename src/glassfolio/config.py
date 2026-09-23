"""Filesystem locations. Real data never lives inside the repository."""

import os
from pathlib import Path

DEFAULT_HOME = "~/Library/Application Support/Glassfolio"


def data_home() -> Path:
    return Path(os.environ.get("GLASSFOLIO_HOME", DEFAULT_HOME)).expanduser()
