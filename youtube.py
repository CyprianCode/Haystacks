"""
YouTube videos as a source of transcripts: the subtitles YouTube already has
(the uploader's, else its automatic captions) are fetched with yt-dlp, one
WebVTT file per video, and searched like any imported transcripts. No video
or audio is downloaded.

A YouTube source is a folder entry
    {"path": D, "transcripts": D, "youtube": <url>, "lang": "en", ...}
where D holds "<upload date> <title> [<video id>].<lang>.vtt". The date at the
front is the recording date search uses (search_data.parse_date); the id at the
end is what the player plays.

yt-dlp itself is downloaded on first use into the engine folder (so the
uninstaller removes it) and updated at most once a day, since YouTube changes
often break older versions.
"""
import datetime as dt
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import paths
import pipeline
import transcripts
import updater
from asr_external import NO_WINDOW, Job
from search_data import parse_date, write_atomic

TOOL_DIR = paths.ENGINE / "yt-dlp"
EXE = TOOL_DIR / "yt-dlp.exe"
STAMP = TOOL_DIR / "_updated"
RELEASE = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/"
UPDATE_EVERY_S = 24 * 3600
STATE = "_youtube.json"   # videos that had no subtitles: {id: {"checked", "tries"}}
RETRY_AFTER_S = 24 * 3600  # automatic captions can take hours to appear
MAX_TRIES = 3

ID = r"[A-Za-z0-9_-]{11}"
ID_RE = re.compile(rf"\[({ID})\](?:\.[\w-]+)?$")       # "... [id]" or "... [id].en"
NAME_RE = re.compile(rf"^(?:\d{{4}}-\d\d-\d\d |NA )?(.*?) ?\[{ID}\](?:\.[\w-]+)?$")
TAB_SUFFIX = re.compile(r" - (Videos|Shorts|Live|Streams|Home)$")
OUT_TEMPLATE = "%(upload_date>%Y-%m-%d)s %(title).120B [%(id)s].%(ext)s"
MARK = "@@video "
# yt-dlp's stand-ins for characters Windows doesn't allow in file names.
UNSAFE = str.maketrans({"\uff1f": "?", "\uff1a": ":", "\u29f8": "/", "\u29f9": "\\",
                        "\uff02": '"', "\uff0a": "*", "\uff1c": "<", "\uff1e": ">",
                        "\uff5c": "|"})


def video_id(stem):
    """The YouTube id at the end of a subtitle file's name, or None."""
    m = ID_RE.search(stem)
    return m.group(1) if m else None


def title(stem):
    """The video title from a subtitle file's name (no date, id or language)."""
    m = NAME_RE.match(stem)
    return ((m.group(1) if m else stem) or stem).translate(UNSAFE)


def parse_url(text):
    """The video id in a YouTube link (watch, youtu.be, shorts, live, embed)
    or a bare id, or None."""
    text = text.strip()
    if re.fullmatch(ID, text):
        return text
    m = re.search(rf"(?:[?&]v=|youtu\.be/|/shorts/|/live/|/embed/)({ID})(?![\w-])", text)
    return m.group(1) if m else None


def watch_url(vid, seconds=0):
    return f"https://www.youtube.com/watch?v={vid}" + (f"&t={int(seconds)}s" if seconds else "")


def sub_langs(lang):
    """yt-dlp --sub-langs for one language: the plain code, plus the uploader's
    regional versions (en-US, pt-BR, zh-Hans). yt-dlp matches without regard to
    case, so the inline (?-i:) keeps out translated tracks like "en-de"."""
    lang = re.escape(lang)
    return f"{lang},(?-i:{lang}-[A-Z0-9][A-Za-z0-9]*)"


# ---- the yt-dlp program ---------------------------------------------------------
def ensure_tool(log=print, cancel=None):
    """Download yt-dlp if it isn't there; otherwise update it once a day."""
    TOOL_DIR.mkdir(parents=True, exist_ok=True)
    if not EXE.exists():
        log("Downloading yt-dlp (fetches YouTube subtitles)...")
        sums = updater._get(RELEASE + "SHA2-256SUMS").read().decode("utf-8", "replace")
        sha = next((line.split()[0] for line in sums.splitlines()
                    if line.split() and line.split()[-1].lstrip("*") == EXE.name), None)
        if not sha:
            raise RuntimeError("yt-dlp's release has no checksum, so it can't be verified")
        updater.fetch_file(RELEASE + EXE.name, EXE, sha, cancel=cancel)
        STAMP.touch()
        return
    try:
        fresh = time.time() - STAMP.stat().st_mtime < UPDATE_EVERY_S
    except OSError:
        fresh = False
    if not fresh:
        log("Checking for a newer yt-dlp...")
        code, lines = run(["-U"], cancel=cancel)
        updated = [l for l in lines if l.startswith("Updated yt-dlp")]
        if updated:
            log(updated[-1])
        elif code:
            log("Could not update yt-dlp: " + (lines[-1] if lines else f"code {code}"))
        STAMP.touch()


