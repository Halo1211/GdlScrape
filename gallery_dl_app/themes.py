"""Application styles and native-window theme synchronization."""

from __future__ import annotations

import ctypes
import platform
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget


def _colorref(hex_color: str) -> int:
    """Convert #RRGGBB to the BGR COLORREF layout used by DWM."""
    value = hex_color.lstrip("#")
    red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
    return red | (green << 8) | (blue << 16)


@lru_cache(maxsize=2)
def application_icon(dark: bool = True) -> QIcon:
    """Load the theme-aware GdlScrape brand mark, with a generated fallback."""
    asset_name = "gdlscrape-logo-dark.png" if dark else "gdlscrape-logo-light.png"
    logo_path = Path(__file__).resolve().parent / "assets" / asset_name
    if logo_path.is_file():
        branded_icon = QIcon(str(logo_path))
        if not branded_icon.isNull():
            return branded_icon

    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 128):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        margin = max(1.0, size * 0.08)
        painter.setPen(QPen(QColor("#35e5c7"), max(1.0, size * 0.055)))
        painter.setBrush(QColor("#0b2530"))
        painter.drawRoundedRect(
            QRectF(margin, margin, size - (2 * margin), size - (2 * margin)),
            size * 0.2,
            size * 0.2,
        )

        center = size * 0.5
        painter.setPen(QPen(QColor("#eaffff"), max(1.2, size * 0.075), Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(center, size * 0.22), QPointF(center, size * 0.59))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#55eadc"))
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(size * 0.29, size * 0.51),
                    QPointF(center, size * 0.73),
                    QPointF(size * 0.71, size * 0.51),
                ]
            )
        )
        painter.setPen(QPen(QColor("#55eadc"), max(1.0, size * 0.06), Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(size * 0.27, size * 0.79), QPointF(size * 0.73, size * 0.79))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def action_icon(name: str, *, dark: bool = True) -> QIcon:
    """Draw small interface symbols explicitly so platform icon themes cannot hide them."""
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    color = QColor("#e5eefb" if dark else "#26364d")
    painter.setPen(QPen(color, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    if name in {"open", "folder"}:
        painter.drawLine(2, 6, 8, 6)
        painter.drawLine(8, 6, 10, 8)
        painter.drawLine(10, 8, 17, 8)
        painter.drawRoundedRect(2, 8, 16, 10, 1.5, 1.5)
    elif name == "paste":
        painter.drawRoundedRect(5, 4, 11, 13, 1, 1)
        painter.drawLine(8, 2, 13, 2)
        painter.drawLine(8, 8, 13, 8)
        painter.drawLine(8, 11, 13, 11)
    elif name == "trash":
        painter.drawLine(4, 5, 16, 5)
        painter.drawLine(8, 3, 12, 3)
        painter.drawRoundedRect(6, 7, 8, 10, 1, 1)
        painter.drawLine(9, 9, 9, 15)
        painter.drawLine(11, 9, 11, 15)
    elif name == "download":
        painter.drawLine(10, 2, 10, 13)
        painter.drawLine(6, 9, 10, 13)
        painter.drawLine(10, 13, 14, 9)
        painter.drawLine(4, 17, 16, 17)
    elif name == "pause":
        painter.drawRoundedRect(5, 4, 3, 12, 0.5, 0.5)
        painter.drawRoundedRect(12, 4, 3, 12, 0.5, 0.5)
    elif name == "cancel":
        painter.drawLine(5, 5, 15, 15)
        painter.drawLine(15, 5, 5, 15)
    elif name == "retry":
        painter.drawArc(4, 4, 12, 12, 30 * 16, 295 * 16)
        painter.drawLine(14, 4, 17, 4)
        painter.drawLine(17, 4, 17, 7)
    elif name == "stop":
        painter.drawRoundedRect(5, 5, 10, 10, 1, 1)
    elif name == "info":
        painter.drawEllipse(3, 3, 14, 14)
        painter.drawLine(10, 9, 10, 14)
        painter.drawPoint(10, 6)
    elif name == "warning":
        painter.drawLine(10, 3, 18, 17)
        painter.drawLine(18, 17, 2, 17)
        painter.drawLine(2, 17, 10, 3)
        painter.drawLine(10, 8, 10, 12)
        painter.drawPoint(10, 15)
    elif name == "error":
        painter.drawEllipse(3, 3, 14, 14)
        painter.drawLine(7, 7, 13, 13)
        painter.drawLine(13, 7, 7, 13)
    painter.end()
    return QIcon(pixmap)


def apply_native_window_theme(widget: QWidget, dark: bool) -> bool:
    """Keep the Windows caption/title bar aligned with the Qt theme."""
    if platform.system() != "Windows":
        return False
    try:
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        enabled = ctypes.c_int(1 if dark else 0)
        # Attribute 20 is current; 19 covers older Windows 10 builds.
        result = dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(enabled), ctypes.sizeof(enabled))
        if result != 0:
            dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(enabled), ctypes.sizeof(enabled))

        # Windows 11 supports explicit caption/text colors. Calls are harmless
        # on older builds (they return an unsupported-attribute code).
        caption = ctypes.c_int(_colorref("#11161d" if dark else "#ffffff"))
        text = ctypes.c_int(_colorref("#eef3ff" if dark else "#172033"))
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), ctypes.sizeof(text))
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False


