"""
Setting up the speech engine from inside the app, so users never need a
terminal. UI-free; engine_dialog.py drives it.

Models (MODELS): Orukeet (the default and recommended one), three Whisper
models run by whisper.cpp, a custom whisper.cpp model file, or a custom
command. The choice is app-wide and recorded in installation.json; a file
without an "engine" key is an Orukeet install from before there was a choice.

Device choice: NVIDIA card -> CUDA, other graphics card -> Vulkan (much faster
than the CPU, e.g. 3x on an AMD integrated GPU), else CPU. Orukeet's own
"--device auto" never picks Vulkan, so Haystacks decides itself. whisper.cpp
publishes Windows builds for CPU and CUDA only, so Whisper uses the CPU on
non-NVIDIA PCs. After downloading, a short self-test transcribes silence; if
the graphics engine fails, the CPU engine is installed instead.
"""
import json
import os
import shutil
import subprocess
import tempfile
import wave
import zipfile
from functools import cache
from pathlib import Path

import paths
from search_data import write_atomic

DEVICES = {
    "cuda": "NVIDIA graphics card (CUDA)",
    "vulkan": "Other graphics card (Vulkan)",
    "cpu": "Processor only (CPU)",
}
SHORT_DEVICES = {"cuda": "NVIDIA", "vulkan": "Vulkan", "cpu": "CPU"}
NO_WINDOW = 0x08000000

HF = "https://huggingface.co"
WHISPER_CREDIT = ('Speech model: <a href="https://github.com/openai/whisper">Whisper</a> '
                  'by OpenAI (MIT), run by <a href="https://github.com/ggml-org/whisper.cpp">'
                  'whisper.cpp</a> (MIT).')
MODELS = {
    "orukeet": {
        "name": "Orukeet", "short": "Orukeet", "engine": "orukeet",
        "note": "English and 24 other European languages; fastest, about 800 MB",
        "devices": ["cuda", "vulkan", "cpu"],
        "credit": 'Speech model: <a href="https://github.com/Oruk-AI/orukeet">Orukeet</a> '
                  "by Oruk AI, based on NVIDIA Parakeet TDT 0.6B v3, licensed "
                  '<a href="https://creativecommons.org/licenses/by-sa/4.0/">CC BY-SA 4.0</a>.',
    },
    "large-v3-turbo": {
        "name": "Whisper large-v3-turbo", "short": "Whisper turbo", "engine": "whisper",
        "note": "99 languages, very accurate; 574 MB",
        "devices": ["cuda", "cpu"], "credit": WHISPER_CREDIT,
        "file": "ggml-large-v3-turbo-q5_0.bin", "size": 574041195,
        "url": f"{HF}/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin",
        "sha256": "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
    },
    "small": {
        "name": "Whisper small", "short": "Whisper small", "engine": "whisper",
        "note": "99 languages, light and quick, less accurate; 190 MB",
        "devices": ["cuda", "cpu"], "credit": WHISPER_CREDIT,
        "file": "ggml-small-q5_1.bin", "size": 190085487,
        "url": f"{HF}/ggerganov/whisper.cpp/resolve/main/ggml-small-q5_1.bin",
        "sha256": "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb",
    },
    "distil-large-v3": {
        "name": "Distil-Whisper large-v3", "short": "Distil-Whisper", "engine": "whisper",
        "note": "English only, accurate and faster than full Whisper; 1.5 GB",
        "devices": ["cuda", "cpu"], "language": "en",
        "credit": 'Speech model: <a href="https://github.com/huggingface/distil-whisper">'
                  "Distil-Whisper</a> by Hugging Face (MIT), run by "
                  '<a href="https://github.com/ggml-org/whisper.cpp">whisper.cpp</a> (MIT).',
        "file": "ggml-distil-large-v3.bin", "size": 1519521155,
        "url": f"{HF}/distil-whisper/distil-large-v3-ggml/resolve/main/ggml-distil-large-v3.bin",
        "sha256": "2883a11b90fb10ed592d826edeaee7d2929bf1ab985109fe9e1e7b4d2b69a298",
    },
    "custom": {
        "name": "Custom Whisper model", "short": "Custom Whisper", "engine": "whisper",
        "note": "any whisper.cpp model file (.bin), on this PC or a download link",
        "devices": ["cuda", "cpu"],
        "credit": 'Run by <a href="https://github.com/ggml-org/whisper.cpp">whisper.cpp</a> (MIT).',
    },
    "command": {
        "name": "Custom command", "short": "Custom command", "engine": "command",
        "note": "any program that turns a WAV file into JSON, SRT or VTT",
        "devices": [], "credit": "",
    },
}
DEFAULT = "orukeet"

