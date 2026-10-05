"""Regressions for GUI edits, option arity, bounded probes and file failures."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gallery_dl.option import build_parser
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QPushButton, QTableWidget

import gallery_dl_app as package
from gallery_dl_app.advanced_tools import AdvancedToolsMixin
from gallery_dl_app.composer import _read_json_config
from gallery_dl_app.core import parse_text_database, safe_filename, split_command
from gallery_dl_app.management import ManagementMixin
from gallery_dl_app.reports import ReportsMixin
from gallery_dl_app.system_tools import SystemToolsMixin
from gallery_dl_app.workers import CommandProbeWorker


class OptionsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_page(self, catalog=None):
        editor = QPlainTextEdit("https://example.com/a")
        target = SimpleNamespace(txt_commands=editor, active_workers=0,
                                 gdl_cmd="gallery-dl --", _table=ManagementMixin._table,
                                 _rebuild_from_text=MagicMock(return_value=True))
        with patch("gallery_dl_app.management.QProcess.start") as start:
            if catalog is None:
                page = ManagementMixin._build_options_tab(target)
            else:
                with patch("gallery_dl_app.management.parse_help_options", return_value=catalog):
                    page = ManagementMixin._build_options_tab(target)
        self.addCleanup(self.app.processEvents)
        self.addCleanup(editor.deleteLater)
        self.addCleanup(page.deleteLater)
        return target, page, start

    @staticmethod
    def select_option(page, option):
        table = page.findChild(QTableWidget)
        for row in range(table.rowCount()):
            if table.item(row, 0).text() == option:
                table.selectRow(row)
                return
        raise AssertionError("option missing: " + option)

    @staticmethod
    def value_editor(page):
        return next(edit for edit in page.findChildren(QLineEdit)
                    if edit.placeholderText() == "Value for the selected option")

    @staticmethod
    def click(page, label):
        next(button for button in page.findChildren(QPushButton) if button.text() == label).click()

    def test_rejected_option_edit_keeps_editor_and_queue(self):
        target, page, _ = self.make_page()
        self.select_option(page, "--range")
        self.value_editor(page).setText("1-10")
        before = target.txt_commands.toPlainText()
        with patch("gallery_dl_app.core.MAX_COMMAND_LINE_CHARS", 28), patch("gallery_dl_app.management.QMessageBox.warning") as warning:
            self.click(page, "Apply Selected Option to Current Input")
        self.assertEqual(target.txt_commands.toPlainText(), before)
        target._rebuild_from_text.assert_not_called()
        warning.assert_called_once()

    def test_rejected_filter_edit_keeps_editor_and_queue(self):
        target, page, _ = self.make_page()
        before = target.txt_commands.toPlainText()
        with patch("gallery_dl_app.core.MAX_COMMAND_LINE_CHARS", 50), patch("gallery_dl_app.management.QMessageBox.warning") as warning:
            self.click(page, "Apply --filter to Current Input")
        self.assertEqual(target.txt_commands.toPlainText(), before)
        target._rebuild_from_text.assert_not_called()
        warning.assert_called_once()

    def test_multi_value_option_roundtrips_to_upstream_parser(self):
        target, page, _ = self.make_page([("--print-to-file", "Print FORMAT into FILE", True)])
        self.select_option(page, "--print-to-file")
        self.value_editor(page).setText('"[{id}]" "fixture output.txt"')
        self.click(page, "Apply Selected Option to Current Input")
        tokens = split_command(target.txt_commands.toPlainText())
        index = tokens.index("--print-to-file")
        self.assertEqual(tokens[index + 1:], ["[{id}]", "fixture output.txt"])
        self.assertEqual(build_parser().parse_args(tokens).urls, ["https://example.com/a"])

    def test_multi_value_option_rejects_missing_second_value(self):
        target, page, _ = self.make_page([("--print-to-file", "Print FORMAT into FILE", True)])
        self.select_option(page, "--print-to-file")
        self.value_editor(page).setText("[{id}]")
        before = target.txt_commands.toPlainText()
        with patch("gallery_dl_app.management.QMessageBox.warning") as warning:
            self.click(page, "Apply Selected Option to Current Input")
        self.assertEqual(target.txt_commands.toPlainText(), before)
        warning.assert_called_once()

    def test_optional_option_keeps_an_explicit_value(self):
        target, page, _ = self.make_page([("--mtime", "Set modification time", False)])
        self.select_option(page, "--mtime")
        self.value_editor(page).setText("date")
        self.click(page, "Apply Selected Option to Current Input")
        tokens = split_command(target.txt_commands.toPlainText())
        self.assertEqual(tokens[-2:], ["--mtime", "date"])
        self.assertEqual(build_parser().parse_args(tokens).urls, ["https://example.com/a"])

    def test_catalog_help_argument_precedes_positional_boundary(self):
        _target, _page, start = self.make_page()
        argv = start.call_args.args[1]
        self.assertLess(argv.index("--help"), argv.index("--"))


class ProbeTimeoutTests(unittest.TestCase):
    def test_timeout_cleanup_failure_still_reports_timeout(self):
        worker = CommandProbeWorker("gallery-dl", ["--version"], timeout=1)
        outputs = []
        worker.done.connect(lambda *args: outputs.append(args))
        proc = MagicMock(returncode=None)
        proc.poll.return_value = None
        proc.communicate.side_effect = [subprocess.TimeoutExpired("fixture", 1),
                                        subprocess.TimeoutExpired("fixture", 2),
                                        OSError("fixture broken pipe")]
        with patch("gallery_dl_app.workers.subprocess.Popen", return_value=proc), patch.object(worker, "stop", side_effect=worker._probe_stopped.set):
            worker.run()
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0][1], -1)
        self.assertIn("timed out", outputs[0][2])
        self.assertTrue(all(call.kwargs.get("timeout", 0) > 0 for call in proc.communicate.call_args_list))
        self.assertIsNone(worker._probe_proc)


class FilenameTests(unittest.TestCase):
    def test_tag_folder_name_removes_windows_control_characters(self):
        for char in (chr(0), chr(1), chr(7), chr(31)):
            with self.subTest(char=ord(char)):
                name = safe_filename("fixture" + char + "tag")
                self.assertFalse(any(ord(item) < 32 for item in name))

    def test_truncating_tag_does_not_restore_invalid_trailing_dot_or_space(self):
        for character in (".", " "):
            with self.subTest(character=character):
                self.assertFalse(safe_filename("a" * 119 + character + "suffix").endswith(character))


class FileFailureTests(unittest.TestCase):
    def test_file_task_start_failure_releases_worker_and_reports_error(self):
        for method, attribute in (("backup_app_data", "_backup_file_worker"), ("scan_output_folder", "_scan_file_worker")):
            with self.subTest(method=method), tempfile.TemporaryDirectory() as folder:
                target = MagicMock(active_workers=0, _backup_file_worker=None, _scan_file_worker=None)
                target.edit_output.text.return_value = folder
                worker = MagicMock()
                worker.start.side_effect = RuntimeError("fixture no thread resources")
                with (
                    patch("gallery_dl_app.reports.app_data_dir"),
                    patch("gallery_dl_app.reports.QFileDialog.getSaveFileName", return_value=(str(Path(folder) / "backup.zip"), "")),
                    patch("gallery_dl_app.reports.CancellableFileTask", return_value=worker),
                ):
                    getattr(ReportsMixin, method)(target)
                self.assertIsNone(getattr(target, attribute))
                worker.deleteLater.assert_called_once()
                target.show_compact_message.assert_called_once()

    def test_account_config_merge_rejects_nonfinite_numbers(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.json"
            config.write_text('{"extractor":{"timeout":NaN}}', encoding="utf-8")
            target = SimpleNamespace(active_account_profile_id=1, config_path=str(config),
                                     cleanup_run_account_config=lambda: None,
                                     feature_store=SimpleNamespace(account=lambda _: {
                                         "site": "pixiv", "auth_kind": "browser", "cookie_source": "chrome",
                                     }))
            with patch("gallery_dl_app.management.SECURE_RUNTIME_DIR", Path(folder)):
                with self.assertRaisesRegex(RuntimeError, "cannot be merged"):
                    ManagementMixin.prepare_active_account_config(target)
            self.assertEqual(list(Path(folder).glob("secure-config-*.json")), [])

    def test_project_profile_directory_errors_are_reported(self):
        for method in ("save_project_profile", "load_project_profile", "export_settings_snapshot"):
            with self.subTest(method=method):
                target = MagicMock(active_workers=0)
                with patch("gallery_dl_app.advanced_tools.app_data_dir", side_effect=PermissionError("fixture denied")):
                    getattr(AdvancedToolsMixin, method)(target)
                target.show_compact_message.assert_called_once()

    def test_backup_directory_errors_are_reported_before_worker_start(self):
        for method in ("backup_config_file", "backup_app_data"):
            with self.subTest(method=method), tempfile.TemporaryDirectory() as folder:
                config = Path(folder) / "config.json"
                config.write_text("{}", encoding="utf-8")
                target = MagicMock(active_workers=0, config_path=str(config), _backup_file_worker=None)
                with patch("gallery_dl_app.reports.app_data_dir", side_effect=PermissionError("fixture denied")):
                    getattr(ReportsMixin, method)(target)
                target.show_compact_message.assert_called_once()

    def test_config_read_rejects_nonfinite_numbers(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.json"
            config.write_text('{"extractor":{"timeout":NaN}}', encoding="utf-8")
            parsed, error = _read_json_config(str(config))
            self.assertEqual(parsed, {})
            self.assertTrue(error)

    def test_config_validator_does_not_report_nonfinite_json_as_valid(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.json"
            config.write_text('{"extractor":{"timeout":1e309}}', encoding="utf-8")
            target = MagicMock(config_path=str(config))
            SystemToolsMixin.validate_config(target)
            args = target.show_compact_message.call_args.args
            self.assertEqual(args[0], "Invalid config")


class QueueMutationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_output_by_tag_rejects_oversized_result_before_editor_change(self):
        before = "# fixture-tag\nhttps://example.com/a"
        editor = QPlainTextEdit(before)
        target = SimpleNamespace(active_workers=0, _rebuild_from_text=lambda: True,
                                 jobs=parse_text_database(before), txt_commands=editor,
                                 edit_output=QLineEdit(), show_compact_message=MagicMock(),
                                 append_log=MagicMock())
        try:
            with (
                patch("gallery_dl_app.core.MAX_COMMAND_LINE_CHARS", 50),
                patch("gallery_dl_app.advanced_tools.QFileDialog.getExistingDirectory", return_value="C:/fixture/" + "a" * 50),
                patch("gallery_dl_app.advanced_tools.QMessageBox.question", return_value=16384) as question,
            ):
                AdvancedToolsMixin.apply_output_folder_by_tag(target)
            self.assertEqual(editor.toPlainText(), before)
            question.assert_not_called()
            target.show_compact_message.assert_called_once()
            target.append_log.assert_not_called()
        finally:
            editor.deleteLater()
            target.edit_output.deleteLater()
            self.app.processEvents()

    def check_composer(self, *, running=False, save=False):
        with (
            patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None),
            patch.object(SystemToolsMixin, "autosave_session", lambda _self: None),
            patch.object(ManagementMixin, "_start_management_services", lambda _self: None),
            tempfile.TemporaryDirectory() as folder,
        ):
            app, window = package.create_application([])
            config = Path(folder) / "config.json"
            original_config = '{"extractor":{"timeout":35}}\n'
            config.write_text(original_config, encoding="utf-8")
            window.config_path = str(config)
            window.help_language = "English"
            before = "https://example.com/original"
            window.txt_commands.setPlainText(before)
            window._rebuild_from_text()

            def exercise_dialog(dialog):
                url_editor = next(edit for edit in dialog.findChildren(QPlainTextEdit)
                                  if edit.placeholderText() == "One URL per line")
                url_editor.setPlainText("https://example.com/new")
                if running:
                    window.active_workers = 1
                label = "Save Defaults" if save else "Add to Queue"
                next(button for button in dialog.findChildren(QPushButton) if button.text() == label).click()
                return 0

            try:
                with (
                    patch("gallery_dl_app.composer.QDialog.exec", new=exercise_dialog),
                    patch("gallery_dl_app.composer.QMessageBox.question", return_value=16384),
                    patch.object(window, "show_compact_message") as message,
                    patch("gallery_dl_app.core.MAX_QUEUE_JOBS", 1),
                ):
                    window.open_download_composer()
                self.assertEqual(window.txt_commands.toPlainText(), before)
                self.assertEqual(config.read_text(encoding="utf-8"), original_config)
                self.assertEqual([job.url for job in window.jobs], [before])
                message.assert_called_once()
                self.assertIn(message.call_args.args[-1], ("info", "warning", "error"))
            finally:
                window.active_workers = 0
                window.close()
                app.processEvents()

    def test_composer_rejects_queue_over_limit_before_editor_change(self):
        self.check_composer()

    def test_composer_blocks_queue_edit_when_run_started_after_dialog_open(self):
        self.check_composer(running=True)

    def test_composer_blocks_config_write_when_run_started_after_dialog_open(self):
        self.check_composer(running=True, save=True)


if __name__ == "__main__":
    unittest.main()
