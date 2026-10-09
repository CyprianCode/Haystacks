"""
The main window Haystacks opens on: search every folder's transcripts,
browse the results in a smooth list, and play any moment in the built-in
player. "Add videos" opens the transcription window.
"""
import os
import queue
import subprocess
import threading
from collections import Counter
from pathlib import Path

from PySide6.QtCore import (QAbstractListModel, QByteArray, QDate, QDateTime, QEvent,
                            QModelIndex, QRect, QSize, Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QFontMetrics, QGuiApplication, QKeySequence, QPainter, QPen, QShortcut, QTextDocument
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QComboBox,
                               QDateEdit, QDateTimeEdit, QDialog, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListView, QListWidget, QMainWindow, QMenu,
                               QMessageBox,
                               QPushButton, QSplitter, QStackedWidget, QStyle,
                               QStyledItemDelegate, QVBoxLayout, QWidget)

import pipeline
import theme
import youtube
from library import Library, hms, highlight_spans, parse_terms
from measure import Measurer
from player import LEAD_IN_S, PlayerPanel
from update_banner import UpdateBanner
from version import __version__

HINTS = {
    "search": 'All words must appear in the same sentence. Put words in "quotes" for an '
              "exact phrase. Click a result to play it; right-click for more.",
    "loud": "The loudest moment of each stretch, ranked across all recordings. 0 dB is "
            "the most the mic can record, so closer to 0 is louder.",
}
SORTS = {"Newest first": "new", "Oldest first": "old", "Loudest first": "loud"}
NO_DATE = QDate(1990, 1, 1)  # shown as "Any"
PAD = 14
TIME_W = 78
DB_W = 64
METER_W = 150


def html_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clip_before(text, n=90):
    return text if len(text) <= n else "…" + text[-n:].split(" ", 1)[-1]


def clip_after(text, n=120):
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


# ---- results list ------------------------------------------------------------
# Rows: ("head", file index, match count), ("hit", sentence index, show file),
#       ("loud", (file index, time, dB, sentence index)).
class ResultsModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.rows = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.UserRole and index.isValid():
            return self.rows[index.row()]
        return None

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()


