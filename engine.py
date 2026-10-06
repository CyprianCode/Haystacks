"""
Setting up the speech engine (Orukeet model + native runtime) from inside the
app, so users never need a terminal. UI-free; engine_dialog.py drives it.

Device choice: NVIDIA card -> CUDA, other graphics card -> Vulkan (much faster
than the CPU, e.g. 3x on an AMD integrated GPU), else CPU. Orukeet's own
"--device auto" never picks Vulkan, so Haystacks decides itself. After
downloading, a short self-test transcribes silence; if the graphics engine
fails, the CPU engine is installed instead.
"""
import json
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import paths
from search_data import write_atomic

DEVICES = {
    "cuda": "NVIDIA graphics card (CUDA)",
    "vulkan": "Other graphics card (Vulkan)",
    "cpu": "Processor only (CPU)",
}
NO_WINDOW = 0x08000000


def cache_dir() -> Path:
    """Where model and runtime files go: next to installation.json, so a
    source checkout keeps reusing its existing orukeet-cache."""
    return paths.install_json().parent / "orukeet-cache"


def current():
    """The installed engine as {"model", "runtime", "device"}, or None."""
    try:
        cfg = json.loads(paths.install_json().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    base = paths.install_json().parent
    ok = all((Path(cfg.get(k, "")) if Path(cfg.get(k, "")).is_absolute()
              else base / cfg.get(k, "")).exists() for k in ("model", "runtime"))
    return cfg if ok and cfg.get("device") else None


def ready():
    return current() is not None


def has_nvidia():
    smi = shutil.which("nvidia-smi")
    if not smi:
        return False
    try:
        return subprocess.run([smi, "-L"], capture_output=True, timeout=5,
                              creationflags=NO_WINDOW).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def has_vulkan():
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    return (system / "vulkan-1.dll").exists()


def recommended():
    """(device, reason) for this PC."""
    if has_nvidia():
        return "cuda", "an NVIDIA graphics card was found"
    if has_vulkan():
        return "vulkan", "a graphics card with Vulkan support was found"
    return "cpu", "no supported graphics card was found"


def sizes(device):
    """(model bytes, runtime bytes) still to download for `device`."""
    from orukeet import install as oi
    catalog = json.loads(Path(oi.__file__).with_name("artifacts.json").read_text())
    spec = catalog["files"]["q8"]
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


def folder_size(path: Path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def self_test(model, runtime, device):
    """Transcribe two seconds of silence; raises if the engine can't run."""
    from orukeet import Orukeet
    fd, name = tempfile.mkstemp(prefix="Haystacks-test-", suffix=".wav")
    os.close(fd)
    try:
        with wave.open(name, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\0\0" * 32000)
        with Orukeet(str(model), str(runtime), device=device) as asr:
            asr.transcribe(name)
    finally:
        Path(name).unlink(missing_ok=True)


def install(device, log=print):
    """Download (if needed), test and record the engine for `device`.
    Returns the device actually installed (CPU if the graphics engine failed)."""
    from orukeet import install as oi
    cache = cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    log("Downloading the speech engine...")
    runtime = oi.install_runtime(device, cache)
    catalog = json.loads(Path(oi.__file__).with_name("artifacts.json").read_text())
    model = cache / "models" / catalog["files"]["q8"]["path"]
    if sizes(device)[0]:  # not downloaded yet (fetch checks size and SHA-256)
        log("Downloading the speech model...")
        model = oi.fetch("q8", cache)
    log("Testing the speech engine...")
    try:
        self_test(model, runtime, device)
    except Exception as e:
        if device == "cpu":
            raise
        log(f"The {DEVICES[device]} engine didn't work here ({e}). Using the processor instead.")
        device = "cpu"
        runtime = oi.install_runtime(device, cache)
        self_test(model, runtime, device)
    target = paths.install_json()
    target.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(target, json.dumps({"model": str(Path(model).resolve()),
                                     "runtime": str(Path(runtime).resolve()),
                                     "device": device}, indent=2))
    return device
