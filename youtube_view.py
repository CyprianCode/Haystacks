"""
Plays YouTube videos inside the player panel, using YouTube's own embedded
player (the IFrame Player API) in a Qt WebEngine view. The panel's play,
skip, seek, volume and fullscreen controls drive it; YouTube's controls are
hidden, and a transparent layer over the video takes the clicks so they work
like on the built-in player (click: play/pause, double-click: fullscreen).

Imported only when the first YouTube video plays, so the web engine doesn't
slow down startup.
"""
import json

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QWidget

# YouTube refuses to play embeds from a page without a web address
# ("Error 153"), so the page gets one. Nothing is loaded from it.
ORIGIN = "https://haystacks.invalid"
POLL_MS = 250

ERRORS = {
    2: "YouTube says the video link is not valid.",
    5: "YouTube's player couldn't play this video here.",
    100: "This video was removed or made private.",
    101: "The owner doesn't allow this video to play outside YouTube.",
    150: "The owner doesn't allow this video to play outside YouTube.",
    152: "YouTube refused to play this video here.",
    153: "YouTube refused to play this video here.",
}

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="referrer" content="strict-origin-when-cross-origin">
<style>html,body{margin:0;height:100%;overflow:hidden;background:#000}
#player{position:absolute;inset:0;width:100%;height:100%}</style></head>
<body><div id="player"></div><script>
var hs = {player: null, ready: false, want: null, error: null, volume: 80, muted: false};
hs.load = function (id, start) {
  hs.error = null;
  if (!hs.ready) { hs.want = [id, start]; return; }
  hs.player.loadVideoById({videoId: id, startSeconds: start});
};
hs.state = function () {
  var p = hs.player, ok = hs.ready && p && p.getCurrentTime;
  return JSON.stringify({t: ok ? p.getCurrentTime() : 0, d: ok ? p.getDuration() : 0,
                         s: ok ? p.getPlayerState() : -1, e: hs.error});
};
function onYouTubeIframeAPIReady() {
  hs.player = new YT.Player('player', {
    width: '100%', height: '100%',
    playerVars: {autoplay: 1, controls: 0, disablekb: 1, fs: 0, rel: 0,
                 playsinline: 1, iv_load_policy: 3, origin: '%ORIGIN%'},
    events: {
      onReady: function () {
        hs.ready = true;
        hs.player.setVolume(hs.volume);
        if (hs.muted) hs.player.mute();
        if (hs.want) { hs.load(hs.want[0], hs.want[1]); hs.want = null; }
      },
      onError: function (e) { hs.error = e.data; }
    }
  });
}
var tag = document.createElement('script');
tag.src = 'https://www.youtube.com/iframe_api';
tag.onerror = function () { hs.error = 'offline'; };
document.head.appendChild(tag);
</script></body></html>""".replace("%ORIGIN%", ORIGIN)


class _FullWindow(QWidget):
    """Black window over the whole screen that holds the video in fullscreen."""
    def __init__(self, owner):
        super().__init__(None, Qt.Window)
        self.owner = owner
        self.setStyleSheet("background: #000;")
        self.setWindowTitle("Haystacks")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.owner._fit(self)

    def closeEvent(self, event):  # Alt+F4 comes back instead of losing the video
        event.ignore()
        self.owner.set_full_screen(False)


class _Overlay(QWidget):
    """Transparent layer over the video that takes mouse and keyboard input."""
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.owner.is_full_screen():
            self.owner.toggle_play.emit()

    def mouseDoubleClickEvent(self, event):
        self.owner.set_full_screen(not self.owner.is_full_screen())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.owner.is_full_screen():
            self.owner.set_full_screen(False)
        elif event.key() == Qt.Key_Space:
            self.owner.toggle_play.emit()
        else:
            super().keyPressEvent(event)


class YouTubeView(QWidget):
    """Same signals the panel uses from QMediaPlayer, in milliseconds."""
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playingChanged = Signal(bool)
    failed = Signal(str)
    toggle_play = Signal()

    def __init__(self, volume=0.8, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: #000;")
        self.web = QWebEngineView(self)
        self.web.setContextMenuPolicy(Qt.NoContextMenu)
        page = QWebEnginePage(self.web)
        page.setBackgroundColor(QColor("#000"))
        self.web.setPage(page)
        self.web.settings().setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
        self.loaded = False
        self.waiting = []  # JavaScript sent before the page was ready
        self.web.loadFinished.connect(self._loaded)
        self.web.setHtml(PAGE, QUrl(ORIGIN + "/"))
        self.overlay = _Overlay(self)
        self.full_window = None

        self.video_id = None
        self.position = 0
        self.duration = 0
        self.playing = False
        self.full = False
        self._pending_seek = None
        self._seek_polls = 0
        self.set_volume(volume)
        self.timer = QTimer(self, interval=POLL_MS, timeout=self._poll)

    # ---- control --------------------------------------------------------------
    def play_at(self, video_id, seconds):
        """Play a video from `seconds`; the same video just seeks."""
        if video_id == self.video_id and self.duration:
            self.seek(int(seconds * 1000))
            self.play()
            return
        self.video_id = video_id
        self.position = int(seconds * 1000)
        self.duration = 0
        self.durationChanged.emit(0)
        self._js(f"hs.load({json.dumps(video_id)}, {float(seconds)})")
        self.timer.start()

    def play(self):
        self._js("hs.ready && hs.player.playVideo()")

    def pause(self):
        self._js("hs.ready && hs.player.pauseVideo()")

    def toggle(self):
        if self.playing:
            self.pause()
        else:
            self.play()

    def seek(self, ms):
        ms = max(0, min(int(ms), self.duration or int(ms)))
        self.position = ms
        self._pending_seek = ms  # polls right after a seek still report the old time
        self._seek_polls = 0
        self.positionChanged.emit(ms)
        self._js(f"hs.ready && hs.player.seekTo({ms / 1000}, true)")

    def set_volume(self, volume):
        """volume: 0.0 to 1.0, like QAudioOutput."""
        v = round(volume * 100)
        self._js(f"hs.volume = {v}; hs.muted = false; "
                 f"if (hs.ready) {{ hs.player.setVolume({v}); hs.player.unMute(); }}")

    def set_muted(self, muted):
        m = "true" if muted else "false"
        self._js(f"hs.muted = {m}; if (hs.ready) {{ {m} ? hs.player.mute() : hs.player.unMute(); }}")

    def stop(self):
        self.pause()
        self.timer.stop()

    def is_full_screen(self):
        return self.full

    def set_full_screen(self, on):
        """Show the video on its own over the whole screen (Esc or double-click
        to come back), like QVideoWidget.setFullScreen."""
        if on == self.full:
            return
        self.full = on
        if on:  # move the video into a window of its own; this widget stays put
            self.full_window = _FullWindow(self)
            self._fit(self.full_window)
            self.full_window.setGeometry(self.screen().geometry())
            self.full_window.showFullScreen()
        else:
            self._fit(self)
            self.full_window.hide()
            self.full_window.deleteLater()
            self.full_window = None
        self.overlay.setFocus()

    # ---- internals ------------------------------------------------------------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.full:
            self._fit(self)

    def _fit(self, host):
        """Put the video and the click layer in `host`, filling it."""
        for w in (self.web, self.overlay):
            if w.parentWidget() is not host:
                w.setParent(host)
            w.setGeometry(host.rect())
            w.show()
        self.overlay.raise_()

    def _loaded(self, ok):
        self.loaded = True
        for code in self.waiting:
            self.web.page().runJavaScript(code, 0)
        self.waiting = []

    def _js(self, code):
        if self.loaded:
            self.web.page().runJavaScript(code, 0)
        else:
            self.waiting.append(code)

    def _poll(self):
        if self.loaded:
            self.web.page().runJavaScript("hs.state()", 0, self._got_state)

    def _got_state(self, raw):
        try:
            st = json.loads(raw)
        except (TypeError, ValueError):
            return
        if st.get("e") is not None:
            self.timer.stop()
            err = st["e"]
            self.failed.emit("Can't reach YouTube. Check the internet connection."
                             if err == "offline" else ERRORS.get(err, f"YouTube error {err}."))
            self._js("hs.error = null")
            return
        duration = int((st.get("d") or 0) * 1000)
        if duration and duration != self.duration:
            self.duration = duration
            self.durationChanged.emit(duration)
        playing = st.get("s") in (1, 3)  # playing or buffering
        if playing != self.playing:
            self.playing = playing
            self.playingChanged.emit(playing)
        pos = int((st.get("t") or 0) * 1000)
        if self._pending_seek is not None:
            self._seek_polls += 1
            if abs(pos - self._pending_seek) > 1500 and self._seek_polls < 8:
                return  # the seek hasn't landed yet
            self._pending_seek = None
        if pos != self.position:
            self.position = pos
            self.positionChanged.emit(pos)