class ResultsDelegate(QStyledItemDelegate):
    """Paints each row; every row has the same height, so huge lists scroll
    smoothly (only the rows on screen are ever drawn)."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.body = QFont(theme.BODY, 11)
        self.small = QFont(theme.BODY, 9)
        self.bold = QFont(theme.BODY, 11, QFont.Bold)
        self.head = QFont(theme.HEAD, 12)
        self.line = QFontMetrics(self.body).lineSpacing()
        self.height = self.line * 2 + 22

    def sizeHint(self, option, index):
        return QSize(200, self.height)

    def pill_rect(self, rect, row):
        """The Hide recording button's rectangle for rows that have one."""
        if row[0] not in ("head", "loud") and not (row[0] == "hit" and row[2]):
            return None
        w = QFontMetrics(self.small).horizontalAdvance("Hide recording") + 22
        h = QFontMetrics(self.small).height() + 10
        y = rect.top() + (rect.height() - h) // 2 if row[0] == "head" else rect.top() + 8
        return QRect(rect.right() - PAD - w, y, w, h)

    def paint(self, p, option, index):
        row = index.data(Qt.UserRole)
        lib = self.view.lib
        c = theme.C
        r = option.rect
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        hovered = self.view.hover_row == index.row()
        playing = self.view.playing_key is not None and self.view.row_key(row) == self.view.playing_key
        if row[0] != "head" and (hovered or playing or option.state & QStyle.State_Selected):
            p.fillRect(r, QColor(c["hover"]))
        if playing:
            p.fillRect(QRect(r.left(), r.top() + 4, 3, r.height() - 8), QColor(c["accent"]))
        p.setPen(QPen(QColor(c["line"]), 1))
        p.drawLine(r.left() + PAD, r.top(), r.right() - PAD, r.top())

        if row[0] == "head":
            self.paint_head(p, r, row, lib, c)
        elif row[0] == "hit":
            self.paint_hit(p, r, row, lib, c)
        else:
            self.paint_loud(p, r, row, lib, c)
        pill = self.pill_rect(r, row)
        if pill is not None:
            self.paint_pill(p, pill, c, self.view.hover_pill == index.row())
        p.restore()

    def paint_pill(self, p, rect, c, hot):
        p.setPen(QPen(QColor(c["muted"] if hot else c["button_border"]), 1))
        p.setBrush(QColor(c["button_hover"] if hot else c["button"]))
        p.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 4, 4)
        p.setPen(QColor(c["ink"]))
        p.setFont(self.small)
        p.drawText(rect, Qt.AlignCenter, "Hide recording")

    def paint_head(self, p, r, row, lib, c):
        _, fi, count = row
        f = lib.files[fi]
        x = r.left() + PAD
        base = r.top() + r.height() // 2 + QFontMetrics(self.head).ascent() // 2 - 1
        p.setFont(self.head)
        p.setPen(QColor(c["ink"]))
        title = f["label"] or f["name"]
        p.drawText(x, base, title)
        x += QFontMetrics(self.head).horizontalAdvance(title) + 12
        p.setFont(self.small)
        p.setPen(QColor(c["muted"]))
        meta = (f"{f['name']}    " if f["label"] else "") + \
            f"{count} {'match' if count == 1 else 'matches'}"
        p.drawText(x, base, meta)

    def paint_time(self, p, x, y, fi, t, c):
        f = self.view.lib.files[fi]
        has_video = f["video"] is not None or f["youtube"] is not None
        p.setFont(self.bold)
        p.setPen(QColor(c["accent"] if has_video else c["muted"]))
        p.drawText(x, y + QFontMetrics(self.bold).ascent(), hms(t))

    def rich(self, p, x, y, width, height, html):
        doc = QTextDocument()
        doc.setDefaultFont(self.body)
        doc.setDocumentMargin(0)
        doc.setTextWidth(width)
        doc.setHtml(html)
        p.save()
        p.translate(x, y)
        p.setClipRect(0, 0, width, height)
        doc.drawContents(p)
        p.restore()

    def sentence_html(self, i, terms, with_context, c):
        lib = self.view.lib
        text = lib.segs[i][2]
        out, pos = [], 0
        for a, b in highlight_spans(text, terms):
            out.append(html_escape(text[pos:a]))
            out.append(f'<span style="background:{c["accent_dim"]}">{html_escape(text[a:b])}</span>')
            pos = b
        out.append(html_escape(text[pos:]))
        middle = f'<span style="color:{c["ink"]}">{"".join(out)}</span>'
        if not with_context:
            return middle
        before, after = lib.context(i)
        muted = f'color:{c["muted"]}'
        return ((f'<span style="{muted}">{html_escape(clip_before(before))} </span>' if before else "")
                + middle
                + (f'<span style="{muted}"> {html_escape(clip_after(after))}</span>' if after else ""))

    def paint_db(self, p, r, db, c):
        if db is None:
            return
        hot = db > -15
        p.setFont(QFont(theme.BODY, 9, QFont.Bold if hot else QFont.Normal))
        p.setPen(QColor(c["loud"] if hot else c["muted"]))
        p.drawText(QRect(r.right() - PAD - DB_W, r.top() + 10, DB_W, self.line),
                   Qt.AlignRight | Qt.AlignVCenter, f"{round(db)} dB")

    def paint_hit(self, p, r, row, lib, c):
        _, i, with_file = row
        fi, start, _, db = lib.segs[i]
        top = r.top() + 10
        self.paint_time(p, r.left() + PAD, top, fi, start, c)
        x = r.left() + PAD + TIME_W
        if with_file:
            f = lib.files[fi]
            p.setFont(self.small)
            p.setPen(QColor(c["muted"]))
            pill = self.pill_rect(r, row)
            label_w = pill.left() - x - 10
            p.drawText(QRect(x, top, label_w, self.line), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetrics(self.small).elidedText(f["label"] or f["name"],
                                                           Qt.ElideRight, label_w))
            width = r.right() - PAD - x
            self.rich(p, x, top + self.line, width, self.line,
                      self.sentence_html(i, self.view.terms, False, c))
        else:
            width = r.right() - PAD - DB_W - 10 - x
            self.rich(p, x, top, width, self.line * 2,
                      self.sentence_html(i, self.view.terms, True, c))
            self.paint_db(p, r, db, c)

    def paint_loud(self, p, r, row, lib, c):
        fi, t, db, si = row[1]
        f = lib.files[fi]
        top = r.top() + 10
        x = r.left() + PAD
        p.setFont(QFont(theme.BODY, 10, QFont.Bold))
        p.setPen(QColor(c["loud"]))
        p.drawText(QRect(x, top, 56, self.line), Qt.AlignLeft | Qt.AlignVCenter, f"{round(db)} dB")
        filled = max(0, min(10, round((db + 50) / 5)))  # -50 dB empty, 0 dB full
        seg_w, gap = 7, 2
        for k in range(10):
            p.fillRect(QRect(x + 58 + k * (seg_w + gap), top + 4, seg_w, self.line - 8),
                       QColor(c["loud"] if k < filled else c["line"]))
        x += METER_W
        self.paint_time(p, x, top, fi, t, c)
        x += TIME_W
        pill = self.pill_rect(r, row)
        p.setFont(self.small)
        p.setPen(QColor(c["muted"]))
        label_w = pill.left() - x - 10
        p.drawText(QRect(x, top, label_w, self.line), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(self.small).elidedText(f["label"] or f["name"],
                                                       Qt.ElideRight, label_w))
        width = r.right() - PAD - x
        if si >= 0:
            self.rich(p, x, top + self.line, width, self.line,
                      f'<span style="color:{c["ink"]}">{html_escape(lib.segs[si][2])}</span>')
        else:
            p.setFont(QFont(theme.BODY, 11, italic=True))
            p.drawText(QRect(x, top + self.line, width, self.line),
                       Qt.AlignLeft | Qt.AlignVCenter, "No speech transcribed here")


