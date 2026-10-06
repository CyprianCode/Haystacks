"""
The Add videos window: folders, audio track, and transcription progress.
Closing it only hides it, so a run keeps going; the main window shows its
progress in the header (summary()).
"""
import contextlib
import queue
import threading
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog,
                               QFileDialog, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

import pipeline
import theme
import transcripts

GENERIC_TRACKS = [f"{n}" for n in range(1, 5)]
BAR_MAX = 1000


def minutes(seconds):
    m = round(seconds / 60)
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{max(m, 1)} min"


def approx(seconds):
    return "under a minute" if seconds < 60 else "about " + minutes(seconds)


def clock(seconds):
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def button(text, action, accent=False):
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    if accent:
        b.setProperty("accent", True)
    b.clicked.connect(action)
    return b


def label(text="", name=None):
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    return w


class TranscribeWindow(QWidget):
    """on_change() is called when the searchable transcripts may have changed."""

    def __init__(self, settings, save, on_change=lambda: None):
        super().__init__(None)
        self.setObjectName("window")
        self.settings = settings
        self.save = save
        self.on_change = on_change
        self.folders = settings.setdefault("folders", [])
        self.speed = settings.setdefault("speed", {})
        self.track_cache = {}           # folder path -> list of track labels
        self.msgs = queue.Queue()
        self.stop = threading.Event()
        self.running = False
        self.run = None                 # progress state of the current run

        self.setWindowTitle("Add videos")
        self.setMinimumSize(860, 700)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(16)

        head = QHBoxLayout()
        head.addWidget(label("Add videos", "title"))
        head.addSpacing(12)
        head.addWidget(label("Transcribed videos show up in search when a run finishes",
                             "subtitle"))
        head.addStretch(1)
        outer.addLayout(head)

        # Folders panel
        fp = theme.panel()
        fl = QVBoxLayout(fp)
        fl.setContentsMargins(18, 16, 18, 16)
        fl.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(label("Folders", "section"))
        top.addStretch(1)
        self.add_btn = button("Add folder...", self.add_folder)
        self.import_btn = button("Import transcripts...", self.import_transcripts)
        self.remove_btn = button("Remove from list", self.remove_folder)
        self.refresh_btn = button("Refresh counts", self.refresh)
        for b in (self.add_btn, self.import_btn, self.remove_btn, self.refresh_btn):
            top.addWidget(b)
        fl.addLayout(top)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Folder", "Audio track", "Name contains", "Transcribed"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setFixedHeight(170)
        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        for col, w in ((1, 110), (2, 140), (3, 130)):
            hdr.setSectionResizeMode(col, QHeaderView.Fixed)
            hdr.resizeSection(col, w)
        hdr.setStretchLastSection(False)
        self.tree.itemSelectionChanged.connect(self.on_select)
        fl.addWidget(self.tree)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        grid.addWidget(label("Audio track"), 0, 0)
        self.track_box = QComboBox()
        self.track_box.setMinimumWidth(280)
        self.track_box.activated.connect(lambda _: self.apply_settings())
        grid.addWidget(self.track_box, 1, 0)
        grid.addWidget(label("Only videos whose name contains"), 0, 1)
        self.filter_entry = QLineEdit()
        self.filter_entry.setFixedWidth(240)
        self.filter_entry.editingFinished.connect(self.apply_settings)
        grid.addWidget(self.filter_entry, 1, 1)
        grid.addWidget(label("Blank means all videos. Press Enter to apply.", "muted"), 2, 1)
        grid.setColumnStretch(2, 1)
        self.clear_btn = button("Clear finished videos...", self.clear_finished)
        grid.addWidget(self.clear_btn, 1, 3)
        fl.addLayout(grid)
        outer.addWidget(fp)

        # Transcription panel
        tp = theme.panel()
        tl = QVBoxLayout(tp)
        tl.setContentsMargins(18, 16, 18, 16)
        tl.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(label("Transcription", "section"))
        top.addStretch(1)
        self.run_sel_btn = button("Transcribe selected folder",
                                  lambda: self.start(all_folders=False), accent=True)
        self.run_all_btn = button("Transcribe all folders", lambda: self.start(all_folders=True))
        self.stop_btn = button("Stop after this video", self.request_stop)
        self.stop_btn.setEnabled(False)
        for b in (self.run_sel_btn, self.run_all_btn, self.stop_btn):
            top.addWidget(b)
        tl.addLayout(top)

        # The deck readouts: elapsed, time remaining, speed.
        ro = QHBoxLayout()
        ro.setSpacing(56)
        self.readouts = {}
        for key, caption in (("elapsed", "elapsed"), ("left", "time remaining"),
                             ("speed", "speed")):
            cell = QVBoxLayout()
            cell.setSpacing(0)
            value = label("0:00:00" if key == "elapsed" else "--", "readout")
            cap = label(caption, "caption")
            cell.addWidget(value)
            cell.addWidget(cap)
            ro.addLayout(cell)
            self.readouts[key] = (value, cap)
        ro.addStretch(1)
        tl.addSpacing(6)
        tl.addLayout(ro)
        tl.addSpacing(10)

        self.cur_label, self.cur_detail, self.cur_bar = self.progress_row(tl, "Current video: none")
        tl.addSpacing(8)
        self.all_label, self.all_detail, self.all_bar = self.progress_row(tl, "All videos: none")
        tl.addSpacing(6)
        self.status = label("Ready.", "muted")
        tl.addWidget(self.status)
        outer.addWidget(tp)

        # Activity log
        lp = theme.panel()
        ll = QVBoxLayout(lp)
        ll.setContentsMargins(18, 16, 18, 16)
        ll.addWidget(label("Activity", "section"))
        self.log_box = QPlainTextEdit()
        self.log_box.setObjectName("log")
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(90)
        ll.addWidget(self.log_box)
        outer.addWidget(lp, 1)

        # Disabled during a run. The folder fields are handled by sync_folder_fields.
        self.edit_widgets = [self.add_btn, self.import_btn, self.refresh_btn, self.run_sel_btn,
                             self.run_all_btn]

        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(min(1040, screen.width() - 80), min(920, screen.height() - 60))

        self.refresh()
        self.tree.setFocus()
        if self.folders:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        else:
            self.on_select()
            self.log("Click 'Add folder...' to choose a folder of videos.")
        if not pipeline.find_tool("ffmpeg"):
            self.log("ffmpeg was not found. Install it from PowerShell with:\n"
                     f"    {pipeline.APP_DIR}\\.venv\\Scripts\\python.exe -m pip install "
                     "static-ffmpeg\nthen restart this app.")
        tr, dec = self.speed.get("transcribe"), self.speed.get("decode")
        if tr:
            speed = 1 / (1 / tr + (1 / dec if dec else 0.0))
            self.set_readout("speed", f"{speed:.1f}×", "realtime speed, from last run")
        self.timer = QTimer(self, interval=100, timeout=self.poll)
        self.timer.start()

    def progress_row(self, layout, text):
        row = QHBoxLayout()
        name = label(text)
        detail = label("", "muted")
        row.addWidget(name)
        row.addStretch(1)
        row.addWidget(detail)
        layout.addLayout(row)
        bar = QProgressBar()
        bar.setRange(0, BAR_MAX)
        bar.setValue(0)
        layout.addWidget(bar)
        return name, detail, bar

    # ---- helpers ----------------------------------------------------------
    def bring_up(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def showEvent(self, event):
        super().showEvent(event)
        theme.style_window(self)

    def closeEvent(self, event):
        # Only hide: a run keeps going and the main window shows its progress.
        event.ignore()
        self.hide()

    def summary(self):
        """One line about the run in progress, for the main window; "" if none."""
        if not self.running or not self.run:
            return ""
        r = self.run
        if not r["files"]:
            return "Getting ready to transcribe..."
        text = f"Transcribing video {min(r['done_files'] + 1, r['files'])} of {r['files']}"
        total_left = self.estimate()[3]
        return text + (f", {approx(total_left)} left" if total_left is not None else "")

    def log(self, text):
        self.log_box.appendPlainText(text)

    def ask(self, title, text, warn=False):
        box = QMessageBox(QMessageBox.Warning if warn else QMessageBox.Question,
                          title, text, QMessageBox.Yes | QMessageBox.No, self)
        box.setDefaultButton(QMessageBox.No if warn else QMessageBox.Yes)
        return box.exec() == QMessageBox.Yes

    def selected(self):
        items = self.tree.selectedItems()
        return self.tree.indexOfTopLevelItem(items[0]) if items else None

    def refresh(self):
        keep = self.selected()
        self.tree.blockSignals(True)
        self.tree.clear()
        for f in self.folders:
            folder, out, imported = pipeline.entry_dirs(f)
            try:
                videos, todo = pipeline.pending(folder, f["filter"], out)
                done = f"{len(videos) - len(todo)} of {len(videos)}"
            except OSError:
                done = "folder not found"
            name = theme.short_path(folder, 56) + ("  (imported)" if imported else "")
            item = QTreeWidgetItem([name, str(f["track"]), f["filter"] or "(all videos)", done])
            item.setToolTip(0, f"Videos: {folder}\nTranscripts: {out}")
            self.tree.addTopLevelItem(item)
        if keep is not None and keep < len(self.folders):
            self.tree.setCurrentItem(self.tree.topLevelItem(keep))
        self.tree.blockSignals(False)

    def tracks_for(self, f):
        """Track labels for a folder, read from one of its videos."""
        if f["path"] in self.track_cache:
            return self.track_cache[f["path"]]
        labels = GENERIC_TRACKS
        try:
            folder = Path(f["path"])
            sample = (pipeline.list_videos(folder, f["filter"])
                      or pipeline.list_videos(folder, ""))
            if sample:
                tracks = pipeline.list_tracks(sample[0])
                if tracks:
                    labels = [f"{n}: {desc}" if desc else str(n) for n, desc in tracks]
                    if len(tracks) > 1:
                        self.log(f"{folder.name}: videos have {len(tracks)} audio tracks. "
                                 f"Choose the one to transcribe.")
        except Exception as e:
            self.log(f"Could not read audio tracks: {e}")
        self.track_cache[f["path"]] = labels
        return labels

    # ---- events -----------------------------------------------------------
    def sync_folder_fields(self):
        """Folder settings can be changed only with a folder selected and no run going."""
        i = self.selected()
        on = i is not None and not self.running
        for w in (self.track_box, self.filter_entry, self.remove_btn, self.clear_btn):
            w.setEnabled(on)
        imported = on and bool(self.folders[i].get("transcripts"))
        if imported:  # never delete transcripts the user brought in
            self.clear_btn.setEnabled(False)
        self.clear_btn.setToolTip("Transcripts for this folder were imported, so the app "
                                  "won't delete them." if imported else "")

    def on_select(self):
        i = self.selected()
        self.sync_folder_fields()
        if i is None:  # nothing selected: show empty fields, not the last folder's
            self.track_box.clear()
            self.filter_entry.setText("")
            return
        f = self.folders[i]
        self.filter_entry.setText(f["filter"])
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            labels = self.tracks_for(f)
        finally:
            QApplication.restoreOverrideCursor()
        if not any(l.split(":")[0] == str(f["track"]) for l in labels):
            labels = labels + [str(f["track"])]
        self.track_box.clear()
        self.track_box.addItems(labels)
        self.track_box.setCurrentIndex(
            next(n for n, l in enumerate(labels) if l.split(":")[0] == str(f["track"])))

    def apply_settings(self):
        i = self.selected()
        if i is None or self.running:
            return
        f = self.folders[i]
        track = int((self.track_box.currentText() or "1").split(":")[0])
        filt = self.filter_entry.text().strip()
        if (track, filt) != (f["track"], f["filter"]):
            f["track"], f["filter"] = track, filt
            self.save()
            self.refresh()

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder of videos")
        if not d:
            return
        path = str(Path(d))
        for i, f in enumerate(self.folders):
            if f["path"].lower() == path.lower():
                self.tree.setCurrentItem(self.tree.topLevelItem(i))
                return
        self.folders.append({"path": path, "track": 1, "filter": ""})
        self.save()
        self.refresh()
        self.tree.setCurrentItem(self.tree.topLevelItem(len(self.folders) - 1))
        self.on_change()  # it may already hold transcripts from before

    def import_transcripts(self):
        dlg = ImportDialog(self, [f["path"] for f in self.folders], self.tracks_for)
        theme.style_window(dlg)
        if dlg.exec() != QDialog.Accepted:
            return
        self.folders.append(dlg.entry)
        self.save()
        self.refresh()
        self.tree.setCurrentItem(self.tree.topLevelItem(len(self.folders) - 1))
        self.log(f"Imported transcripts for {dlg.entry['path']} "
                 f"(read in place from {dlg.entry['transcripts']}).")
        self.on_change()

    def remove_folder(self):
        i = self.selected()
        if i is None:
            return
        path = self.folders[i]["path"]
        if self.ask("Remove folder", f"Remove this folder from the list?\n\n{path}\n\n"
                                     f"Its videos and transcripts are not deleted."):
            del self.folders[i]
            self.save()
            self.tree.clearSelection()
            self.refresh()
            if self.folders:
                self.tree.setCurrentItem(self.tree.topLevelItem(min(i, len(self.folders) - 1)))
            else:
                self.on_select()
            self.on_change()

    def clear_finished(self):
        i = self.selected()
        if i is None:
            return
        folder, out, imported = pipeline.entry_dirs(self.folders[i])
        if imported:
            return
        n = pipeline.finished_count(out)
        if not n:
            QMessageBox.information(self, "Clear finished videos",
                                    "No videos in this folder are marked as finished.")
            return
        if not self.ask("Clear finished videos",
                        f"Forget the {n} finished {'video' if n == 1 else 'videos'} in\n"
                        f"{folder}?\n\nThis deletes their transcripts, so every video in "
                        f"this folder will be transcribed again next time.\n\n"
                        f"The videos themselves are not touched.", warn=True):
            return
        try:
            removed = pipeline.clear_finished(out)
            self.log(f"Cleared {removed} finished videos in {folder}.")
        except OSError as e:
            QMessageBox.critical(self, "Clear finished videos",
                                 f"Could not delete everything:\n{e}")
        self.refresh()
        self.on_change()

    # ---- running ----------------------------------------------------------
    def start(self, all_folders):
        self.apply_settings()
        if not pipeline.find_tool("ffmpeg"):
            QMessageBox.critical(self, "ffmpeg missing",
                                 "ffmpeg is needed. Install it from PowerShell with:\n\n"
                                 f"{pipeline.APP_DIR}\\.venv\\Scripts\\python.exe -m pip "
                                 "install static-ffmpeg\n\nthen restart this app.")
            return
        if all_folders:
            jobs = [dict(f) for f in self.folders]
        else:
            i = self.selected()
            jobs = [dict(self.folders[i])] if i is not None else []
        if not jobs:
            QMessageBox.information(self, "Transcribe", "Add or select a folder first.")
            return
        self.running = True
        self.stop.clear()
        self.run = {"start": time.time(), "files": 0, "audio": 0.0,
                    "done_files": 0, "done_audio": 0.0, "cur": None,
                    "dec_audio": 0.0, "dec_wall": 0.0, "tr_audio": 0.0, "tr_wall": 0.0}
        for w in self.edit_widgets:
            w.setEnabled(False)
        self.sync_folder_fields()
        self.stop_btn.setEnabled(True)
        self.stop_btn.setText("Stop after this video")
        self.set_bar(self.cur_bar, 0)
        self.set_bar(self.all_bar, 0)
        threading.Thread(target=self.work, args=(jobs,), daemon=True).start()

    def request_stop(self):
        self.stop.set()
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("Stopping...")

    def work(self, jobs):
        """Runs in a background thread; talks to the window through self.msgs."""
        post = lambda *m: self.msgs.put(m)
        log = lambda text: post("log", text)
        pipeline.keep_awake(True)
        done = failed = 0
        audio_s = 0.0
        t_start = time.time()
        try:
            plan = []
            for job in jobs:
                folder, out, imported = pipeline.entry_dirs(job)
                if not folder.is_dir():
                    log(f"Folder not found, skipped: {folder}")
                    continue
                videos, todo = pipeline.pending(folder, job["filter"], out)
                log(f"{folder}: {len(videos)} videos, {len(todo)} to transcribe.")
                plan.append((folder, job["track"], todo, out, imported))
            all_todo = [v for _, _, todo, _, _ in plan for v in todo]
            total = len(all_todo)

            # Video lengths, so progress and time left are weighted by audio length.
            lengths = {}
            if total:
                post("status", f"Measuring the length of {total} videos...")
                with ThreadPoolExecutor(8) as pool:
                    lengths = dict(zip(all_todo, pool.map(pipeline.probe_duration, all_todo)))
                known = [d for d in lengths.values() if d]
                fallback = sum(known) / len(known) if known else 0.0
                lengths = {v: d or fallback for v, d in lengths.items()}
                log(f"{total} videos to transcribe, {minutes(sum(lengths.values()))} of video.")
            post("plan", total, sum(lengths.values()))

            with contextlib.ExitStack() as stack:
                asr = None
                for folder, track, todo, out, imported in plan:
                    for video in todo:
                        if self.stop.is_set():
                            break
                        if asr is None:  # load the model only if there is work
                            post("status", "Loading Orukeet...")
                            model, device = pipeline.load_asr()
                            asr = stack.enter_context(model)
                            log(f"Orukeet ready ({device}).")
                        est = lengths.get(video, 0.0)
                        post("video", done + 1, video.name, est)
                        post("status", f"Working in {folder.name}")
                        last = [-1.0]

                        def on_decode(f, last=last):
                            if f - last[0] >= 0.01:  # at most ~100 updates per video
                                last[0] = f
                                post("decode", f)

                        try:
                            dur, n, dec_wall, tr_wall = pipeline.process(
                                asr, video, track, est or None,
                                on_phase=lambda ph: post("phase", ph, time.time()),
                                on_decode=on_decode, out=out)
                            audio_s += dur
                            log(f"[{done + 1}/{total}] {video.name}: {dur / 60:.0f} min of "
                                f"audio, read in {dec_wall:.0f}s, transcribed in "
                                f"{tr_wall:.0f}s ({dur / max(tr_wall, 1e-6):.1f}x), "
                                f"{n} sentence{'' if n == 1 else 's'}")
                            post("video_done", est, dur, dec_wall, tr_wall)
                        except Exception as e:
                            failed += 1
                            pipeline.log_failure(video, e, out)
                            log(f"[{done + 1}/{total}] FAILED {video.name}: {e}")
                            post("video_done", est, 0.0, 0.0, 0.0)
                        done += 1
                    try:  # dates for transcripts made before dates were stored
                        if not imported:  # imported files are never changed
                            pipeline.add_missing_dates(folder, log)
                    except Exception as e:
                        log(f"Could not read recording dates in {folder}: {e}")
                    if self.stop.is_set():
                        break

            summary = (f"Stopped after {done} of {total} videos." if self.stop.is_set()
                       else f"Finished: {done} videos." if total
                       else "Nothing new to transcribe.")
            if audio_s:
                summary += f" {minutes(audio_s)} of audio in {minutes(time.time() - t_start)}."
            if failed:
                summary += f" {failed} failed, see _failed.txt in the folder."
        except Exception as e:
            log(traceback.format_exc())
            summary = f"Error: {e}"
        finally:
            pipeline.keep_awake(False)
        post("done", summary)

    # ---- progress display -------------------------------------------------
    def speeds(self):
        """(decode, transcribe) speed in audio seconds per second, measured in
        this run if possible, otherwise remembered from earlier runs."""
        r = self.run
        dec = r["dec_audio"] / r["dec_wall"] if r["dec_wall"] > 0.5 else self.speed.get("decode")
        tr = r["tr_audio"] / r["tr_wall"] if r["tr_wall"] > 0.5 else self.speed.get("transcribe")
        return dec, tr

    def estimate(self):
        """(current video fraction, its seconds left, overall fraction, total
        seconds left). Fractions/times are None when there is nothing to go on."""
        r = self.run
        dec, tr = self.speeds()
        cur = r["cur"]
        cur_frac = cur_left = None
        if cur:
            a = cur["audio"]
            t_dec = a / dec if dec else 0.0
            t_tr = a / tr if tr and a else None
            elapsed = time.time() - cur["phase_start"]
            if t_tr is not None:
                if cur["phase"] == "decode":
                    f = cur["decode_frac"]
                    cur_frac = f * t_dec / (t_dec + t_tr)
                    cur_left = (1 - f) * t_dec + t_tr
                else:
                    cur_frac = min((cur["decode_wall"] + elapsed) /
                                   (cur["decode_wall"] + t_tr), 0.99)
                    cur_left = max(t_tr - elapsed, 0.0)

        if r["audio"]:
            in_cur = cur["audio"] * cur_frac if cur and cur_frac else 0.0
            all_frac = (r["done_audio"] + in_cur) / r["audio"]
        else:
            all_frac = r["done_files"] / r["files"] if r["files"] else 0.0

        total_left = None
        if tr:
            per_audio = 1 / tr + (1 / dec if dec else 0.0)  # wall seconds per audio second
            rest = r["audio"] - r["done_audio"] - (cur["audio"] if cur else 0.0)
            total_left = max(rest, 0.0) * per_audio
            if cur:
                total_left += cur_left if cur_left is not None else cur["audio"] * per_audio
        return cur_frac, cur_left, min(all_frac, 1.0), total_left

    @staticmethod
    def set_bar(bar, fraction):
        """Show a fraction, or a moving bar when fraction is None."""
        if fraction is None:
            bar.setRange(0, 0)
        else:
            if bar.maximum() == 0:
                bar.setRange(0, BAR_MAX)
            bar.setValue(int(fraction * BAR_MAX))

    def update_progress(self):
        r = self.run
        if not r:
            return
        cur_frac, cur_left, all_frac, total_left = self.estimate()
        cur = r["cur"]
        if cur:
            self.cur_label.setText(f"Current video ({cur['index']} of {r['files']}): {cur['name']}")
            if cur["phase"] == "decode":
                # Before the first speed measurement, show plain decoding progress.
                self.set_bar(self.cur_bar, cur_frac if cur_frac is not None
                             else cur["decode_frac"])
                detail = f"Reading audio {cur['decode_frac']:.0%}"
            elif cur_frac is None:
                self.set_bar(self.cur_bar, None)
                detail = "Transcribing (first video: measuring speed)"
            else:
                self.set_bar(self.cur_bar, cur_frac)
                detail = (f"Transcribing {cur_frac:.0%}, {approx(cur_left)} left"
                          if cur_left > 0 else "Transcribing, almost done")
            if cur_left is not None and cur["phase"] == "decode":
                detail += f", {approx(cur_left)} left for this video"
            self.cur_detail.setText(detail)
        else:
            self.cur_label.setText("Current video: none")
            self.cur_detail.setText("")
            self.set_bar(self.cur_bar, 0)

        if r["files"]:
            audio = (f", {minutes(r['done_audio'])} of {minutes(r['audio'])} of audio"
                     if r["audio"] else "")
            self.all_label.setText(f"All videos: {r['done_files']} of {r['files']} done{audio}")
            self.all_detail.setText(f"{all_frac:.0%}")
        self.set_bar(self.all_bar, all_frac)

        self.set_readout("elapsed", clock(time.time() - r["start"]), "elapsed")
        if not self.running:
            self.set_readout("left", "--", "time remaining")
        elif total_left is not None:
            self.set_readout("left", "< 1 min" if total_left < 60 else minutes(total_left),
                             "time remaining (estimate)")
        elif r["files"]:
            self.set_readout("left", "--", "time remaining: known after the first video")
        dec, tr = self.speeds()
        if tr:
            speed = 1 / (1 / tr + (1 / dec if dec else 0.0))
            source = "realtime speed" if r["tr_wall"] > 0.5 else "realtime speed, from last run"
            self.set_readout("speed", f"{speed:.1f}×", source)

    def set_readout(self, key, value, caption):
        val, cap = self.readouts[key]
        if val.text() != value:
            val.setText(value)
        if cap.text() != caption:
            cap.setText(caption)

    def handle(self, kind, args):
        r = self.run
        if kind == "log":
            self.log(args[0])
        elif kind == "status":
            self.status.setText(args[0])
        elif kind == "plan":
            r["files"], r["audio"] = args
        elif kind == "video":
            index, name, est = args
            r["cur"] = {"index": index, "name": name, "audio": est, "phase": "decode",
                        "phase_start": time.time(), "decode_frac": 0.0, "decode_wall": 0.0}
        elif kind == "phase":
            phase, ts = args
            cur = r["cur"]
            if cur:
                if phase == "transcribe":
                    cur["decode_wall"] = ts - cur["phase_start"]
                    cur["decode_frac"] = 1.0
                cur["phase"], cur["phase_start"] = phase, ts
        elif kind == "decode":
            if r["cur"]:
                r["cur"]["decode_frac"] = args[0]
        elif kind == "video_done":
            est, dur, dec_wall, tr_wall = args
            r["done_files"] += 1
            r["done_audio"] += est
            r["cur"] = None
            if dur:
                r["dec_audio"] += dur
                r["dec_wall"] += dec_wall
                r["tr_audio"] += dur
                r["tr_wall"] += tr_wall
                if r["tr_wall"] > 0.5:
                    self.speed["transcribe"] = round(r["tr_audio"] / r["tr_wall"], 3)
                if r["dec_wall"] > 0.5:
                    self.speed["decode"] = round(r["dec_audio"] / r["dec_wall"], 3)
                self.save()
        elif kind == "done":
            self.running = False
            r["cur"] = None
            self.update_progress()
            self.log(args[0])
            self.status.setText(args[0])
            for w in self.edit_widgets:
                w.setEnabled(True)
            self.sync_folder_fields()
            self.stop_btn.setEnabled(False)
            self.stop_btn.setText("Stop after this video")
            self.refresh()
            self.on_change()

    def poll(self):
        try:
            while True:
                kind, *args = self.msgs.get_nowait()
                self.handle(kind, args)
        except queue.Empty:
            pass
        if self.running:
            self.update_progress()


class ImportDialog(QDialog):
    """Pick a videos folder and the folder holding their existing transcripts.
    Shows what it found before anything is added. On accept, .entry is the
    new folder entry (with "transcripts" set, which marks it as imported)."""

    def __init__(self, parent, existing_paths, tracks_for):
        super().__init__(parent)
        self.setWindowTitle("Import transcripts")
        self.setMinimumWidth(760)
        self.existing = {p.lower() for p in existing_paths}
        self.tracks_for = tracks_for
        self.entry = None
        self.track_dir = None  # videos folder the track list was read from

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(12)
        lay.addWidget(label("Import transcripts", "section"))
        intro = label("Use transcripts you already have: Haystacks JSON from another PC, "
                      "Whisper output, or subtitle files (.srt, .vtt). They stay where they are; the app reads "
                      "them in place and never changes or moves them.", "muted")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self.videos_edit = QLineEdit()
        self.videos_edit.setReadOnly(True)
        self.videos_edit.setPlaceholderText("The folder with the videos")
        self.trans_edit = QLineEdit()
        self.trans_edit.setReadOnly(True)
        self.trans_edit.setPlaceholderText("The folder with the transcripts")
        grid.addWidget(label("Videos folder"), 0, 0)
        grid.addWidget(self.videos_edit, 0, 1)
        grid.addWidget(button("Browse...", self.pick_videos), 0, 2)
        grid.addWidget(label("Transcripts folder"), 1, 0)
        grid.addWidget(self.trans_edit, 1, 1)
        grid.addWidget(button("Browse...", self.pick_transcripts), 1, 2)
        note = label("Keep it the same as the videos folder if the transcripts sit next to "
                     "the videos.", "muted")
        grid.addWidget(note, 2, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        box = theme.panel()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(16, 12, 16, 12)
        self.summary = QLabel("Choose the folder with the videos.")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.PlainText)
        bl.addWidget(self.summary)
        lay.addWidget(box)

        opts = QGridLayout()
        opts.setHorizontalSpacing(16)
        opts.setVerticalSpacing(4)
        opts.addWidget(label("Audio track (for playback and new transcriptions)"), 0, 0)
        self.track_box = QComboBox()
        self.track_box.setMinimumWidth(260)
        self.track_box.addItems(GENERIC_TRACKS)
        opts.addWidget(self.track_box, 1, 0)
        opts.addWidget(label("Only videos whose name contains"), 0, 1)
        self.filter_edit = QLineEdit()
        self.filter_edit.editingFinished.connect(self.scan)
        opts.addWidget(self.filter_edit, 1, 1)
        opts.addWidget(label("Used when transcribing videos that have no transcript yet.",
                             "muted"), 2, 1)
        opts.setColumnStretch(2, 1)
        lay.addLayout(opts)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button("Cancel", self.reject))
        self.ok = button("Import", self.accept_import, accent=True)
        self.ok.setEnabled(False)
        row.addWidget(self.ok)
        lay.addLayout(row)

    def pick_videos(self):
        d = QFileDialog.getExistingDirectory(self, "Choose the folder with the videos")
        if not d:
            return
        d = str(Path(d))
        if not self.trans_edit.text() or self.trans_edit.text() == self.videos_edit.text():
            self.trans_edit.setText(d)
        self.videos_edit.setText(d)
        self.scan()

    def pick_transcripts(self):
        start = self.trans_edit.text() or self.videos_edit.text()
        d = QFileDialog.getExistingDirectory(self, "Choose the folder with the transcripts", start)
        if d:
            self.trans_edit.setText(str(Path(d)))
            self.scan()

    def scan(self):
        """Look at both folders and say what an import would bring in."""
        self.ok.setEnabled(False)
        vtext, ttext = self.videos_edit.text(), self.trans_edit.text()
        if not vtext:
            self.summary.setText("Choose the folder with the videos.")
            return
        if vtext.lower() in self.existing:
            self.summary.setText("This videos folder is already in the list. Remove it "
                                 "there first to import transcripts for it instead.")
            return
        vdir, tdir = Path(vtext), Path(ttext or vtext)
        filt = self.filter_edit.text().strip()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            videos = pipeline.list_videos(vdir, "")
            stems = {v.stem for v in videos}
            found = transcripts.match(tdir, list(stems))
            formats, bad, valid = Counter(), [], []
            for stem, path in found.items():
                try:
                    formats[transcripts.FORMAT_NAMES[transcripts.read(path)["format"]]] += 1
                    valid.append(stem)
                except Exception:
                    bad.append(path.name)
            matched = [s for s in valid if s in stems]
            todo = [v for v in pipeline.list_videos(vdir, filt) if v.stem not in found]
            need_loud = [s for s in matched if not (tdir / "_loudness" / f"{s}.json").exists()]
            if self.track_dir != vtext:
                self.track_dir = vtext
                labels = self.tracks_for({"path": vtext, "filter": filt})
                self.track_box.clear()
                self.track_box.addItems(labels)
        except OSError as e:
            self.summary.setText(f"Can't read these folders: {e}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        if not valid:
            self.summary.setText(
                "No transcripts found in the transcripts folder. The app looks for .json "
                "(Haystacks or Whisper), .srt and .vtt files named like the videos, "
                "for example \"clip.srt\" or \"clip.en.srt\" for \"clip.mp4\"."
                + (f"\n\n{len(bad)} files couldn't be read as transcripts." if bad else ""))
            return
        def n(count, one, many):
            return f"{count:,} {one if count == 1 else many}"

        kinds = ", ".join(f"{c:,} {name}" for name, c in formats.most_common())
        lines = [f"Found {n(len(valid), 'transcript', 'transcripts')}: {kinds}."]
        unmatched = len(valid) - len(matched)
        lines.append(f"{n(len(matched), 'matches', 'match')} a video by file name."
                     + (f" {n(unmatched, 'has', 'have')} no matching video; "
                        f"{'it' if unmatched == 1 else 'they'} can be searched but not played."
                        if unmatched else ""))
        if bad:
            lines.append(f"{n(len(bad), 'file', 'files')} couldn't be read and will be skipped.")
        if todo:
            lines.append(f"{n(len(todo), 'video has', 'videos have')} no transcript yet. "
                         "Transcribe will do them and save the new transcripts in the "
                         "transcripts folder.")
        lines.append(f"Loudness will be measured in the background for "
                     f"{n(len(need_loud), 'video', 'videos')} (each video's audio is read "
                     "once). Search works right away."
                     if need_loud else "Loudness data is already there.")
        self.summary.setText("\n".join(lines))
        self.ok.setEnabled(True)

    def accept_import(self):
        vtext = self.videos_edit.text()
        self.entry = {"path": vtext,
                      "track": int((self.track_box.currentText() or "1").split(":")[0]),
                      "filter": self.filter_edit.text().strip(),
                      "transcripts": self.trans_edit.text() or vtext}
        self.accept()
