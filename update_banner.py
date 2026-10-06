"""
The "new version available" banner at the top of the main window, with
one-click updating (see updater.py). Hidden until there is something to say.
"""
import queue
import threading

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton

import paths
import updater
from version import __version__

FIRST_CHECK_MS = 8000  # let the app settle before going online


class UpdateBanner(QFrame):
    def __init__(self, settings, save, busy, quit_app, parent=None):
        super().__init__(parent)
        self.setObjectName("banner")
        self.settings = settings
        self.save = save
        self.busy = busy            # True while a transcription runs
        self.quit_app = quit_app    # closes the app without asking
        self.release = None
        self.msgs = queue.Queue()
        self.cancel = threading.Event()

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 12, 10)
        row.setSpacing(10)
        self.text = QLabel("")
        self.text.setWordWrap(True)
        row.addWidget(self.text, 1)
        self.bar = QProgressBar()
        self.bar.setFixedWidth(180)
        self.bar.setVisible(False)
        row.addWidget(self.bar)
        self.notes_btn = self._button("What's new", self.open_page)
        self.later_btn = self._button("Later", self.later)
        self.update_btn = self._button("Update now" if paths.FROZEN else "Download",
                                       self.update_now)
        for b in (self.notes_btn, self.later_btn, self.update_btn):
            row.addWidget(b)
        self.setVisible(False)

        self.timer = QTimer(self, interval=200, timeout=self.poll)
        QTimer.singleShot(FIRST_CHECK_MS, self.check)

    def _button(self, text, action, accent=False):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        if accent:
            b.setProperty("accent", True)
        b.clicked.connect(action)
        return b

    # ---- checking -----------------------------------------------------------
    def check(self):
        def work():
            try:
                self.msgs.put(("found", updater.check(self.settings)))
            except Exception as e:
                # Offline etc.: stay quiet and try again next time, except when
                # testing with HAYSTACKS_UPDATE_URL, where the reason helps.
                self.msgs.put(("check_failed", f"{type(e).__name__}: {e}"))
        threading.Thread(target=work, daemon=True).start()
        self.timer.start()

    def poll(self):
        try:
            while True:
                kind, *args = self.msgs.get_nowait()
                getattr(self, "on_" + kind)(*args)
        except queue.Empty:
            pass

    def on_found(self, release):
        self.timer.stop()
        self.save()  # records the check time
        if not release:
            return
        self.release = release
        self.text.setText(f"Haystacks {release['version']} is available. You have {__version__}."
                          + ("" if paths.FROZEN else " You're running from source: update "
                             "with git pull, or download the installer."))
        self.setVisible(True)

    def on_check_failed(self, error):
        self.timer.stop()
        if updater.TEST_URL:
            self.text.setText(f"Update check failed: {error}")
            for b in (self.notes_btn, self.later_btn, self.update_btn):
                b.setVisible(False)
            self.setVisible(True)

    # ---- buttons ------------------------------------------------------------
    def open_page(self):
        QDesktopServices.openUrl(QUrl(self.release["page"]))

    def later(self):
        """Hide, and don't offer this version again (a newer one will show)."""
        self.settings.setdefault("update", {})["skipped"] = self.release["tag"]
        self.save()
        self.setVisible(False)

    def update_now(self):
        if not paths.FROZEN:
            return self.open_page()
        if self.busy() and QMessageBox.question(
                self, "Update Haystacks",
                "Videos are being transcribed. Updating closes Haystacks and stops "
                "the run; the video in progress is redone next time.\n\nUpdate now?") \
                != QMessageBox.Yes:
            return
        for b in (self.notes_btn, self.later_btn, self.update_btn):
            b.setEnabled(False)
        self.bar.setVisible(True)
        self.bar.setRange(0, 1000)
        self.text.setText(f"Downloading Haystacks {self.release['version']}...")
        self.cancel.clear()

        def work():
            try:
                path = updater.download(
                    self.release, cancel=self.cancel,
                    on_progress=lambda got, total: self.msgs.put(("progress", got, total)))
                self.msgs.put(("downloaded", path))
            except Exception as e:
                self.msgs.put(("failed", str(e)))
        threading.Thread(target=work, daemon=True).start()
        self.timer.start()

    def on_progress(self, got, total):
        if total:
            self.bar.setValue(int(got / total * 1000))
            self.text.setText(f"Downloading Haystacks {self.release['version']}: "
                              f"{got // 1_000_000} of {total // 1_000_000} MB")

    def on_downloaded(self, path):
        self.timer.stop()
        self.text.setText("Installing the update; Haystacks will restart...")
        try:
            updater.run_installer(path)
        except OSError as e:
            return self.on_failed(f"couldn't start the installer: {e}")
        QTimer.singleShot(300, self.quit_app)

    def on_failed(self, error):
        self.timer.stop()
        self.bar.setVisible(False)
        self.text.setText(f"The update didn't download: {error}")
        for b in (self.notes_btn, self.later_btn, self.update_btn):
            b.setEnabled(True)
        self.update_btn.setText("Try again")

    def stop(self):
        self.cancel.set()
