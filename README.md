# Haystacks

Find the needle: search everything that was said across hours of video.

Haystacks is a Windows desktop app that transcribes whole folders of videos
on your own PC, then lets you search what was said and jump straight to that
moment in a built-in player. Nothing is uploaded anywhere; transcription runs
locally with the [Orukeet](https://github.com/Oruk-AI/orukeet) speech
recognition engine.

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
- Video formats: `.mp4`, `.mkv`, `.mov`, `.avi`, `.mts`, `.m4v`, `.webm`.
- Light and dark theme, following your Windows setting.

## Requirements

- Windows 10 or 11
- Python 3.12 or newer
- ffmpeg
- A GPU is recommended. On an NVIDIA GPU (CUDA) transcription runs at
  hundreds of times real time; AMD and Intel GPUs work through Vulkan, and
  CPU-only works too, just slower.

## Setup

Run these in PowerShell.

1. **Install Python and Git** (skip if you have them):

   ```powershell
   winget install Python.Python.3.13 Git.Git
   ```

   Close and reopen PowerShell afterwards.

2. **Download Haystacks and set up its Python environment:**

   ```powershell
   cd $HOME\Documents
   git clone https://github.com/YOUR-USERNAME/Haystacks.git
   cd Haystacks
   py -3.13 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. **Download the speech engine** (model and runtime, about 700 MB):

   ```powershell
   .\.venv\Scripts\orukeet.exe install --device auto --cache .\orukeet-cache --output installation.json
   ```

   `--device auto` picks CUDA on NVIDIA GPUs and otherwise the CPU. On an AMD
   or Intel GPU, use `--device vulkan` instead; it is several times faster
   than the CPU.

4. **Install ffmpeg** (either one):

   ```powershell
   winget install Gyan.FFmpeg
   ```

   or, inside the app's environment:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install static-ffmpeg
   ```

5. **Create a desktop shortcut:**

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\make_shortcut.ps1
   ```

   Or start it directly: `.\.venv\Scripts\pythonw.exe Haystacks.pyw`

## Where things are stored

- **Transcripts** for each folder go in a `_Haystacks` folder inside it:
  `<stem>.json` per video, plus `_loudness\` (loudness data) and
  `_failed.txt` (if a video couldn't be transcribed). No audio files are
  kept; audio is decoded from the video into memory.
- **Settings** (folder list, hidden recordings, window layout):
  `%APPDATA%\Haystacks\settings.json`.
- "Clear finished videos..." in Add videos deletes only the files Haystacks
  wrote in `_Haystacks`, never your videos.

## Project layout

| File | Role |
|---|---|
| `Haystacks.pyw` | Entry point: settings, theme, crash dialog. |
| `search_window.py` | Main window: search, filters, results list, Loudest moments. |
| `player.py` | Built-in video player panel. |
| `transcribe_window.py` | "Add videos" window: folders, audio tracks, transcription progress, importing. |
| `pipeline.py` | ffmpeg/ffprobe, audio decoding, Orukeet, loudness, output files. |
| `search_data.py` | Turns a folder of transcripts into search data. |
| `library.py` | Search logic, independent of the UI. |
| `transcripts.py` | Reads every importable transcript format. |
| `measure.py` | Background loudness and date measuring for imported folders. |
| `theme.py` | Colors, fonts, stylesheet and icons. |
| `make_shortcut.ps1` | Creates the desktop shortcut. |

## License

Haystacks is free software, licensed under the
[GNU General Public License v3.0](LICENSE).

It uses, but does not include:

- [Orukeet](https://github.com/Oruk-AI/orukeet): MIT License. Its model
  weights (downloaded by `orukeet install`) are licensed CC BY-SA 4.0.
- [PySide6 / Qt](https://www.qt.io/qt-for-python): LGPL-3.0.
- [NumPy](https://numpy.org): BSD-3-Clause.
- [ffmpeg](https://ffmpeg.org): installed separately; LGPL/GPL.
