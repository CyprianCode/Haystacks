"""
Haystacks: transcribe whole folders of videos and search what was said.

Opens on the main window (search_window.py): search every folder's
transcripts and play any moment in the built-in player. "Add videos" opens
the transcription window (transcribe_window.py): add a folder, pick the
audio track, press Transcribe. Finished videos are skipped, so you can stop
anytime and continue later.

Installed copies are built with PyInstaller (haystacks.spec) and set up by
the installer (installer.iss); the speech engine is downloaded from inside
the app on first use. From source: install requirements.txt into .venv, then
start it with the desktop shortcut (make_shortcut.ps1 creates it), or:
    .\\.venv\\Scripts\\pythonw.exe Haystacks.pyw
"""
import json
import os
import sys
import traceback
from pathlib import Path


def show_error(text):
    """Error box that works even if Qt failed to load (pythonw has no console)."""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        box = QMessageBox(QMessageBox.Critical, "Haystacks",
                          "Something went wrong. The details below help with fixing it.")
        box.setDetailedText(text)
        box.exec()
    except Exception:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text[-1500:], "Haystacks", 0x10)


try:
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    import paths
    import pipeline
    import theme
    import youtube
    from search_window import MainWindow
    from transcribe_window import TranscribeWindow
except Exception:
    show_error("Could not start. Make sure all the .py files are next to "
               "Haystacks.pyw and that you start it with the Python from the .venv "
               "folder next to it (with PySide6 installed).\n\n" + traceback.format_exc())
    raise SystemExit(1)

SETTINGS = Path(os.environ.get("APPDATA", Path.home())) / "Haystacks" / "settings.json"


def load_settings():
    """{"folders": [{"path", "track", "filter"}], "speed": {"decode", "transcribe"},
        "hidden": ["<folder path>|<stem>", ...], "volume": 0-1, "ui": {...}}
    Speeds are seconds of audio handled per second, remembered between runs
    so time estimates work from the first video. "hidden" lists recordings
    hidden from search; "ui" holds window size and the results/player split."""
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data):
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    pipeline.write_atomic(SETTINGS, json.dumps(data, indent=2))


def main():
    pipeline.cleanup_temp()
    youtube.clear_downloads()
    try:  # own taskbar entry and icon, instead of Python's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Haystacks.Haystacks")
    except Exception:
        pass
    # Needed before the app starts for the YouTube player's web engine, which is
    # only loaded when the first YouTube video plays.
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setApplicationName("Haystacks")
    app.setWindowIcon(QIcon(str(paths.resource("assets/haystacks.ico"))))
    theme.apply(app)
    # Errors inside the app show a box instead of vanishing (no console).
    sys.excepthook = lambda kind, value, tb: show_error(
        "".join(traceback.format_exception(kind, value, tb)))
    settings = load_settings()
    save = lambda: save_settings(settings)
    win = MainWindow(settings, save, make_transcriber=lambda on_change: TranscribeWindow(
        settings, save, on_change))
    win.show()
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        show_error(traceback.format_exc())