def run(args, on_line=None, cancel=None, stdin=None):
    """Run yt-dlp; (exit code, output lines). on_line(line) sees each line as
    it comes. Setting `cancel` ends yt-dlp right away (InterruptedError)."""
    job = Job()
    try:
        proc = subprocess.Popen([str(EXE), "--ignore-config", "--encoding", "utf-8", *args],
                                stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                creationflags=NO_WINDOW)
    except FileNotFoundError:
        raise RuntimeError("yt-dlp is missing; try again to download it") from None
    job.add(proc)
    if stdin:
        proc.stdin.write(stdin.encode("utf-8"))
        proc.stdin.close()
    stopped = threading.Event()

    def watch():
        while proc.poll() is None:
            if cancel is not None and cancel.wait(0.2):
                stopped.set()
                # yt-dlp.exe unpacks itself and runs the real program as a child
                # process, so end the whole tree, not just the first process.
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=NO_WINDOW)
                job.close()
                return
            if cancel is None:
                time.sleep(0.2)
    threading.Thread(target=watch, daemon=True).start()

    lines = []
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").rstrip()
        if line:
            lines.append(line)
            if on_line:
                on_line(line)
    code = proc.wait()
    job.close()
    if stopped.is_set():
        raise InterruptedError("stopped")
    return code, lines


def last_error(lines):
    errors = [l[len("ERROR: "):] for l in lines if l.startswith("ERROR: ")]
    return errors[-1] if errors else (lines[-1] if lines else "no output")


# ---- listing and fetching --------------------------------------------------------
def list_videos(url, cancel=None):
    """(name, [(id, title)]) for a video, playlist or channel link."""
    code, lines = run(["--flat-playlist", "--no-warnings", "--print",
                       f"{MARK}%(playlist_title|)s\t%(channel,uploader|)s\t%(id)s\t%(title|)s",
                       url], cancel=cancel)
    videos, name = [], ""
    for line in lines:
        if not line.startswith(MARK):
            continue
        parts = line[len(MARK):].split("\t")
        if len(parts) < 4 or not re.fullmatch(ID, parts[2]):
            continue
        playlist, channel, vid, vtitle = parts[0], parts[1], parts[2], "\t".join(parts[3:])
        if not name:
            name = TAB_SUFFIX.sub("", playlist) or channel or vtitle
        if all(v != vid for v, _ in videos):
            videos.append((vid, vtitle))
    if not videos:
        raise RuntimeError(f"no videos found at that link ({last_error(lines)})"
                           if code else "no videos found at that link")
    return name, videos


def video_info(vid, cancel=None):
    """(title, upload date as "YYYY-MM-DD" or None) of one video. Raises
    RuntimeError if YouTube doesn't know it."""
    code, lines = run(["--skip-download", "--no-warnings", "--print",
                       f"{MARK}%(upload_date>%Y-%m-%d|)s\t%(title|)s", watch_url(vid)],
                      cancel=cancel)
    for line in lines:
        if line.startswith(MARK):
            date, _, vtitle = line[len(MARK):].partition("\t")
            return vtitle, date or None
    raise RuntimeError(last_error(lines))


def files_by_id(folder: Path):
    """{video id: [subtitle files]} in a YouTube folder."""
    out = {}
    if folder.is_dir():
        for p in folder.iterdir():
            vid = video_id(p.stem) if p.suffix.lower() == ".vtt" else None
            if vid:
                out.setdefault(vid, []).append(p)
    return out


def keep_one(files, lang):
    """When yt-dlp wrote two tracks for a video (e.g. "en" and the uploader's
    "en-US"), keep the regional one, which is always the uploader's own."""
    if len(files) < 2:
        return
    files = sorted(files, key=lambda p: (p.stem.endswith("." + lang), p.name))
    for extra in files[1:]:
        extra.unlink(missing_ok=True)