class NativeWindowThemeFilter(QObject):
    """Apply caption colors to existing and newly shown top-level windows."""

    def __init__(self, app: QApplication, *, dark: bool = True) -> None:
        super().__init__(app)
        self.app = app
        self.dark = bool(dark)

    def set_dark(self, dark: bool) -> None:
        self.dark = bool(dark)
        for widget in self.app.topLevelWidgets():
            if widget.isWindow():
                apply_native_window_theme(widget, self.dark)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Show and isinstance(watched, QWidget) and watched.isWindow():
            apply_native_window_theme(watched, self.dark)
        return False


def theme_palette(dark: bool) -> QPalette:
    """Return a complete palette for controls not fully covered by QSS."""
    palette = QPalette()
    colors = {
        QPalette.Window: "#11161d" if dark else "#f5f7fb",
        QPalette.WindowText: "#eef3ff" if dark else "#172033",
        QPalette.Base: "#080d13" if dark else "#ffffff",
        QPalette.AlternateBase: "#151b23" if dark else "#f7f9fc",
        QPalette.ToolTipBase: "#101827" if dark else "#ffffff",
        QPalette.ToolTipText: "#dbe7f7" if dark else "#26364d",
        QPalette.Text: "#eef3ff" if dark else "#172033",
        QPalette.Button: "#1f2935" if dark else "#ffffff",
        QPalette.ButtonText: "#f3f7ff" if dark else "#172033",
        QPalette.BrightText: "#ffffff",
        QPalette.Link: "#58a8ff" if dark else "#2368c4",
        QPalette.Highlight: "#4ea1ff" if dark else "#2368c4",
        QPalette.HighlightedText: "#ffffff",
        QPalette.PlaceholderText: "#738299" if dark else "#7b899d",
    }
    for role, value in colors.items():
        palette.setColor(role, QColor(value))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#667386" if dark else "#9aa6b8"))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#667386" if dark else "#9aa6b8"))
    return palette

