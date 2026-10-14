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
