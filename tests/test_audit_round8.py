"""Regression checks for executable discovery and untrusted UI messages."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from gallery_dl_app.core import command_executable_available, command_string_to_argv
from gallery_dl_app.management import ManagementMixin
from gallery_dl_app.reports import ReportsMixin


class ExecutableTests(unittest.TestCase):
    def test_environment_executable_path_works_in_compound_command(self):
        with patch.dict(os.environ, {"GDL_AUDIT_PYTHON": sys.executable}):
            argv = command_string_to_argv('"%GDL_AUDIT_PYTHON%" -m gallery_dl')
            self.assertEqual(argv[:3], [sys.executable, "-m", "gallery_dl"])
            self.assertTrue(command_executable_available('"%GDL_AUDIT_PYTHON%" -m gallery_dl'))
            result = subprocess.run([*argv, "--version"], capture_output=True, text=True, timeout=12)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.strip())

    def test_environment_executable_path_works_as_standalone_command(self):
        with patch.dict(os.environ, {"GDL_AUDIT_PYTHON": sys.executable}):
            self.assertEqual(command_string_to_argv('"%GDL_AUDIT_PYTHON%"'), [sys.executable])

    def test_nonexecutables_do_not_get_ready_status_on_windows(self):
        with tempfile.TemporaryDirectory() as folder, patch("gallery_dl_app.core.IS_WINDOWS", True):
            for filename, content in (("broken.exe", b"not a PE file"), ("script.py", b"print(1)"), ("partial.exe", b"MZ")):
                with self.subTest(filename=filename):
                    candidate = Path(folder) / filename
                    candidate.write_bytes(content)
                    self.assertFalse(command_executable_available(str(candidate)))

    def test_real_python_executable_remains_accepted(self):
        self.assertTrue(command_executable_available(sys.executable))


class MessageRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_external_error_is_displayed_as_literal_text(self):
        host = QWidget()
        host.current_theme = "dark"
        host._ui_translate_text = lambda value: value
        message = '<img src="file:///fixture/private.txt"> Access denied'
        captured = []

        def inspect(dialog):
            labels = [label for label in dialog.findChildren(QLabel) if label.text() == message]
            self.assertEqual(len(labels), 1)
            captured.append(labels[0].textFormat())
            return 0

        try:
            with patch("gallery_dl_app.reports.QDialog.exec", new=inspect):
                ReportsMixin.show_compact_message(host, "Download failed", message, "error")
            self.assertEqual(captured, [Qt.PlainText])
        finally:
            host.deleteLater()
            self.app.processEvents()

    def test_runtime_command_hides_password_and_displays_literal_text(self):
        host = QWidget()
        host.gdl_cmd = 'gallery-dl --password fixture-secret <img src="file:///fixture/private.txt">'
        try:
            with patch.object(sys, "frozen", True, create=True):
                page = ManagementMixin._build_runtime_tab(host)
            labels = page.findChildren(QLabel)
            current = next(label for label in labels if label.text().startswith("gallery-dl in use:"))
            self.assertNotIn("fixture-secret", current.text())
            self.assertIn("***", current.text())
            self.assertEqual(current.textFormat(), Qt.PlainText)
        finally:
            host.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
