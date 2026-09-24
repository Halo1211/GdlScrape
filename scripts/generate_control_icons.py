"""Regenerate the PNG control glyphs used by Qt stylesheets."""

from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF


ASSETS = Path(__file__).resolve().parents[1] / "gallery_dl_app" / "assets"


def render(name: str, color: str, points: tuple[tuple[int, int], ...]) -> None:
    image = QImage(24, 24, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(color), 2.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in points]))
    painter.end()
    if not image.save(str(ASSETS / name)):
        raise OSError(f"Could not write control icon: {name}")


for theme, color in (("dark", "#dce8f8"), ("light", "#38516b")):
    render(f"control-up-{theme}.png", color, ((5, 15), (12, 8), (19, 15)))
    render(f"control-down-{theme}.png", color, ((5, 9), (12, 16), (19, 9)))
render("control-check.png", "#ffffff", ((5, 12), (10, 17), (19, 7)))
