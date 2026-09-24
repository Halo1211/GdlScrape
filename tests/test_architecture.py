import json
import os
import socket
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
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
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
from gallery_dl_app.themes import action_icon
from gallery_dl_app.ui_shell import UiShellMixin


class ArchitectureTests(unittest.TestCase):
    def test_release_metadata_matches_public_version(self):
        metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"]["name"], "gdlscrape")
        self.assertEqual(metadata["project"]["version"], package.__version__)
        windows_version = Path("packaging/version_info.txt").read_text(encoding="utf-8")
        self.assertIn("StringStruct('FileVersion', '1.0.2')", windows_version)
        self.assertIn("StringStruct('ProductVersion', '1.0.2')", windows_version)

    def test_packaging_requires_gallery_dl_with_pawchive_support(self):
        metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
        requirements = Path("requirements.txt").read_text(encoding="utf-8")
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("gallery-dl>=1.32.10", metadata["project"]["dependencies"])
        self.assertIn("gallery-dl>=1.32.10", requirements.splitlines())
        self.assertIn("gallery-dl`](https://gdl-org.github.io/docs/) 1.32.10 or newer", readme)

    def test_standalone_build_bundles_dynamic_gallery_dl_extractors(self):
        spec = Path("packaging/GdlScrape.spec").read_text(encoding="utf-8")

        self.assertIn("collect_submodules", spec)
        self.assertIn('collect_submodules("gallery_dl.extractor")', spec)
        self.assertIn("hiddenimports=gallery_dl_hiddenimports", spec)

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
            self.assertEqual(window.windowTitle(), "GdlScrape v1.0.2")
            self.assertEqual(package.__version__, "1.0.2")
            self.assertEqual(window.lbl_app_version.text(), "v1.0.2  •  DESKTOP")
            self.assertGreater(window._stable_table_font().pointSizeF(), 0)
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
            dark_icon = window.windowIcon().pixmap(64, 64).toImage()
            window.set_theme("light", persist=False)
            self.assertEqual(window.current_theme, "light")
            self.assertEqual(window.combo_theme.currentData(), "light")
            self.assertGreater(app.palette().color(QPalette.Window).lightness(), 200)
            light_icon = window.windowIcon().pixmap(64, 64).toImage()
            self.assertNotEqual(dark_icon, light_icon)
            self.assertFalse(window.lbl_brand_mark.pixmap().isNull())
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
            self.assertEqual(window.windowIcon().pixmap(64, 64).toImage(), dark_icon)
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
            self.assertEqual(window.btn_action_config.text(), "CONFIG")
            self.assertTrue(window.btn_action_config.toolTip())
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
            window.resize(1000, 640)
            app.processEvents()
            self.assertLessEqual(window.minimumSizeHint().width(), window.width())
            self.assertFalse(window.lbl_brand_tagline.isVisible())
            self.assertFalse(window.lbl_app_version.isVisible())
        finally:
            window.close()
            app.processEvents()

    def test_worker_replacement_updates_count_and_worker_log_filter(self):
        with patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None):
            app, window = package.create_application([])
        try:
            window.jobs = [package.parse_line(f"https://example.com/{index}") for index in range(3)]
            window.populate_queue_table()
            window.active_workers = 3
            window.active_job_indices = {0, 1}
            window._update_counts()
            self.assertEqual(window.lbl_active_workers.text(), "2 / 3")
            with patch.object(window.feature_store, "mark_run_item"):
                window.on_job_started(2, 2, "https://example.com/2")
            self.assertEqual(window.lbl_active_workers.text(), "3 / 3")
            window.combo_log_filter.setCurrentText("Worker 3")
            window.on_worker_log(1, "other worker")
            window.on_worker_log(2, "downloaded 25 files")
            window.refresh_log_view()
            self.assertIn("downloaded 25 files", window.log_all.toPlainText())
            self.assertNotIn("other worker", window.log_all.toPlainText())
        finally:
            window.active_workers = 0
            window.close()
            app.processEvents()

    def test_worker_status_updates_do_not_reparse_queue_for_duplicates(self):
        with patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None):
            app, window = package.create_application([])
        try:
            window.jobs = [package.parse_line(f"https://example.com/{index}") for index in range(100)]
            window._duplicate_count_dirty = True
            with patch("gallery_dl_app.queue_controller.find_exact_duplicate_groups", return_value=[]) as grouped:
                for _ in range(5):
                    window._update_counts()
                self.assertEqual(grouped.call_count, 1)
                window._duplicate_count_dirty = True
                window._update_counts()
                self.assertEqual(grouped.call_count, 2)
        finally:
            window.close()
            app.processEvents()

    def test_message_box_icon_keeps_its_natural_width(self):
        with patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None):
            app, window = package.create_application([])
        try:
            message = QMessageBox(
                QMessageBox.Information, "Runtime",
                "Wait for downloads or the current runtime operation to finish.",
                QMessageBox.Ok, window,
            )
            message.show()
            app.processEvents()
            icon = message.findChild(QLabel, "qt_msgboxex_icon_label")
            self.assertIsNotNone(icon)
            self.assertLessEqual(icon.width(), 64)
            self.assertLessEqual(message.width(), 520)
            message.close()
        finally:
            window.close()
            app.processEvents()

    def test_all_bundled_action_icons_have_visible_pixels(self):
        with patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None):
            app, window = package.create_application([])
        try:
            for dark in (True, False):
                for name in (
                    "open", "folder", "paste", "trash", "download", "pause",
                    "cancel", "retry", "stop", "info", "warning", "error",
                ):
                    image = action_icon(name, dark=dark).pixmap(20, 20).toImage()
                    opaque = sum(
                        image.pixelColor(x, y).alpha() > 100
                        for x in range(image.width())
                        for y in range(image.height())
                    )
                    self.assertGreater(opaque, 12, (name, dark))
        finally:
            window.close()
            app.processEvents()

    def test_spinbox_and_combobox_arrows_render_in_dark_theme(self):
        app, window = package.create_application([])
        controls = [QSpinBox(), QComboBox()]
        controls[1].addItem("none")
        try:
            for control in controls:
                control.setStyleSheet(package.DARK_QSS)
                control.resize(180, 34)
                control.show()
                app.processEvents()
                image = control.grab().toImage()
                bright_arrow_pixels = sum(
                    all(value > 160 for value in (
                        image.pixelColor(x, y).red(),
                        image.pixelColor(x, y).green(),
                        image.pixelColor(x, y).blue(),
                    ))
                    for x in range(image.width() - 20, image.width() - 3)
                    for y in range(3, image.height() - 3)
                )
                self.assertGreater(bright_arrow_pixels, 5, type(control).__name__)
        finally:
            for control in controls:
                control.close()
            window.close()
            app.processEvents()

    def test_config_builder_opens_in_config_only_mode(self):
        with patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None):
            app, window = package.create_application([])
        dialogs = []
        try:
            with patch.object(QDialog, "exec", lambda dialog: dialogs.append(dialog) or 0):
                window.open_config_builder()
            self.assertEqual(len(dialogs), 1)
            dialog = dialogs[0]
            self.assertEqual(dialog.windowTitle(), "Config Builder")
            buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
            self.assertTrue(buttons["Add to Queue"].isHidden())
            self.assertFalse(buttons["Save Defaults"].isHidden())
            self.assertFalse(buttons["Save As..."].isHidden())
        finally:
            window.close()
            app.processEvents()

    def test_packaged_app_uses_its_bundled_downloader_after_session_restore(self):
        with patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None):
            app, window = package.create_application([])
        try:
            with tempfile.TemporaryDirectory() as folder:
                app_exe = Path(folder) / "GdlScrape.exe"
                bundled = Path(folder) / "gallery-dl.exe"
                app_exe.touch()
                bundled.touch()
                with patch("gallery_dl_app.system_tools.sys.executable", str(app_exe)), patch(
                    "gallery_dl_app.system_tools.sys.frozen", True, create=True
                ):
                    window.apply_session_data({"gdl_cmd": "C:/old/gallery-dl.exe", "commands": ""})
                    self.assertEqual(Path(window.gdl_cmd).resolve(), bundled.resolve())
                    runtime = window._build_runtime_tab()
                    self.assertFalse(runtime.findChildren(QPushButton))
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

    def test_pixiv_oauth_prompt_is_live_and_code_reaches_child_stdin(self):
        with patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None):
            app, window = package.create_application([])
        page = None
        try:
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                helper = root / "fake_pixiv_oauth.py"
                helper.write_text(
                    "import sys\n"
                    "assert 'extractor.input=true' in sys.argv\n"
                    "sys.stdout.write('1) Open Developer Tools and copy the code\\n')\n"
                    "sys.stdout.write('code: ')\n"
                    "sys.stdout.flush()\n"
                    "code = sys.stdin.readline().strip()\n"
                    "sys.stdout.write(\"\\nYour 'refresh-token' is\\n\\n\" + 'fake-refresh-' + code + '\\n\\n')\n"
                    "sys.stdout.flush()\n",
                    encoding="utf-8",
                )
                window.feature_store = FeatureStore(root / "oauth.sqlite3")
                window.gdl_cmd = subprocess.list2cmdline([sys.executable, "-u", str(helper)])
                page = ManagementMixin._build_accounts_tab(window)
                page.findChild(QLineEdit, "accountCachePath").setText(str(root / "private-cache.sqlite3"))
                method = page.findChild(QComboBox, "accountMethodCombo")
                method.setCurrentIndex(method.findData("oauth"))
                site = page.findChild(QComboBox, "accountSiteCombo")
                site.setCurrentIndex(site.findData("pixiv"))
                output = page.findChild(QPlainTextEdit, "accountProcessOutput")
                page.findChild(QPushButton, "accountOAuthButton").click()
                deadline = time.time() + 5
                while "code: " not in output.toPlainText() and time.time() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                self.assertIn("Open Developer Tools", output.toPlainText())
                self.assertIn("code: ", output.toPlainText())
                self.assertNotIn("refresh-token", output.toPlainText())
                page.findChild(QLineEdit, "accountOAuthResponse").setText("example-code")
                page.findChild(QPushButton, "accountOAuthSend").click()
                while "finished with exit code" not in output.toPlainText() and time.time() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                self.assertIn("finished with exit code 0", output.toPlainText())
                self.assertIn(package.REDACTED, output.toPlainText())
                self.assertNotIn("fake-refresh-example-code", output.toPlainText())
        finally:
            if page is not None:
                for process in page.findChildren(QProcess):
                    if process.state() != QProcess.NotRunning:
                        process.kill()
                        process.waitForFinished(1000)
                page.deleteLater()
            window.close()
            app.processEvents()

    def test_deviantart_oauth_callback_is_delivered_from_account_ui(self):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", 6414))
            except OSError:
                self.skipTest("OAuth callback port 6414 is in use")
        with patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None):
            app, window = package.create_application([])
        page = None
        try:
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                helper = root / "fake_callback_oauth.py"
                helper.write_text(
                    "import socket\n"
                    "with socket.socket() as server:\n"
                    "    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
                    "    server.bind(('127.0.0.1', 6414))\n"
                    "    server.listen(1)\n"
                    "    server.settimeout(5)\n"
                    "    print('Waiting for response', flush=True)\n"
                    "    conn, _ = server.accept()\n"
                    "    with conn:\n"
                    "        request = conn.recv(4096)\n"
                    "        assert b'GET /?state=abc&code=xyz ' in request\n"
                    "        conn.sendall(b'HTTP/1.1 200 OK\\r\\nContent-Length: 2\\r\\n\\r\\nOK')\n"
                    "print('Callback accepted', flush=True)\n",
                    encoding="utf-8",
                )
                window.feature_store = FeatureStore(root / "oauth.sqlite3")
                window.gdl_cmd = subprocess.list2cmdline([sys.executable, "-u", str(helper)])
                page = ManagementMixin._build_accounts_tab(window)
                page.findChild(QLineEdit, "accountCachePath").setText(str(root / "private-cache.sqlite3"))
                method = page.findChild(QComboBox, "accountMethodCombo")
                method.setCurrentIndex(method.findData("oauth"))
                site = page.findChild(QComboBox, "accountSiteCombo")
                site.setCurrentIndex(site.findData("deviantart"))
                output = page.findChild(QPlainTextEdit, "accountProcessOutput")
                page.findChild(QPushButton, "accountOAuthButton").click()
                deadline = time.time() + 5
                while "Waiting for response" not in output.toPlainText() and time.time() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                self.assertIn("Waiting for response", output.toPlainText())
                page.findChild(QLineEdit, "accountOAuthResponse").setText(
                    "https://mikf.github.io/gallery-dl/oauth-redirect.html?state=abc&code=xyz"
                )
                page.findChild(QPushButton, "accountOAuthSend").click()
                while "finished with exit code" not in output.toPlainText() and time.time() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                self.assertIn("Callback accepted", output.toPlainText())
                self.assertIn("finished with exit code 0", output.toPlainText())
        finally:
            if page is not None:
                for process in page.findChildren(QProcess):
                    if process.state() != QProcess.NotRunning:
                        process.kill()
                        process.waitForFinished(1000)
                page.deleteLater()
            window.close()
            app.processEvents()

    def test_composer_pixiv_oauth_accepts_code_without_console(self):
        with patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None):
            app, window = package.create_application([])
        dialogs = []
        try:
            with tempfile.TemporaryDirectory() as folder:
                helper = Path(folder) / "fake_composer_oauth.py"
                helper.write_text(
                    "import sys\n"
                    "assert 'extractor.input=true' in sys.argv\n"
                    "print('Copy the callback code', flush=True)\n"
                    "sys.stdout.write('code: '); sys.stdout.flush()\n"
                    "code = sys.stdin.readline().strip()\n"
                    "print(\"\\nYour 'refresh-token' is\\n\\n\" + 'composer-secret-' + code + '\\n', flush=True)\n",
                    encoding="utf-8",
                )
                window.gdl_cmd = subprocess.list2cmdline([sys.executable, "-u", str(helper)])

                def inspect(dialog):
                    dialogs.append(dialog)
                    dialog.findChild(QComboBox, "oauthSiteCombo").setCurrentIndex(0)
                    start = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Start OAuth")
                    start.click()
                    status = dialog.findChild(QPlainTextEdit, "composerOAuthStatus")
                    deadline = time.time() + 5
                    while "code: " not in status.toPlainText() and time.time() < deadline:
                        app.processEvents()
                        time.sleep(0.01)
                    self.assertIn("Copy the callback code", status.toPlainText())
                    self.assertIn("code: ", status.toPlainText())
                    # The running OAuth flow belongs to Pixiv even if the
                    # user changes the site picker before submitting.
                    dialog.findChild(QComboBox, "oauthSiteCombo").setCurrentText("reddit")
                    dialog.findChild(QLineEdit, "composerOAuthResponse").setText("test-code")
                    dialog.findChild(QPushButton, "composerOAuthSend").click()
                    while "finished with exit code" not in status.toPlainText() and time.time() < deadline:
                        app.processEvents()
                        time.sleep(0.01)
                    self.assertIn("finished with exit code 0", status.toPlainText())
                    self.assertIn(package.REDACTED, status.toPlainText())
                    self.assertNotIn("composer-secret-test-code", status.toPlainText())
                    return QDialog.Rejected

                with patch.object(QDialog, "exec", inspect):
                    window.open_download_composer()
        finally:
            for dialog in dialogs:
                for process in dialog.findChildren(QProcess):
                    if process.state() != QProcess.NotRunning:
                        process.kill()
                        process.waitForFinished(1000)
                dialog.deleteLater()
            window.close()
            app.processEvents()

    def test_managed_runtime_reports_unusable_runtime_directory(self):
        with patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None):
            app, window = package.create_application([])
        errors = []
        original_hook = sys.excepthook
        try:
            with tempfile.TemporaryDirectory() as folder:
                runtime_path = Path(folder) / "runtime"
                runtime_path.write_text("occupied", encoding="utf-8")
                page = ManagementMixin._build_runtime_tab(window)
                install = next(
                    button
                    for button in page.findChildren(QPushButton)
                    if button.text() == "Install / Update Managed Runtime"
                )
                sys.excepthook = lambda *details: errors.append(details)
                with (
                    patch("gallery_dl_app.management.SECURE_RUNTIME_DIR", runtime_path),
                    patch(
                        "gallery_dl_app.management.QMessageBox.question",
                        return_value=QMessageBox.Yes,
                    ),
                    patch.object(window, "show_compact_message") as show_message,
                ):
                    install.click()
                    app.processEvents()

                self.assertEqual(errors, [])
                show_message.assert_called_once()
                self.assertEqual(show_message.call_args.args[2], "error")
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
            if dialog.objectName() == "siteConfigStudio":
                studio_tabs = dialog.findChild(QTabWidget, "siteConfigTabs")
                captured["site_studio_tabs"] = [
                    studio_tabs.tabText(index) for index in range(studio_tabs.count())
                ]
                captured["site_studio_fields"] = {
                    name: dialog.findChild(QLineEdit, name) is not None
                    for name in (
                        "siteArchivePath",
                        "redditClientId",
                        "redditOAuthUserAgent",
                        "pixivRefreshToken",
                        "pixivPhpsessid",
                        "advancedSiteOptionKey",
                    )
                }
                scope_picker = dialog.findChild(QComboBox, "configScopeCombo")
                site_picker = dialog.findChild(QComboBox, "configEditorSiteCombo")
                captured["detailed_config_editor"] = (
                    scope_picker is not None
                    and site_picker is not None
                    and not site_picker.isEditable()
                    and dialog.findChild(QTableWidget, "configOptionTable") is not None
                )
                option_key = dialog.findChild(QComboBox, "configOptionKey")
                option_value = dialog.findChild(QComboBox, "configOptionValue")
                option_key.setCurrentIndex(option_key.findData("retries"))
                option_value.setEditText("7")
                dialog.findChild(QPushButton, "setConfigOption").click()
                scope_picker.setCurrentIndex(scope_picker.findData("site"))
                site_picker.setCurrentText("pixiv")
                option_key.setCurrentIndex(option_key.findData("metadata"))
                option_value.setEditText("true")
                dialog.findChild(QPushButton, "setConfigOption").click()
                dialog.findChild(QLineEdit, "siteArchivePath").setText(
                    "E:/RIPS/Database/kemono.sqlite3"
                )
                dialog.findChild(QPushButton, "applySiteArchive").click()
                dialog.findChild(QLineEdit, "redditClientId").setText(
                    "private-test-client-id"
                )
                dialog.findChild(QLineEdit, "redditOAuthUserAgent").setText(
                    "Python:GdlScrape:v1.0.2 (by /u/test)"
                )
                dialog.findChild(QPushButton, "applyRedditSettings").click()
                pixiv_tab = next(index for index in range(studio_tabs.count()) if studio_tabs.tabText(index) == "Pixiv")
                studio_tabs.setCurrentIndex(pixiv_tab)
                dialog.resize(860, 640)
                dialog.show()
                app.processEvents()
                artwork_check = dialog.findChild(QCheckBox, "pixivInclude_artworks")
                avatar_check = dialog.findChild(QCheckBox, "pixivInclude_avatar")
                self.assertEqual(artwork_check.y(), avatar_check.y())
                include_card = artwork_check.parentWidget()
                self.assertGreater(avatar_check.x(), artwork_check.x())
                self.assertLess(avatar_check.geometry().right(), include_card.width())
                self.assertGreater(include_card.width(), dialog.width() * 0.75)
                art_card = dialog.findChild(QComboBox, "pixivTags").parentWidget()
                novel_card = dialog.findChild(QComboBox, "pixivNovelTags").parentWidget()
                self.assertGreater(art_card.width(), dialog.width() * 0.35)
                self.assertGreater(novel_card.width(), dialog.width() * 0.35)
                dialog.hide()
                dialog.findChild(QComboBox, "pixivTags").setCurrentText("translated")
                dialog.findChild(QComboBox, "pixivNovelTags").setCurrentText("original")
                dialog.findChild(QPushButton, "applyPixivSettings").click()
                reference_search = dialog.findChild(QLineEdit, "configReferenceSearch")
                reference_search.setText("output.progress")
                dialog.findChild(QComboBox, "configReferenceType").setCurrentIndex(
                    dialog.findChild(QComboBox, "configReferenceType").findData("boolean")
                )
                dialog.findChild(QLineEdit, "configReferenceValue").setText("false")
                dialog.findChild(QPushButton, "setReferenceOption").click()
                captured["site_studio_preview"] = dialog.findChild(
                    QPlainTextEdit, "siteConfigDraftPreview"
                ).toPlainText()
                return QDialog.Rejected
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
            oauth_picker = dialog.findChild(QComboBox, "oauthSiteCombo")
            captured["oauth_picker"] = (
                oauth_picker is not None
                and not oauth_picker.isEditable()
                and oauth_picker.count() == 7
                and dialog.findChild(QLineEdit, "oauthMastodonInstance") is not None
            )
            captured["has_site_studio"] = dialog.findChild(
                QPushButton, "siteConfigStudioButton"
            ) is not None
            guided_site = dialog.findChild(QComboBox, "guidedUrlSite")
            guided_mode = dialog.findChild(QComboBox, "guidedUrlMode")
            guided_target = dialog.findChild(QLineEdit, "guidedUrlTarget")
            guided_site.setCurrentText("hypnohub")
            guided_mode.setCurrentText("tag")
            guided_target.setText("sleepy cat")
            dialog.findChild(QPushButton, "guidedUrlAdd").click()
            captured["guided_url"] = any(
                "https://hypnohub.net/index.php?page=post&s=list&tags=sleepy+cat" in editor.toPlainText()
                for editor in dialog.findChildren(QPlainTextEdit)
            )
            captured["masked_secrets"] = sum(
                field.echoMode() == QLineEdit.Password
                for field in dialog.findChildren(QLineEdit)
            )
            dialog.findChild(QPushButton, "siteConfigStudioButton").click()
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
            self.assertTrue(captured["oauth_picker"])
            self.assertTrue(captured["has_site_studio"])
            self.assertTrue(captured["guided_url"])
            self.assertEqual(
                captured["site_studio_tabs"],
                ["All Options", "Full Reference", "Archive", "Reddit", "Pixiv", "Advanced", "Safe Preview"],
            )
            self.assertTrue(all(captured["site_studio_fields"].values()))
            self.assertTrue(captured["detailed_config_editor"])
            self.assertIn("E:/RIPS/Database/kemono.sqlite3", captured["site_studio_preview"])
            self.assertIn('"duplicates": false', captured["site_studio_preview"])
            self.assertIn('"user-agent-oauth"', captured["site_studio_preview"])
            self.assertIn('"client-id": "<hidden>"', captured["site_studio_preview"])
            self.assertIn('"retries": 7', captured["site_studio_preview"])
            self.assertIn('"metadata": true', captured["site_studio_preview"])
            pixiv_preview = json.loads(captured["site_studio_preview"])
            self.assertEqual(pixiv_preview["extractor"]["pixiv"]["tags"], "translated")
            self.assertEqual(pixiv_preview["extractor"]["pixiv"]["include"], ["artworks"])
            self.assertEqual(pixiv_preview["extractor"]["pixiv-novel"]["tags"], "original")
            self.assertNotIn("covers", pixiv_preview["extractor"]["pixiv"])
            self.assertIs(pixiv_preview["output"]["progress"], False)
            self.assertNotIn("private-test-client-id", captured["site_studio_preview"])
            self.assertGreaterEqual(captured["masked_secrets"], 2)
        finally:
            window.close()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