DARK_QSS = """
* {
    font-family: 'Segoe UI', 'Inter', 'Helvetica Neue', Arial, sans-serif;
    font-size: 12px;
    color: #eef3ff;
}
QMainWindow, QWidget { background: #11161d; }
QLabel { color: #aeb8c8; min-height: 16px; }
QLabel#title { color: #59a2ff; font-size: 24px; font-weight: 800; }
QLabel#subtle { color: #8b98aa; }
QLabel#good { color: #48dd73; font-weight: 700; }
QLabel#bad { color: #ff5d57; font-weight: 700; }
QLabel#metric { color: #eaf1ff; font-weight: 700; }
QLabel#sectionTitle { color: #ffffff; font-size: 13px; font-weight: 900; min-height: 18px; }
QFrame#separatorLine { background: #303a48; border: none; max-height: 1px; }
QFrame#card { background: #151b23; border: 1px solid #303a48; border-radius: 10px; }
QGroupBox {
    background: #151b23;
    border: 1px solid #303a48;
    border-radius: 14px;
    margin-top: 14px;
    padding: 12px;
    font-weight: 800;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 16px;
    padding: 0 8px;
    color: #ffffff;
    background: #11161d;
}
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QTableWidget {
    background: #080d13;
    border: 1px solid #303a48;
    border-radius: 8px;
    selection-background-color: #4ea1ff;
    selection-color: #ffffff;
}
QPlainTextEdit, QTextEdit {
    font-family: 'Cascadia Mono', 'JetBrains Mono', 'Consolas', monospace;
    font-size: 12px;
    color: #f4f7ff;
}
QLineEdit, QSpinBox, QComboBox { padding: 3px 8px; min-height: 22px; }
QComboBox#headerCombo { min-width: 0; padding: 3px 4px; }
QLabel#fieldLabel { color: #aeb8c8; font-size: 12px; font-weight: 600; min-height: 17px; padding: 0; margin: 0; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #080d13; color: #eef3ff; border: 1px solid #303a48; selection-background-color: #273247; selection-color: #ffffff; }
QSpinBox { min-width: 88px; }
QComboBox { min-width: 108px; }
QSpinBox::up-button, QSpinBox::down-button {
    width: 18px;
    border-left: 1px solid #303a48;
    background: #111820;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #243044; }
QSpinBox::up-arrow, QSpinBox::down-arrow { width: 8px; height: 8px; }

QScrollBar:vertical { background: #0b1118; width: 10px; border: none; margin: 0; }
QScrollBar::handle:vertical { background: #3a4657; border-radius: 5px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: #53667c; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; border: none; background: transparent; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: #0b1118; height: 10px; border: none; margin: 0; }
QScrollBar::handle:horizontal { background: #3a4657; border-radius: 5px; min-width: 32px; }
QScrollBar::handle:horizontal:hover { background: #53667c; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; border: none; background: transparent; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
QPushButton {
    background: #1f2935;
    border: 1px solid #3b4858;
    border-radius: 7px;
    color: #f3f7ff;
    padding: 4px 8px;
    font-weight: 600;
}
QPushButton:hover { background: #2a3646; border-color: #53667c; }
QPushButton:pressed { background: #334253; }
QPushButton:disabled { color: #667386; background: #171d25; border-color: #252e39; }
QPushButton#primary {
    background: #58a8ff;
    color: #06111f;
    border: none;
    font-size: 13px;
    font-weight: 900;
    padding: 5px 10px;
}
QPushButton#primary:hover { background: #73b7ff; }
QPushButton#danger {
    background: #ff554f;
    color: #ffffff;
    border: none;
    font-weight: 900;
    padding: 5px 10px;
}
QPushButton#danger:hover { background: #ff6d67; }
QPushButton#warn {
    background: #8a6519;
    color: #fff1c4;
    border: none;
    font-weight: 800;
}
QPushButton#mini { padding: 3px 6px; min-width: 64px; }
QMessageBox QLabel#qt_msgbox_label { max-width: 480px; qproperty-wordWrap: true; }
QMessageBox QPushButton { min-width: 68px; max-width: 96px; }
QCheckBox { color: #f2f6ff; spacing: 8px; }
QCheckBox::indicator { width: 15px; height: 15px; }
QCheckBox::indicator:unchecked { border: 1px solid #465366; border-radius: 4px; background: #11161d; }
QCheckBox::indicator:checked { border: 1px solid #58a8ff; border-radius: 4px; background: #58a8ff; }
QProgressBar {
    background: #080d13;
    border: 1px solid #253040;
    border-radius: 6px;
    height: 14px;
    text-align: center;
    color: #dfeaff;
}
QProgressBar::chunk { border-radius: 5px; background: #58a8ff; }
QHeaderView::section {
    background: #1d2532;
    color: #8fc2ff;
    border: none;
    border-bottom: 1px solid #303a48;
    padding: 8px;
    font-weight: 800;
}
QTableWidget { gridline-color: #202937; }
QTabWidget::pane { border: 1px solid #303a48; border-radius: 10px; top: -1px; }
QTabBar::tab {
    background: #1a2230;
    color: #aeb8c8;
    padding: 6px 10px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    margin-right: 2px;
}
QTabBar::tab:selected { background: #273247; color: #ffffff; }
QSplitter::handle { background: #303a48; }
QMessageBox { background: #11161d; }
QMessageBox QLabel#qt_msgbox_label { color: #eef3ff; max-width: 500px; min-height: 24px; padding: 6px; qproperty-wordWrap: true; }
QMessageBox QPushButton { min-width: 68px; min-height: 26px; padding: 4px 10px; }
QDialog QLabel { min-height: 14px; }
QDialog QPushButton { min-height: 26px; }
QMenu { background: #0b111a; border: 1px solid #304057; padding: 6px; }
QMenu::item { color: #d7e1f0; padding: 7px 28px 7px 10px; border-radius: 5px; }
QMenu::item:selected { background: #173044; color: #65eee1; }
QMenu::separator { height: 1px; background: #26364a; margin: 5px 8px; }

"""

