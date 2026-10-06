# Haystacks

Find the needle: search everything that was said across hours of video.

Haystacks is a Windows desktop app that transcribes whole folders of videos
on your own PC, then lets you search what was said and jump straight to that
moment in a built-in player. Nothing is uploaded anywhere; transcription runs
locally with the [Orukeet](https://github.com/Oruk-AI/orukeet) speech
recognition engine, or a Whisper model if you prefer.

## Features

- **Transcribe folders of videos.** Add a folder, pick which audio track to
  use, and press Transcribe. Finished videos are skipped, so you can stop at
  any time and continue later. Progress, time remaining and speed are shown
  as it runs.
- **Search as you type.** All words must appear in the same sentence; use
  quotes for an exact phrase. Each hit shows the sentences around it.
- **Play the moment.** Click a result to play the video from just before that
  sentence, or open it in another player (VLC if installed).
- **Loudest moments.** A ranked list of the loudest moments across all your
  recordings, with what was being said at the time.
- **Filter and sort** by folder and date, newest, oldest or loudest first,
  and hide recordings you don't want in results.
- **Import existing transcripts** instead of re-transcribing: Haystacks JSON,
  Whisper JSON (openai-whisper, faster-whisper, whisper.cpp), and `.srt` /
  `.vtt` subtitles named like the videos (`clip.srt` or `clip.en.srt` for
  `clip.mp4`). Imported files are read in place and never changed.
- **Updates itself.** When a new version is out, a banner offers to install
  it with one click.
- Video formats: `.mp4`, `.mkv`, `.mov`, `.avi`, `.mts`, `.m4v`, `.webm`.
- Light and dark theme, following your Windows setting.

## Install

1. Download `Haystacks-Setup-<version>.exe` from the
   [latest release](https://github.com/CyprianStream/Haystacks/releases/latest).
2. Run it. It installs for your Windows user only, so no administrator
   rights are needed.

   The installer isn't code-signed yet, so Windows may show "Windows
   protected your PC". Click **More info**, then **Run anyway**.
3. Start Haystacks from the Start menu. The first time you transcribe, it
   downloads the speech engine (about 750 to 850 MB, once). It picks the
   fastest engine for your PC, and you can switch later under **Add videos >
   Speech engine**:
   - **NVIDIA graphics card (CUDA):** hundreds of times faster than real time.
   - **Other graphics cards (Vulkan),** such as AMD and Intel: several times
     faster than the CPU.
   - **CPU only:** works everywhere, just slower.

### Choosing a speech model

Orukeet is the default and is recommended for English and 24 other European
languages. In **Add videos > Speech engine** you can switch to another model
(each is downloaded once, when first chosen):

| Model | Good for | Download |
|---|---|---|
| **Orukeet** (recommended) | English and European languages; fastest | ~800 MB |
| **Whisper large-v3-turbo** | 99 languages, very accurate | 574 MB + engine |
| **Whisper small** | 99 languages on slower PCs; less accurate | 190 MB + engine |
| **Distil-Whisper large-v3** | English only, faster than full Whisper | 1.5 GB + engine |
| **Custom Whisper model** | Any [whisper.cpp](https://github.com/ggml-org/whisper.cpp) model file (`.bin`), e.g. one fine-tuned for your language; a file on your PC or a download link | varies |
| **Custom command** | Any speech program you already use | none |

Whisper models run with [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
(engine download: 9 MB for the CPU, about 675 MB for NVIDIA cards). They use
NVIDIA graphics cards or the CPU; on AMD and Intel graphics Orukeet is much
faster. Whisper models let you set the spoken language or detect it.

**Custom command:** enter a command line with `{input}` and `{output}`, for
example `"C:\Tools\my-asr.exe" --audio {input} --out {output}`. `{input}` is
a 16 kHz mono WAV file; `{output}` is a path without an extension, and the
program must write `{output}.json` (Whisper or whisper.cpp JSON),
`{output}.srt` or `{output}.vtt`. Haystacks tests the command on two seconds
of silence before using it.

Switching models doesn't change videos that are already transcribed; use
"Clear finished videos..." to redo a folder with the new model.

Requirements: 64-bit Windows 10 or 11. Searching imported transcripts works
without the speech engine.

## Where things are stored

- **Transcripts** for each folder go in a `_Haystacks` folder inside it:
  `<stem>.json` per video, plus `_loudness\` (loudness data) and
  `_failed.txt` (if a video couldn't be transcribed). No audio files are
  kept; audio is decoded from the video into memory.
- **Settings** (folder list, hidden recordings, window layout):
  `%APPDATA%\Haystacks\settings.json`.
- **The app** is installed in `%LOCALAPPDATA%\Programs\Haystacks`.
- **The speech engine** is stored in `%LOCALAPPDATA%\Haystacks\engine`.
- "Clear finished videos..." in Add videos deletes only the files Haystacks
  wrote in `_Haystacks`, never your videos.
- Uninstalling (Windows Settings > Apps) removes the app and the speech
  engine; your videos, transcripts and settings stay.

## Run from source

For development. Run these in PowerShell:

```powershell
winget install Python.Python.3.13 Git.Git
cd $HOME\Documents
git clone https://github.com/CyprianStream/Haystacks.git
cd Haystacks
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\pythonw.exe Haystacks.pyw
```

The speech engine is set up from inside the app on first use, as above.
`powershell -ExecutionPolicy Bypass -File .\make_shortcut.ps1` creates a
desktop shortcut to the source version.

## Building and releasing

- **Build an installer locally:**

  ```powershell
  winget install JRSoftware.InnoSetup
  powershell -ExecutionPolicy Bypass -File .\build.ps1
  ```

  This produces `dist\Haystacks-Setup-<version>.exe`.
- **Publish a release:**
  1. Set the new number in `version.py`, for example `0.2.0`.
  2. Commit, then tag and push:

     ```powershell
     git commit -am "Release 0.2.0"
     git tag v0.2.0
     git push origin main v0.2.0
     ```

  The [Release workflow](.github/workflows/release.yml) builds the installer
  and publishes it on GitHub. Installed copies offer the update within a
  day.

## Project layout

| File | Role |
|---|---|
| `Haystacks.pyw` | Entry point: settings, theme, crash dialog. |
| `search_window.py` | Main window: search, filters, results list, Loudest moments. |
| `player.py` | Built-in video player panel. |
| `transcribe_window.py` | "Add videos" window: folders, audio tracks, transcription progress, importing. |
| `engine.py`, `engine_dialog.py` | Choosing, downloading and testing the speech model and engine. |
| `asr_external.py` | Runs whisper.cpp or a custom command as the speech engine. |
| `updater.py`, `update_banner.py` | Checking GitHub for new versions and installing them. |
| `pipeline.py` | Reading audio (PyAV), transcribing, loudness, output files. |
| `search_data.py` | Turns a folder of transcripts into search data. |
| `library.py` | Search logic, independent of the UI. |
| `transcripts.py` | Reads every importable transcript format. |
| `measure.py` | Background loudness and date measuring for imported folders. |
| `theme.py` | Colors, fonts, stylesheet and icons. |
| `paths.py`, `version.py` | Where files live; the version number. |
| `haystacks.spec`, `installer.iss`, `build.ps1` | Building the app and its installer. |
| `tools/third_party.py` | Writes the license notices bundled with the installer. |
| `assets/` | The app icon and the script that draws it. |

## License

Haystacks is free software, licensed under the
[GNU General Public License v3.0](LICENSE).

The installer includes Python, [PySide6 / Qt](https://www.qt.io/qt-for-python)
(LGPL-3.0), [NumPy](https://numpy.org) (BSD-3-Clause),
[PyAV](https://pyav.org) (BSD-3-Clause) with [FFmpeg](https://ffmpeg.org)
(LGPL-2.1-or-later), and [Orukeet](https://github.com/Oruk-AI/orukeet)'s
Python package (MIT). Their full license texts are in
`THIRD-PARTY-LICENSES.txt` next to the installed app.

The Orukeet speech model is downloaded on first use and is licensed
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). It is based
on NVIDIA Parakeet TDT 0.6B v3. The optional Whisper models, Distil-Whisper
and whisper.cpp are downloaded only if chosen, and are licensed MIT.