def read_state(folder: Path):
    try:
        return json.loads((folder / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def due(info, now):
    """Should a video that had no subtitles be tried again?"""
    try:
        checked = dt.datetime.fromisoformat(info["checked"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return True
    return info.get("tries", 1) < MAX_TRIES and now - checked >= RETRY_AFTER_S


def count(entry):
    """Number of videos with subtitles in a YouTube folder entry."""
    return len(files_by_id(Path(entry["transcripts"])))


def fetch(entry, log=print, on_plan=None, on_video=None, cancel=None):
    """Fetch subtitles for every video at entry["youtube"] that doesn't have
    them yet. on_plan(n) once the work is known; on_video(i, title) as each
    video starts. Returns (new subtitle files, videos without subtitles,
    total videos). Safe to stop and run again: finished videos are skipped."""
    folder = Path(entry["transcripts"])
    lang = entry.get("lang") or "en"
    folder.mkdir(parents=True, exist_ok=True)

    name, videos = list_videos(entry["youtube"], cancel)
    have = files_by_id(folder)
    state = read_state(folder)
    now = time.time()
    todo = [(v, t) for v, t in videos
            if v not in have and (v not in state or due(state[v], now))]
    waiting = sum(1 for v, _ in videos if v not in have and v in state and not due(state[v], now))
    log(f"{name}: {len(videos)} videos, {len(videos) - len(todo) - waiting} have subtitles, "
        f"{len(todo)} to fetch" + (f", {waiting} without subtitles (checked recently)"
                                   if waiting else "") + ".")
    if on_plan:
        on_plan(len(todo))
    if not todo:
        return 0, waiting, len(videos)

    titles = dict(todo)
    started, errors = [], {}
    halt, limited, ended = threading.Event(), threading.Event(), threading.Event()

    def pass_on_stop():  # the user's Stop, or a rate limit, ends yt-dlp
        while not ended.is_set():
            if cancel is not None and cancel.wait(0.2):
                halt.set()
                return
            if cancel is None:
                time.sleep(0.2)
    threading.Thread(target=pass_on_stop, daemon=True).start()

    def on_line(line):
        if line.startswith(MARK):
            vid = line[len(MARK):].strip()
            started.append(vid)
            if on_video:
                on_video(len(started), titles.get(vid, vid))
        elif line.startswith("ERROR: "):
            if "429" in line or "Too Many Requests" in line:
                limited.set()
                halt.set()
                return
            m = re.search(rf"\b({ID}): (.*)", line)
            if m:
                errors[m.group(1)] = m.group(2)
            else:
                log("  " + line)

    args = ["-a", "-", "--ignore-errors", "--skip-download", "--no-simulate",
            "--write-subs", "--write-auto-subs", "--sub-langs", sub_langs(lang),
            "--sub-format", "vtt", "--convert-subs", "vtt", "--sleep-subtitles", "2",
            "--windows-filenames", "--no-warnings", "--no-progress",
            "-P", str(folder), "-o", OUT_TEMPLATE, "--print", f"{MARK}%(id)s"]
    complete = False
    try:
        run(args, on_line, halt, stdin="\n".join(watch_url(v) for v, _ in todo) + "\n")
        complete = True
    except InterruptedError:
        pass
    finally:
        ended.set()

    # A video is finished once the next one has started (or yt-dlp ended
    # normally). Videos that failed before starting (private, removed) count
    # as finished only after a complete run.
    finished = started if complete else started[:-1]
    if complete:
        finished += [v for v, _ in todo if v in errors and v not in started]
    have = files_by_id(folder)
    new = missing = 0
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    for vid in finished:
        if vid in have:
            keep_one(have[vid], lang)
            state.pop(vid, None)
            new += 1
        else:
            reason = errors.get(vid)
            log(f"  No {lang} subtitles: {titles.get(vid, vid)}" + (f" ({reason})" if reason else ""))
            tries = state.get(vid, {}).get("tries", 0) + 1
            state[vid] = {"checked": stamp, "tries": tries}
            missing += 1
    if finished or (folder / STATE).exists():
        write_atomic(folder / STATE, json.dumps(state, indent=1))
    if limited.is_set():
        log("YouTube is limiting requests right now. What was fetched is kept; "
            "try again later to get the rest.")
    elif not complete:
        raise InterruptedError("stopped")
    return new, missing + waiting, len(videos)


# ---- transcripts you already have ------------------------------------------------
def same_dir(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def link(folders, transcript: Path, vid, date=None):
    """Link a transcript file to a YouTube video, so its results play that
    video. The link is stored in the folder entry that reads the transcript's
    folder (one is added if there is none, reading it in place like Import
    transcripts). date ("YYYY-MM-DD") is used if the transcript has no date
    of its own. Returns (entry, added?)."""
    transcript = Path(transcript)
    if transcript.suffix.lower() not in transcripts.EXTS:
        raise ValueError("Haystacks reads transcripts in .json, .srt and .vtt files.")
    if not transcript.is_file():
        raise ValueError(f"Can't find {transcript}.")
    folder = transcript.parent
    entry = next((f for f in folders if same_dir(pipeline.entry_dirs(f)[1], folder)), None)
    videos = []
    if entry and Path(entry["path"]).is_dir():
        videos = [v.stem for v in pipeline.list_videos(Path(entry["path"]), "")]
    found = transcripts.match(folder, videos)
    stem = next((s for s, p in found.items() if same_dir(p, transcript)), None)
    if stem is None:
        other = next((p.name for s, p in found.items()
                      if s.lower() in (c.lower() for c in transcripts.candidates(transcript))), "")
        raise ValueError((f"{other} in the same folder" if other else "Another file")
                         + " is used for this recording instead. Link that file, or move "
                         "this one to its own folder.")
    data = transcripts.read(transcript)  # raises ValueError if it isn't a transcript
    added = entry is None
    if added:
        entry = {"path": str(folder), "transcripts": str(folder), "name": folder.name,
                 "track": 1, "filter": ""}
        folders.append(entry)
    entry.setdefault("links", {})[stem] = vid
    out = pipeline.entry_dirs(entry)[1]
    if date and not data.get("created") and parse_date(stem)[0] is None             and not pipeline.read_dates(out).get(stem):
        pipeline.store_dates(out, {stem: date})
    return entry, added
