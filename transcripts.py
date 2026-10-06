"""
Reading transcripts in every format Haystacks can import:
  - its own JSON
  - Whisper JSON (openai-whisper / faster-whisper "segments", whisper.cpp "transcription")
  - SRT and WebVTT subtitles (cues are merged into sentences so search works)

Transcripts are matched to videos by file name: "clip.json", "clip.srt" and
"clip.en.srt" all belong to "clip.mp4". Imported files are only ever read.
"""
import html
import json
import re
from pathlib import Path

EXTS = {".json": 0, ".srt": 1, ".vtt": 2}  # preference when a video has several
FORMAT_NAMES = {"haystacks": "Haystacks JSON", "whisper": "Whisper JSON",
                "whisper.cpp": "Whisper JSON", "srt": "SRT subtitles", "vtt": "WebVTT subtitles"}
LANG_SUFFIX = re.compile(r"\.[a-z]{2,3}(?:[-_][a-z]{2,4})?$", re.IGNORECASE)  # .en .eng .en-US
TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{1,3})")
TAG = re.compile(r"<[^>]*>|\{\\[^}]*\}")  # <i>, <v Name>, <00:01.000>, {\an8}
SENTENCE_END = re.compile(r"[.!?…][\"')\]]*$")
MERGE_GAP_S = 1.0     # cues closer than this can join one sentence
MERGE_MAX_CHARS = 220  # captions without punctuation are cut at about this length


def candidates(path: Path):
    """Video names a transcript file could belong to, most specific first."""
    stem = path.stem
    out = [stem]
    m = LANG_SUFFIX.search(stem)
    if m and m.start() > 0:
        out.append(stem[:m.start()])
    return out


def find(folder: Path):
    """Transcript-like files directly in folder (not subfolders), skipping
    the app's own bookkeeping files (names starting with _)."""
    if not folder.is_dir():
        return []
    return [p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in EXTS and not p.name.startswith("_")]


def match(folder: Path, video_stems):
    """{stem: transcript path}. A transcript matched to a video uses the
    video's stem; one without a video keeps its own name."""
    by_lower = {s.lower(): s for s in video_stems}
    chosen = {}
    for path in find(folder):
        names = candidates(path)
        stem = next((by_lower[n.lower()] for n in names if n.lower() in by_lower), names[0])
        old = chosen.get(stem)
        if old is None or EXTS[path.suffix.lower()] < EXTS[old.suffix.lower()]:
            chosen[stem] = path
    return chosen


def clean(text):
    text = html.unescape(TAG.sub("", text))
    return " ".join(text.split())


def seconds(m):
    h, mi, s, frac = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(frac.ljust(3, "0")) / 1000


def parse_cues(raw):
    """[(start, end, text)] from SRT or WebVTT text."""
    cues, prev_lines = [], []
    for block in re.split(r"\n\s*\n", raw.replace("\r\n", "\n").replace("\r", "\n")):
        lines = [l for l in block.split("\n") if l.strip()]
        timing = next((n for n, l in enumerate(lines) if "-->" in l), None)
        if timing is None:
            continue  # header, NOTE, STYLE, REGION
        matches = list(TIME.finditer(lines[timing]))
        if len(matches) < 2:
            continue
        start, end = seconds(matches[0]), seconds(matches[1])
        text_lines = [clean(l) for l in lines[timing + 1:]]
        text_lines = [l for l in text_lines if l]
        # YouTube's automatic captions repeat the previous line before the new
        # words ("rolling" captions); keep only what is new.
        while text_lines and prev_lines and text_lines[0] == prev_lines[-1]:
            text_lines.pop(0)
        if text_lines:
            prev_lines = text_lines
            cues.append((start, end, " ".join(text_lines)))
    return cues


def merge_cues(cues):
    """Join subtitle cues into sentences, so all the words of a sentence can
    be found together."""
    out, cur = [], None
    for start, end, text in cues:
        if cur and start - cur[1] <= MERGE_GAP_S and not SENTENCE_END.search(cur[2]) \
                and len(cur[2]) + len(text) < MERGE_MAX_CHARS:
            cur = (cur[0], max(cur[1], end), cur[2] + " " + text)
        else:
            if cur:
                out.append(cur)
            cur = (start, end, text)
    if cur:
        out.append(cur)
    return out


def read(path: Path):
    """{"format", "segments": [(start, end, text)], optional "created"}.
    Raises ValueError for files that aren't transcripts."""
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(raw)
        except ValueError:
            raise ValueError("not valid JSON") from None
        if isinstance(data, dict) and isinstance(data.get("transcription"), list):
            segs = [(o["offsets"]["from"] / 1000, o["offsets"]["to"] / 1000, clean(o.get("text", "")))
                    for o in data["transcription"]
                    if isinstance(o, dict) and isinstance(o.get("offsets"), dict)]
            return {"format": "whisper.cpp", "segments": [s for s in segs if s[2]]}
        if isinstance(data, dict) and isinstance(data.get("segments"), list):
            segs = [(float(s.get("start", 0)), float(s.get("end", 0)), clean(str(s.get("text", ""))))
                    for s in data["segments"] if isinstance(s, dict)]
            out = {"format": "haystacks" if "file" in data else "whisper",
                   "segments": [s for s in segs if s[2]]}
            if "created" in data:
                out["created"] = data["created"]
            return out
        raise ValueError("not a transcript")
    return {"format": path.suffix.lower()[1:], "segments": merge_cues(parse_cues(raw))}
