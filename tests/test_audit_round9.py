"""Regressions for history and diagnostic-report UI failure handling."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem

from gallery_dl_app.advanced_tools import AdvancedToolsMixin
from gallery_dl_app.reports import ReportsMixin


class HistoryFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_refresh_failure_keeps_existing_history_visible(self):
        table = QTableWidget(1, 5)
        table.setItem(0, 0, QTableWidgetItem("previous history"))
        target = SimpleNamespace(
            tbl_history=table,
            feature_store=SimpleNamespace(list_history=MagicMock(side_effect=OSError("database unavailable"))),
            show_compact_message=MagicMock(),
        )
        self.addCleanup(table.deleteLater)

        AdvancedToolsMixin.load_history_table(target)

        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 0).text(), "previous history")
        target.show_compact_message.assert_called_once()


class AuditReportFailureTests(unittest.TestCase):
    def test_app_data_preparation_failure_is_reported(self):
        target = SimpleNamespace(show_compact_message=MagicMock())
        with patch("gallery_dl_app.reports.app_data_dir", side_effect=OSError("storage unavailable")):
            ReportsMixin.export_audit_report(target)
        target.show_compact_message.assert_called_once()

    def test_report_generation_failure_is_reported_without_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            target = SimpleNamespace(
                diagnostic_html=MagicMock(side_effect=OSError("diagnostics unavailable")),
                _safe_write_file=MagicMock(),
                show_compact_message=MagicMock(),
            )
            with (
                patch("gallery_dl_app.reports.app_data_dir", return_value=Path(folder)),
                patch("gallery_dl_app.reports.APP_DIR", Path(folder)),
                patch("gallery_dl_app.reports.QFileDialog.getSaveFileName", return_value=(str(Path(folder) / "audit.html"), "")),
            ):
                ReportsMixin.export_audit_report(target)
            target._safe_write_file.assert_not_called()
            target.show_compact_message.assert_called_once()

    def test_archive_example_preparation_failure_is_reported(self):
        target = SimpleNamespace(show_compact_message=MagicMock())
        with patch("gallery_dl_app.reports.app_data_dir", side_effect=OSError("storage unavailable")):
            ReportsMixin.export_archive_config_example(target)
        target.show_compact_message.assert_called_once()

    def test_open_app_data_preparation_failure_is_reported(self):
        target = SimpleNamespace(show_compact_message=MagicMock())
        with patch("gallery_dl_app.reports.app_data_dir", side_effect=OSError("storage unavailable")):
            ReportsMixin.open_app_data_dir(target)
        target.show_compact_message.assert_called_once()


class AutosaveFailureTests(unittest.TestCase):
    def test_transient_read_failure_does_not_quarantine_valid_autosave(self):
        with tempfile.TemporaryDirectory() as folder:
            autosave = Path(folder) / "autosave_session.json"
            autosave.write_text('{"schema": 4, "commands": "https://example.com/a"}', encoding="utf-8")
            target = SimpleNamespace(append_log=MagicMock(), apply_session_data=MagicMock())
            with (
                patch("gallery_dl_app.reports.AUTOSAVE_FILE", autosave),
                patch("gallery_dl_app.reports.read_text_safely", side_effect=PermissionError("temporarily locked")),
            ):
                ReportsMixin._load_autosave_silently(target)
            self.assertTrue(autosave.is_file())
            self.assertEqual(len(list(Path(folder).iterdir())), 1)
            target.apply_session_data.assert_not_called()

    def test_oversized_autosave_is_kept_for_manual_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            autosave = Path(folder) / "autosave_session.json"
            autosave.write_text('{"schema": 4}', encoding="utf-8")
            target = SimpleNamespace(append_log=MagicMock(), apply_session_data=MagicMock())
            with (
                patch("gallery_dl_app.reports.AUTOSAVE_FILE", autosave),
                patch("gallery_dl_app.reports.read_text_safely", side_effect=ValueError("file exceeds import limit")),
            ):
                ReportsMixin._load_autosave_silently(target)
            self.assertTrue(autosave.is_file())
            self.assertEqual(len(list(Path(folder).iterdir())), 1)

    def test_invalid_json_is_still_quarantined(self):
        with tempfile.TemporaryDirectory() as folder:
            autosave = Path(folder) / "autosave_session.json"
            autosave.write_text("{invalid json", encoding="utf-8")
            target = SimpleNamespace(append_log=MagicMock(), apply_session_data=MagicMock())
            with patch("gallery_dl_app.reports.AUTOSAVE_FILE", autosave):
                ReportsMixin._load_autosave_silently(target)
            self.assertFalse(autosave.exists())
            self.assertEqual(len(list(Path(folder).glob("autosave_session.json.bad_*"))), 1)
