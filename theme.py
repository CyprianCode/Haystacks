"""
The Haystacks look (Qt), shared by every window so they all match:
graphite panels like an editing suite, amber deck-style readouts, and buttons
with their own raised fill and a visible edge. Follows the Windows light/dark
app setting.
"""
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QStyleFactory

PALETTES = {
    "dark": {"bg": "#1A1C20", "panel": "#22252A", "line": "#30343A", "ink": "#E4E6E9",
             "muted": "#8A919B", "accent": "#F0AE3C", "accent_hover": "#F5BF5E",
             "on_accent": "#1A1C20", "accent_dim": "#4A3A1C", "field": "#1A1C20",
             "hover": "#2B2F35", "loud": "#FF7A4D", "video": "#000000",
             # Buttons get their own raised fill and a visible edge, distinct
             # from both the panels and the window background.
             "button": "#363A41", "button_hover": "#41464E", "button_pressed": "#2C2F35",
             "button_border": "#50565F", "button_off": "#2A2D33",
             "button_off_border": "#3A3F46", "button_off_ink": "#6B717A"},
    "light": {"bg": "#ECEEF1", "panel": "#FFFFFF", "line": "#D3D8DE", "ink": "#1B1E22",
              "muted": "#5D646E", "accent": "#A15F00", "accent_hover": "#8A5100",
              "on_accent": "#FFFFFF", "accent_dim": "#F5E2C0", "field": "#FFFFFF",
              "hover": "#F3F4F6", "loud": "#B4381A", "video": "#101114",
              "button": "#E7EAEE", "button_hover": "#DADFE5", "button_pressed": "#CDD3DA",
              "button_border": "#9EA6B0", "button_off": "#F1F3F5",
              "button_off_border": "#D3D8DE", "button_off_ink": "#A2A9B1"},
}
HEAD = BODY = READ = MONO = "Segoe UI"  # replaced in apply() by what is installed
C = PALETTES["light"]  # the palette in use, set by apply()
DARK = False


def windows_dark():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def style_window(widget):
    """Dark title bar for a top-level window in dark mode (Windows 10/11)."""
    if not DARK:
        return
    try:
        import ctypes
        on = ctypes.c_int(1)
        hwnd = int(widget.winId())
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, newer then older builds
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(on), ctypes.sizeof(on)) == 0:
                break
    except Exception:
        pass


def apply(app: QApplication):
    """Set fonts, palette and stylesheet for the whole app. Returns the palette."""
    global HEAD, BODY, READ, MONO, C, DARK
    families = set(QFontDatabase.families())
    pick = lambda *names: next((n for n in names if n in families), "Segoe UI")
    HEAD = pick("Bahnschrift SemiBold", "Segoe UI Semibold")
    READ = pick("Bahnschrift Light", "Segoe UI Light")
    BODY = pick("Segoe UI")
    MONO = pick("Cascadia Mono", "Consolas")
    DARK = windows_dark()
    C = c = PALETTES["dark" if DARK else "light"]

    app.setStyle(QStyleFactory.create("Fusion"))
    app.setFont(QFont(BODY, 10))
    pal = QPalette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.Base, "field"),
                      (QPalette.AlternateBase, "panel"), (QPalette.Text, "ink"),
                      (QPalette.WindowText, "ink"), (QPalette.Button, "button"),
                      (QPalette.ButtonText, "ink"), (QPalette.Highlight, "accent_dim"),
                      (QPalette.HighlightedText, "ink"), (QPalette.ToolTipBase, "panel"),
                      (QPalette.ToolTipText, "ink"), (QPalette.PlaceholderText, "muted"),
                      (QPalette.Mid, "line"), (QPalette.Dark, "line"),
                      (QPalette.Link, "accent")):
        pal.setColor(role, QColor(c[key]))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(c["button_off_ink"]))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(c["button_off_ink"]))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(c["button_off_ink"]))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(c))
    return c


