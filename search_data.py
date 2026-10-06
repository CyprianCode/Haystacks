"""
collect() turns a folder of transcripts into search data, plus the shared
helpers it needs (recording dates, loudest moments, atomic writes).
Not run directly.
"""
import datetime as dt
import re
from pathlib import Path

import numpy as np

import transcripts

WINDOW_S = 0.5          # loudness is measured in half-second windows
MOMENTS_PER_FILE = 10   # loudest moments kept per recording
MIN_GAP_S = 15          # loudest moments must be at least this far apart

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".mts", ".m4v", ".webm"}

# Matches dates like 2024-01-05 18-30-12, 2024_01_05, 20240105_183012
DATE_RE = re.compile(
    r"(?<!\d)((?:19|20)\d\d)[-_. ]?(0[1-9]|1[0-2])[-_. ]?(0[1-9]|[12]\d|3[01])"
    r"(?:[ _T-]+([01]\d|2[0-3])[-_.:h]?([0-5]\d)(?:[-_.:m]?([0-5]\d))?)?(?!\d)"
)


def date_label(when, with_time=True):
    label = f"{when:%b} {when.day}, {when.year}"
    if with_time:
        h12 = when.hour % 12 or 12
        label += f", {h12}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'}"
    return label


def parse_date(stem):
    for m in DATE_RE.finditer(stem):
        y, mo, d, h, mi, s = m.groups()
        try:
            when = dt.datetime(int(y), int(mo), int(d),
                               int(h or 0), int(mi or 0), int(s or 0))
        except ValueError:
            continue
        return when, date_label(when, bool(h))
    return None, None


def recording_date(data, stem):
    """The date stored in the video ("created", added by the app), else the
    date in the file name, else (None, None)."""
    created = data.get("created")
    if created:
        try:
            when = dt.datetime.fromisoformat(created)
            return when, date_label(when)
        except ValueError:
            pass
    return parse_date(stem)


def loudest_moments(db: np.ndarray):
    gap = int(MIN_GAP_S / WINDOW_S)
    picked = []
    for i in np.argsort(db)[::-1]:
        if db[i] < -60:  # nothing left but near-silence
            break
        if all(abs(int(i) - p) >= gap for p in picked):
            picked.append(int(i))
            if len(picked) == MOMENTS_PER_FILE:
                break
    return picked


def write_atomic(path: Path, text: str):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def collect(transcripts_dir: Path, video_dir: Path, get_loudness, log=print, dates=None):
    """Read every transcript in transcripts_dir (any format transcripts.py
    reads) into search data.

    get_loudness(stem, n, total) returns a numpy array of dBFS values (one per
    WINDOW_S) or None if there is no loudness data for that recording.
    dates: {stem: ISO date or None} for transcripts that don't store one.
    Returns (files, segs, moments, undated):
      files   [stem, video Path or None, datetime or None, date label or None],
              oldest first, undated last
      segs    [file index, start, text, loudest dB or None], in file order
      moments [file index, time, dB, seg index or -1]
    """
    log("Indexing videos...")
    videos = {p.stem.lower(): p for p in video_dir.iterdir()
              if p.suffix.lower() in VIDEO_EXTS} if video_dir.is_dir() else {}

    log("Reading transcripts...")
    records = []
    found = transcripts.match(transcripts_dir, [v.stem for v in videos.values()])
    for stem, path in found.items():
        try:
            data = transcripts.read(path)
        except Exception as e:
            log(f"  skipped {path.name}: {e}")
            continue
        if "created" not in data and dates:
            data["created"] = dates.get(stem)
        when, label = recording_date(data, stem)
        records.append((when, label, stem, data))

    # Sort oldest to newest; files without a date go last.
    records.sort(key=lambda r: (r[0] is None, r[0] or dt.datetime.min, r[2]))
    undated = sum(1 for r in records if r[0] is None)

    files, segs, moments = [], [], []
    for n, (when, label, stem, data) in enumerate(records, 1):
        fi = len(files)
        files.append([stem, videos.get(stem.lower()), when, label])

        db = get_loudness(stem, n, len(records))

        file_segs = []
        for start, end, text in data["segments"]:
            text = text.strip()
            if not text:
                continue
            seg_db = None
            if db is not None and len(db):
                i0 = min(int(start / WINDOW_S), len(db) - 1)
                i1 = max(i0 + 1, int(np.ceil(end / WINDOW_S)))
                seg_db = round(float(db[i0:i1].max()), 1)
            file_segs.append((start, end, len(segs)))
            segs.append([fi, round(start, 1), text, seg_db])

        if db is not None and len(db):
            for i in loudest_moments(db):
                t = i * WINDOW_S
                # Attach the sentence being spoken at that moment, if any.
                best, best_dist = -1, 5.0
                for start, end, gi in file_segs:
                    dist = 0 if start - 1 <= t <= end + 1 else min(abs(t - start), abs(t - end))
                    if dist < best_dist:
                        best, best_dist = gi, dist
                moments.append([fi, round(t, 1), round(float(db[i]), 1), best])
    return files, segs, moments, undated

