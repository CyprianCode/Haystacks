"""
Transcription pipeline used by the Haystacks app. Not run directly.

Audio is decoded straight from each video into memory with PyAV (FFmpeg's
libraries, which ship with it), so no separate ffmpeg is needed and no audio
files are kept. Outputs go to the folder's transcripts location: normally
<video folder>\\_Haystacks\\, or wherever imported transcripts already live
(the folder entry's "transcripts"):
    <stem>.json            transcript (its existence marks the video as done)
    _loudness\\<stem>.json  loudness per half second
    _dates.json            recording dates for imported transcripts
    _failed.txt            videos that could not be transcribed
Imported transcript files are only read, never changed.
"""
import contextlib
import ctypes
import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import av
import numpy as np

import engine
import transcripts
from search_data import VIDEO_EXTS, WINDOW_S, collect, write_atomic

SAMPLE_RATE = 16000
RESAMPLE_CHUNK_S = 5  # decoded audio is resampled in chunks of this many seconds
OUT_NAME = "_Haystacks"
NO_WINDOW = 0x08000000  # don't flash a console window for helper programs


def keep_awake(on: bool):
    """Prevent Windows from sleeping while transcription runs."""
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass


def open_media(video: Path):
    """Open a video with PyAV. Raises RuntimeError with a readable message."""
    try:
        return av.open(str(video), metadata_errors="ignore")
    except Exception as e:
        raise RuntimeError(f"can't read {video.name}: {e}") from None


def out_dir(folder: Path, transcripts_dir=None) -> Path:
    """Where a folder's transcripts live: its own location if they were
    imported, else <folder>\\_Haystacks."""
    return Path(transcripts_dir) if transcripts_dir else folder / OUT_NAME


def entry_dirs(entry):
    """(video folder, transcripts folder, imported?) for a settings folder entry."""
    folder = Path(entry["path"])
    return folder, out_dir(folder, entry.get("transcripts")), bool(entry.get("transcripts"))


def list_videos(folder: Path, name_filter: str):
    f = name_filter.strip().lower()
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in VIDEO_EXTS
                  and f in p.stem.lower())


def pending(folder: Path, name_filter: str, out=None):
    """(all matching videos, those without a transcript yet). Any format
    counts: an imported .srt marks its video as done too."""
    videos = list_videos(folder, name_filter)
    done = transcripts.match(out or out_dir(folder), [v.stem for v in videos])
    return videos, [v for v in videos if v.stem not in done]


def stream_channels(stream):
    ctx = stream.codec_context
    with contextlib.suppress(Exception):
        return ctx.layout.nb_channels
    return getattr(ctx, "channels", None)


def list_tracks(video: Path):
    """Audio tracks in a video as [(number from 1, description)]."""
    tracks = []
    with open_media(video) as container:
        for n, s in enumerate(container.streams.audio, 1):
            tags = {k.lower(): v for k, v in s.metadata.items()}
            lang = tags.get("language", "")
            parts = [tags.get("title"),
                     LANGUAGES.get(lang, lang) if lang not in ("", "und") else None,
                     channel_name(stream_channels(s)),
                     codec_name(s.codec_context.name or "")]
            tracks.append((n, ", ".join(p for p in parts if p)))
    return tracks


LANGUAGES = {"eng": "English", "spa": "Spanish", "fra": "French", "fre": "French",
             "deu": "German", "ger": "German", "ita": "Italian", "por": "Portuguese",
             "jpn": "Japanese", "zho": "Chinese", "chi": "Chinese", "kor": "Korean"}


def channel_name(channels):
    return {1: "mono", 2: "stereo", 6: "5.1 surround", 8: "7.1 surround"}.get(
        channels, f"{channels} channels" if channels else None)


def codec_name(codec):
    if codec.startswith("pcm_"):
        return "PCM"
    return {"aac": "AAC", "mp3": "MP3", "opus": "Opus", "vorbis": "Vorbis",
            "flac": "FLAC", "ac3": "Dolby Digital", "eac3": "Dolby Digital Plus",
            "alac": "ALAC"}.get(codec, codec.upper() or None)


