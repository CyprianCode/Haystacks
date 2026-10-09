"""
The built-in video player: plays a recording from the moment a result was
said, using the folder's chosen audio track. Qt's player decodes with FFmpeg,
so it handles MKV, MOV and camera MP4s that a browser can't.

YouTube results play in the same panel through youtube_view.YouTubeView
(mode "youtube"); the controls work on whichever is active.
"""
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QSlider, QStackedWidget, QVBoxLayout)

import theme
from library import hms

SKIP_MS = 5000
LEAD_IN_S = 2  # start a little before the sentence


class VideoView(QVideoWidget):
    """Video surface: double-click for fullscreen, Esc to leave it."""
    toggle_play = Signal()

    def mouseDoubleClickEvent(self, event):
        self.setFullScreen(not self.isFullScreen())

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.isFullScreen():
            self.toggle_play.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.isFullScreen():
            self.setFullScreen(False)
        elif event.key() == Qt.Key_Space:
            self.toggle_play.emit()
        else:
            super().keyPressEvent(event)


class PlayerPanel(QFrame):
    def __init__(self, settings, save, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.settings = settings
        self.save = save
        self.source = None          # path of the loaded video, or "youtube:<id>"
        self.mode = "file"          # or "youtube"
        self.yt = None              # YouTubeView, made on the first YouTube video
        self.pending_ms = None      # seek once the video has loaded
        self.pending_track = None   # audio track to switch to once known
        self.dragging = False

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(float(settings.get("volume", 0.8)))
        self.player.setAudioOutput(self.audio)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        self.title = QLabel("Player")
        self.title.setObjectName("playerTitle")
        self.subtitle = QLabel("Click a result to play it here.")
        self.subtitle.setObjectName("muted")
        self.subtitle.setWordWrap(True)
        lay.addWidget(self.title)
        lay.addWidget(self.subtitle)

        self.stack = QStackedWidget()
        self.stack.setMinimumSize(320, 180)
        self.stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.message = QLabel("Nothing playing")
        self.message.setAlignment(Qt.AlignCenter)
        self.message.setWordWrap(True)
        self.message.setStyleSheet(f"background: {theme.C['video']}; color: #8A919B;"
                                   "border-radius: 4px; padding: 20px;")
        self.video = VideoView()
        self.video.setStyleSheet(f"background: {theme.C['video']};")
        self.video.toggle_play.connect(self.toggle)
        self.player.setVideoOutput(self.video)
        self.stack.addWidget(self.message)
        self.stack.addWidget(self.video)
        lay.addWidget(self.stack, 1)

        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.sliderPressed.connect(lambda: setattr(self, "dragging", True))
        self.seek.sliderReleased.connect(self._seek_released)
        self.seek.sliderMoved.connect(lambda v: self.time.setText(self._time_text(v)))
        lay.addWidget(self.seek)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.play_btn = self._icon_button("play", "Play or pause (Space)", self.toggle)
        back = self._icon_button("back", "Back 5 seconds", lambda: self.skip(-SKIP_MS))
        fwd = self._icon_button("forward", "Forward 5 seconds", lambda: self.skip(SKIP_MS))
        self.time = QLabel("0:00 / 0:00")
        self.time.setObjectName("time")
        self.mute_btn = self._icon_button("volume", "Mute", self.toggle_mute)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(round(self.audio.volume() * 100))
        self.volume.setFixedWidth(90)
        self.volume.valueChanged.connect(self._volume_changed)
        self.volume.sliderReleased.connect(self._store_volume)
        full = self._icon_button("fullscreen", "Fullscreen (double-click the video; Esc to leave)",
                                 self.full_screen)
        for w in (self.play_btn, back, fwd):
            row.addWidget(w)
        row.addSpacing(8)
        row.addWidget(self.time)
        row.addStretch(1)
        row.addWidget(self.mute_btn)
        row.addWidget(self.volume)
        row.addWidget(full)
        lay.addLayout(row)
        self.controls = [self.play_btn, back, fwd, self.seek, full]
        self._set_controls(False)

        self.player.positionChanged.connect(lambda pos: self._position(pos, "file"))
        self.player.durationChanged.connect(lambda d: self._duration_changed(d, "file"))
        self.player.playbackStateChanged.connect(
            lambda st: self._playing(st == QMediaPlayer.PlayingState, "file"))
        self.player.mediaStatusChanged.connect(self._status)
        self.player.tracksChanged.connect(self._tracks)
        self.player.errorOccurred.connect(self._error)

    # ---- public ---------------------------------------------------------------
    def play_at(self, video: Path, seconds, title, subtitle, track=1):
        """Play `video` from just before `seconds`, on audio track `track` (from 1)."""
        start = max(0, int((seconds - LEAD_IN_S) * 1000))
        self._switch("file")
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.stack.setCurrentWidget(self.video)
        self._set_controls(True)
        if self.source == str(video) and self.player.mediaStatus() not in (
                QMediaPlayer.NoMedia, QMediaPlayer.InvalidMedia):
            self.player.setPosition(start)
            self.player.play()
            return
        self.source = str(video)
        # Set these after setSource: when switching videos, setSource reports
        # LoadedMedia for the old video before the new one starts loading, which
        # would use up the seek and play the new video from the start.
        self.player.setSource(QUrl.fromLocalFile(str(video)))
        self.pending_ms = start
        self.pending_track = max(0, track - 1)
        self.player.play()

    def play_youtube(self, video_id, seconds, title, subtitle):
        """Play a YouTube video from just before `seconds`."""
        if self.yt is None:
            from youtube_view import YouTubeView  # loads the web engine
            self.yt = YouTubeView(self.audio.volume())
            self.yt.toggle_play.connect(self.toggle)
            self.yt.positionChanged.connect(lambda pos: self._position(pos, "youtube"))
            self.yt.durationChanged.connect(lambda d: self._duration_changed(d, "youtube"))
            self.yt.playingChanged.connect(lambda on: self._playing(on, "youtube"))
            self.yt.failed.connect(self._youtube_failed)
            self.stack.addWidget(self.yt)
        self._switch("youtube")
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.stack.setCurrentWidget(self.yt)
        self._set_controls(True)
        if self.source != f"youtube:{video_id}":
            same = self.yt.video_id == video_id  # back from a file: duration already known
            self.seek.setRange(0, self.yt.duration if same else 0)
        self.source = f"youtube:{video_id}"
        self.yt.set_muted(self.audio.isMuted())
        self.yt.play_at(video_id, max(0.0, seconds - LEAD_IN_S))

    def toggle(self):
        if not self.source:
            return
        if self.mode == "youtube":
            self.yt.toggle()
        elif self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def skip(self, ms):
        if not self.source:
            return
        if self.mode == "youtube":
            self.yt.seek(self.yt.position + ms)
        else:
            self.player.setPosition(max(0, self.player.position() + ms))

    def pause(self):
        self.player.pause()
        if self.yt:
            self.yt.pause()

    def stop(self):
        self.player.stop()
        if self.yt:
            self.yt.stop()

    def full_screen(self):
        if self.mode == "youtube":
            self.yt.set_full_screen(True)
        else:
            self.video.setFullScreen(True)

    # ---- internals ------------------------------------------------------------
    def _icon_button(self, kind, tip, action):
        b = QPushButton()
        b.setProperty("icon", True)
        b.setIcon(theme.icon(kind))
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(action)
        return b

    def _set_controls(self, on):
        for w in self.controls:
            w.setEnabled(on)

    def _switch(self, mode):
        """Pause whichever player isn't the one about to play."""
        if mode == self.mode:
            return
        if self.mode == "youtube":
            self.yt.stop()
        else:
            self.player.pause()
        self.mode = mode
        self.source = None

    def _duration(self):
        return self.yt.duration if self.mode == "youtube" else self.player.duration()

    def _time_text(self, pos):
        return f"{hms(pos / 1000)} / {hms(self._duration() / 1000)}"

    def _duration_changed(self, d, mode):
        if mode == self.mode:
            self.seek.setRange(0, d)

    def _position(self, pos, mode):
        if mode == self.mode and not self.dragging:
            self.seek.setValue(pos)
            self.time.setText(self._time_text(pos))

    def _seek_released(self):
        self.dragging = False
        if self.mode == "youtube":
            self.yt.seek(self.seek.value())
        else:
            self.player.setPosition(self.seek.value())

    def _playing(self, playing, mode):
        if mode == self.mode:
            self.play_btn.setIcon(theme.icon("pause" if playing else "play"))

    def _youtube_failed(self, text):
        self.source = None
        self._set_controls(False)
        self.message.setText(f"This video can't play here.\n{text}\n\nRight-click the "
                             "result and choose Open on YouTube.")
        self.stack.setCurrentWidget(self.message)

    def _status(self, status):
        if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) \
                and self.pending_ms is not None:
            self.player.setPosition(self.pending_ms)
            self.pending_ms = None

    def _tracks(self):
        tracks = self.player.audioTracks()
        if self.pending_track is not None and tracks:
            self.player.setActiveAudioTrack(min(self.pending_track, len(tracks) - 1))
            self.pending_track = None

    def _error(self, error, text):
        if error == QMediaPlayer.NoError:
            return
        self.source = None
        self._set_controls(False)
        self.message.setText(f"This video can't play here.\n{text}\n\nRight-click the "
                             "result and choose Open in another player.")
        self.stack.setCurrentWidget(self.message)

    def _volume_changed(self, value):
        self.audio.setVolume(value / 100)
        self.audio.setMuted(False)
        if self.yt:
            self.yt.set_volume(value / 100)
        self.mute_btn.setIcon(theme.icon("volume" if value else "mute"))

    def _store_volume(self):
        self.settings["volume"] = round(self.audio.volume(), 2)
        self.save()

    def toggle_mute(self):
        self.audio.setMuted(not self.audio.isMuted())
        if self.yt:
            self.yt.set_muted(self.audio.isMuted())
        self.mute_btn.setIcon(theme.icon("mute" if self.audio.isMuted() else "volume"))
