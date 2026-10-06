"""
Where Haystacks keeps its files, both when installed and when run from source.

Installed (a PyInstaller build): the app lives in
%LOCALAPPDATA%\\Programs\\Haystacks and is replaced by every update, so the
speech engine (model + runtime, ~800 MB) goes in %LOCALAPPDATA%\\Haystacks\\engine
where updates don't touch it. Settings stay in %APPDATA%\\Haystacks.

From source: an installation.json next to the code (made by
"orukeet install --output installation.json") is used if present.
"""
import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent
# Bundled read-only files (icons) live in PyInstaller's _internal folder.
RESOURCES = Path(getattr(sys, "_MEIPASS", APP_DIR))
DATA = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Haystacks"
ENGINE = DATA / "engine"


def install_json() -> Path:
    """The speech engine's installation.json (it may not exist yet)."""
    dev = APP_DIR / "installation.json"
    if not FROZEN and dev.exists():
        return dev
    return ENGINE / "installation.json"


def resource(name) -> Path:
    return RESOURCES / name