def stylesheet(c):
    return f"""
QWidget {{ color: {c['ink']}; font-family: "{BODY}"; font-size: 10pt; }}
QMainWindow, QDialog, QWidget#window {{ background: {c['bg']}; }}
QFrame#panel {{ background: {c['panel']}; border: 1px solid {c['line']}; border-radius: 6px; }}
QFrame#panel QLabel, QFrame#panel QFrame#row {{ background: transparent; }}
QFrame#banner {{ background: {c['accent_dim']}; border: 1px solid {c['accent']}; border-radius: 6px; }}
QFrame#banner QLabel {{ background: transparent; }}
QLabel#title {{ font-family: "{HEAD}"; font-size: 20pt; }}
QLabel#subtitle {{ color: {c['muted']}; padding-top: 8px; }}
QLabel#section {{ font-family: "{HEAD}"; font-size: 13pt; }}
QLabel#muted, QLabel#runStatus {{ color: {c['muted']}; }}
QLabel#readout {{ color: {c['accent']}; font-family: "{READ}"; font-size: 28pt; }}
QLabel#caption {{ color: {c['muted']}; font-size: 9pt; }}
QLabel#empty {{ color: {c['muted']}; font-size: 12pt; }}
QLabel#playerTitle {{ font-family: "{HEAD}"; font-size: 12pt; }}
QLabel#time {{ color: {c['muted']}; font-family: "{READ}"; font-size: 11pt; }}

QPushButton {{ background: {c['button']}; border: 1px solid {c['button_border']};
  border-radius: 4px; padding: 6px 14px; color: {c['ink']}; }}
QPushButton:hover {{ background: {c['button_hover']}; border-color: {c['muted']}; }}
QPushButton:pressed {{ background: {c['button_pressed']}; }}
QPushButton:disabled {{ background: {c['button_off']}; border-color: {c['button_off_border']};
  color: {c['button_off_ink']}; }}
QPushButton:focus {{ outline: none; border-color: {c['accent']}; }}
QPushButton[accent="true"] {{ background: {c['accent']}; border-color: {c['accent']};
  color: {c['on_accent']}; font-weight: bold; }}
QPushButton[accent="true"]:hover {{ background: {c['accent_hover']}; border-color: {c['accent_hover']}; }}
QPushButton[accent="true"]:disabled {{ background: {c['button_off']};
  border-color: {c['button_off_border']}; color: {c['button_off_ink']}; }}
QPushButton[tab="true"]:checked {{ background: {c['accent_dim']}; border-color: {c['accent']}; }}
QPushButton[icon="true"] {{ padding: 5px 8px; }}

QLineEdit, QComboBox, QDateEdit {{ background: {c['field']}; border: 1px solid {c['button_border']};
  border-radius: 4px; padding: 5px 8px; selection-background-color: {c['accent_dim']};
  selection-color: {c['ink']}; }}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus {{ border-color: {c['accent']}; }}
QLineEdit:disabled, QComboBox:disabled, QDateEdit:disabled {{ background: {c['panel']};
  color: {c['button_off_ink']}; border-color: {c['button_off_border']}; }}
QLineEdit#search {{ font-size: 15pt; padding: 8px 12px; }}
QComboBox QAbstractItemView {{ background: {c['field']}; border: 1px solid {c['line']};
  selection-background-color: {c['accent_dim']}; selection-color: {c['ink']}; outline: none; }}

QRadioButton {{ spacing: 8px; padding: 3px 0; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px;
  border: 1px solid {c['muted']}; background: {c['field']}; }}
QRadioButton::indicator:hover {{ border-color: {c['accent']}; }}
QRadioButton::indicator:checked {{ border-color: {c['accent']};
  background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
    stop:0 {c['accent']}, stop:0.5 {c['accent']}, stop:0.62 {c['field']}, stop:1 {c['field']}); }}
QRadioButton::indicator:disabled {{ border-color: {c['button_off_border']}; background: {c['button_off']}; }}
QRadioButton::indicator:checked:disabled {{ border-color: {c['button_off_border']};
  background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
    stop:0 {c['button_off_ink']}, stop:0.5 {c['button_off_ink']}, stop:0.62 {c['button_off']},
    stop:1 {c['button_off']}); }}
QRadioButton:disabled {{ color: {c['button_off_ink']}; }}

QTreeWidget, QListWidget {{ background: {c['field']}; border: 1px solid {c['line']};
  border-radius: 4px; outline: none; }}
QTreeWidget::item, QListWidget::item {{ padding: 6px 4px; }}
QTreeWidget::item:selected, QListWidget::item:selected {{ background: {c['accent_dim']};
  color: {c['ink']}; }}
QHeaderView::section {{ background: {c['panel']}; color: {c['muted']}; border: none;
  border-bottom: 1px solid {c['line']}; padding: 6px 8px; font-size: 9pt; }}
QListView#results {{ background: {c['panel']}; border: none; outline: none; }}
QPlainTextEdit#log {{ background: {c['field']}; color: {c['muted']}; border: 1px solid {c['line']};
  border-radius: 4px; font-family: "{MONO}"; font-size: 9pt; padding: 6px; }}

QProgressBar {{ background: {c['line']}; border: none; border-radius: 4px; max-height: 8px;
  min-height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}

QSlider::groove:horizontal {{ height: 4px; background: {c['line']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {c['accent']}; width: 14px; height: 14px;
  margin: -5px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: {c['accent_hover']}; }}

QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {c['button_border']}; border-radius: 4px;
  min-height: 32px; margin: 2px 3px; }}
QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {c['button_border']}; border-radius: 4px;
  min-width: 32px; margin: 3px 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: {c['bg']}; }}
QMenu {{ background: {c['panel']}; border: 1px solid {c['line']}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 3px; }}
QMenu::item:selected {{ background: {c['accent_dim']}; }}
QMenu::separator {{ height: 1px; background: {c['line']}; margin: 4px 6px; }}
QToolTip {{ background: {c['panel']}; color: {c['ink']}; border: 1px solid {c['line']}; padding: 4px; }}
QMessageBox {{ background: {c['bg']}; }}
"""


