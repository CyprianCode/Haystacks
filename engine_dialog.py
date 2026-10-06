"""
The "Speech engine" dialog: one-time download of the Orukeet model and the
runtime that suits this PC, with progress, plus switching device later.
"""
import queue
import threading
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QDialog, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QRadioButton, QVBoxLayout)

import engine
import theme

MB = 1_000_000


class EngineDialog(QDialog):
    def __init__(self, parent=None, first_time=False):
        super().__init__(parent)
        self.setWindowTitle("Speech engine")
        self.setMinimumWidth(620)
        self.msgs = queue.Queue()
        self.busy = False
        self.installed = None  # device name once set up

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(10)
        head = QLabel("Speech engine")
        head.setObjectName("section")
        lay.addWidget(head)
        cur = engine.current()
        intro = ("Haystacks transcribes on this PC with the Orukeet speech engine. "
                 "Before the first transcription it needs a one-time download of about "
                 "750 to 850 MB." if first_time or not cur else
                 f"Installed: {engine.DEVICES[cur['device']]}. You can switch to another "
                 "engine; files already downloaded are reused.")
        text = QLabel(intro)
        text.setObjectName("muted")
        text.setWordWrap(True)
        lay.addWidget(text)

        best, reason = engine.recommended()
        self.choice = QButtonGroup(self)
        notes = {"cuda": "fastest", "vulkan": "fast; AMD, Intel and NVIDIA cards",
                 "cpu": "slowest, works on every PC"}
        for device, name in engine.DEVICES.items():
            label = f"{name}: {notes[device]}"
            if device == best:
                label += f"  (recommended: {reason})"
            b = QRadioButton(label)
            b.setProperty("device", device)
            b.setChecked(device == (cur["device"] if cur and not first_time else best))
            self.choice.addButton(b)
            lay.addWidget(b)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setVisible(False)
        lay.addWidget(self.bar)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        credit = QLabel('Speech model: <a href="https://github.com/Oruk-AI/orukeet">Orukeet</a> '
                        "by Oruk AI, based on NVIDIA Parakeet TDT 0.6B v3, licensed "
                        '<a href="https://creativecommons.org/licenses/by-sa/4.0/">CC BY-SA 4.0</a>.')
        credit.setObjectName("caption")
        credit.setOpenExternalLinks(True)
        credit.setWordWrap(True)
        lay.addWidget(credit)

        row = QHBoxLayout()
        row.addStretch(1)
        self.close_btn = QPushButton("Cancel")
        self.close_btn.clicked.connect(self.reject)
        self.go = QPushButton("Download and set up" if not cur else "Switch engine")
        self.go.setProperty("accent", True)
        self.go.clicked.connect(self.start)
        row.addWidget(self.close_btn)
        row.addWidget(self.go)
        lay.addLayout(row)
        self.timer = QTimer(self, interval=250, timeout=self.poll)

    def device(self):
        return self.choice.checkedButton().property("device")

    def start(self):
        device = self.device()
        model_bytes, runtime_bytes = engine.sizes(device)
        # Downloads land in the cache folder, and runtimes are unzipped there
        # too, so its growth tracks progress well enough for a bar.
        self.expected = model_bytes + 2 * runtime_bytes
        self.start_size = engine.folder_size(engine.cache_dir())
        self.busy = True
        for b in self.choice.buttons():
            b.setEnabled(False)
        self.go.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.bar.setVisible(True)
        self.bar.setRange(0, 1000 if self.expected else 0)
        self.started = time.time()

        def work():
            try:
                self.msgs.put(("done", engine.install(device, log=lambda t: self.msgs.put(("log", t)))))
            except Exception as e:
                self.msgs.put(("error", str(e)))
        threading.Thread(target=work, daemon=True).start()
        self.timer.start()

    def poll(self):
        try:
            while True:
                kind, value = self.msgs.get_nowait()
                if kind == "log":
                    self.status.setText(value)
                elif kind == "done":
                    self.finish(value)
                    return
                elif kind == "error":
                    self.fail(value)
                    return
        except queue.Empty:
            pass
        if self.expected:
            got = max(0, engine.folder_size(engine.cache_dir()) - self.start_size)
            self.bar.setValue(min(999, int(got / self.expected * 1000)))
            rate = got / max(1, time.time() - self.started)
            left = (self.expected - got) / rate if rate > 0 else 0
            when = f", about {max(1, round(left / 60))} min left" if got > 5 * MB else ""
            if "Downloading" in self.status.text() or not self.status.text():
                self.status.setText(f"Downloading: {got // MB:,} of about "
                                    f"{self.expected // MB:,} MB{when}")

    def finish(self, device):
        self.timer.stop()
        self.busy = False
        self.installed = device
        self.bar.setRange(0, 1000)
        self.bar.setValue(1000)
        chosen = self.device()
        msg = f"Ready: {engine.DEVICES[device]}."
        if device != chosen:
            msg = (f"The {engine.DEVICES[chosen]} engine didn't work on this PC, so "
                   f"Haystacks will use the {engine.DEVICES[device]}. " + msg)
        self.status.setText(msg)
        self.go.setVisible(False)
        self.close_btn.setText("Done")
        self.close_btn.setProperty("accent", True)
        self.close_btn.style().unpolish(self.close_btn)
        self.close_btn.style().polish(self.close_btn)
        self.close_btn.setEnabled(True)
        self.close_btn.clicked.disconnect()
        self.close_btn.clicked.connect(self.accept)

    def fail(self, error):
        self.timer.stop()
        self.busy = False
        self.bar.setVisible(False)
        self.status.setText(f"Setup didn't finish: {error}\n\nCheck the internet connection "
                            "and try again; finished parts are kept.")
        for b in self.choice.buttons():
            b.setEnabled(True)
        self.go.setEnabled(True)
        self.go.setText("Try again")
        self.close_btn.setEnabled(True)

    def showEvent(self, event):
        super().showEvent(event)
        theme.style_window(self)

    def reject(self):
        if not self.busy:  # a download can't be interrupted halfway
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)