LIGHT_QSS = """
* { font-family: 'Segoe UI', 'Inter', Arial, sans-serif; font-size: 12px; color: #172033; }
QMainWindow, QWidget { background: #f5f7fb; }
QLabel { color: #46566e; min-height: 16px; }
QLabel#title { color: #2368c4; font-size: 24px; font-weight: 800; }
QLabel#subtle { color: #6c7890; }
QLabel#good { color: #198754; font-weight: 700; }
QLabel#bad { color: #d63333; font-weight: 700; }
QLabel#metric { color: #172033; font-weight: 700; }
QLabel#sectionTitle { color: #172033; font-size: 13px; font-weight: 900; min-height: 18px; }
QFrame#separatorLine { background: #d9e1ec; border: none; max-height: 1px; }
QFrame#card { background: #ffffff; border: 1px solid #d9e1ec; border-radius: 10px; }
QGroupBox { background: #ffffff; border: 1px solid #d9e1ec; border-radius: 14px; margin-top: 14px; padding: 12px; font-weight: 800; }
QGroupBox::title { subcontrol-origin: margin; left: 16px; padding: 0 8px; color: #172033; background: #f5f7fb; }
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QTableWidget { background: #ffffff; border: 1px solid #d9e1ec; border-radius: 8px; selection-background-color: #2368c4; selection-color: #ffffff; }
QPlainTextEdit, QTextEdit { font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 12px; }
QLineEdit, QSpinBox, QComboBox { padding: 3px 8px; min-height: 22px; }
QComboBox#headerCombo { min-width: 0; padding: 3px 4px; }
QComboBox QAbstractItemView { background: #ffffff; color: #172033; border: 1px solid #ccd7e6; selection-background-color: #e5f0ff; selection-color: #1559a6; }
QLabel#fieldLabel { color: #46566e; font-size: 12px; font-weight: 600; min-height: 17px; padding: 0; margin: 0; }
QSpinBox { min-width: 88px; }
QComboBox { min-width: 108px; }
QSpinBox::up-button, QSpinBox::down-button {
    width: 18px;
    border-left: 1px solid #d9e1ec;
    background: #eef3fb;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #dde8f7; }
QSpinBox::up-arrow, QSpinBox::down-arrow { width: 8px; height: 8px; }

QScrollBar:vertical { background: #eef3f8; width: 10px; border: none; margin: 0; }
QScrollBar::handle:vertical { background: #b8c5d6; border-radius: 5px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: #8fa1ba; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; border: none; background: transparent; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: #eef3f8; height: 10px; border: none; margin: 0; }
QScrollBar::handle:horizontal { background: #b8c5d6; border-radius: 5px; min-width: 32px; }
QScrollBar::handle:horizontal:hover { background: #8fa1ba; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; border: none; background: transparent; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
QPushButton { background: #ffffff; border: 1px solid #ccd7e6; border-radius: 7px; padding: 4px 8px; font-weight: 600; }
QPushButton:hover { background: #eef4ff; }
QPushButton:disabled { color: #9aa6b8; background: #f3f5f8; }
QPushButton#primary { background: #4a9cff; color: #ffffff; border: none; font-size: 13px; font-weight: 900; padding: 5px 10px; }
QPushButton#danger { background: #ff554f; color: #ffffff; border: none; font-weight: 900; padding: 5px 10px; }
QPushButton#warn { background: #ffd166; color: #4d3600; border: none; font-weight: 800; }
QProgressBar { background: #e8edf5; border: 1px solid #d9e1ec; border-radius: 6px; height: 14px; text-align: center; }
QMessageBox QLabel#qt_msgbox_label { max-width: 480px; qproperty-wordWrap: true; }
QMessageBox QPushButton { min-width: 68px; max-width: 96px; }
QProgressBar::chunk { background: #4a9cff; border-radius: 5px; }
QHeaderView::section { background: #edf3fb; color: #2368c4; border: none; border-bottom: 1px solid #d9e1ec; padding: 8px; font-weight: 800; }
QTabBar::tab { background: #edf3fb; padding: 6px 10px; border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 2px; }
QTabBar::tab:selected { background: #ffffff; color: #172033; }
QMessageBox { background: #f5f7fb; }
QMessageBox QLabel#qt_msgbox_label { color: #172033; max-width: 500px; min-height: 24px; padding: 6px; qproperty-wordWrap: true; }
QMessageBox QPushButton { min-width: 68px; min-height: 26px; padding: 4px 10px; }
QDialog QLabel { min-height: 14px; }
QDialog QPushButton { min-height: 26px; }
QMenu { background: #ffffff; border: 1px solid #ccd7e6; padding: 6px; }
QMenu::item { color: #172033; padding: 7px 28px 7px 10px; border-radius: 5px; }
QMenu::item:selected { background: #e5f0ff; color: #1559a6; }
QMenu::separator { height: 1px; background: #d9e1ec; margin: 5px 8px; }

"""

