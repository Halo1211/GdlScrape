"""Keep Qt alive for the complete session, including isolated GUI test runs."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid


@pytest.fixture(scope="session", autouse=True)
def qt_app():
    # Icons and pixmaps also need an application, even when dialog widgets are
    # mocked. A strong session reference avoids native aborts between tests.
    app = QApplication.instance()
    owns_app = app is None
    if owns_app:
        app = QApplication([])
    yield app
    if owns_app:
        # Release Qt while its Python bindings are still intact, rather than
        # deferring GUI teardown to interpreter finalization on Windows.
        app.shutdown()


@pytest.fixture(autouse=True)
def qt_widgets(qt_app):
    # A closed widget can stay alive through Python signal/slot references.
    # Release each test's widgets while Qt is intact, rather than accumulating
    # them until QApplication's native destructor runs at session teardown.
    existing = set(qt_app.topLevelWidgets())
    yield
    for widget in qt_app.topLevelWidgets():
        if widget not in existing and isValid(widget):
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