class ResultsView(QListView):
    play = Signal(object)        # a row
    hide_file = Signal(int)      # file index
    menu = Signal(object, object)  # row, global position

    def __init__(self):
        super().__init__()
        self.setObjectName("results")
        self.lib = None
        self.terms = []
        self.hover_row = -1
        self.hover_pill = -1
        self.playing_key = None
        self.model_ = ResultsModel()
        self.setModel(self.model_)
        self.delegate = ResultsDelegate(self)
        self.setItemDelegate(self.delegate)
        self.setUniformItemSizes(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    @staticmethod
    def row_key(row):
        if row[0] == "hit":
            return ("seg", row[1])
        if row[0] == "loud":
            return ("moment", row[1][0], row[1][1])
        return None

    def set_rows(self, rows):
        self.hover_row = self.hover_pill = -1
        self.model_.set_rows(rows)
        self.scrollToTop()

    def row_at(self, pos):
        idx = self.indexAt(pos)
        return (idx, self.model_.rows[idx.row()]) if idx.isValid() else (idx, None)

    def mouseMoveEvent(self, event):
        idx, row = self.row_at(event.position().toPoint())
        hover = idx.row() if row else -1
        pill = self.delegate.pill_rect(self.visualRect(idx), row) if row else None
        hover_pill = hover if pill is not None and pill.contains(event.position().toPoint()) else -1
        if (hover, hover_pill) != (self.hover_row, self.hover_pill):
            self.hover_row, self.hover_pill = hover, hover_pill
            self.viewport().update()
        clickable = row is not None and (row[0] != "head" or hover_pill >= 0)
        self.viewport().setCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.hover_row = self.hover_pill = -1
        self.viewport().update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() != Qt.LeftButton:
            return
        idx, row = self.row_at(event.position().toPoint())
        if not row:
            return
        pill = self.delegate.pill_rect(self.visualRect(idx), row)
        if pill is not None and pill.contains(event.position().toPoint()):
            self.hide_file.emit(self.file_of(row))
        elif row[0] != "head":
            self.play.emit(row)

    def keyPressEvent(self, event):
        idx = self.currentIndex()
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and idx.isValid():
            row = self.model_.rows[idx.row()]
            if row[0] != "head":
                self.play.emit(row)
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        idx, row = self.row_at(event.pos())
        if row:
            self.menu.emit(row, event.globalPos())

    def file_of(self, row):
        if row[0] == "head":
            return row[1]
        if row[0] == "hit":
            return self.lib.segs[row[1]][0]
        return row[1][0]


# ---- main window -------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self, settings, save, make_transcriber):
        super().__init__()
        self.settings = settings
        self.save = save
        self.make_transcriber = make_transcriber
        self.tw = None             # the Add videos window, made on first use
        self.lib = None
        self.tab = "search"
        self.hidden = set(settings.setdefault("hidden", []))
        self.loads = queue.Queue()
        self.load_gen = 0
        self.keep_view = False
        self.folder_paths = []
        # Dates and loudness for imported transcripts, measured in the background.
        self.measurer = Measurer(settings, busy=lambda: bool(self.tw and self.tw.running))
        QTimer.singleShot(4000, self.measurer.start)  # after the first load settles

        self.setWindowTitle(f"Haystacks {__version__}")
        self.force_quit = False  # set when an update closes the app
        self.setMinimumSize(980, 640)
        central = QWidget()
        central.setObjectName("window")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(16)

        # Header, laid out like the Add videos window's.
        head = QHBoxLayout()
        title = QLabel("Haystacks")
        title.setObjectName("title")
        sub = QLabel("Search what was said in your videos")
        sub.setObjectName("subtitle")
        head.addWidget(title)
        head.addSpacing(12)
        head.addWidget(sub)
        head.addStretch(1)
        self.run_label = QLabel("")
        self.run_label.setObjectName("runStatus")
        head.addWidget(self.run_label)
        head.addSpacing(12)
        add = QPushButton("Add videos")
        add.setProperty("accent", True)
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(self.open_transcriber)
        head.addWidget(add)
        outer.addLayout(head)

        # "New version available", hidden until an update check finds one.
        self.banner = UpdateBanner(settings, save,
                                   busy=lambda: bool(self.tw and self.tw.running),
                                   quit_app=self.quit_for_update)
        outer.addWidget(self.banner)

        # Search controls
        sp = theme.panel()
        sl = QVBoxLayout(sp)
        sl.setContentsMargins(18, 16, 18, 14)
        sl.setSpacing(10)
        self.query = QLineEdit()
        self.query.setObjectName("search")
        self.query.setPlaceholderText("Search what was said...")
        self.query.setClearButtonEnabled(True)
        sl.addWidget(self.query)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.tab_group = QButtonGroup(self)
        self.tab_btns = {}
        for key, text in (("search", "Search"), ("loud", "Loudest moments")):
            b = QPushButton(text)
            b.setProperty("tab", True)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _, k=key: self.set_tab(k))
            self.tab_group.addButton(b)
            self.tab_btns[key] = b
            row.addWidget(b)
        row.addSpacing(16)
        row.addWidget(QLabel("Folder"))
        self.folder_box = QComboBox()
        self.folder_box.setMinimumWidth(200)
        self.folder_box.activated.connect(lambda _: self.run())
        row.addWidget(self.folder_box)
        row.addSpacing(10)
        row.addWidget(QLabel("From"))
        self.date_from = self.date_edit()
        row.addWidget(self.date_from)
        row.addWidget(QLabel("To"))
        self.date_to = self.date_edit()
        row.addWidget(self.date_to)
        clear = QPushButton("Clear dates")
        clear.setCursor(Qt.PointingHandCursor)
        clear.clicked.connect(self.clear_dates)
        row.addWidget(clear)
        row.addSpacing(10)
        self.sort_label = QLabel("Sort")
        self.sort_box = QComboBox()
        self.sort_box.addItems(list(SORTS))
        self.sort_box.activated.connect(lambda _: self.run())
        row.addWidget(self.sort_label)
        row.addWidget(self.sort_box)
        row.addStretch(1)
        self.hidden_btn = QPushButton()
        self.hidden_btn.setCursor(Qt.PointingHandCursor)
        self.hidden_btn.clicked.connect(self.show_hidden)
        row.addWidget(self.hidden_btn)
        sl.addLayout(row)

        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        self.status = QLabel("Loading transcripts...")
        sl.addWidget(self.hint)
        sl.addWidget(self.status)
        outer.addWidget(sp)

        # Results | player
        self.split = QSplitter(Qt.Horizontal)
        self.split.setHandleWidth(16)
        rp = theme.panel()
        rl = QVBoxLayout(rp)
        rl.setContentsMargins(2, 6, 2, 6)
        self.results_stack = QStackedWidget()
        self.results = ResultsView()
        self.results.play.connect(self.play_row)
        self.results.hide_file.connect(self.hide)
        self.results.menu.connect(self.row_menu)
        self.empty = QLabel("")
        self.empty.setObjectName("empty")
        self.empty.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.empty.setWordWrap(True)
        self.empty.setContentsMargins(30, 40, 30, 30)
        self.results_stack.addWidget(self.results)
        self.results_stack.addWidget(self.empty)
        rl.addWidget(self.results_stack)
        self.player = PlayerPanel(settings, save)
        self.split.addWidget(rp)
        self.split.addWidget(self.player)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)
        outer.addWidget(self.split, 1)

        self.debounce = QTimer(self, singleShot=True, interval=200, timeout=self.run)
        self.query.textChanged.connect(lambda _: self.debounce.start())
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: (
            self.query.setFocus(), self.query.selectAll()))
        QShortcut(QKeySequence(Qt.Key_Escape), self.query, activated=self.query.clear)
        self.poller = QTimer(self, interval=300, timeout=self.poll)
        self.poller.start()

        self.restore_layout()
        self.set_tab("search", run=False)
        self.update_hidden_btn()
        self.reload()

    # ---- widgets ---------------------------------------------------------------
    def date_edit(self):
        d = QDateEdit()
        d.setCalendarPopup(True)
        d.setDisplayFormat("yyyy-MM-dd")
        d.setMinimumDate(NO_DATE)
        d.setSpecialValueText("Any")
        d.setDate(NO_DATE)
        d.setFixedWidth(118)
        d.dateChanged.connect(lambda _: self.run())
        cal = d.calendarWidget()
        cal.installEventFilter(self)  # open the calendar on this month, not 1990
        d._cal = cal
        return d

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show:
            for d in (self.date_from, self.date_to):
                if obj is d._cal and d.date() == NO_DATE:
                    today = QDate.currentDate()
                    obj.setCurrentPage(today.year(), today.month())
        return False

    @staticmethod
    def date_value(d):
        return None if d.date() == NO_DATE else d.date().toString(Qt.ISODate)

    def showEvent(self, event):
        super().showEvent(event)
        theme.style_window(self)
        self.query.setFocus()

    # ---- loading ---------------------------------------------------------------
    def on_library_change(self):
        """Folders or transcripts changed in the Add videos window."""
        self.reload()
        self.measurer.start()

    def reload(self, background=False):
        """Re-read every folder's transcripts in a thread. A background reload
        (new loudness data) keeps the results list where it is."""
        self.load_gen += 1
        gen = self.load_gen
        self.keep_view = background
        entries = [dict(f) for f in self.settings.get("folders", [])]
        folders = [f["path"] for f in entries]
        current = self.folder_box.currentData()
        self.folder_paths = folders
        self.folder_box.clear()
        self.folder_box.addItem("All folders", None)
        for p in folders:
            self.folder_box.addItem(theme.short_path(p, 40), p)
        i = self.folder_box.findData(current)
        self.folder_box.setCurrentIndex(max(0, i))

        def work():
            try:
                self.loads.put((gen, Library(entries), None))
            except Exception as e:
                self.loads.put((gen, None, e))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            while True:
                gen, lib, err = self.loads.get_nowait()
                if gen != self.load_gen:
                    continue  # an older load finished after a newer one started
                if err:
                    self.status.setText(f"Could not read the transcripts: {err}")
                else:
                    self.lib = self.results.lib = lib
                    self.results.playing_key = None
                    scroll = self.results.verticalScrollBar().value()
                    self.run()
                    if self.keep_view:
                        self.results.verticalScrollBar().setValue(scroll)
        except queue.Empty:
            pass
        if self.measurer.changed:
            self.measurer.changed = False
            self.reload(background=True)
        summary = (self.tw.summary() if self.tw else "") or self.measurer.summary()
        if self.run_label.text() != summary:
            self.run_label.setText(summary)

    # ---- searching -------------------------------------------------------------
    def set_tab(self, tab, run=True):
        self.tab = tab
        self.tab_btns[tab].setChecked(True)
        is_search = tab == "search"
        self.query.setEnabled(is_search)
        self.sort_label.setVisible(is_search)
        self.sort_box.setVisible(is_search)
        self.hint.setText(HINTS[tab])
        if run:
            self.run()
        if is_search:
            self.query.setFocus()

    def clear_dates(self):
        for d in (self.date_from, self.date_to):
            d.blockSignals(True)
            d.setDate(NO_DATE)
            d.blockSignals(False)
        self.run()

    def show_message(self, text):
        self.empty.setText(text)
        self.results_stack.setCurrentWidget(self.empty)

    def show_rows(self, rows):
        self.results.set_rows(rows)
        self.results_stack.setCurrentWidget(self.results)

    def run(self):
        lib = self.lib
        if lib is None:
            return
        if not lib.files:
            self.status.setText("")
            if not self.settings.get("folders"):
                return self.show_message("Nothing to search yet.\nClick Add videos to choose "
                                         "a folder of videos and transcribe it.")
            if self.tw and self.tw.running:
                return self.show_message("Transcription is running.\nVideos show up here "
                                         "when the run finishes.")
            return self.show_message("No transcripts yet.\nClick Add videos, pick a folder "
                                     "and press Transcribe.")
        ok = lib.allowed(self.hidden, self.folder_box.currentData(),
                         self.date_value(self.date_from), self.date_value(self.date_to))
        if self.tab == "search":
            self.run_search(lib, ok)
        else:
            self.run_loud(lib, ok)

    def run_search(self, lib, ok):
        terms = parse_terms(self.query.text())
        self.results.terms = terms
        if not terms or len("".join(terms)) < 2:
            self.status.setText(f"{len(ok)} of {len(lib.files)} recordings in range, "
                                f"{len(lib.segs):,} sentences to search.")
            return self.show_message("Type above to search what was said.")
        sort = SORTS[self.sort_box.currentText()]
        hits = lib.search(terms, ok, sort)
        if not hits:
            self.status.setText("No matches.")
            return self.show_message("No matches. Try fewer words, check the spelling, "
                                     "or widen the dates.")
        per_file = Counter(lib.segs[i][0] for i in hits)
        n = len(per_file)
        self.status.setText(f"{len(hits):,} match{'es' if len(hits) != 1 else ''} in {n} "
                            f"recording{'s' if n != 1 else ''}.")
        if sort == "loud":
            rows = [("hit", i, True) for i in hits]
        else:
            rows, last = [], None
            for i in hits:
                fi = lib.segs[i][0]
                if fi != last:
                    rows.append(("head", fi, per_file[fi]))
                    last = fi
                rows.append(("hit", i, False))
        self.show_rows(rows)

    def run_loud(self, lib, ok):
        if not lib.moments:
            self.status.setText("No loudness data for these recordings.")
            return self.show_message("No loudness data yet. It is measured while videos "
                                     "are transcribed.")
        moments = lib.loudest(ok)
        if not moments:
            self.status.setText("")
            return self.show_message("No recordings in this range, or all of them are hidden.")
        self.status.setText(f"{len(moments):,} loud moments in range.")
        self.show_rows([("loud", m) for m in moments])

    # ---- actions ---------------------------------------------------------------
    def row_target(self, row):
        """(file index, time) a row points at."""
        if row[0] == "hit":
            fi, start, _, _ = self.lib.segs[row[1]]
            return fi, start
        if row[0] == "loud":
            return row[1][0], row[1][1]
        return row[1], 0

    def track_for(self, folder):
        for f in self.settings.get("folders", []):
            if f["path"] == folder:
                return int(f.get("track", 1))
        return 1

    def play_row(self, row):
        fi, t = self.row_target(row)
        f = self.lib.files[fi]
        local = f["video"] is not None and f["video"].exists()
        if f["youtube"] and not local:  # a recording on disk plays from disk
            self.results.playing_key = self.results.row_key(row)
            self.results.viewport().update()
            self.player.play_youtube(f["youtube"], t, f["label"] or f["name"],
                                     f"{f['name']}  at {hms(t)}")
            return
        if not local:
            QMessageBox.information(self, "Play video", f"Can't find the video for "
                                    f"{f['stem']}. It may have been moved or renamed.")
            return
        self.results.playing_key = self.results.row_key(row)
        self.results.viewport().update()
        title = f["label"] or f["name"]
        subtitle = f"{f['video'].name}  at {hms(t)}"
        self.player.play_at(f["video"], t, title, subtitle, self.track_for(f["folder"]))

    def row_menu(self, row, pos):
        fi, t = self.row_target(row)
        f = self.lib.files[fi]
        menu = QMenu(self)
        if row[0] != "head":
            menu.addAction("Play here", lambda: self.play_row(row))
            if f["video"]:
                menu.addAction("Open in another player", lambda: self.play_external(fi, t))
            if f["youtube"]:
                menu.addAction("Open on YouTube", lambda: self.open_youtube(fi, t))
                menu.addAction("Copy YouTube link to this moment",
                               lambda: QGuiApplication.clipboard().setText(
                                   youtube.watch_url(f["youtube"], max(0, t - LEAD_IN_S))))
        if f["video"]:
            menu.addAction("Show video in its folder", lambda: subprocess.Popen(
                ["explorer", "/select,", str(f["video"])]))
        if row[0] == "hit":
            menu.addAction("Copy sentence", lambda: QGuiApplication.clipboard().setText(
                self.lib.segs[row[1]][2]))
        entry = self.entry_for(f["folder"])
        if entry is not None and not entry.get("youtube"):  # YouTube subtitles are linked already
            linked = f["stem"] in entry.get("links", {})
            menu.addSeparator()
            menu.addAction("Change YouTube link..." if linked else "Link to YouTube video...",
                           lambda: self.link_youtube(fi))
            if linked:
                menu.addAction("Remove YouTube link", lambda: self.set_link(fi, None))
        menu.addSeparator()
        if entry is not None:
            menu.addAction("Change recording date...", lambda: self.change_date(fi))
            if f["stem"] in entry.get("dates", {}):
                menu.addAction("Use the video's own date", lambda: self.set_date(fi, None))
        menu.addAction("Hide recording", lambda: self.hide(fi))
        menu.exec(pos)

    def entry_for(self, folder):
        return next((e for e in self.settings.get("folders", []) if e["path"] == folder), None)

    def link_youtube(self, fi):
        """Ask for the YouTube video a recording's transcript belongs to."""
        f = self.lib.files[fi]
        current = youtube.watch_url(f["youtube"]) if f["youtube"] else ""
        while True:
            text, ok = QInputDialog.getText(
                self, "Link to YouTube video",
                f"Link to the video on YouTube for\n{f['label'] or f['name']}:\n\n"
                "Results from this recording will play it when the video file isn't "
                "on this PC. Its timings must match the transcript.",
                QLineEdit.Normal, current)
            if not ok or not text.strip():
                return
            vid = youtube.parse_url(text)
            if vid:
                return self.set_link(fi, vid)
            QMessageBox.information(self, "Link to YouTube video",
                                    "That doesn't look like a YouTube video link. It should "
                                    "look like https://www.youtube.com/watch?v=... or "
                                    "https://youtu.be/...")
            current = text

    def set_link(self, fi, vid):
        """Store (or with None, remove) a recording's YouTube link."""
        f = self.lib.files[fi]
        entry = self.entry_for(f["folder"])
        if entry is None:
            return
        links = entry.setdefault("links", {})
        if vid:
            links[f["stem"]] = vid
        else:
            links.pop(f["stem"], None)
        self.save()
        self.reload(background=True)

    def change_date(self, fi):
        """Ask for a recording's date, for videos whose camera clock was wrong."""
        f = self.lib.files[fi]
        dlg = QDialog(self)
        dlg.setWindowTitle("Change recording date")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18, 16, 18, 16)
        note = QLabel(f"When was {f['name']} recorded?\n\nThis date is used for sorting "
                      "and date filters instead of the one stored in the video.")
        note.setWordWrap(True)
        lay.addWidget(note)
        edit = QDateTimeEdit(f["when"] or QDateTime.currentDateTime())
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("yyyy-MM-dd  HH:mm")
        lay.addWidget(edit)
        btns = QHBoxLayout()
        btns.addStretch(1)
        ok = QPushButton("Change date")
        ok.setProperty("accent", True)
        ok.clicked.connect(dlg.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(dlg.reject)
        for b in (ok, cancel):
            btns.addWidget(b)
        lay.addLayout(btns)
        theme.style_window(dlg)
        if dlg.exec():
            when = edit.dateTime().toPython().replace(second=0, microsecond=0)
            self.set_date(fi, when.isoformat(timespec="seconds"))

    def set_date(self, fi, when):
        """Store (or with None, remove) the date a recording was given by hand."""
        f = self.lib.files[fi]
        entry = self.entry_for(f["folder"])
        if entry is None:
            return
        dates = entry.setdefault("dates", {})
        if when:
            dates[f["stem"]] = when
        else:
            dates.pop(f["stem"], None)
        self.save()
        self.reload(background=True)

    def open_youtube(self, fi, t):
        self.player.pause()
        vid = self.lib.files[fi]["youtube"]
        QDesktopServices.openUrl(QUrl(youtube.watch_url(vid, max(0, t - LEAD_IN_S))))

    def play_external(self, fi, t):
        f = self.lib.files[fi]
        video = f["video"]
        if not video or not video.exists():
            if f["youtube"]:
                self.open_youtube(fi, t)
            return
        self.player.pause()
        kind = pipeline.play_video(video, t)
        if kind == "default":
            self.status.setText("Opened the video from the start in your default player.")

    def hide(self, fi):
        self.hidden.add(self.lib.files[fi]["key"])
        self.store_hidden()

    def store_hidden(self):
        self.settings["hidden"] = sorted(self.hidden)
        self.save()
        self.update_hidden_btn()
        self.run()

    def update_hidden_btn(self):
        n = len(self.hidden)
        self.hidden_btn.setText(f"{n} hidden recording{'s' if n != 1 else ''}")
        self.hidden_btn.setVisible(bool(n))

    def show_hidden(self):
        """A small window listing hidden recordings, to show them again."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Hidden recordings")
        dlg.setMinimumWidth(620)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(18, 16, 18, 16)
        head = QLabel("Hidden recordings")
        head.setObjectName("section")
        lay.addWidget(head)
        note = QLabel("These are left out of search results and loudest moments.")
        note.setObjectName("muted")
        lay.addWidget(note)
        keys = sorted(self.hidden)
        by_key = {f["key"]: f for f in (self.lib.files if self.lib else [])}
        lst = QListWidget()
        lst.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for k in keys:
            folder, stem = k.rsplit("|", 1)
            f = by_key.get(k)
            when = f"{f['label']}    " if f and f["label"] else ""
            lst.addItem(f"{when}{stem}    ({theme.short_path(folder, 40)})")
        lay.addWidget(lst)
        btns = QHBoxLayout()
        btns.addStretch(1)

        def show_again(which):
            for k in which:
                self.hidden.discard(k)
            self.store_hidden()
            dlg.accept()

        sel = QPushButton("Show selected again")
        sel.setProperty("accent", True)
        sel.clicked.connect(lambda: show_again([keys[i.row()] for i in lst.selectedIndexes()]))
        everything = QPushButton("Show all again")
        everything.clicked.connect(lambda: show_again(keys))
        close = QPushButton("Close")
        close.clicked.connect(dlg.reject)
        for b in (sel, everything, close):
            btns.addWidget(b)
        lay.addLayout(btns)
        theme.style_window(dlg)
        dlg.exec()

    def open_transcriber(self):
        if self.tw is None:
            self.tw = self.make_transcriber(self.on_library_change)
        self.tw.bring_up()

    # ---- window state ----------------------------------------------------------
    def restore_layout(self):
        ui = self.settings.get("ui", {})
        try:
            if ui.get("geometry"):
                self.restoreGeometry(QByteArray.fromBase64(ui["geometry"].encode()))
            else:
                screen = QGuiApplication.primaryScreen().availableGeometry()
                self.resize(min(1400, screen.width() - 80), min(900, screen.height() - 80))
            if ui.get("split"):
                self.split.restoreState(QByteArray.fromBase64(ui["split"].encode()))
        except Exception:
            pass

    def quit_for_update(self):
        self.force_quit = True
        self.close()

    def closeEvent(self, event):
        if not self.force_quit and self.tw and self.tw.running and QMessageBox.question(
                self, "Transcription running",
                "Videos are still being transcribed. Quit anyway?\n\n"
                "The video in progress will be redone next time.") != QMessageBox.Yes:
            event.ignore()
            return
        self.player.stop()
        self.measurer.stop()
        self.banner.stop()
        self.settings["ui"] = {
            "geometry": bytes(self.saveGeometry().toBase64()).decode(),
            "split": bytes(self.split.saveState().toBase64()).decode()}
        self.save()
        event.accept()
        QApplication.quit()
