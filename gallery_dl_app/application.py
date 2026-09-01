"""Application bootstrap kept separate from the UI implementation."""

from __future__ import annotations

import sys

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from .core import APP_NAME, IS_WINDOWS
from .themes import DARK_QSS, NativeWindowThemeFilter, application_icon, theme_palette
from .window import MainWindow


def create_application(argv: list[str] | None = None) -> tuple[QApplication, MainWindow]:
    """Create the QApplication and main window without starting the event loop."""
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    candidates = (
        ["Segoe UI Variable Text", "Segoe UI", "Inter", "Arial"]
        if IS_WINDOWS
        else ["Inter", "Noto Sans", "DejaVu Sans", "Arial"]
    )
    installed = set(QFontDatabase.families())
    family = next((candidate for candidate in candidates if candidate in installed), app.font().family())
    font = QFont(family, 10)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)
    icon = application_icon()
    app.setWindowIcon(icon)
    app.setPalette(theme_palette(True))
    app.setStyleSheet(DARK_QSS)
    theme_filter = getattr(app, "_native_window_theme_filter", None)
    if theme_filter is None:
        theme_filter = NativeWindowThemeFilter(app, dark=True)
        app.installEventFilter(theme_filter)
        app._native_window_theme_filter = theme_filter
    else:
        theme_filter.set_dark(True)
    window = MainWindow()
    window.setWindowIcon(icon)
    return app, window


def main() -> int:
    app, window = create_application()
    window.show()
    return app.exec()


__all__ = ["create_application", "main"]
