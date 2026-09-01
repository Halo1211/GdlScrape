import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gallery_dl_app as package
from gallery_dl import util as gallery_dl_util
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTabWidget,
)
from PySide6.QtGui import QPalette

from gallery_dl_app.composer import ComposerFlowchart, ComposerMixin
from gallery_dl_app.dashboard_ui import DashboardUiMixin
from gallery_dl_app.feature_store import FeatureStore
from gallery_dl_app.queue_controller import QueueControllerMixin
from gallery_dl_app.management import ManagementMixin
from gallery_dl_app.reports import ReportsMixin
from gallery_dl_app.system_tools import SystemToolsMixin
from gallery_dl_app.ui_shell import UiShellMixin


class ArchitectureTests(unittest.TestCase):
    def test_release_metadata_matches_public_version(self):
        metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"]["name"], "gdlscrape")
        self.assertEqual(metadata["project"]["version"], package.__version__)

    def test_responsibilities_live_in_separate_mixins(self):
        self.assertIn("_build_ui", UiShellMixin.__dict__)
        self.assertIn("start_download", QueueControllerMixin.__dict__)
        self.assertIn("validate_config", SystemToolsMixin.__dict__)
        self.assertIn("export_audit_report", ReportsMixin.__dict__)
        self.assertIn("open_download_composer", ComposerMixin.__dict__)
        self.assertIn("open_command_builder", ComposerMixin.__dict__)
        self.assertIn("open_config_guide_dialog", ComposerMixin.__dict__)
        self.assertIn("_build_ui", DashboardUiMixin.__dict__)
        self.assertIn("open_management_center", ManagementMixin.__dict__)
        self.assertNotIn("start_download", package.MainWindow.__dict__)

    def test_worker_avoids_thread_unsafe_preexec_fn(self):
        # Resolve from the project root instead of relying on the current
        # package loader implementation.
        source = Path("gallery_dl_app/workers.py")
        text = source.read_text(encoding="utf-8")
        self.assertNotIn("preexec_fn=", text)
        self.assertIn("start_new_session=not IS_WINDOWS", text)

    def test_user_facing_runtime_text_does_not_depend_on_emoji_fonts(self):
        forbidden = set("▶✘✅❌⊘■⚠ℹ📁📋🗑⏸⏹🔁⛔⏳✓✔✕❗🔒🔓📦🧪⬇●⚙📊⚡🧰🛠🔎📘")
        for relative in (
            "gallery_dl_app/workers.py",
            "gallery_dl_app/system_tools.py",
            "gallery_dl_app/advanced_tools.py",
            "gallery_dl_app/ui_shell.py",
        ):
            text = Path(relative).read_text(encoding="utf-8")
            with self.subTest(file=relative):
                self.assertFalse(forbidden.intersection(text), forbidden.intersection(text))

    def test_gui_smoke_and_shortcuts(self):
        # A smoke test must not inherit the real user's autosave (language,
        # theme, compact mode, etc.), otherwise assertions vary by machine.
        with patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None):
            app, window = package.create_application([])
        try:
            window.show()
            app.processEvents()
            self.assertEqual(window.windowTitle(), "GdlScrape v1.0")
            self.assertEqual(package.__version__, "1.0")
            self.assertEqual(window.lbl_title.text(), "GDL")
            self.assertEqual(window.lbl_subtitle.text(), "SCRAPE")
            self.assertEqual(len(window._shortcuts), 6)
            self.assertIn("Ready", window.statusBar().currentMessage())
            self.assertEqual(
                [window.tabs.tabText(index) for index in range(window.tabs.count())],
                ["TRANSFER", "LOG", "WORKERS", "HISTORY"],
            )
            self.assertEqual(window.lbl_metric_completed.text(), "0 / 0")
            self.assertTrue(hasattr(window, "activity_graph"))
            self.assertEqual(
                [window.combo_theme.itemData(index) for index in range(window.combo_theme.count())],
                ["dark", "light"],
            )
            window.set_theme("light", persist=False)
            self.assertEqual(window.current_theme, "light")
            self.assertEqual(window.combo_theme.currentData(), "light")
            self.assertGreater(app.palette().color(QPalette.Window).lightness(), 200)
            native_filter = getattr(app, "_native_window_theme_filter", None)
            self.assertIsNotNone(native_filter)
            self.assertFalse(native_filter.dark)
            with patch("gallery_dl_app.themes.apply_native_window_theme") as apply_native:
                child_dialog = QDialog(window)
                child_dialog.show()
                app.processEvents()
                self.assertTrue(
                    any(
                        call.args[0] is child_dialog and call.args[1] is False
                        for call in apply_native.call_args_list
                    )
                )
                child_dialog.close()
            window.set_theme("dark", persist=False)
            self.assertLess(app.palette().color(QPalette.Window).lightness(), 80)
            self.assertTrue(native_filter.dark)
            described_controls = [
                window.combo_theme,
                window.txt_commands,
                window.spin_workers,
                window.btn_start,
                window.btn_action_builder,
                window.search_queue,
            ]
            self.assertTrue(all(widget.toolTip() for widget in described_controls))
            self.assertTrue(all(widget.statusTip() for widget in described_controls))
            self.assertEqual(
                [
                    window.btn_action_builder.text(),
                    window.btn_action_preview.text(),
                    window.btn_action_dedupe.text(),
                    window.btn_action_help.text(),
                ],
                ["COMPOSER", "PREVIEW", "DEDUPE", "HELP"],
            )
            self.assertIs(window.btn_action_builder, window.btn_action_composer)
            self.assertFalse(hasattr(window, "btn_action_config"))
            self.assertIs(window.open_command_builder.__func__, ComposerMixin.open_command_builder)
            self.assertIs(window.open_config_guide_dialog.__func__, ComposerMixin.open_config_guide_dialog)
            self.assertEqual([action.text() for action in window.menuBar().actions()], [])
            self.assertFalse(hasattr(window, "dashboard_stack"))
            self.assertFalse(hasattr(window, "side_tabs"))
            self.assertFalse(hasattr(window, "feature_dialog"))
            self.assertEqual(window.combo_archive.currentText(), "zip")
            window.jobs = [
                package.parse_line("https://example.com/a"),
                package.parse_line("https://example.com/b"),
            ]
            window.total_run = 2
            window.processed_run = 1
            window.done_indices = {0}
            window.results = {0: package.JobResult(0, "done", downloaded=4)}
            window._update_counts()
            self.assertEqual(window.lbl_metric_completed.text(), "1 / 2")
            self.assertEqual(window.lbl_metric_files.text(), "4")
        finally:
            window.close()
            app.processEvents()

    def test_main_icons_fit_without_icon_font_glyphs(self):
        with patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None):
            app, window = package.create_application([])
        try:
            window.show()
            app.processEvents()
            self.assertFalse(app.windowIcon().isNull())
            self.assertFalse(window.windowIcon().isNull())
            self.assertFalse(window.btn_cancel.isEnabled())
            self.assertEqual(window.btn_output.text(), "")
            self.assertLessEqual(window.btn_output.minimumWidth(), window.btn_output.maximumWidth())
            self.assertLessEqual(window.btn_output.width(), 48)
            for button in (
                window.btn_import,
                window.btn_paste,
                window.btn_clear_input,
                window.btn_output,
                window.btn_start,
                window.btn_pause,
                window.btn_cancel,
                window.btn_retry,
                window.btn_stop,
            ):
                self.assertFalse(button.icon().isNull(), button.text())
            for button in (window.btn_import, window.btn_paste, window.btn_clear_input):
                self.assertLessEqual(button.minimumSizeHint().width(), button.width())
            self.assertTrue(all(ord(character) < 128 for character in window.btn_start.text()))
            self.assertTrue(all(ord(character) < 128 for character in window.btn_stop.text()))
        finally:
            window.close()
            app.processEvents()

    def test_rate_presets_use_duration_values_supported_by_gallery_dl_129(self):
        with patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None):
            app, window = package.create_application([])
        try:
            rate_config = json.loads(window.gallery_dl_config_template_text("Rate"))
            rate_extractor = rate_config["extractor"]
            for key in ("sleep-retries", "sleep-429"):
                value = rate_extractor[key]
                duration = gallery_dl_util.build_duration_func(value)
                self.assertIsNotNone(duration)
                self.assertGreaterEqual(duration(), 0)

            definitions = window.gallery_dl_site_preset_definitions()
            for definition in definitions:
                value = definition.get("sleep-429")
                if value:
                    with self.subTest(site=definition["key"]):
                        duration = gallery_dl_util.build_duration_func(value)
                        self.assertIsNotNone(duration)
                        self.assertGreaterEqual(duration(), 0)

            guide = window.gallery_dl_command_guide_text("English")
            self.assertNotIn("--sleep-429 exp:", guide)
        finally:
            window.close()
            app.processEvents()

    def test_gui_live_simulation_completes_and_restores_controls(self):
        with (
            patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None),
            patch.object(ManagementMixin, "_offer_crash_recovery", lambda _self: None),
        ):
            app, window = package.create_application([])
        try:
            with tempfile.TemporaryDirectory() as folder:
                window.feature_store = FeatureStore(Path(folder) / "simulation.sqlite3")
                window.autosave_session = lambda: None
                window.notify_finished = lambda: None
                window.gdl_cmd = subprocess.list2cmdline(
                    [sys.executable, "-c", "print('C:/Simulated/output.jpg')"]
                )
                window.spin_workers.setValue(2)
                window.txt_commands.setPlainText(
                    "https://example.com/gallery/123 --simulate\n"
                    "https://example.com/gallery/456 --simulate"
                )
                self.assertTrue(window._start_download_now())
                self.assertTrue(window.btn_cancel.isEnabled())
                deadline = time.time() + 10
                while window.active_workers > 0 and time.time() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                app.processEvents()
                self.assertEqual(window.active_workers, 0)
                self.assertEqual(window.processed_run, 2)
                self.assertEqual(
                    [window.results[index].status for index in sorted(window.results)],
                    ["done", "done"],
                )
                self.assertTrue(window.btn_start.isEnabled())
                self.assertFalse(window.btn_pause.isEnabled())
                self.assertFalse(window.btn_cancel.isEnabled())
                self.assertFalse(window.btn_stop.isEnabled())
        finally:
            window.close()
            app.processEvents()

    def test_worker_start_failure_rolls_back_run_and_ui(self):
        with (
            patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None),
            patch.object(ManagementMixin, "_offer_crash_recovery", lambda _self: None),
        ):
            app, window = package.create_application([])
        try:
            with tempfile.TemporaryDirectory() as folder:
                window.feature_store = FeatureStore(Path(folder) / "start-failure.sqlite3")
                window.autosave_session = lambda: None
                window.notify_finished = lambda: None
                messages = []
                window.show_compact_message = lambda *args: messages.append(args)
                window.txt_commands.setPlainText("https://example.com/a")

                with patch(
                    "gallery_dl_app.queue_controller.DownloadWorker.start",
                    side_effect=RuntimeError("thread resource unavailable"),
                ):
                    started = window._start_download_now()

                self.assertFalse(started)
                self.assertEqual(window.active_workers, 0)
                self.assertIsNone(window.current_run_id)
                self.assertEqual(window.running_indices, set())
                self.assertEqual(window.feature_store.interrupted_runs(), [])
                self.assertEqual(window.results[0].status, "failed")
                self.assertEqual(window.results[0].errors, 1)
                self.assertEqual(window.task_queue.unfinished_tasks, 0)
                self.assertTrue(window.btn_start.isEnabled())
                self.assertFalse(window.btn_pause.isEnabled())
                self.assertFalse(window.btn_stop.isEnabled())
                self.assertEqual(messages[-1][0], "Start failed")
                with window.feature_store._connect() as db:
                    status = db.execute(
                        "SELECT status FROM runs ORDER BY id DESC LIMIT 1"
                    ).fetchone()[0]
                self.assertEqual(status, "start_failed")
        finally:
            window.close()
            app.processEvents()

    def test_partial_worker_start_failure_records_start_failed(self):
        with (
            patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None),
            patch.object(ManagementMixin, "_offer_crash_recovery", lambda _self: None),
        ):
            app, window = package.create_application([])
        try:
            with tempfile.TemporaryDirectory() as folder:
                window.feature_store = FeatureStore(Path(folder) / "partial-start.sqlite3")
                window.autosave_session = lambda: None
                window.notify_finished = lambda: None
                window.show_compact_message = lambda *_args: None
                window.gdl_cmd = subprocess.list2cmdline(
                    [sys.executable, "-c", "import time; time.sleep(10)"]
                )
                window.spin_workers.setValue(2)
                window.txt_commands.setPlainText(
                    "https://example.com/a\nhttps://example.com/b"
                )

                original_start = package.DownloadWorker.start
                calls = 0

                def fail_second_start(worker):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise RuntimeError("second worker unavailable")
                    return original_start(worker)

                with patch(
                    "gallery_dl_app.queue_controller.DownloadWorker.start",
                    new=fail_second_start,
                ):
                    started = window._start_download_now()

                deadline = time.time() + 5
                while any(worker.isRunning() for worker in window.workers) and time.time() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                app.processEvents()

                self.assertFalse(started)
                self.assertEqual(window.active_workers, 0)
                self.assertIsNone(window.current_run_id)
                self.assertEqual(window.feature_store.interrupted_runs(), [])
                self.assertEqual(set(window.results), {0, 1})
                self.assertTrue(all(result.status in {"failed", "stopped"} for result in window.results.values()))
                self.assertEqual(window.task_queue.unfinished_tasks, 0)
                self.assertTrue(window.btn_start.isEnabled())
                with window.feature_store._connect() as db:
                    status = db.execute(
                        "SELECT status FROM runs ORDER BY id DESC LIMIT 1"
                    ).fetchone()[0]
                self.assertEqual(status, "start_failed")
        finally:
            window.close()
            app.processEvents()

    def test_close_during_active_subprocess_leaves_no_worker_or_interrupted_run(self):
        with (
            patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None),
            patch.object(ManagementMixin, "_offer_crash_recovery", lambda _self: None),
        ):
            app, window = package.create_application([])
        closed = False
        try:
            with tempfile.TemporaryDirectory() as folder:
                window.feature_store = FeatureStore(Path(folder) / "close-race.sqlite3")
                window.autosave_session = lambda: None
                window.notify_finished = lambda: None
                window.gdl_cmd = subprocess.list2cmdline(
                    [
                        sys.executable,
                        "-c",
                        "import time; print('child-ready', flush=True); time.sleep(30)",
                    ]
                )
                window.show()
                app.processEvents()
                window.txt_commands.setPlainText("https://example.com/slow")
                self.assertTrue(window._start_download_now())

                deadline = time.time() + 5
                while not window.active_job_indices and time.time() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                self.assertEqual(window.active_job_indices, {0})

                window._force_quit = True
                with patch(
                    "gallery_dl_app.advanced_tools.QMessageBox.question",
                    return_value=QMessageBox.Yes,
                ):
                    started_closing = time.monotonic()
                    closed = window.close()
                app.processEvents()

                self.assertTrue(closed)
                self.assertLess(time.monotonic() - started_closing, 8)
                self.assertFalse(any(worker.isRunning() for worker in window.workers))
                self.assertEqual(window.feature_store.interrupted_runs(), [])
                with window.feature_store._connect() as db:
                    status = db.execute(
                        "SELECT status FROM runs ORDER BY id DESC LIMIT 1"
                    ).fetchone()[0]
                self.assertEqual(status, "stopped")
        finally:
            if not closed:
                window._force_quit = True
                window.close()
            app.processEvents()

    def test_scheduled_run_restores_manual_queue_after_completion(self):
        with (
            patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None),
            patch.object(ManagementMixin, "_offer_crash_recovery", lambda _self: None),
        ):
            app, window = package.create_application([])
        try:
            with tempfile.TemporaryDirectory() as folder:
                window.feature_store = FeatureStore(Path(folder) / "schedule-restore.sqlite3")
                window.autosave_session = lambda: None
                window.notify_finished = lambda: None
                window.gdl_cmd = subprocess.list2cmdline(
                    [sys.executable, "-c", "print('C:/Simulated/scheduled.jpg')"]
                )
                manual_queue = "https://example.com/manual-work"
                scheduled_queue = "https://example.com/scheduled-work --simulate"
                window.txt_commands.setPlainText(manual_queue)
                window.feature_store.save_schedule({
                    "name": "Queue preservation",
                    "command_text": scheduled_queue,
                    "frequency": "interval",
                    "interval_minutes": 60,
                    "next_run_at": time.time() - 1,
                    "enabled": True,
                })

                window._poll_schedules()
                self.assertEqual(window.txt_commands.toPlainText(), scheduled_queue)
                deadline = time.time() + 10
                while window.active_workers > 0 and time.time() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                app.processEvents()

                self.assertEqual(window.active_workers, 0)
                self.assertEqual(window.txt_commands.toPlainText(), manual_queue)
                self.assertEqual([job.url for job in window.jobs], [manual_queue])
                self.assertIsNone(window._scheduled_queue_restore)
        finally:
            window.close()
            app.processEvents()

    def test_management_probe_can_finish_after_dialog_closes(self):
        with patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None):
            app, window = package.create_application([])
        errors = []
        original_hook = sys.excepthook
        try:
            sys.excepthook = lambda *details: errors.append(details)
            window.gdl_cmd = subprocess.list2cmdline(
                [
                    sys.executable,
                    "-c",
                    "import time; time.sleep(0.1); print('  --simulate  dry run')",
                ]
            )
            window.open_management_center()
            app.processEvents()
            dialog = window._management_dialog
            self.assertIsNotNone(dialog)
            dialog.close()
            deadline = time.time() + 1
            while time.time() < deadline:
                app.processEvents()
                time.sleep(0.01)
            self.assertEqual(errors, [])
            self.assertIsNone(window._management_dialog)
        finally:
            sys.excepthook = original_hook
            window.close()
            app.processEvents()

    def test_scheduled_account_does_not_get_global_browser_cookie_override(self):
        with (
            patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None),
            patch.object(ManagementMixin, "_offer_crash_recovery", lambda _self: None),
        ):
            app, window = package.create_application([])
        try:
            with tempfile.TemporaryDirectory() as folder:
                window.feature_store = FeatureStore(Path(folder) / "schedule.sqlite3")
                account_id = window.feature_store.save_account(
                    {
                        "name": "Scheduled browser profile",
                        "site": "example",
                        "auth_kind": "browser",
                        "cookie_source": "firefox",
                    }
                )
                window.autosave_session = lambda: None
                window.notify_finished = lambda: None
                window.gdl_cmd = subprocess.list2cmdline(
                    [sys.executable, "-c", "print('C:/Simulated/account.jpg')"]
                )
                window.combo_cookies.setCurrentText("chrome")
                window.txt_commands.setPlainText("https://example.com/gallery/789 --simulate")
                self.assertTrue(window._rebuild_from_text())
                window._start_indices(
                    [0],
                    reset_result_sets=True,
                    run_source="schedule:1",
                    account_profile_id=account_id,
                    use_account_override=True,
                )
                self.assertEqual(window.workers[0].cookies_browser, "none")
                secure_config = Path(window._run_config_path)
                self.assertTrue(secure_config.exists())
                deadline = time.time() + 10
                while window.active_workers > 0 and time.time() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                app.processEvents()
                self.assertEqual(window.results[0].status, "done")
                self.assertFalse(secure_config.exists())
        finally:
            window.close()
            app.processEvents()

    def test_composer_contains_detailed_guide_and_flowchart(self):
        with patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None):
            app, window = package.create_application([])
        captured = {}

        def inspect_dialog(dialog):
            tab_sets = [
                [widget.tabText(index) for index in range(widget.count())]
                for widget in dialog.findChildren(QTabWidget)
            ]
            captured["tabs"] = max(tab_sets, key=len)
            charts = dialog.findChildren(ComposerFlowchart)
            captured["chart_name"] = charts[0].accessibleName() if charts else ""
            captured["has_steps"] = any(
                "Detailed steps" in label.text()
                for label in dialog.findChildren(QLabel)
            )
            stack = dialog.findChild(QStackedWidget, "authMethodStack")
            captured["auth_methods"] = stack.count() if stack else 0
            captured["has_oauth"] = any(
                button.text() == "Start OAuth"
                for button in dialog.findChildren(QPushButton)
            )
            captured["masked_secrets"] = sum(
                field.echoMode() == QLineEdit.Password
                for field in dialog.findChildren(QLineEdit)
            )
            return QDialog.Rejected

        try:
            with patch.object(QDialog, "exec", inspect_dialog):
                window.open_download_composer()
            self.assertEqual(
                captured["tabs"],
                ["How to use", "Download", "Options", "Login & Cookies", "Sites", "Preview"],
            )
            self.assertEqual(captured["chart_name"], "Download Composer usage flow")
            self.assertTrue(captured["has_steps"])
            self.assertEqual(captured["auth_methods"], 5)
            self.assertTrue(captured["has_oauth"])
            self.assertGreaterEqual(captured["masked_secrets"], 2)
        finally:
            window.close()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
