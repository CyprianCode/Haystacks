"""
Background job for imported transcripts: reads each video's recording date
(quick) and measures its loudness (one full read of the audio), so imported
recordings get proper dates and show up under Loudest moments. Also stores
the date in the app's own transcripts made before dates were stored.

Resumable: anything already measured is skipped, so it simply picks up again
the next time the app starts. Waits while a transcription is running.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pipeline

RELOAD_EVERY_S = 90  # let search pick up new loudness data this often


class Measurer:
    def __init__(self, settings, busy=lambda: False):
        self.settings = settings
        self.busy = busy              # True while transcription runs
        self.thread = None
        self.rescan = False
        self.cancel = threading.Event()
        self.total = self.done = 0
        self.changed = False          # new data written; the search should reload
        self.failed = []

    def start(self):
        """Look for work and do it in the background. Safe to call often."""
        if self.thread and self.thread.is_alive():
            self.rescan = True
            return
        self.cancel.clear()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self):
        self.cancel.set()

    def summary(self):
        if self.thread and self.thread.is_alive() and self.total:
            text = f"Measuring loudness of imported videos: {self.done} of {self.total}"
            return text + (" (paused while transcribing)" if self.busy() else "")
        return ""

    def run(self):
        while not self.cancel.is_set():
            self.rescan = False
            work = []
            for entry in list(self.settings.get("folders", [])):
                folder, out, imported = pipeline.entry_dirs(entry)
                if not imported:
                    # The app's own transcripts have loudness; only ones made
                    # before dates were stored need their date. A transcription
                    # run does this itself at the end, so leave it to that.
                    if not self.busy():
                        try:
                            if pipeline.add_missing_dates(folder, log=lambda s: None):
                                self.changed = True
                        except Exception:
                            pass  # folder offline (e.g. a network drive); try next time
                    continue
                try:
                    need_date, need_loud = pipeline.measure_todo(folder, out)
                except OSError:
                    continue  # folder offline (e.g. a network drive); try next time
                if need_date:
                    self.fill_dates(out, need_date)
                work += [(v, int(entry.get("track", 1)), out) for v in need_loud]
            self.measure(work)
            if not self.rescan:
                break

    def fill_dates(self, out: Path, videos):
        def probe(video):
            try:
                return video.stem, pipeline.probe_created(video), True
            except Exception:
                return video.stem, None, False  # unreadable now; try next time
        with ThreadPoolExecutor(8) as pool:
            found = {stem: when for stem, when, ok in pool.map(probe, videos) if ok}
        if found:
            pipeline.store_dates(out, found)
            self.changed = True

    def measure(self, work):
        self.total, self.done = len(work), 0
        last_reload = time.time()
        for video, track, out in work:
            while self.busy() and not self.cancel.is_set():
                time.sleep(2)
            if self.cancel.is_set():
                return
            try:
                pipeline.measure_loudness(video, track, out, cancel=self.cancel)
            except InterruptedError:
                return
            except Exception as e:
                self.failed.append(f"{video.name}: {e}")
            self.done += 1
            if time.time() - last_reload > RELOAD_EVERY_S:
                self.changed = True
                last_reload = time.time()
        if work:
            self.changed = True