# whisper.cpp's Windows builds (pinned; tagged releases carry no binaries).
WHISPER_BUILD = "b5130"
_WGH = f"https://github.com/ggml-org/whisper.cpp/releases/download/{WHISPER_BUILD}"
WHISPER_RUNTIMES = {
    "cpu": {"url": f"{_WGH}/whisper-bin-x64.zip", "size": 8573270,
            "sha256": "f9ec6c52a2e949b62ab51fa21d0d497958f9e41c3010c157c4e42932d5316f3c"},
    "cuda": {"url": f"{_WGH}/whisper-cublas-12.4.0-bin-x64.zip", "size": 674539285,
             "sha256": "af520ddd034d985b55dfeea3e465ed93653ba2aee1a55e865033edc548c272a7"},
}
LANGUAGES = [("auto", "Detect automatically"), ("en", "English"), ("es", "Spanish"),
             ("fr", "French"), ("de", "German"), ("it", "Italian"), ("pt", "Portuguese"),
             ("nl", "Dutch"), ("pl", "Polish"), ("ru", "Russian"), ("uk", "Ukrainian"),
             ("tr", "Turkish"), ("ar", "Arabic"), ("hi", "Hindi"), ("zh", "Chinese"),
             ("ja", "Japanese"), ("ko", "Korean")]


def cache_dir() -> Path:
    """Where model and runtime files go: next to installation.json, so a
    source checkout keeps reusing its existing orukeet-cache."""
    return paths.install_json().parent / "orukeet-cache"


def whisper_dir() -> Path:
    return cache_dir() / "whisper"


def _resolve(value) -> Path:
    p = Path(str(value or ""))
    return p if p.is_absolute() else paths.install_json().parent / p


def model_id(cfg):
    """Which MODELS entry an installation.json describes."""
    engine = cfg.get("engine", "orukeet")
    if engine == "whisper":
        return cfg.get("model_id") if cfg.get("model_id") in MODELS else "custom"
    return engine if engine in MODELS else DEFAULT


