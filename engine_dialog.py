"""
The "Speech engine" dialog: pick a speech model (Orukeet by default), where it
runs, and do the one-time download with progress. Also used to switch later.
"""
import queue
import threading
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QFileDialog, QHBoxLayout,
                               QLabel, QLineEdit, QProgressBar, QPushButton, QRadioButton,
                               QVBoxLayout, QWidget)

import engine
import theme

MB = 1_000_000
DEVICE_NOTES = {"cuda": "fastest", "vulkan": "fast; AMD, Intel and NVIDIA cards",
                "cpu": "slowest, works on every PC"}


def label(text, name=None):
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    w.setWordWrap(True)
    return w


class EngineDialog(QDialog):
    def __init__(self, parent=None, first_time=False):
        super().__init__(parent)
        self.setWindowTitle("Speech engine")
        self.setMinimumWidth(660)
        self.msgs = queue.Queue()
        self.busy = False
        self.installed = None  # installation config once set up
        cur = engine.current()
        self.cur = cur if cur and not first_time else None
        cur_model = engine.model_id(self.cur) if self.cur else engine.DEFAULT

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(8)
        lay.addWidget(label("Speech engine", "section"))
        intro = ("Haystacks transcribes on this PC. Orukeet is recommended; the other "
                 "models suit other languages. The chosen model is downloaded once."
                 if not self.cur else
                 f"Installed: {engine.label(self.cur)}. You can switch to another model or "
                 "engine; files already downloaded are reused.")
        lay.addWidget(label(intro, "muted"))

        # Custom Whisper model: a file or a link.
        self.custom = QWidget()
        row = QHBoxLayout(self.custom)
        row.setContentsMargins(24, 0, 0, 0)
        self.path = QLineEdit()
        self.path.setPlaceholderText("Model file (.bin) or https:// download link")
        if self.cur and engine.model_id(self.cur) == "custom":
            self.path.setText(self.cur.get("model", ""))
        browse = QPushButton("Browse...")
        browse.clicked.connect(self.browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)

        # Custom command.
        self.command_box = QWidget()
        col = QVBoxLayout(self.command_box)
        col.setContentsMargins(24, 0, 0, 0)
        self.command = QLineEdit()
        self.command.setPlaceholderText('"C:\\Tools\\my-asr.exe" --audio {input} --out {output}')
        if self.cur and self.cur.get("engine") == "command":
            self.command.setText(self.cur["command"])
        col.addWidget(self.command)
        col.addWidget(label("{input} is a 16 kHz mono WAV file. {output} is a path without an "
                            "extension: the program must write {output}.json (Whisper or "
                            "whisper.cpp format), {output}.srt or {output}.vtt.", "caption"))

        self.extras = {"custom": self.custom, "command": self.command_box}
        lay.addWidget(label("Model", "section"))
        self.models = QButtonGroup(self)
        for mid, m in engine.MODELS.items():
            text = f"{m['name']}: {m['note']}"
            if mid == engine.DEFAULT:
                text += "  (recommended)"
            b = QRadioButton(text)
            b.setProperty("model", mid)
            b.setChecked(mid == cur_model)
            self.models.addButton(b)
            lay.addWidget(b)
            if mid in self.extras:  # its settings go right under it
                lay.addWidget(self.extras[mid])
        self.models.buttonToggled.connect(lambda b, on: on and self.model_changed())

        # Language, for Whisper models.
        self.lang_row = QWidget()
        row = QHBoxLayout(self.lang_row)
        row.setContentsMargins(0, 4, 0, 0)
        row.addWidget(QLabel("Language:"))
        self.language = QComboBox()
        self.language.setEditable(True)  # any Whisper language code
        for code, name in engine.LANGUAGES:
            self.language.addItem(name, code)
        lang = (self.cur or {}).get("language", "auto")
        i = self.language.findData(lang)
        if i >= 0:
            self.language.setCurrentIndex(i)
        else:
            self.language.setEditText(lang)
        row.addWidget(self.language)
        row.addStretch(1)
        lay.addWidget(self.lang_row)

        self.device_head = label("Run on", "section")
        lay.addWidget(self.device_head)
        self.devices = QButtonGroup(self)
        self.device_buttons = {}
        for device, name in engine.DEVICES.items():
            b = QRadioButton()
            b.setProperty("device", device)
            self.devices.addButton(b)
            self.device_buttons[device] = b
            lay.addWidget(b)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setVisible(False)
        lay.addWidget(self.bar)
        self.status = label("")
        lay.addWidget(self.status)

        self.credit = label("", "caption")
        self.credit.setOpenExternalLinks(True)
        lay.addWidget(self.credit)

        row = QHBoxLayout()
        row.addStretch(1)
        self.close_btn = QPushButton("Cancel")
        self.close_btn.clicked.connect(self.reject)
        self.go = QPushButton("Download and set up" if not self.cur else "Switch")
        self.go.setProperty("accent", True)
        self.go.clicked.connect(self.start)
        row.addWidget(self.close_btn)
        row.addWidget(self.go)
        lay.addLayout(row)
        self.timer = QTimer(self, interval=250, timeout=self.poll)
        self.model_changed(initial=True)

    # ---- choice -------------------------------------------------------------
    def model(self):
        return self.models.checkedButton().property("model")

    def device(self):
        b = self.devices.checkedButton()
        device = b.property("device") if b else None
        return device if device in engine.MODELS[self.model()]["devices"] else None

    def model_changed(self, initial=False):
        mid = self.model()
        m = engine.MODELS[mid]
        self.custom.setVisible(mid == "custom")
        self.command_box.setVisible(mid == "command")
        self.lang_row.setVisible(m["engine"] == "whisper" and "language" not in m)
        self.device_head.setVisible(bool(m["devices"]))
        best, reason = engine.recommended(mid)
        keep = self.cur["device"] if initial and self.cur and self.cur.get("device") in m["devices"] else best
        for device, b in self.device_buttons.items():
            text = f"{engine.DEVICES[device]}: {DEVICE_NOTES[device]}"
            if device == best:
                text += f"  (recommended: {reason})"
            b.setText(text)
            b.setVisible(device in m["devices"])
            if device == keep:
                b.setChecked(True)
        self.credit.setText(m["credit"])
        self.credit.setVisible(bool(m["credit"]))
        self.adjustSize()

    def browse(self):
        name, _ = QFileDialog.getOpenFileName(self, "Whisper model file", "",
                                              "whisper.cpp models (*.bin);;All files (*)")
        if name:
            self.path.setText(name)

    def choice(self):
        lang = self.language.currentData()
        if lang is None or self.language.currentText() != self.language.itemText(
                self.language.currentIndex()):
            lang = self.language.currentText().strip() or "auto"  # typed by hand
        return {"model": self.model(), "device": self.device() or "cpu", "language": lang,
                "path": self.path.text(), "command": self.command.text()}

    # ---- setup --------------------------------------------------------------
    def set_editable(self, on):
        for w in (self.models.buttons() + self.devices.buttons()
                  + [self.custom, self.command_box, self.lang_row, self.go]):
            w.setEnabled(on)

    def start(self):
        choice = self.choice()
        file_bytes, archive_bytes = engine.sizes(choice)
        # Downloads land in the cache folder, and archives are unpacked there
        # too, so its growth tracks progress well enough for a bar.
        self.expected = file_bytes + 2 * archive_bytes
        self.start_size = engine.folder_size(engine.cache_dir())
        self.busy = True
        self.set_editable(False)
        self.close_btn.setEnabled(False)
        self.bar.setVisible(True)
        self.bar.setRange(0, 1000 if self.expected else 0)
        self.status.setText("")
        self.started = time.time()

        def work():
            try:
                self.msgs.put(("done", engine.install(choice, log=lambda t: self.msgs.put(("log", t)))))
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

    def finish(self, cfg):
        self.timer.stop()
        self.busy = False
        self.installed = cfg
        self.bar.setRange(0, 1000)
        self.bar.setValue(1000)
        msg = f"Ready: {engine.label(cfg)}."
        chosen = self.device()
        if chosen and cfg.get("device") != chosen:
            msg = (f"The {engine.DEVICES[chosen]} engine didn't work on this PC, so "
                   f"Haystacks will use the {engine.DEVICES[cfg['device']]}. " + msg)
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
        hint = ("Check the command and try again." if self.model() == "command" else
                "Check the internet connection and try again; finished parts are kept.")
        self.status.setText(f"Setup didn't finish: {error}\n\n{hint}")
        self.set_editable(True)
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
