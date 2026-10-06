"""
Draws the Haystacks icon (an amber haystack and a needle on a graphite tile)
and writes assets/haystacks.ico with the sizes Windows uses. Run it again
after changing the drawing:  .venv\\Scripts\\python.exe assets\\make_icon.py
"""
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
OUT = Path(__file__).with_name("haystacks.ico")


def draw(size):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 256, size / 256)

    # Graphite tile
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#22252A"))
    p.drawRoundedRect(QRectF(8, 8, 240, 240), 48, 48)

    # Haystack: a rounded mound with a few straw lines
    mound = QPainterPath(QPointF(40, 206))
    mound.cubicTo(44, 120, 92, 66, 128, 64)
    mound.cubicTo(164, 66, 212, 120, 216, 206)
    mound.closeSubpath()
    grad = QLinearGradient(0, 64, 0, 206)
    grad.setColorAt(0, QColor("#F5BF5E"))
    grad.setColorAt(1, QColor("#D9922A"))
    p.setBrush(grad)
    p.drawPath(mound)
    if size >= 32:  # straw lines only where they stay crisp
        p.setPen(QPen(QColor("#B8781C"), 6, Qt.SolidLine, Qt.RoundCap))
        for x0, x1 in ((84, 72), (112, 106), (144, 150), (172, 184)):
            p.drawLine(QPointF(x0, 112 + abs(128 - x0) * 0.35), QPointF(x1, 196))

    # The needle, glinting out of the stack
    p.setPen(QPen(QColor("#E4E6E9"), 12, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(150, 150), QPointF(214, 50))
    p.setPen(QPen(QColor("#22252A"), 5, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(203, 66), QPointF(209, 57))  # the eye
    p.end()
    return img


def png_bytes(img):
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def write_ico(path, images):
    """An .ico holding PNG images (supported since Windows Vista)."""
    pngs = [png_bytes(img) for img in images]
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries = b""
    for img, png in zip(images, pngs):
        side = img.width() if img.width() < 256 else 0  # 0 means 256
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    path.write_bytes(header + entries + b"".join(pngs))


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    write_ico(OUT, [draw(s) for s in SIZES])
    draw(256).save(str(OUT.with_suffix(".png")))
    print(f"Wrote {OUT}")