def current():
    """The installed engine (installation.json contents), or None."""
    try:
        cfg = json.loads(paths.install_json().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    engine = cfg.get("engine", "orukeet")
    if engine == "command":
        return cfg if str(cfg.get("command", "")).strip() else None
    if engine not in ("orukeet", "whisper"):
        return None
    ok = all(cfg.get(k) and _resolve(cfg[k]).exists() for k in ("model", "runtime"))
    return cfg if ok and cfg.get("device") else None


def ready():
    return current() is not None


def label(cfg):
    """Short name for buttons and the log, e.g. "Orukeet, NVIDIA"."""
    m = MODELS[model_id(cfg)]
    return f"{m['short']}, {SHORT_DEVICES[cfg['device']]}" if cfg.get("device") else m["short"]


def key(cfg):
    """Changes whenever measured speeds stop applying."""
    if not cfg:
        return None
    return (model_id(cfg), cfg.get("device"), str(cfg.get("model", "")), cfg.get("command"))


@cache
def has_nvidia():
    smi = shutil.which("nvidia-smi")
    if not smi:
        return False
    try:
        return subprocess.run([smi, "-L"], capture_output=True, timeout=5,
                              creationflags=NO_WINDOW).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@cache
def has_vulkan():
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    return (system / "vulkan-1.dll").exists()


def recommended(model=DEFAULT):
    """(device, reason) for this PC and model."""
    devices = MODELS[model]["devices"]
    if has_nvidia():
        return "cuda", "an NVIDIA graphics card was found"
    if has_vulkan():
        if "vulkan" in devices:
            return "vulkan", "a graphics card with Vulkan support was found"
        return "cpu", "Whisper can't use this graphics card; Orukeet is faster here"
    return "cpu", "no supported graphics card was found"


# ---- Orukeet ------------------------------------------------------------

def _orukeet_catalog():
    from orukeet import install as oi
    return json.loads(Path(oi.__file__).with_name("artifacts.json").read_text())


def _orukeet_sizes(device):
    from orukeet import install as oi
    spec = _orukeet_catalog()["files"]["q8"]
    model_path = cache_dir() / "models" / spec["path"]
    model = 0 if model_path.exists() and model_path.stat().st_size == spec["size"] else spec["size"]
    runtime = 0
    try:
        rt = oi.runtime_spec(device)
        if not list(cache_dir().glob(f"runtime-{device}-*/receipt.json")):
            runtime = int(rt.get("install_bytes", 0))
    except ValueError:
        pass
    return model, runtime


def _install_orukeet(device, log):
    from orukeet import install as oi
    cache = cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    log("Downloading the speech engine...")
    runtime = oi.install_runtime(device, cache)
    model = cache / "models" / _orukeet_catalog()["files"]["q8"]["path"]
    if _orukeet_sizes(device)[0]:  # not downloaded yet (fetch checks size and SHA-256)
        log("Downloading the speech model...")
        model = oi.fetch("q8", cache)
    cfg = {"engine": "orukeet", "model": str(Path(model).resolve()),
           "runtime": str(Path(runtime).resolve()), "device": device}

    def cpu():
        return dict(cfg, runtime=str(Path(oi.install_runtime("cpu", cache)).resolve()),
                    device="cpu")
    return _tested(cfg, cpu, log)


# ---- Whisper (whisper.cpp) ----------------------------------------------

def _runtime_dir(device) -> Path:
    return whisper_dir() / f"whisper-{WHISPER_BUILD}-{device}"


def whisper_cli(runtime: Path) -> Path:
    found = sorted(Path(runtime).rglob("whisper-cli.exe"))
    if not found:
        raise RuntimeError(f"whisper-cli.exe is missing from {runtime}")
    return found[0]


def _model_path(model):
    return whisper_dir() / "models" / MODELS[model]["file"]


def _whisper_sizes(choice):
    """(model bytes, runtime archive bytes) still to download."""
    m = MODELS[choice["model"]]
    model = 0
    if "file" in m:
        p = _model_path(choice["model"])
        model = 0 if p.exists() and p.stat().st_size == m["size"] else m["size"]
    runtime = 0 if _runtime_dir(choice["device"]).is_dir() else \
        WHISPER_RUNTIMES[choice["device"]]["size"]
    return model, runtime


def _install_runtime(device, log):
    from updater import fetch_file
    target = _runtime_dir(device)
    if target.is_dir():
        return target
    spec = WHISPER_RUNTIMES[device]
    whisper_dir().mkdir(parents=True, exist_ok=True)
    log("Downloading the speech engine...")
    archive = fetch_file(spec["url"], whisper_dir() / Path(spec["url"]).name,
                         spec["sha256"], spec["size"])
    log("Unpacking the speech engine...")
    tmp = target.with_name(target.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(tmp)
    tmp.replace(target)  # complete folders only, so is_dir() means installed
    archive.unlink(missing_ok=True)
    return target


def _whisper_model(choice, log):
    from updater import fetch_file
    if choice["model"] != "custom":
        m = MODELS[choice["model"]]
        path = _model_path(choice["model"])
        if not (path.exists() and path.stat().st_size == m["size"]):
            log("Downloading the speech model...")
            path.parent.mkdir(parents=True, exist_ok=True)
            fetch_file(m["url"], path, m["sha256"], m["size"])
        return path
    source = str(choice.get("path", "")).strip().strip('"')
    if source.lower().startswith(("https://", "http://")):
        name = source.split("?")[0].rstrip("/").rsplit("/", 1)[-1] or "custom-model.bin"
        path = whisper_dir() / "models" / "custom" / name
        if not path.exists():
            log("Downloading the speech model...")
            path.parent.mkdir(parents=True, exist_ok=True)
            fetch_file(source, path)
        return path
    path = Path(source)
    if not source or not path.is_file():
        raise RuntimeError(f"model file not found: {source or '(none chosen)'}")
    return path


def _install_whisper(choice, log):
    device = choice["device"]
    model = _whisper_model(choice, log)
    runtime = _install_runtime(device, log)
    m = MODELS[choice["model"]]
    cfg = {"engine": "whisper", "model_id": choice["model"],
           "model": str(Path(model).resolve()), "runtime": str(runtime.resolve()),
           "device": device, "language": m.get("language") or choice.get("language") or "auto"}

    def cpu():
        return dict(cfg, runtime=str(_install_runtime("cpu", log).resolve()), device="cpu")
    return _tested(cfg, cpu, log)


# ---- everything ---------------------------------------------------------

def sizes(choice):
    """(file bytes, archive bytes) still to download for a choice
    {"model", "device", ...}. Archives are unpacked, so they need room twice."""
    engine = MODELS[choice["model"]]["engine"]
    if engine == "orukeet":
        return _orukeet_sizes(choice["device"])
    if engine == "whisper":
        return _whisper_sizes(choice)
    return 0, 0


def folder_size(path: Path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def load(cfg=None):
    """The engine as a context manager whose transcribe(wav_path) returns
    {"segments": [{"start", "end", "text"}, ...]}."""
    cfg = cfg or current()
    if cfg is None:
        raise RuntimeError("The speech engine isn't set up yet. Open Add videos and "
                           "click Speech engine... to set it up.")
    engine = cfg.get("engine", "orukeet")
    if engine == "command":
        from asr_external import CommandASR
        return CommandASR(cfg["command"])
    if engine == "whisper":
        from asr_external import WhisperCpp
        return WhisperCpp(whisper_cli(_resolve(cfg["runtime"])), _resolve(cfg["model"]),
                          cfg.get("language", "auto"))
    from orukeet import Orukeet
    return Orukeet(str(_resolve(cfg["model"])), str(_resolve(cfg["runtime"])),
                   device=cfg["device"])


def self_test(cfg):
    """Transcribe two seconds of silence; raises if the engine can't run."""
    fd, name = tempfile.mkstemp(prefix="Haystacks-test-", suffix=".wav")
    os.close(fd)
    try:
        with wave.open(name, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\0\0" * 32000)
        with load(cfg) as asr:
            asr.transcribe(name)
    finally:
        Path(name).unlink(missing_ok=True)


def _tested(cfg, cpu_fallback, log):
    """Self-test cfg; if a graphics engine fails, test and return the CPU one."""
    log("Testing the speech engine...")
    try:
        self_test(cfg)
        return cfg
    except Exception as e:
        if cfg.get("device") in (None, "cpu"):
            raise
        log(f"The {DEVICES[cfg['device']]} engine didn't work here ({e}). "
            "Using the processor instead.")
        cfg = cpu_fallback()
        self_test(cfg)
        return cfg


def install(choice, log=print):
    """Download (if needed), test and record the engine for a choice:
    {"model": MODELS key, "device", "language", "path" (custom model file or
    link), "command" (custom command)}. Returns the installed config; its
    device is "cpu" if the graphics engine failed."""
    engine = MODELS[choice["model"]]["engine"]
    if engine == "orukeet":
        cfg = _install_orukeet(choice["device"], log)
    elif engine == "whisper":
        cfg = _install_whisper(choice, log)
    else:
        command = str(choice.get("command", "")).strip()
        if "{input}" not in command or "{output}" not in command:
            raise RuntimeError("the command needs both {input} and {output}")
        cfg = {"engine": "command", "command": command}
        log("Testing the command...")
        self_test(cfg)
    target = paths.install_json()
    target.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(target, json.dumps(cfg, indent=2))
    return cfg