def probe_duration(video: Path):
    """Length of a video in seconds, or None if it can't be told."""
    try:
        with open_media(video) as container:
            return container.duration / av.time_base if container.duration else None
    except RuntimeError:
        return None


def probe_created(video: Path):
    """Recording date stored in the video (creation_time), as a local-time
    'YYYY-MM-DDTHH:MM:SS' string, or None if the video has no date.
    Raises if the file can't be read, so a failed read isn't saved as
    'no date'."""
    with open_media(video) as container:
        tag_sets = [dict(container.metadata)] + [dict(s.metadata) for s in container.streams]
    for tags in tag_sets:
        for key, raw in tags.items():
            if key.lower() not in ("creation_time", "date_utc", "date"):
                continue
            try:
                when = dt.datetime.fromisoformat(str(raw).strip())
            except ValueError:
                continue
            if when.tzinfo:  # stored as UTC; show it in this PC's time zone
                when = when.astimezone().replace(tzinfo=None)
            # Some files carry a zero date (1904/1970) instead of none at all.
            if 1990 <= when.year <= dt.datetime.now().year + 1:
                return when.isoformat(timespec="seconds")
    return None


def decode_audio(video: Path, track: int, duration=None, on_progress=None,
                 cancel=None) -> np.ndarray:
    """One audio track (numbered from 1) as 16 kHz mono 16-bit samples.
    on_progress(fraction) is called as audio arrives, if the length is known.
    Setting the `cancel` event stops reading and raises InterruptedError."""
    out = []
    with open_media(video) as container:
        streams = container.streams.audio
        if len(streams) < track:
            raise RuntimeError(f"this video has no audio track {track}")
        stream = streams[track - 1]
        # Skip the video (and other tracks) at the file level: a 4K camera file
        # then reads ~10x faster, and far less comes over the network.
        for other in container.streams:
            if other is not stream:
                other.discard = av.stream.Discard.all
        total = duration or (container.duration / av.time_base if container.duration else 0)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        # Decoded frames are tiny (~1024 samples); resampling them in chunks of
        # a few seconds instead is much faster and gives identical output.
        pending, key, count = [], None, 0

        def flush():
            nonlocal pending, count
            if pending:
                fmt, layout, rate = key
                chunk = av.AudioFrame.from_ndarray(np.concatenate(pending, axis=1),
                                                   format=fmt, layout=layout)
                chunk.sample_rate = rate
                out.extend(f.to_ndarray() for f in resampler.resample(chunk))
            pending, count = [], 0

        try:
            for frame in container.decode(stream):
                k = (frame.format.name, frame.layout.name, frame.sample_rate)
                if k != key:
                    flush()
                    key = k
                pending.append(frame.to_ndarray())
                count += frame.samples
                if count >= RESAMPLE_CHUNK_S * frame.sample_rate:
                    flush()
                    if cancel is not None and cancel.is_set():
                        raise InterruptedError("stopped")
                    if on_progress and total and frame.time is not None:
                        on_progress(min(max(frame.time / total, 0.0), 1.0))
        except av.error.FFmpegError as e:
            if not (out or pending):
                raise RuntimeError(f"can't decode audio track {track}: {e}") from None
            # A damaged end of file: keep what was read, like ffmpeg does.
        flush()
        out.extend(f.to_ndarray() for f in resampler.resample(None))  # the resampler's tail
    if on_progress:
        on_progress(1.0)
    audio = np.concatenate(out, axis=1).ravel().astype("<i2") if out else np.zeros(0, "<i2")
    if not len(audio):
        raise RuntimeError(f"audio track {track} is empty")
    return audio


def loudness(audio: np.ndarray) -> list:
    """Loudness (dBFS) of each half-second window (WINDOW_S)."""
    win = int(SAMPLE_RATE * WINDOW_S)
    chunk = win * 120  # 60 seconds at a time
    out = []
    for i in range(0, len(audio), chunk):
        x = audio[i:i + chunk].astype(np.float32) / 32768
        n = len(x) // win
        if n:
            rms = np.sqrt((x[: n * win].reshape(n, win) ** 2).mean(axis=1))
            out.extend(np.round(20 * np.log10(np.maximum(rms, 1e-5)), 1).tolist())
    return out


