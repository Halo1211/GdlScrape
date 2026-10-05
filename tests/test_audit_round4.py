"""Regression checks for command boundaries, account paths, and recovery limits."""

import json
import os
import queue
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gallery_dl.option import build_parser
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QPushButton, QTableWidget

import gallery_dl_app as package
from gallery_dl_app.reports import ReportsMixin
from gallery_dl_app.system_tools import SystemToolsMixin
from gallery_dl_app.core import append_extra_args_to_command, parse_line, parse_text_database, split_command
from gallery_dl_app.management import ManagementMixin
from gallery_dl_app.feature_store import FeatureStore
from gallery_dl_app.workers import DownloadWorker


def worker_for(**changes):
    values = dict(
        worker_id=0, task_queue=queue.Queue(), gdl_cmd="gallery-dl", config_path=None,
        output_dir="", cookies_browser="none", retries=0, extra_args="",
        pause_event=threading.Event(), stop_event=threading.Event(), cancelled_indices=set(),
        compress_enabled=False, compress_format="zip", convert_png_webp=False,
    )
    values.update(changes)
    return DownloadWorker(**values)


class CommandTests(unittest.TestCase):
    def test_extra_overrides_are_checked_before_base_boundary(self):
        argv = worker_for(gdl_cmd="gallery-dl --", extra_args="--cookies-from-browser firefox", cookies_browser="chrome").build_command(
            parse_line("https://example.com/a")
        )
        self.assertEqual(argv.count("--cookies-from-browser"), 1)

    def test_python_module_base_retry_is_not_overridden(self):
        argv = worker_for(gdl_cmd="python -m gallery_dl -R7", retries=2).build_command(parse_line("https://example.com/a"))
        self.assertEqual(build_parser().parse_args(argv[3:]).options[-1], ((), "retries", 7))

    def test_append_options_before_positional_boundary(self):
        command = append_extra_args_to_command('gallery-dl -- "https://example.com/a b"', "--retries 7")
        parsed = build_parser().parse_args(split_command(command)[1:])
        self.assertEqual(parsed.urls, ["https://example.com/a b"])
        self.assertEqual(parsed.options[-1], ((), "retries", 7))

    def test_append_preserves_boundary_looking_option_value(self):
        command = append_extra_args_to_command('gallery-dl --filename=-- https://example.com/a', "--retries 7")
        parsed = build_parser().parse_args(split_command(command)[1:])
        self.assertEqual(parsed.filename, "--")
        self.assertEqual(parsed.options[-1], ((), "retries", 7))

    def test_base_retry_setting_is_not_overridden_by_gui_default(self):
        argv = worker_for(gdl_cmd="gallery-dl --retries 7", retries=2).build_command(parse_line("https://example.com/a"))
        self.assertEqual(build_parser().parse_args(argv[1:]).options[-1], ((), "retries", 7))

    def test_base_cookie_setting_is_not_overridden_by_gui_default(self):
        argv = worker_for(gdl_cmd="gallery-dl --cookies-from-browser firefox", cookies_browser="chrome").build_command(
            parse_line("https://example.com/a")
        )
        self.assertEqual(argv.count("--cookies-from-browser"), 1)

    def test_base_boundary_does_not_turn_gui_defaults_into_urls(self):
        argv = worker_for(gdl_cmd="gallery-dl --", retries=2).build_command(parse_line("https://example.com/a"))
        parsed = build_parser().parse_args(argv[1:])
        self.assertEqual(parsed.urls, ["https://example.com/a"])
        self.assertEqual(parsed.options[-1], ((), "retries", 2))