SERVICE_COLORS = {
    "kemono": "#ff7ab6", "coomer": "#ff9f43", "patreon": "#ff6b4a",
    "fanbox": "#4ea8ff", "gumroad": "#3ecf8e", "pixiv": "#5b8cff",
    "twitter": "#1da1f2", "instagram": "#e1306c", "reddit": "#ff4500",
}

STATUS_COLORS = {
    "queued": "#9aa8ba",
    "running": "#58a8ff",
    "done": "#48dd73",
    "failed": "#ff5d57",
    "stopped": "#f0a020",
    "cancelled": "#c08cff",
    "skipped": "#9aa8ba",
    "idle": "#9aa8ba",
    "paused": "#f0a020",
}

# Dashboard-specific cyber treatment layered over the shared dark controls.
DARK_QSS += """
QMainWindow, QWidget#appRoot, QWidget#dashboard { background: #05080e; }
QLabel { background: transparent; }
QFrame#topBar {
    background: #060910;
    border: none;
    border-bottom: 1px solid #1d2d3b;
}
QFrame#accentLine {
    border: none;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #39dfe1, stop:0.34 #39dfe1, stop:0.66 #8f7cff, stop:1 #142431);
}
QLabel#brandMark { color: #76eff0; font-size: 34px; font-weight: 300; }
QLabel#brandSoft { color: #8894aa; font-size: 18px; font-weight: 400; }
QLabel#brandStrong { color: #f2f5ff; font-size: 18px; font-weight: 800; }
QLabel#brandTagline, QLabel#versionLabel {
    color: #738299;
    font-size: 10px;
    font-weight: 700;
}
QLabel#eyebrow, QLabel#metricCaption {
    color: #91a2bc;
    font-size: 10px;
    font-weight: 800;
}
QLabel#statusPill {
    color: #35e5b5;
    background: #071a18;
    border: 1px solid #1f745f;
    border-radius: 12px;
    padding: 4px 12px;
    font-size: 10px;
    font-weight: 900;
}
QLabel#good {
    color: #35e5b5;
    background: #071a18;
    border: 1px solid #1f745f;
    border-radius: 12px;
    padding: 4px 12px;
    font-size: 10px;
    font-weight: 900;
}
QLabel#bad {
    color: #ff7a8f;
    background: #211017;
    border: 1px solid #713345;
    border-radius: 12px;
    padding: 4px 12px;
    font-size: 10px;
    font-weight: 900;
}
QLabel#countBadge {
    color: #35e5c7;
    background: transparent;
    font-size: 10px;
    font-weight: 800;
}
QFrame#leftRail { background: #080c13; border-right: 1px solid #263445; }
QFrame#panel, QFrame#card {
    background: #0a0f18;
    border: 1px solid #253346;
    border-radius: 12px;
}
QFrame#metricBlock { background: transparent; border: none; }
QLabel#metricValue {
    color: #39e0cf;
    font-size: 24px;
    font-weight: 800;
}
QLabel#metricValuePurple {
    color: #9687ff;
    font-size: 24px;
    font-weight: 800;
}
QLabel#pipelineSummary {
    color: #f1ad55;
    font-size: 11px;
}
QPlainTextEdit#linkEditor, QPlainTextEdit#liveLog {
    background: #050810;
    border: 1px solid #243246;
    border-radius: 9px;
    color: #75e5dc;
    padding: 8px;
    font-family: 'Cascadia Mono', 'Consolas', monospace;
    font-size: 11px;
}
QLineEdit, QSpinBox, QComboBox {
    background: #070b12;
    border: 1px solid #28384c;
    border-radius: 8px;
    color: #d9e4f5;
    min-height: 26px;
    padding: 3px 8px;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {
    border: 1px solid #38daca;
}
QPushButton {
    background: #101725;
    border: 1px solid #304057;
    border-radius: 8px;
    color: #c4cede;
    min-height: 28px;
    padding: 4px 10px;
    font-weight: 700;
}
QPushButton:hover { background: #152235; border-color: #42bcb6; color: #ffffff; }
QPushButton#primary {
    background: #0b2c2d;
    border: 1px solid #277e79;
    color: #58eee0;
    min-height: 36px;
    font-size: 11px;
    font-weight: 900;
}
QPushButton#primary:hover { background: #0d3b3a; border-color: #4be5d5; }
QPushButton#danger {
    background: #ff6379;
    border: none;
    color: #13070b;
    min-height: 36px;
    font-size: 11px;
    font-weight: 900;
}
QPushButton#danger:hover { background: #ff7f91; }
QPushButton#ghost, QPushButton#navButton {
    background: transparent;
    border: 1px solid #29394d;
    color: #93a3bc;
}
QPushButton#navButton { font-size: 10px; }
QPushButton#iconButton { padding: 0; }
QProgressBar {
    background: #111724;
    border: none;
    border-radius: 4px;
    min-height: 7px;
    max-height: 7px;
}
QProgressBar::chunk {
    border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3de4e0, stop:1 #8c7cff);
}
QTabWidget#transferTabs::pane { border: none; background: transparent; }
QTabWidget#transferTabs QTabBar::tab {
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: #75849a;
    padding: 10px 16px;
    font-size: 10px;
    font-weight: 800;
}
QTabWidget#transferTabs QTabBar::tab:selected {
    color: #43ddce;
    border-bottom: 2px solid #43ddce;
}
QTableWidget {
    background: #070b12;
    alternate-background-color: #0b0e19;
    border: 1px solid #1f2c3e;
    border-radius: 9px;
    color: #c7d2e4;
    selection-background-color: #171638;
    selection-color: #ffffff;
}
QTableWidget::item { padding: 8px 6px; border-bottom: 1px solid #1d2638; }
QHeaderView::section {
    background: #090e17;
    color: #8291aa;
    border: none;
    border-bottom: 1px solid #28364a;
    padding: 8px;
    font-size: 10px;
    font-weight: 800;
}
QSplitter#dashboardSplitter::handle { background: #263546; width: 1px; }
QStatusBar {
    background: #05080e;
    color: #697990;
    border-top: 1px solid #172331;
    font-size: 10px;
}
QScrollBar:vertical { background: #080c13; width: 8px; }
QScrollBar::handle:vertical { background: #2c3c51; border-radius: 4px; min-height: 28px; }
QToolTip { background: #101827; color: #dbe7f7; border: 1px solid #34465e; padding: 5px; }
"""

