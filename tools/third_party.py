"""
Writes THIRD-PARTY-LICENSES.txt for the installer: the license of every
Python package bundled into the app, read from the build environment, plus
Python itself and source links for Qt and FFmpeg (LGPL). Run by build.ps1
and the release workflow before Inno Setup.
"""
import sys
from importlib import metadata
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "THIRD-PARTY-LICENSES.txt"
# Build tools that are not part of the installed app.
SKIP = {"pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib",
        "altgraph", "pefile", "pywin32-ctypes"}
LICENSE_NAMES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")

HEADER = """Haystacks bundles the software below. Each part keeps its own license; the
full texts follow. Haystacks itself is GPL-3.0 (see LICENSE.txt).

Qt (PySide6) is used under the LGPL-3.0. Its source is at
https://download.qt.io/official_releases/qt/ and https://code.qt.io/. The Qt
libraries are separate DLL files in the _internal folder and can be replaced.

FFmpeg (inside PyAV and Qt Multimedia) is used under the LGPL-2.1-or-later.
Its source is at https://ffmpeg.org/download.html.

The Orukeet speech model is not bundled: it is downloaded on first use and is
licensed CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/); it is
an adaptation of NVIDIA Parakeet TDT 0.6B v3 (CC BY 4.0).
"""


def license_texts(dist):
    texts = []
    for f in dist.files or []:
        if Path(f.name).stem.upper().startswith(LICENSE_NAMES):
            try:
                raw = Path(f.locate()).read_bytes()
            except OSError:
                continue
            if b"\x00" not in raw:  # skip compiled files (some ship AUTHORS.pyc)
                texts.append((f.name, raw.decode("utf-8", errors="replace").strip()))
    return texts


def main():
    parts = [HEADER]
    py_license = Path(sys.base_prefix) / "LICENSE.txt"
    if py_license.exists():
        parts.append(f"{'=' * 78}\nPython {sys.version.split()[0]}\n{'=' * 78}\n"
                     + py_license.read_text(encoding="utf-8", errors="replace").strip())
    seen = set()
    for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata["Name"] or "").lower()):
        name = dist.metadata["Name"] or "?"
        if name.lower() in SKIP or name.lower() in seen:
            continue
        seen.add(name.lower())
        lic = dist.metadata.get("License-Expression") or dist.metadata.get("License") or ""
        if len(lic) > 80:  # some packages put the whole text here
            lic = lic.splitlines()[0]
        block = [f"{'=' * 78}\n{name} {dist.version}" + (f"  ({lic})" if lic else "")]
        home = dist.metadata.get("Home-page") or next(
            (u.split(",", 1)[1].strip() for u in dist.metadata.get_all("Project-URL") or []
             if u.lower().startswith(("homepage", "source", "repository"))), "")
        if home:
            block.append(home)
        block.append("=" * 78)
        for fname, text in license_texts(dist):
            block.append(f"--- {fname} ---\n{text}")
        parts.append("\n".join(block))
    OUT.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} ({len(seen)} packages)")


if __name__ == "__main__":
    main()
