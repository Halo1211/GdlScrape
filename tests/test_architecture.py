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
    def test_command_preview_uses_account_config_without_removing_run_config(self):
        with (
            patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None),
            patch.object(SystemToolsMixin, "autosave_session", lambda _self: None),
            patch.object(ManagementMixin, "_start_management_services", lambda _self: None),
            tempfile.TemporaryDirectory() as folder,
        ):
            root = Path(folder)
            runtime = root / "runtime"
            app, window = package.create_application([])
            try:
                window.feature_store = FeatureStore(root / "features.sqlite3")
                window.active_account_profile_id = window.feature_store.save_account({
                    "name": "Preview profile", "site": "example", "auth_kind": "browser",
                    "cookie_source": "firefox",
                })
                window.combo_cookies.setCurrentText("chrome")
                window.config_path = None
                existing = root / "existing-run-config.json"
                existing.write_text("{}")
                window._run_config_path = str(existing)
                window.txt_commands.setPlainText("https://example.com/a")

                def inspect_preview(_title, message, _level):
                    configs = list(runtime.glob("secure-config-*.json"))
                    self.assertEqual(len(configs), 1)
                    config = json.loads(configs[0].read_text(encoding="utf-8"))
                    self.assertEqual(config["extractor"]["example"]["cookies"], ["firefox"])
                    self.assertIn("--config", message)
                    self.assertNotIn("--cookies-from-browser chrome", message)

                with (
                    patch("gallery_dl_app.management.SECURE_RUNTIME_DIR", runtime),
                    patch.object(window, "show_scroll_message", side_effect=inspect_preview),
                ):
                    window.command_preview()
                self.assertEqual(list(runtime.glob("secure-config-*.json")), [])
                self.assertTrue(existing.exists())
                self.assertEqual(window._run_config_path, str(existing))
            finally:
                window._run_config_path = None
                window.close()
                app.processEvents()

    def test_queue_review_preserves_real_ui_results_selection_and_disabled_jobs(self):
        from gallery_dl_app.models import JobResult

        with (
            patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None),
            patch.object(SystemToolsMixin, "autosave_session", lambda _self: None),
            patch.object(ManagementMixin, "_start_management_services", lambda _self: None),
        ):
            app, window = package.create_application([])
            try:
                window.txt_commands.setPlainText("https://example.com/a\nhttps://example.com/b")
                self.assertTrue(window._rebuild_from_text())
                window.results[0] = JobResult(0, "done", downloaded=3)
                window.done_indices.add(0)
                window.jobs[1].enabled = False
                window.populate_queue_table()
                window.tbl_queue.selectRow(1)
                self.assertTrue(window._rebuild_from_text())
                self.assertEqual(window.results[0].downloaded, 3)
                self.assertIn(0, window.done_indices)
                self.assertFalse(window.jobs[1].enabled)
                self.assertEqual(window.selected_indices(), [1])
                window.txt_commands.setPlainText("https://example.com/new")
                self.assertTrue(window._rebuild_from_text())
                self.assertEqual(window.results, {})
                self.assertEqual(window.done_indices, set())
            finally:
                window.close()
                app.processEvents()

    def test_pending_editor_insert_does_not_delete_a_different_selected_job(self):
        with (
            patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None),
            patch.object(SystemToolsMixin, "autosave_session", lambda _self: None),
            patch.object(ManagementMixin, "_start_management_services", lambda _self: None),
        ):
            app, window = package.create_application([])
            try:
                window.txt_commands.setPlainText("https://example.com/a\nhttps://example.com/b")
                window._rebuild_from_text()
                window.tbl_queue.selectRow(1)
                window.txt_commands.setPlainText(
                    "https://example.com/new\nhttps://example.com/a\nhttps://example.com/b"
                )
                self.assertTrue(window._text_debounce.isActive())
                window.delete_selected()
                self.assertEqual([job.url for job in window.jobs], [
                    "https://example.com/new", "https://example.com/a",
                ])
                self.assertEqual(
                    [job.url for job in package.parse_text_database(window.txt_commands.toPlainText())],
                    [job.url for job in window.jobs],
                )
            finally:
                window.close()
                app.processEvents()

    def test_release_metadata_matches_public_version(self):
        metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"]["name"], "gdlscrape")
        self.assertEqual(metadata["project"]["version"], package.__version__)
        windows_version = Path("packaging/version_info.txt").read_text(encoding="utf-8")
        self.assertIn(f"StringStruct('FileVersion', '{package.__version__}')", windows_version)
        self.assertIn(f"StringStruct('ProductVersion', '{package.__version__}')", windows_version)
        version_tuple = tuple(map(int, package.__version__.split("."))) + (0,)
        self.assertIn(f"filevers={version_tuple}", windows_version)
        self.assertIn(f"prodvers={version_tuple}", windows_version)

    def test_packaging_requires_gallery_dl_with_pawchive_support(self):
        metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
        requirements = Path("requirements.txt").read_text(encoding="utf-8")
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("gallery-dl>=1.32.14", metadata["project"]["dependencies"])
        self.assertIn("gallery-dl>=1.32.14", requirements.splitlines())
        self.assertIn("gallery-dl`](https://gdl-org.github.io/docs/) 1.32.14 or newer", readme)

    def test_standalone_build_bundles_dynamic_gallery_dl_extractors(self):
        import runpy
        from types import SimpleNamespace
        from unittest.mock import Mock

        analysis = Mock(return_value=SimpleNamespace(binaries=[], pure=[], scripts=[], datas=[]))
        packages = ("gallery_dl.extractor", "gallery_dl.downloader", "gallery_dl.postprocessor")
        with patch("PyInstaller.utils.hooks.collect_submodules", side_effect=lambda name: [name + ".fixture"]) as collect:
            runpy.run_path("packaging/GdlScrape.spec", init_globals={
                "SPECPATH": str(Path("packaging").resolve()), "Analysis": analysis,
                "PYZ": Mock(), "EXE": Mock(), "COLLECT": Mock(),
            })
        self.assertEqual([call.args[0] for call in collect.call_args_list], list(packages))
        self.assertEqual(analysis.call_args.kwargs["hiddenimports"], [name + ".fixture" for name in packages])

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
            self.assertEqual(window.windowTitle(), f"GdlScrape v{package.__version__}")
            self.assertEqual(window.lbl_app_version.text(), f"v{package.__version__}  •  DESKTOP")
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
                ["ADD DOWNLOADS", "PREVIEW", "DEDUPE", "HELP"],
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
            tabs = dialog.findChild(QTabWidget, "composerTabs")
            self.assertEqual(tabs.tabText(tabs.currentIndex()), "Start here")
            buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
            self.assertTrue(buttons["Add to Queue"].isHidden())
            self.assertFalse(buttons["Save config file"].isHidden())
            self.assertFalse(buttons["Save As..."].isHidden())
            self.assertIsNotNone(dialog.findChild(QPushButton, "configWebsiteSettingsButton"))
            self.assertTrue(any(chart.config_mode for chart in dialog.findChildren(ComposerFlowchart)))
            starter = dialog.findChild(QCheckBox, "configStarterHistory")
            starter.setChecked(True)
            summary = dialog.findChild(QPlainTextEdit, "configReviewSummary").toPlainText()
            self.assertIn("Download history file", summary)
            self.assertIn("sqlite3", summary)
        finally:
            window.close()
            app.processEvents()

    def test_website_settings_creates_config_at_displayed_default_location(self):
        with (
            patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None),
            patch.object(SystemToolsMixin, "autosave_session", lambda _self: None),
            patch.object(ManagementMixin, "_start_management_services", lambda _self: None),
            tempfile.TemporaryDirectory() as folder,
        ):
            config_folder = Path(folder) / "new-folder"
            target = config_folder / "config.json"
            app, window = package.create_application([])
            window.config_path = None

            def inspect_dialog(dialog):
                if dialog.objectName() != "siteConfigStudio":
                    dialog.findChild(QPushButton, "configWebsiteSettingsButton").click()
                    return QDialog.Rejected
                self.assertIn(str(target), dialog.findChild(QLabel, "siteConfigFilePath").text())
                picker = dialog.findChild(QComboBox, "siteOptionsSiteCombo")
                picker.setCurrentIndex(picker.findText("twitter"))
                dialog.findChild(QLineEdit, "siteOptionValue_timeout").setText("60")
                dialog.findChild(QPushButton, "siteConfigSave").click()
                self.assertEqual(dialog.result(), QDialog.Accepted)
                return QDialog.Accepted

            try:
                with (
                    patch.object(QDialog, "exec", inspect_dialog),
                    patch("gallery_dl_app.composer.APP_DIR", config_folder),
                    patch("gallery_dl_app.composer.detect_config_path", return_value=None),
                    patch.object(window, "show_compact_message"),
                    patch.object(window, "_refresh_env"),
                ):
                    window.open_config_builder()
                self.assertEqual(window.config_path, str(target))
                saved = json.loads(target.read_text(encoding="utf-8"))
                self.assertEqual(saved["extractor"]["twitter"]["timeout"], 60.0)
            finally:
                window.config_path = None
                window.close()
                app.processEvents()

    def test_website_settings_saves_all_sites_and_stays_open_on_failure(self):
        with (
            patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None),
            patch.object(SystemToolsMixin, "autosave_session", lambda _self: None),
            patch.object(ManagementMixin, "_start_management_services", lambda _self: None),
            tempfile.TemporaryDirectory() as folder,
        ):
            target = Path(folder) / "config.json"
            original = {"extractor": {"twitter": {"custom-setting": "keep"}}, "custom-root": 42}
            target.write_text(json.dumps(original), encoding="utf-8")
            app, window = package.create_application([])
            window.config_path = str(target)

            def inspect_dialog(dialog):
                if dialog.objectName() != "siteConfigStudio":
                    dialog.findChild(QPushButton, "configWebsiteSettingsButton").click()
                    return QDialog.Rejected
                self.assertIn(str(target), dialog.findChild(QLabel, "siteConfigFilePath").text())
                picker = dialog.findChild(QComboBox, "siteOptionsSiteCombo")
                picker.setCurrentIndex(picker.findText("twitter"))
                dialog.findChild(QLineEdit, "siteOptionValue_timeout").setText("invalid")
                dialog.findChild(QPushButton, "siteConfigSave").click()
                self.assertEqual(dialog.result(), QDialog.Rejected)
                self.assertEqual(json.loads(target.read_text(encoding="utf-8")), original)
                dialog.findChild(QLineEdit, "siteOptionValue_timeout").setText("60")
                picker.setCurrentIndex(picker.findText("imagefap"))
                dialog.findChild(QLineEdit, "siteOptionValue_timeout").setText("45")
                with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
                    dialog.findChild(QPushButton, "siteConfigSave").click()
                self.assertEqual(dialog.result(), QDialog.Rejected)
                self.assertEqual(json.loads(target.read_text(encoding="utf-8")), original)
                with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
                    dialog.findChild(QPushButton, "siteConfigSave").click()
                self.assertEqual(dialog.result(), QDialog.Accepted)
                return QDialog.Accepted

            try:
                with (
                    patch.object(QDialog, "exec", inspect_dialog),
                    patch.object(window, "show_compact_message"),
                    patch.object(window, "_refresh_env"),
                ):
                    window.open_config_builder()
                saved = json.loads(target.read_text(encoding="utf-8"))
                self.assertEqual(saved["extractor"]["twitter"]["timeout"], 60.0)
                self.assertEqual(saved["extractor"]["imagefap"]["timeout"], 45.0)
                self.assertEqual(saved["extractor"]["twitter"]["custom-setting"], "keep")
                self.assertEqual(saved["custom-root"], 42)
                backups = list(target.parent.glob("config.json.bak_*"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8")), original)
            finally:
                window.config_path = None
                window.close()
                app.processEvents()

    def test_full_catalog_saves_postprocessors_family_settings_and_page_overrides(self):
        with (
            patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None),
            patch.object(SystemToolsMixin, "autosave_session", lambda _self: None),
            patch.object(ManagementMixin, "_start_management_services", lambda _self: None),
            tempfile.TemporaryDirectory() as folder,
        ):
            target = Path(folder) / "config.json"
            target.write_text(json.dumps({"extractor": {"postprocessors": [{"name": "metadata", "filename": "keep.json"}]}}), encoding="utf-8")
            app, window = package.create_application([])
            window.config_path = str(target)

            def inspect_dialog(dialog):
                if dialog.objectName() != "siteConfigStudio":
                    dialog.findChild(QPushButton, "configWebsiteSettingsButton").click()
                    return QDialog.Rejected
                search = dialog.findChild(QLineEdit, "configReferenceSearch")
                value = dialog.findChild(QLineEdit, "configReferenceValue")
                kind = dialog.findChild(QComboBox, "configReferenceType")
                site = dialog.findChild(QComboBox, "configReferenceSite")
                apply = dialog.findChild(QPushButton, "setReferenceOption")
                search.setText("postprocessor.zip.compression")
                choice = dialog.findChild(QComboBox, "configReferenceChoice")
                choice.setCurrentIndex(choice.findData("zip"))
                apply.click()
                search.setText("extractor.[Danbooru].threshold")
                site.setCurrentIndex(site.findText("e621"))
                kind.setCurrentIndex(kind.findData("integer"))
                value.setText("75")
                apply.click()
                search.setText("extractor.*.timeout")
                site.setCurrentIndex(site.findText("twitter"))
                dialog.findChild(QLineEdit, "configReferenceSubcategory").setText("user")
                kind.setCurrentIndex(kind.findData("number"))
                value.setText("60")
                apply.click()
                summary = dialog.findChild(QPlainTextEdit, "siteConfigChangeSummary").toPlainText()
                self.assertIn("e621", summary)
                self.assertIn("twitter.user", summary)
                self.assertIn("zip", summary)
                search.setText("output.progress")
                choice.setCurrentIndex(choice.findText("Disabled"))
                dialog.findChild(QPushButton, "siteConfigSave").click()
                self.assertEqual(dialog.result(), QDialog.Accepted)
                return QDialog.Accepted

            try:
                with (
                    patch.object(QDialog, "exec", inspect_dialog),
                    patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
                    patch.object(window, "show_compact_message"),
                    patch.object(window, "_refresh_env"),
                ):
                    window.open_config_builder()
                saved = json.loads(target.read_text(encoding="utf-8"))
                self.assertEqual(saved["extractor"]["postprocessors"][0]["filename"], "keep.json")
                self.assertEqual(saved["extractor"]["postprocessors"][1], {"name": "zip", "compression": "zip"})
                self.assertEqual(saved["extractor"]["e621"]["threshold"], 75)
                self.assertEqual(saved["extractor"]["twitter"]["user"]["timeout"], 60.0)
                self.assertIs(saved["output"]["progress"], False)
            finally:
                window.config_path = None
                window.close()
                app.processEvents()

    def test_instagram_forms_and_catalog_drafts_survive_navigation_and_save(self):
        from gallery_dl_app.config_value_editor import StructuredConfigEditor

        with (
            patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None),
            patch.object(SystemToolsMixin, "autosave_session", lambda _self: None),
            patch.object(ManagementMixin, "_start_management_services", lambda _self: None),
            tempfile.TemporaryDirectory() as folder,
        ):
            target = Path(folder) / "config.json"
            target.write_text("{}", encoding="utf-8")
            app, window = package.create_application([])
            window.config_path = str(target)

            def inspect_dialog(dialog):
                if dialog.objectName() != "siteConfigStudio":
                    dialog.findChild(QPushButton, "configWebsiteSettingsButton").click()
                    return QDialog.Rejected
                site = dialog.findChild(QComboBox, "siteOptionsSiteCombo")
                site.setCurrentIndex(site.findText("instagram"))
                dialog.findChild(QCheckBox, "siteOptionList_include_reels").setChecked(True)
                video = dialog.findChild(QComboBox, "siteOptionChoice_videos")
                self.assertGreaterEqual(video.findData("merged"), 0)
                video.setCurrentIndex(video.findData("merged"))
                site_search = dialog.findChild(QLineEdit, "siteOptionsSearch")
                site_search.setText("archive-pragma")
                editor = dialog.findChild(StructuredConfigEditor, "siteOptionStructured_archive-pragma")
                self.assertEqual(editor.kind.count(), 1)
                self.assertEqual(editor.kind.currentData(), "list")
                editor.findChild(QPushButton, "structuredFillExample").click()
                self.assertEqual(editor.value(), ["journal_mode=WAL", "synchronous=NORMAL"])
                self.assertTrue(dialog.findChild(QCheckBox, "siteOptionAcknowledge_archive-pragma").isHidden())
                site_search.setText("previews")
                previews = dialog.findChild(StructuredConfigEditor, "siteOptionStructured_previews")
                self.assertIs(previews.value(), False)
                previews.kind.setCurrentIndex(previews.kind.findData("list"))
                previews.add_row(value="video")
                site_search.setText("extension-map")
                mapping = dialog.findChild(StructuredConfigEditor, "siteOptionStructured_extension-map")
                mapping.findChild(QPushButton, "structuredFillExample").click()
                mapping.table.cellWidget(0, 0).setText("jpe")
                self.assertEqual(mapping.value(), {"jpe": "jpg"})

                search = dialog.findChild(QLineEdit, "configReferenceSearch")
                value = dialog.findChild(QLineEdit, "configReferenceValue")
                search.setText("downloader.*.retries")
                value.setText("invalid")
                search.setText("output.progress")
                choice = dialog.findChild(QComboBox, "configReferenceChoice")
                choice.setCurrentIndex(choice.findText("Disabled"))
                search.setText("downloader.*.retries")
                self.assertEqual(value.text(), "invalid")
                search.setText("postprocessor.zip.compression")
                choice.setCurrentIndex(choice.findData("zip"))
                search.setText("postprocessor.metadata.filename")
                value.setText("info.json")
                dialog.findChild(QPushButton, "siteConfigSave").click()
                self.assertEqual(json.loads(target.read_text()), {})
                self.assertEqual(search.text(), "downloader.*.retries")
                self.assertEqual(value.text(), "invalid")
                value.setText("6")
                dialog.findChild(QPushButton, "siteConfigSave").click()
                self.assertEqual(dialog.result(), QDialog.Accepted)
                return QDialog.Accepted

            try:
                with (
                    patch.object(QDialog, "exec", inspect_dialog),
                    patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
                    patch.object(window, "show_compact_message"),
                    patch.object(window, "_refresh_env"),
                ):
                    window.open_config_builder()
                saved = json.loads(target.read_text(encoding="utf-8"))
                instagram = saved["extractor"]["instagram"]
                self.assertEqual(instagram["include"], ["posts", "reels"])
                self.assertEqual(instagram["videos"], "merged")
                self.assertEqual(instagram["previews"], ["video"])
                self.assertEqual(instagram["archive-pragma"], ["journal_mode=WAL", "synchronous=NORMAL"])
                self.assertEqual(instagram["extension-map"], {"jpe": "jpg"})
                self.assertIs(saved["output"]["progress"], False)
                self.assertEqual(saved["downloader"]["retries"], 6)
                self.assertEqual(saved["extractor"]["postprocessors"], [{"name": "zip", "compression": "zip"}, {"name": "metadata", "filename": "info.json"}])
            finally:
                window.config_path = None
                window.close()
                app.processEvents()

    def test_config_import_round_trip_and_guided_site_tasks_preserve_other_sites(self):
        from PySide6.QtWidgets import QFileDialog
        from gallery_dl_app.postprocessor_editor import PostprocessorEditor
        with (
            patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None),
            patch.object(SystemToolsMixin, "autosave_session", lambda _self: None),
            patch.object(ManagementMixin, "_start_management_services", lambda _self: None),
            tempfile.TemporaryDirectory() as folder,
        ):
            source = Path(folder) / "example.json"
            target = Path(folder) / "saved.json"
            original = {"extractor": {
                "pixiv": {"refresh-token": "test-private", "include": ["artworks", "avatar"], "postprocessors": [
                    {"name": "metadata", "mode": "json", "event": "post", "filename": "{title} [{id}].json", "indent": 2, "ascii": False}]},
                "pawchive": {"archive": "F:/history.sqlite3", "custom-old-option": True, "postprocessors": [
                    {"name": "metadata", "event": "post", "indent": 2, "ascii": False}]},
                "instagram": {"include": "posts,stories,reels,highlights", "sleep-request": "15-45"},
                "kemonoparty": {"archive": "F:/kemono.sqlite3", "duplicates": False},
            }, "output": {"progress": True, "colors": {"success": "1;32"}}, "netrc": False}
            source.write_text(json.dumps(original), encoding="utf-8")
            target.write_text("{}", encoding="utf-8")
            app, window = package.create_application([])
            window.config_path = str(target)
            state = {"second": False}
            def inspect_dialog(dialog):
                if dialog.objectName() != "siteConfigStudio":
                    dialog.findChild(QPushButton, "configStarterImport").click()
                    save = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Save config file")
                    save.click()
                    self.assertEqual(json.loads(target.read_text()), original)
                    state["second"] = True
                    dialog.findChild(QPushButton, "configWebsiteSettingsButton").click()
                    return QDialog.Rejected
                tabs = dialog.findChild(QTabWidget, "siteConfigTabs")
                site = dialog.findChild(QComboBox, "siteOptionsSiteCombo")
                site.setCurrentIndex(site.findText("pixiv"))
                goal = dialog.findChild(QComboBox, "siteOptionsGoal")
                goal.setCurrentIndex(goal.findData("after"))
                editor = dialog.findChild(PostprocessorEditor, "postprocessorEditor")
                self.assertIs(tabs.currentWidget(), editor)
                self.assertEqual(editor.site.currentData(), "pixiv")
                self.assertEqual(editor.event.currentData(), "post")
                self.assertEqual(editor.indent.value(), 2)
                editor.indent.setValue(4)
                editor.site.setCurrentIndex(editor.site.findData("pawchive"))
                self.assertEqual(editor.indent.value(), 2)
                tabs.setCurrentIndex(0)
                site.setCurrentIndex(site.findText("instagram"))
                goal.setCurrentIndex(goal.findData("history"))
                dialog.findChild(QCheckBox, "siteHistoryEnabled").setChecked(True)
                self.assertTrue(dialog.findChild(QLineEdit, "siteOptionValue_archive").text().endswith("instagram.sqlite3"))
                goal.setCurrentIndex(goal.findData("paths"))
                filename = dialog.findChild(QLineEdit, "pathRuleDestination_filename")
                filename.setText("{id}_{num:03d}.{extension}")
                self.assertIn("12345_001.jpg", dialog.findChild(QLabel, "pathRulePreview_filename").text())
                search = dialog.findChild(QLineEdit, "siteOptionsSearch")
                site.setCurrentIndex(site.findText("pawchive"))
                search.setText("custom-old-option")
                dialog.findChild(QCheckBox, "siteOptionCheck_custom-old-option").setChecked(False)
                dialog.findChild(QPushButton, "siteConfigSave").click()
                self.assertEqual(dialog.result(), QDialog.Accepted)
                return QDialog.Accepted
            try:
                with (
                    patch.object(QFileDialog, "getOpenFileName", return_value=(str(source), "")),
                    patch.object(QDialog, "exec", inspect_dialog),
                    patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
                    patch.object(window, "show_compact_message"),
                    patch.object(window, "_refresh_env"),
                ):
                    window.open_config_builder()
                self.assertTrue(state["second"])
                saved = json.loads(target.read_text())
                self.assertEqual(saved["extractor"]["pixiv"]["refresh-token"], "test-private")
                self.assertEqual(saved["extractor"]["pixiv"]["postprocessors"][0]["indent"], 4)
                self.assertEqual(saved["extractor"]["pawchive"]["postprocessors"], original["extractor"]["pawchive"]["postprocessors"])
                self.assertFalse(saved["extractor"]["pawchive"]["custom-old-option"])
                self.assertEqual(saved["extractor"]["instagram"]["include"], original["extractor"]["instagram"]["include"])
                self.assertTrue(saved["extractor"]["instagram"]["archive"].endswith("instagram.sqlite3"))
                self.assertEqual(saved["extractor"]["instagram"]["filename"], {"": "{id}_{num:03d}.{extension}"})
                self.assertEqual(saved["extractor"]["kemonoparty"], original["extractor"]["kemonoparty"])
                self.assertEqual(saved["output"], original["output"])
                self.assertEqual(json.loads(source.read_text()), original)
            finally:
                window.config_path = None
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
                    "assert '--config' in sys.argv, 'active OAuth config was not forwarded'\n"
                    "print('Copy the callback code', flush=True)\n"
                    "sys.stdout.write('code: '); sys.stdout.flush()\n"
                    "code = sys.stdin.readline().strip()\n"
                    "print(\"\\nYour 'refresh-token' is\\n\\n\" + 'composer-secret-' + code + '\\n', flush=True)\n",
                    encoding="utf-8",
                )
                window.gdl_cmd = subprocess.list2cmdline([sys.executable, "-u", str(helper)])
                config = Path(folder) / "active.json"
                config.write_text("{}", encoding="utf-8")
                window.config_path = str(config)

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
                advanced_tabs = dialog.findChild(QTabWidget, "siteConfigAdvancedTabs")
                captured["site_advanced_tabs"] = [
                    advanced_tabs.tabText(index) for index in range(advanced_tabs.count())
                ]
                captured["site_studio_fields"] = {
                    name: dialog.findChild(QLineEdit, name) is not None
                    for name in (
                        "siteArchivePath",
                        "advancedSiteOptionKey",
                    )
                }
                scope_picker = dialog.findChild(QComboBox, "configScopeCombo")
                site_picker = dialog.findChild(QComboBox, "configEditorSiteCombo")
                captured["detailed_config_editor"] = (
                    scope_picker is not None
                    and site_picker is not None
                    and site_picker.isEditable()
                    and scope_picker.currentData() == "site"
                    and site_picker.findText("instagram") >= 0
                    and site_picker.findText("twitter") >= 0
                    and dialog.findChild(QLabel, "siteConfigGuide") is not None
                    and dialog.findChild(QTableWidget, "configOptionTable") is not None
                )
                option_key = dialog.findChild(QComboBox, "configOptionKey")
                option_value = dialog.findChild(QComboBox, "configOptionValue")
                site_options = dialog.findChild(QComboBox, "siteOptionsSiteCombo")
                captured["all_sites_have_options_page"] = (
                    site_options is not None
                    and site_options.count() >= 200
                    and site_options.findText("imagefap") >= 0
                    and site_options.findText("twitter") >= 0
                )
                show_all = dialog.findChild(QCheckBox, "siteOptionsShowAll")
                captured["beginner_defaults"] = (
                    show_all is not None
                    and not show_all.isChecked()
                    and dialog.findChild(QLineEdit, "siteOptionValue_archive") is not None
                    and dialog.findChild(QLineEdit, "siteOptionValue_cookies") is None
                    and dialog.findChild(QPushButton, "siteOptionBrowse_archive") is not None
                    and any(
                        "Download history file" in label.text()
                        for label in dialog.findChildren(QLabel)
                    )
                )
                show_all.setChecked(True)
                captured["advanced_settings_available"] = dialog.findChild(
                    QLineEdit, "siteOptionValue_cookies"
                ) is not None
                show_all.setChecked(False)
                scope_picker.setCurrentIndex(scope_picker.findData("general"))
                option_key.setCurrentIndex(option_key.findData("retries"))
                option_value.setEditText("7")
                dialog.findChild(QPushButton, "setConfigOption").click()
                scope_picker.setCurrentIndex(scope_picker.findData("site"))
                site_picker.setEditText("twit")
                captured["site_search_waits_for_choice"] = not dialog.findChild(
                    QPushButton, "setConfigOption"
                ).isEnabled()
                site_picker.setCurrentIndex(site_picker.findText("twitter"))
                option_key.setCurrentIndex(option_key.findData("retries"))
                option_value.setEditText("5")
                dialog.findChild(QPushButton, "setConfigOption").click()
                site_picker.setCurrentIndex(site_picker.findText("pixiv"))
                option_key.setCurrentIndex(option_key.findData("metadata"))
                option_value.setEditText("true")
                dialog.findChild(QPushButton, "setConfigOption").click()
                site_options.setCurrentIndex(site_options.findText("twitter"))
                dialog.findChild(QLineEdit, "siteOptionsSearch").setText("replies")
                reply_control = dialog.findChild(QCheckBox, "siteOptionCheck_replies")
                captured["site_form_checkbox"] = reply_control is not None
                reply_control.setChecked(False)
                captured["form_waits_for_apply"] = "replies" not in dialog.findChild(
                    QPlainTextEdit, "siteConfigDraftPreview"
                ).toPlainText()
                dialog.findChild(QLineEdit, "siteOptionsSearch").setText("ads")
                dialog.findChild(QLineEdit, "siteOptionsSearch").setText("replies")
                captured["form_keeps_pending_change"] = not dialog.findChild(
                    QCheckBox, "siteOptionCheck_replies"
                ).isChecked()
                dialog.findChild(QPushButton, "applyGeneratedSiteSettings").click()
                site_options.setCurrentIndex(site_options.findText("deviantart"))
                captured["site_search_resets"] = not dialog.findChild(
                    QLineEdit, "siteOptionsSearch"
                ).text()
                dialog.findChild(QLineEdit, "siteOptionsSearch").setText("journals")
                captured["site_form_menu"] = dialog.findChild(
                    QComboBox, "siteOptionChoice_journals"
                ) is not None
                site_options.setCurrentIndex(site_options.findText("imagefap"))
                dialog.findChild(QLineEdit, "siteOptionsSearch").setText("archive")
                captured["imagefap_options"] = "imagefap:" in dialog.findChild(
                    QLabel, "siteOptionsStatus"
                ).text()
                dialog.findChild(QLineEdit, "siteOptionValue_archive").setText(
                    "E:/RIPS/Database/imagefap.sqlite3"
                )
                dialog.findChild(QPushButton, "applyGeneratedSiteSettings").click()
                dialog.findChild(QLineEdit, "siteArchivePath").setText(
                    "E:/RIPS/Database/kemono.sqlite3"
                )
                dialog.findChild(QPushButton, "applySiteArchive").click()
                site_options.setCurrentIndex(site_options.findText("reddit"))
                site_search = dialog.findChild(QLineEdit, "siteOptionsSearch")
                site_search.setText("client-id")
                client_id = dialog.findChild(QLineEdit, "siteOptionValue_client-id")
                captured["reddit_uses_site_form"] = client_id is not None
                client_id.setText("private-test-client-id")
                dialog.findChild(QCheckBox, "siteOptionAcknowledge_client-id").setChecked(True)
                dialog.findChild(QPushButton, "applyGeneratedSiteSettings").click()
                site_search.setText("user-agent-oauth")
                dialog.findChild(QLineEdit, "siteOptionValue_user-agent-oauth").setText(
                    f"Python:GdlScrape:v{package.__version__} (by /u/test)"
                )
                dialog.findChild(QPushButton, "applyGeneratedSiteSettings").click()
                site_options.setCurrentIndex(site_options.findText("pixiv"))
                site_search.setText("include")
                artwork_check = dialog.findChild(QCheckBox, "siteOptionList_include_artworks")
                avatar_check = dialog.findChild(QCheckBox, "siteOptionList_include_avatar")
                captured["pixiv_uses_site_form"] = artwork_check is not None and avatar_check is not None
                avatar_check.setChecked(True)
                dialog.findChild(QPushButton, "applyGeneratedSiteSettings").click()
                site_search.setText("tags")
                tags_picker = dialog.findChild(QComboBox, "siteOptionChoice_tags")
                tags_picker.setCurrentIndex(tags_picker.findData("translated"))
                dialog.findChild(QPushButton, "applyGeneratedSiteSettings").click()
                site_options.setCurrentIndex(site_options.findText("pixiv-novel"))
                site_search.setText("tags")
                novel_tags = dialog.findChild(QComboBox, "siteOptionChoice_tags")
                novel_tags.setCurrentIndex(novel_tags.findData("original"))
                dialog.findChild(QPushButton, "applyGeneratedSiteSettings").click()
                dialog.resize(860, 640)
                dialog.show()
                app.processEvents()
                dialog.hide()
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
                site_options.setCurrentIndex(site_options.findText("twitter"))
                dialog.findChild(QLineEdit, "siteOptionValue_timeout").setText("60")
                site_options.setCurrentIndex(site_options.findText("imagefap"))
                dialog.findChild(QLineEdit, "siteOptionValue_timeout").setText("45")
                dialog.findChild(QPushButton, "siteConfigDone").click()
                saved_on_return = json.loads(dialog.findChild(
                    QPlainTextEdit, "siteConfigDraftPreview"
                ).toPlainText())["extractor"]
                captured["done_applies_pending"] = (
                    saved_on_return["imagefap"]["timeout"] == 45.0
                    and saved_on_return["twitter"]["timeout"] == 60.0
                )
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
            guided_mode.setCurrentIndex(guided_mode.findData("tag"))
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
                ["How to use", "1. Links and destination", "2. Options for these jobs", "Login for these jobs", "Sites", "Preview"],
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
                ["Website Settings", "After downloading", "All gallery-dl settings", "Download History", "Review Changes", "More Tools"],
            )
            self.assertEqual(
                captured["site_advanced_tabs"],
                ["All Settings", "Custom Option"],
            )
            self.assertTrue(all(captured["site_studio_fields"].values()))
            self.assertTrue(captured["detailed_config_editor"])
            self.assertTrue(captured["all_sites_have_options_page"])
            self.assertTrue(captured["beginner_defaults"])
            self.assertTrue(captured["advanced_settings_available"])
            self.assertTrue(captured["done_applies_pending"])
            self.assertTrue(captured["imagefap_options"])
            self.assertTrue(captured["site_form_checkbox"])
            self.assertTrue(captured["site_form_menu"])
            self.assertTrue(captured["reddit_uses_site_form"])
            self.assertTrue(captured["pixiv_uses_site_form"])
            self.assertTrue(captured["site_search_resets"])
            self.assertTrue(captured["form_waits_for_apply"])
            self.assertTrue(captured["form_keeps_pending_change"])
            self.assertTrue(captured["site_search_waits_for_choice"])
            self.assertIn("E:/RIPS/Database/kemono.sqlite3", captured["site_studio_preview"])
            self.assertIn('"duplicates": false', captured["site_studio_preview"])
            self.assertIn('"user-agent-oauth"', captured["site_studio_preview"])
            self.assertIn('"client-id": "<hidden>"', captured["site_studio_preview"])
            self.assertIn('"retries": 7', captured["site_studio_preview"])
            self.assertEqual(json.loads(captured["site_studio_preview"])["extractor"]["twitter"]["retries"], 5)
            self.assertIs(json.loads(captured["site_studio_preview"])["extractor"]["twitter"]["replies"], False)
            self.assertEqual(
                json.loads(captured["site_studio_preview"])["extractor"]["imagefap"]["archive"],
                "E:/RIPS/Database/imagefap.sqlite3",
            )
            self.assertIn('"metadata": true', captured["site_studio_preview"])
            pixiv_preview = json.loads(captured["site_studio_preview"])
            self.assertEqual(pixiv_preview["extractor"]["pixiv"]["tags"], "translated")
            self.assertEqual(pixiv_preview["extractor"]["pixiv"]["include"], ["artworks", "avatar"])
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
