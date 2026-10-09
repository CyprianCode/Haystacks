"""
Speech engines that run as a separate program: whisper.cpp's whisper-cli, or
any command the user gives. Both take a WAV path, like Orukeet, and return
{"segments": [{"start", "end", "text"}, ...]}, so pipeline.transcribe works
the same for every engine.

The program runs inside a Windows job object that kills it when Haystacks
closes, so quitting mid-video doesn't leave a transcriber running.
"""
import ctypes
import os
import shlex
import subprocess
import tempfile
from ctypes import wintypes
from pathlib import Path

import transcripts

NO_WINDOW = 0x08000000
OUTPUT_EXTS = (".json", ".srt", ".vtt")


class Job:
    """A job object with kill-on-close: processes added to it end when this
    process ends (or close() is called)."""
    def __init__(self):
        self.handle = None
        try:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        except (AttributeError, OSError):
            return  # not Windows
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.OpenProcess.restype = wintypes.HANDLE
        self.k32 = k32
        handle = k32.CreateJobObjectW(None, None)
        if not handle:
            return

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if k32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.handle = handle  # 9 = JobObjectExtendedLimitInformation
        else:
            k32.CloseHandle(handle)

    def add(self, proc):
        if not self.handle:
            return
        h = self.k32.OpenProcess(0x0101, False, proc.pid)  # TERMINATE | SET_QUOTA
        if h:
            self.k32.AssignProcessToJobObject(self.handle, h)
            self.k32.CloseHandle(h)

    def close(self):
        if self.handle:
            self.k32.CloseHandle(self.handle)
            self.handle = None


class _External:
    """Shared part: run a program, then read the transcript it wrote."""
    def __init__(self):
        self.job = Job()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.job.close()

    def run(self, args, cwd=None):
        try:
            proc = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                    creationflags=NO_WINDOW)
        except FileNotFoundError:
            raise RuntimeError(f"program not found: {args[0]}") from None
        self.job.add(proc)
        _, err = proc.communicate()
        if proc.returncode:
            tail = err.decode("utf-8", "replace").strip().splitlines()[-3:]
            raise RuntimeError(f"the speech program stopped with code {proc.returncode}"
                               + (": " + " / ".join(tail) if tail else ""))

    def transcribe(self, wav):
        fd, name = tempfile.mkstemp(prefix="Haystacks-", suffix=".out")
        os.close(fd)
        base = Path(name).with_suffix("")
        Path(name).unlink(missing_ok=True)
        try:
            self.run_on(wav, base)
            written = next((base.with_suffix(e) for e in OUTPUT_EXTS
                            if base.with_suffix(e).exists()), None)
            if written is None:
                raise RuntimeError("the speech program didn't write a transcript "
                                   f"({base.name}.json, .srt or .vtt)")
            data = transcripts.read(written)
            segs = data["segments"]
            if written.suffix == ".json":  # SRT/VTT are merged by read() already
                segs = transcripts.merge_cues(segs)
            return {"segments": [{"start": s, "end": e, "text": t} for s, e, t in segs]}
        finally:
            for e in OUTPUT_EXTS:
                base.with_suffix(e).unlink(missing_ok=True)


class WhisperCpp(_External):
    def __init__(self, cli, model, language="auto"):
        super().__init__()
        self.cli = Path(cli)
        self.model = str(model)
        self.language = language or "auto"

    def run_on(self, wav, base):
        threads = max(1, min(16, (os.cpu_count() or 4)))
        self.run([str(self.cli), "-m", self.model, "-f", str(wav), "-l", self.language,
                  "-oj", "-of", str(base), "-np", "-t", str(threads)], cwd=self.cli.parent)


def command_args(template, wav, base):
    """The command line with {input} and {output} filled in, as a list."""
    args = shlex.split(template, posix=False)
    out = []
    for a in args:
        if len(a) >= 2 and a[0] == a[-1] == '"':
            a = a[1:-1]  # posix=False keeps the quotes
        out.append(a.replace("{input}", str(wav)).replace("{output}", str(base)))
    return out


class CommandASR(_External):
    def __init__(self, template):
        super().__init__()
        self.template = template

    def run_on(self, wav, base):
        self.run(command_args(self.template, wav, base))