# Dashboard-specific light treatment. The structure stays identical across
# themes so switching appearance never moves the user's controls.
LIGHT_QSS += """
QMainWindow, QWidget#appRoot, QWidget#dashboard { background: #f3f7fb; }
QLabel { background: transparent; }
QFrame#topBar { background: #ffffff; border: none; border-bottom: 1px solid #dbe5ef; }
QFrame#accentLine {
    border: none;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #19bcb6, stop:0.42 #19bcb6, stop:0.72 #7567dc, stop:1 #d8e5ef);
}
QLabel#brandMark { color: #16a6a2; font-size: 34px; font-weight: 300; }
QLabel#brandSoft { color: #66758a; font-size: 18px; font-weight: 400; }
QLabel#brandStrong { color: #172238; font-size: 18px; font-weight: 800; }
QLabel#brandTagline, QLabel#versionLabel, QLabel#eyebrow, QLabel#metricCaption {
    color: #63748c;
    font-size: 10px;
    font-weight: 800;
}
QLabel#statusPill {
    color: #087b65;
    background: #e6f8f2;
    border: 1px solid #91d8c5;
    border-radius: 12px;
    padding: 4px 12px;
    font-size: 10px;
    font-weight: 900;
}
QLabel#countBadge { color: #078b84; font-size: 10px; font-weight: 800; }
QFrame#leftRail { background: #f8fbfe; border-right: 1px solid #d5e1ed; }
QFrame#panel, QFrame#card { background: #ffffff; border: 1px solid #d7e2ed; border-radius: 12px; }
QFrame#metricBlock { background: transparent; border: none; }
QLabel#metricValue { color: #098d85; font-size: 24px; font-weight: 800; }
QLabel#metricValuePurple { color: #6d5bd0; font-size: 24px; font-weight: 800; }
QLabel#pipelineSummary { color: #a75a04; font-size: 11px; }
QPlainTextEdit#linkEditor, QPlainTextEdit#liveLog {
    background: #fbfdff;
    border: 1px solid #cad8e6;
    border-radius: 9px;
    color: #176e70;
    padding: 8px;
    font-family: 'Cascadia Mono', 'Consolas', monospace;
    font-size: 11px;
}
QPushButton#primary { background: #087f7b; border: 1px solid #087f7b; color: #ffffff; min-height: 36px; }
QPushButton#primary:hover { background: #066b68; border-color: #066b68; }
QPushButton#danger { background: #e65368; border: none; color: #ffffff; min-height: 36px; }
QPushButton#ghost { background: transparent; border: 1px solid #cbd9e7; color: #50647d; }
QProgressBar { background: #e8eef5; border: none; border-radius: 4px; min-height: 7px; max-height: 7px; }
QProgressBar::chunk { border-radius: 4px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1bbdb5, stop:1 #7468dc); }
QTabWidget#transferTabs::pane { border: none; background: transparent; }
QTabWidget#transferTabs QTabBar::tab {
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: #687a91;
    padding: 10px 16px;
    font-size: 10px;
    font-weight: 800;
}
QTabWidget#transferTabs QTabBar::tab:selected { color: #087f7b; border-bottom: 2px solid #19aaa3; }
QTableWidget { background: #ffffff; alternate-background-color: #f7f9fc; border: 1px solid #d7e2ed; border-radius: 9px; color: #27364d; }
QTableWidget::item { padding: 8px 6px; border-bottom: 1px solid #e2e9f1; }
QHeaderView::section { background: #f3f7fb; color: #61738b; border: none; border-bottom: 1px solid #d7e2ed; padding: 8px; }
QSplitter#dashboardSplitter::handle { background: #d4dfeb; width: 1px; }
QStatusBar { background: #ffffff; color: #62748c; border-top: 1px solid #d7e2ed; font-size: 10px; }
QToolTip { background: #ffffff; color: #26364d; border: 1px solid #bfcddd; padding: 5px; }
"""


def _control_glyph_qss(theme: str) -> str:
    """Pin control arrows to bundled PNGs; native glyphs vanish under QSS on Windows."""
    assets = Path(__file__).resolve().parent / "assets"
    up = (assets / f"control-up-{theme}.png").as_posix()
    down = (assets / f"control-down-{theme}.png").as_posix()
    check = (assets / "control-check.png").as_posix()
    return f"""
QSpinBox::up-arrow {{ image: url("{up}"); width: 11px; height: 11px; }}
QSpinBox::down-arrow {{ image: url("{down}"); width: 11px; height: 11px; }}
QComboBox::down-arrow {{ image: url("{down}"); width: 11px; height: 11px; }}
QCheckBox::indicator:checked {{
    border: 1px solid #4a9cff;
    border-radius: 4px;
    background: #4a9cff;
    image: url("{check}");
}}
"""


DARK_QSS += _control_glyph_qss("dark")
LIGHT_QSS += _control_glyph_qss("light")

__all__ = [
    'DARK_QSS',
    'LIGHT_QSS',
    'NativeWindowThemeFilter',
    'SERVICE_COLORS',
    'STATUS_COLORS',
    'application_icon',
    'apply_native_window_theme',
    'theme_palette',
]