class AccountPathTests(unittest.TestCase):
    def test_environment_paths_keep_base_config_and_cookie_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "base.json").write_text(json.dumps({"extractor": {"example": {"filename": "fixture"}}}))
            (root / "cookies.txt").write_text("# Netscape HTTP Cookie File\n")
            target = SimpleNamespace(
                active_account_profile_id=1, config_path="$AUDIT_PROFILE_ROOT/base.json",
                feature_store=SimpleNamespace(account=lambda _: {
                    "site": "example", "auth_kind": "cookies_file", "cookie_source": "$AUDIT_PROFILE_ROOT/cookies.txt",
                }), cleanup_run_account_config=lambda: None,
            )
            with patch.dict(os.environ, {"AUDIT_PROFILE_ROOT": str(root)}), patch("gallery_dl_app.management.SECURE_RUNTIME_DIR", root / "runtime"):
                config = ManagementMixin.prepare_active_account_config(target)
            result = json.loads(Path(config).read_text())
            self.assertEqual(result["extractor"]["example"]["filename"], "fixture")
            self.assertEqual(result["extractor"]["example"]["cookies"], str(root / "cookies.txt"))

    def test_worker_config_environment_path_is_loaded(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "base.json"
            config.write_text("{}")
            with patch.dict(os.environ, {"AUDIT_PROFILE_ROOT": folder}):
                argv = worker_for(config_path="$AUDIT_PROFILE_ROOT/base.json").build_command(parse_line("https://example.com/a"))
            self.assertIn("--config", argv)
            self.assertEqual(argv[argv.index("--config") + 1], str(config))


class RecoveryTests(unittest.TestCase):
    def test_closed_window_does_not_read_recovery_database(self):
        target = SimpleNamespace(_management_closed=True, active_workers=0, feature_store=MagicMock())
        target.feature_store.interrupted_runs.return_value = []
        ManagementMixin._offer_crash_recovery(target)
        target.feature_store.interrupted_runs.assert_not_called()

    def test_database_failure_preserves_recovery_records(self):
        for method in ("interrupted_runs", "resolve_interrupted_runs"):
            with self.subTest(method=method):
                store = MagicMock()
                store.interrupted_runs.return_value = [dict(run_id=1, owner_pid=0, command="https://example.com/a")]
                getattr(store, method).side_effect = sqlite3.OperationalError("fixture database is locked")
                target = SimpleNamespace(active_workers=0, feature_store=store, txt_commands=MagicMock())
                with (
                    patch("gallery_dl_app.management.QMessageBox.warning") as warning,
                    patch("gallery_dl_app.management.QMessageBox.question", return_value=16384),
                    patch("gallery_dl_app.management.QTimer.singleShot") as start,
                ):
                    ManagementMixin._offer_crash_recovery(target)
                warning.assert_called_once()
                start.assert_not_called()

    def test_recovery_only_resolves_complete_runs_that_fit_queue(self):
        rows = [dict(run_id=run_id, owner_pid=0, command=f"https://example.com/{run_id}/{i}")
                for run_id in (1, 2) for i in range(2)]
        store = MagicMock()
        store.interrupted_runs.return_value = rows
        target = SimpleNamespace(active_workers=0, feature_store=store, txt_commands=MagicMock())
        with (
            patch("gallery_dl_app.core.MAX_QUEUE_JOBS", 3),
            patch("gallery_dl_app.management.MAX_QUEUE_JOBS", 3),
            patch("gallery_dl_app.management.QMessageBox.question", return_value=16384),
            patch("gallery_dl_app.management.QTimer.singleShot"),
        ):
            ManagementMixin._offer_crash_recovery(target)
            text = target.txt_commands.setPlainText.call_args.args[0]
            self.assertEqual(len(parse_text_database(text)), 2)
        self.assertEqual(set(store.resolve_interrupted_runs.call_args.args[0]), {1})

    def test_unloadable_recovery_is_not_resolved_or_started(self):
        store = MagicMock()
        store.interrupted_runs.return_value = [dict(run_id=1, owner_pid=0, command="https://example.com/" + "a" * 100)]
        target = SimpleNamespace(active_workers=0, feature_store=store, txt_commands=MagicMock())
        with (
            patch("gallery_dl_app.core.MAX_COMMAND_LINE_CHARS", 50),
            patch("gallery_dl_app.management.QMessageBox.warning") as warning,
            patch("gallery_dl_app.management.QMessageBox.question", return_value=16384),
            patch("gallery_dl_app.management.QTimer.singleShot") as start,
        ):
            ManagementMixin._offer_crash_recovery(target)
        warning.assert_called_once()
        target.txt_commands.setPlainText.assert_not_called()
        store.resolve_interrupted_runs.assert_not_called()
        start.assert_not_called()

    def test_recovery_source_rows_are_bounded_without_discarding_run(self):
        store = MagicMock()
        store.interrupted_runs.return_value = [
            dict(run_id=1, owner_pid=0, command=f"https://example.com/{i}", tag=f"tag-{i}", notes="fixture")
            for i in range(2)
        ]
        target = SimpleNamespace(active_workers=0, feature_store=store, txt_commands=MagicMock())
        with (
            patch("gallery_dl_app.core.MAX_QUEUE_SOURCE_ROWS", 5),
            patch("gallery_dl_app.management.QMessageBox.warning") as warning,
            patch("gallery_dl_app.management.QMessageBox.question") as question,
        ):
            ManagementMixin._offer_crash_recovery(target)
        warning.assert_called_once()
        question.assert_not_called()
        store.resolve_interrupted_runs.assert_not_called()


class DatabaseCleanupTests(unittest.TestCase):
    def test_initialization_failure_closes_connection(self):
        store = object.__new__(FeatureStore)
        store.path = Path("fixture.sqlite3")
        connection = MagicMock()
        connection.execute.side_effect = sqlite3.OperationalError("fixture pragma failure")
        with patch("gallery_dl_app.feature_store.sqlite3.connect", return_value=connection):
            with self.assertRaisesRegex(sqlite3.OperationalError, "pragma failure"):
                with store._connect():
                    self.fail("Failed connection must never be yielded")
        connection.close.assert_called_once()

    def test_rollback_failure_does_not_mask_original_error(self):
        store = object.__new__(FeatureStore)
        store.path = Path("fixture.sqlite3")
        connection = MagicMock()
        connection.rollback.side_effect = sqlite3.OperationalError("fixture rollback failure")
        with patch("gallery_dl_app.feature_store.sqlite3.connect", return_value=connection):
            with self.assertRaisesRegex(ValueError, "original fixture"):
                with store._connect():
                    raise ValueError("original fixture")
        connection.close.assert_called_once()


class SchedulerDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_close_cancels_pending_startup_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = FeatureStore(root / "features.sqlite3")
            with (
                patch("gallery_dl_app.management.FeatureStore", return_value=store),
                patch.object(store, "migrate_jsonl_history"),
                patch("gallery_dl_app.management.SECURE_RUNTIME_DIR", root / "runtime"),
                patch.object(ReportsMixin, "_load_autosave_silently", lambda _: None),
                patch.object(SystemToolsMixin, "autosave_session", lambda _: None),
            ):
                _, window = package.create_application([])
                try:
                    window.close()
                    with (
                        patch.object(store, "interrupted_runs", side_effect=sqlite3.OperationalError("closed fixture")) as read,
                        patch("gallery_dl_app.management.QMessageBox.warning") as warning,
                    ):
                        QTest.qWait(750)
                    read.assert_not_called()
                    warning.assert_not_called()
                finally:
                    window.close()
                    window.deleteLater()
                    self.app.processEvents()

    def test_storage_errors_warn_and_preserve_editor(self):
        for action in ("Save", "Delete", "refresh"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as folder:
                store = FeatureStore(Path(folder) / "features.sqlite3")
                store.save_schedule({"name": "Fixture", "command_text": "https://example.com/a"})
                target = SimpleNamespace(feature_store=store, txt_commands=QPlainTextEdit("https://example.com/a"), _table=ManagementMixin._table)
                page = ManagementMixin._build_scheduler_tab(target)
                try:
                    page.findChild(QTableWidget).selectRow(0)
                    button_name = "Save" if action == "refresh" else action
                    button = next(widget for widget in page.findChildren(QPushButton) if widget.text() == button_name)
                    method = {"Save": "save_schedule", "Delete": "delete_schedules", "refresh": "list_schedules"}[action]
                    with (
                        patch.object(store, method, side_effect=sqlite3.OperationalError("fixture database is locked")),
                        patch("gallery_dl_app.management.QMessageBox.warning") as warning,
                        patch("sys.excepthook") as uncaught,
                    ):
                        button.click()
                    uncaught.assert_not_called()
                    warning.assert_called_once()
                    self.assertEqual(page.findChild(QPlainTextEdit).toPlainText(), "https://example.com/a")
                    self.assertEqual(len(store.list_schedules()), 1)
                finally:
                    page.deleteLater()
                    target.txt_commands.deleteLater()
                    self.app.processEvents()
