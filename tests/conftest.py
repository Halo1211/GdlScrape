"""Keep Qt alive for the complete session, including isolated GUI test runs."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session", autouse=True)
def qt_app():
    # Icons and pixmaps also need an application, even when dialog widgets are
    # mocked. A strong session reference avoids native aborts between tests.
    app = QApplication.instance() or QApplication([])
    yield app
