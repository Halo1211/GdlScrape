"""Check Qt session cleanup and native pytest exit codes in fresh processes."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize("fails, external_app", [(False, False), (True, False), (False, True)])
def test_qt_session_cleanup_preserves_test_result_and_external_app(tmp_path, fails, external_app):
    shutil.copyfile(Path(__file__).with_name("conftest.py"), tmp_path / "conftest.py")
    test_file = tmp_path / "test_gui.py"
    test_file.write_text(
        "from PySide6.QtWidgets import QWidget\n"
        "def test_widget(qt_app):\n"
        "    widget = QWidget()\n"
        "    widget.close()\n"
        f"    assert {not fails!r}, 'intentional test failure'\n",
        encoding="utf-8",
    )
    script = (
        "import sys, pytest\n"
        "from PySide6.QtWidgets import QApplication\n"
        f"external = QApplication([]) if {external_app!r} else None\n"
        f"result = pytest.main(['-q', '--confcutdir', {str(tmp_path)!r}, {str(test_file)!r}])\n"
        "if external is None:\n"
        "    assert QApplication.instance() is None, 'Qt application outlived its test session'\n"
        "else:\n"
        "    assert QApplication.instance() is external, 'Session destroyed its host application'\n"
        "    external.shutdown()\n"
        "sys.exit(result)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == int(fails), result.stdout + result.stderr
    assert ("1 failed" if fails else "1 passed") in result.stdout
    assert "Traceback" not in result.stderr, result.stderr


def test_test_widgets_are_released_between_tests_while_the_host_survives(tmp_path):
    shutil.copyfile(Path(__file__).with_name("conftest.py"), tmp_path / "conftest.py")
    (tmp_path / "test_widgets.py").write_text(
        "from PySide6.QtWidgets import QApplication, QWidget\n"
        "from shiboken6 import isValid\n"
        "host = QApplication.instance()\n"
        "existing = QWidget()\n"
        "retained = []\n"
        "destroyed_with_application = []\n"
        "def test_create_window():\n"
        "    window = QWidget()\n"
        "    window.destroyed.connect(lambda: destroyed_with_application.append(QApplication.instance() is host))\n"
        "    retained.append(window)\n"
        "    window.deleteLater()\n"
        "    closed = QWidget()\n"
        "    closed.close()\n"
        "    retained.append(closed)\n"
        "def test_previous_window_was_released():\n"
        "    assert not isValid(retained[0]), 'Test leaked its deferred widget into the next test'\n"
        "    assert not isValid(retained[1]), 'Test leaked its closed widget into the next test'\n"
        "    assert destroyed_with_application == [True]\n"
        "    assert isValid(existing), 'Cleanup destroyed a widget owned by the host'\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-c", (
            "import sys, pytest\n"
            "from PySide6.QtWidgets import QApplication\n"
            "app = QApplication([])\n"
            f"result = pytest.main(['-q', '--confcutdir', {str(tmp_path)!r}, {str(tmp_path)!r}])\n"
            "app.shutdown()\n"
            "sys.exit(result)\n"
        )],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
