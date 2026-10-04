import os
import sys
from pathlib import Path


def state_dir():
    override = os.environ.get("MORI_COMPANION_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "mori-companion"


def ensure_state_dir():
    path = state_dir()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path