def load_asr():
    """Load the chosen speech engine. Returns (context manager, its name);
    use the first in a with block."""
    cfg = engine.current()
    return engine.load(cfg), engine.label(cfg)


def transcribe(asr, audio: np.ndarray) -> dict:
    """Every engine takes a file path, so the audio goes through a temporary
    WAV on the local disk, deleted as soon as transcription finishes."""
    fd, name = tempfile.mkstemp(prefix="Haystacks-", suffix=".wav")
    os.close(fd)
    tmp = Path(name)
    try:
        with wave.open(name, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(audio.tobytes())
        return asr.transcribe(name)
    finally:
        tmp.unlink(missing_ok=True)


def cleanup_temp():
    """Delete temporary WAVs (and transcripts written by external engines)
    left behind if the app was closed mid-file."""
    tmp = Path(tempfile.gettempdir())
    for p in [p for ext in ("wav", "out", "json", "srt", "vtt")
              for p in tmp.glob(f"Haystacks-*.{ext}")]:
        try:
            p.unlink()
        except OSError:
            pass  # still in use by another running copy of the app


def process(asr, video: Path, track: int, duration=None, on_phase=None,
            on_decode=None, out=None):
    """Transcribe one video.

    on_phase(name) is called with "decode" and then "transcribe";
    on_decode(fraction) reports decoding progress (see decode_audio).
    Returns (audio seconds, segment count, decode seconds, transcribe seconds).
    """
    out = out or out_dir(video.parent)
    (out / "_loudness").mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    if on_phase:
        on_phase("decode")
    audio = decode_audio(video, track, duration, on_decode)
    write_atomic(out / "_loudness" / f"{video.stem}.json", json.dumps(loudness(audio)))
    t1 = time.time()
    if on_phase:
        on_phase("transcribe")
    result = transcribe(asr, audio)
    t2 = time.time()

    segments = [
        {"start": round(float(s["start"]), 2),
         "end": round(float(s["end"]), 2),
         "text": s["text"].strip()}
        for s in result.get("segments", []) if s.get("text", "").strip()
    ]
    data = {"file": video.name}
    try:
        data["created"] = probe_created(video)  # None: no date, the name is used
    except Exception:
        pass  # left out, so add_missing_dates tries again after the next run
    data["segments"] = segments
    # Written last and atomically, so a .json only exists when complete.
    write_atomic(out / f"{video.stem}.json",
                 json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    return len(audio) / SAMPLE_RATE, len(segments), t1 - t0, t2 - t1


def finished_count(out: Path) -> int:
    return sum(1 for _ in out.glob("*.json")) if out.is_dir() else 0


def clear_finished(out: Path) -> int:
    """Forget which videos in a folder are done: delete the transcripts,
    loudness data and failure list the app wrote in its
    <folder>\\_Haystacks. Only those files are removed; the videos are
    never touched. Never used on imported transcripts. Returns transcripts deleted."""
    if out.name != OUT_NAME:
        raise ValueError("only the app's own _Haystacks folders can be cleared")
    if not out.is_dir():
        return 0
    removed = 0
    for p in out.glob("*.json"):
        p.unlink()
        removed += 1
    for p in out.glob("*.tmp"):
        p.unlink()
    cache = out / "_loudness"
    if cache.is_dir():
        for p in cache.glob("*.json*"):
            p.unlink()
        with contextlib.suppress(OSError):
            cache.rmdir()
    (out / "_failed.txt").unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        out.rmdir()  # only if nothing else is left in it
    return removed


def log_failure(video: Path, error, out=None):
    out = out or out_dir(video.parent)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "_failed.txt", "a", encoding="utf-8") as f:
        f.write(f"{video.name}\t{error}\n")


def add_missing_dates(folder: Path, log=print):
    """Store the video's recording date in transcripts made before dates were
    read from videos (no "created" key yet). Done once per transcript."""
    out = out_dir(folder)
    videos = {v.stem.lower(): v for v in list_videos(folder, "")}
    todo = []
    for jp in out.glob("*.json"):
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        video = videos.get(Path(data.get("file", jp.name)).stem.lower())
        if "created" not in data and video:
            todo.append((jp, data, video))
    if not todo:
        return
    log(f"Reading recording dates from {len(todo)} videos...")

    def probe(item):
        try:
            return True, probe_created(item[2])
        except Exception:
            return False, None  # unreadable right now; try again after the next run

    with ThreadPoolExecutor(8) as pool:
        for (jp, data, _), (ok, created) in zip(todo, pool.map(probe, todo)):
            if ok:
                data = {"file": data.get("file", jp.name), "created": created,
                        **{k: v for k, v in data.items() if k != "file"}}
                write_atomic(jp, json.dumps(data, ensure_ascii=False,
                                            separators=(",", ":")))


def loudness_reader(out: Path):
    """get_loudness function for search_data.collect that reads the app's cache."""
    cache = out / "_loudness"

    def read(stem, n, total):
        try:
            return np.array(json.loads((cache / f"{stem}.json").read_text()),
                            dtype=np.float32)
        except (OSError, ValueError):
            return None
    return read


def read_dates(out: Path):
    try:
        return json.loads((out / "_dates.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_folder(folder: Path, out=None):
    """Search data for one folder (see search_data.collect), or None if
    nothing in it has been transcribed or imported yet."""
    out = out or out_dir(folder)
    if not out.is_dir() or not transcripts.find(out):
        return None
    return collect(out, folder, loudness_reader(out), log=lambda s: None,
                   dates=read_dates(out))


def measure_todo(folder: Path, out: Path):
    """For imported transcripts: (videos missing a recording date, videos
    missing loudness). Only transcripts matched to an existing video count."""
    videos = {v.stem: v for v in list_videos(folder, "")} if folder.is_dir() else {}
    found = transcripts.match(out, list(videos))
    dates = read_dates(out)
    need_date, need_loud = [], []
    for stem, path in found.items():
        video = videos.get(stem)
        if not video:
            continue
        if stem not in dates:
            stored = False
            if path.suffix.lower() == ".json":
                with contextlib.suppress(Exception):
                    stored = "created" in transcripts.read(path)
            if not stored:
                need_date.append(video)
        if not (out / "_loudness" / f"{stem}.json").exists():
            need_loud.append(video)
    return need_date, need_loud


def store_dates(out: Path, found):
    """Merge {stem: ISO date or None} into <out>\\_dates.json."""
    dates = read_dates(out)
    dates.update(found)
    out.mkdir(parents=True, exist_ok=True)
    write_atomic(out / "_dates.json", json.dumps(dates, indent=1))


def measure_loudness(video: Path, track: int, out: Path, cancel=None):
    """Read one video's audio and store its loudness (no transcription)."""
    audio = decode_audio(video, track, cancel=cancel)
    (out / "_loudness").mkdir(parents=True, exist_ok=True)
    write_atomic(out / "_loudness" / f"{video.stem}.json", json.dumps(loudness(audio)))


def find_vlc():
    """Path to VLC if installed (it can start at a given time), else None."""
    vlc = shutil.which("vlc")
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if not vlc and base and (Path(base) / "VideoLAN" / "VLC" / "vlc.exe").exists():
            vlc = str(Path(base) / "VideoLAN" / "VLC" / "vlc.exe")
    return vlc


def play_video(video: Path, seconds: float):
    """Open a video in another player, a couple of seconds before `seconds`.
    Returns "vlc", or "default" for the Windows default player (which can't
    seek, so it starts at the beginning)."""
    vlc = find_vlc()
    if vlc:
        subprocess.Popen([vlc, f"--start-time={max(0, int(seconds) - 2)}", str(video)])
        return "vlc"
    os.startfile(video)
    return "default"