def panel(parent=None):
    """A raised panel: panel fill with a 1 px line around it."""
    frame = QFrame(parent)
    frame.setObjectName("panel")
    return frame


def short_path(path, limit=64):
    """Shorten a long path in the middle, keeping the drive and the last two
    folders, which are the parts that tell folders apart."""
    s = str(path)
    if len(s) <= limit:
        return s
    parts = Path(s).parts
    out = parts[0].rstrip("\\") + "\\…\\" + "\\".join(parts[-2:])
    return out if len(out) <= limit else "…" + s[-(limit - 1):]


def icon(kind, color=None, size=18):
    """Small drawn icons for the player, crisp at any display scaling."""
    color = QColor(color or C["ink"])
    scale = 3
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(scale, scale)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    s = size

    def tri(x0, x1):
        path = QPainterPath(QPointF(x0, s * 0.2))
        path.lineTo(x1, s * 0.5)
        path.lineTo(x0, s * 0.8)
        path.closeSubpath()
        p.drawPath(path)

    if kind == "play":
        tri(s * 0.3, s * 0.8)
    elif kind == "pause":
        p.drawRoundedRect(QRectF(s * 0.25, s * 0.2, s * 0.17, s * 0.6), 1, 1)
        p.drawRoundedRect(QRectF(s * 0.58, s * 0.2, s * 0.17, s * 0.6), 1, 1)
    elif kind in ("back", "forward"):
        if kind == "forward":
            tri(s * 0.15, s * 0.5)
            tri(s * 0.5, s * 0.85)
        else:
            p.translate(s, 0)
            p.scale(-1, 1)
            tri(s * 0.15, s * 0.5)
            tri(s * 0.5, s * 0.85)
    elif kind in ("volume", "mute"):
        path = QPainterPath(QPointF(s * 0.12, s * 0.38))
        for x, y in ((0.3, 0.38), (0.55, 0.15), (0.55, 0.85), (0.3, 0.62), (0.12, 0.62)):
            path.lineTo(s * x, s * y)
        path.closeSubpath()
        p.drawPath(path)
        pen = QPen(color, s * 0.08, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if kind == "volume":
            p.drawArc(QRectF(s * 0.45, s * 0.3, s * 0.3, s * 0.4), -60 * 16, 120 * 16)
            p.drawArc(QRectF(s * 0.45, s * 0.15, s * 0.45, s * 0.7), -60 * 16, 120 * 16)
        else:
            p.drawLine(QPointF(s * 0.66, s * 0.36), QPointF(s * 0.9, s * 0.64))
            p.drawLine(QPointF(s * 0.9, s * 0.36), QPointF(s * 0.66, s * 0.64))
    elif kind == "fullscreen":
        pen = QPen(color, s * 0.09, Qt.SolidLine, Qt.SquareCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        a, b, k = s * 0.18, s * 0.82, s * 0.22
        for x, y, dx, dy in ((a, a, 1, 1), (b, a, -1, 1), (a, b, 1, -1), (b, b, -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + dx * k, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + dy * k))
    p.end()
    pm.setDevicePixelRatio(scale)
    return QIcon(pm)
