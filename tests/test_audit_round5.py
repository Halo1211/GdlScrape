"""Regressions for profile authentication, persistent queues and management failures."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QTableWidget

from gallery_dl_app.composer import ComposerState, _read_json_config, build_composer_argv
from gallery_dl_app.advanced_tools import AdvancedToolsMixin
from gallery_dl_app.core import parse_text_database, validate_cookies_txt
from gallery_dl_app.feature_store import FeatureStore
from gallery_dl_app.management import ManagementMixin
from gallery_dl_app.system_tools import SystemToolsMixin


class ProfileConfigTests(unittest.TestCase):
    def test_api_profile_keeps_username_required_for_password_style_api_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            target = SimpleNamespace(
                active_account_profile_id=1, config_path=None,
                cleanup_run_account_config=lambda: None,
                feature_store=SimpleNamespace(account=lambda _: {
                    "site": "danbooru", "auth_kind": "api_key", "username": "fixture-user",
                    "secret_ref": "fixture-ref", "secret_key": "password",
                }),
                secret_vault=SimpleNamespace(get=lambda _: "fixture-key"),
            )
            with patch("gallery_dl_app.management.SECURE_RUNTIME_DIR", Path(folder)):
                path = ManagementMixin.prepare_active_account_config(target)
            settings = json.loads(Path(path).read_text())["extractor"]["danbooru"]
            self.assertEqual(settings["username"], "fixture-user")
            self.assertEqual(settings["password"], "fixture-key")


class PersistentQueueTests(unittest.TestCase):
    def test_session_during_scheduled_run_preserves_manual_queue(self):
        target = MagicMock()
        target.gdl_cmd = "gallery-dl"
        target.config_path = None
        target.current_theme = "dark"
        target._scheduled_queue_restore = "# Manual\nhttps://example.com/manual --password fixture-secret"
        target.txt_commands.toPlainText.return_value = "https://example.com/scheduled"
        result = SystemToolsMixin.session_data(target)
        self.assertIn("/manual", result["commands"])
        self.assertNotIn("/scheduled", result["commands"])
        self.assertNotIn("fixture-secret", result["commands"])

    def test_session_without_scheduled_snapshot_uses_current_editor(self):
        target = MagicMock()
        target.gdl_cmd = "gallery-dl"
        target.config_path = None
        target.current_theme = "dark"
        target._scheduled_queue_restore = None
        target.txt_commands.toPlainText.return_value = "https://example.com/manual"
        self.assertEqual(SystemToolsMixin.session_data(target)["commands"], "https://example.com/manual")


class ManagementFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = FeatureStore(Path(self.temp.name) / "features.sqlite3")
        self.editor = QPlainTextEdit("https://example.com/manual")
        self.target = SimpleNamespace(
            feature_store=self.store, txt_commands=self.editor, _table=ManagementMixin._table,
            active_workers=0, active_account_profile_id=None, _management_dialog=None,
            jobs=parse_text_database(self.editor.toPlainText()),
            _rebuild_from_text=MagicMock(return_value=True), append_log=MagicMock(),
        )
        self.pages = []

    def tearDown(self):
        for page in self.pages:
            page.deleteLater()
        self.editor.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def library_page(self):
        self.store.add_library_entry(url="https://example.com/stored", command="https://example.com/stored")
        page = ManagementMixin._build_library_tab(self.target)
        self.pages.append(page)
        page.findChild(QTableWidget).selectRow(0)
        return page

    @staticmethod
    def click(page, name):
        next(button for button in page.findChildren(QPushButton) if button.text() == name).click()

    def test_library_database_errors_are_reported_without_escaping_qt_slots(self):
        for action, method in (("Queue Selected", "list_library"), ("Delete Selected", "delete_library_entries")):
            with self.subTest(action=action):
                page = self.library_page()
                with (
                    patch.object(self.store, method, side_effect=sqlite3.OperationalError("locked password=fixture-secret")),
                    patch("gallery_dl_app.management.QMessageBox.warning") as warning,
                    patch("sys.excepthook") as uncaught,
                ):
                    self.click(page, action)
                uncaught.assert_not_called()
                warning.assert_called_once()
                self.assertNotIn("fixture-secret", str(warning.call_args))
                self.assertEqual(self.editor.toPlainText(), "https://example.com/manual")

    def test_library_refresh_failure_keeps_previous_table(self):
        page = self.library_page()
        with (
            patch.object(self.store, "list_library", side_effect=sqlite3.OperationalError("locked")),
            patch("gallery_dl_app.management.QMessageBox.warning") as warning,
            patch("sys.excepthook") as uncaught,
        ):
            page.findChild(QLineEdit).setText("new search")
        uncaught.assert_not_called()
        warning.assert_called_once()
        self.assertEqual(page.findChild(QTableWidget).rowCount(), 1)

    def test_invalid_library_queue_does_not_replace_manual_queue_or_profile(self):
        page = self.library_page()
        self.target.active_account_profile_id = 42
        with (
            patch("gallery_dl_app.core.MAX_COMMAND_LINE_CHARS", 16),
            patch("gallery_dl_app.management.QMessageBox.warning") as warning,
            patch("sys.excepthook") as uncaught,
        ):
            self.click(page, "Queue Selected")
        uncaught.assert_not_called()
        warning.assert_called_once()
        self.assertEqual(self.editor.toPlainText(), "https://example.com/manual")
        self.assertEqual(self.target.active_account_profile_id, 42)

    def test_library_queue_valid_selection_replaces_editor(self):
        page = self.library_page()
        self.click(page, "Queue Selected")
        self.assertEqual(self.editor.toPlainText(), "https://example.com/stored")

    def test_library_import_is_atomic_if_second_record_cannot_be_written(self):
        page = self.library_page()
        self.target.jobs = parse_text_database("https://example.com/a\nhttps://example.com/b")
        with self.store._connect() as db:
            db.execute("""CREATE TRIGGER fixture_fail BEFORE INSERT ON library_entries
                        WHEN NEW.url='https://example.com/b'
                        BEGIN SELECT RAISE(ABORT, 'fixture failure'); END""")
        with (
            patch("gallery_dl_app.management.QMessageBox.warning") as warning,
            patch("sys.excepthook") as uncaught,
        ):
            self.click(page, "Import Current Queue")
        uncaught.assert_not_called()
        warning.assert_called_once()
        self.assertEqual([row["url"] for row in self.store.list_library()], ["https://example.com/stored"])
        self.target.append_log.assert_not_called()

    def test_library_import_success_preserves_metadata(self):
        page = self.library_page()
        self.target.jobs = parse_text_database("# Fixture\n#@notes my note\nhttps://example.com/a")
        self.click(page, "Import Current Queue")
        record = next(row for row in self.store.list_library() if row["url"].endswith("/a"))
        self.assertEqual((record["tag"], record["notes"]), ("Fixture", "my note"))

    def test_history_clear_failure_keeps_view_and_reports_error(self):
        target = SimpleNamespace(feature_store=self.store, load_history_table=MagicMock(), show_compact_message=MagicMock())
        with (
            patch("gallery_dl_app.advanced_tools.QMessageBox.question", return_value=QMessageBox.Yes),
            patch.object(self.store, "clear_history", side_effect=sqlite3.OperationalError("locked")),
        ):
            AdvancedToolsMixin.clear_history(target)
        target.load_history_table.assert_not_called()
        target.show_compact_message.assert_called_once()


class ConfigPathTests(unittest.TestCase):
    def test_config_validation_uses_environment_path(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"AUDIT_CONFIG_ROOT": folder}):
            (Path(folder) / "base.json").write_text("{}")
            target = SimpleNamespace(config_path="$AUDIT_CONFIG_ROOT/base.json", show_compact_message=MagicMock())
            with patch("gallery_dl_app.system_tools.QMessageBox.question") as missing:
                SystemToolsMixin.validate_config(target)
            missing.assert_not_called()
            target.show_compact_message.assert_called_once_with("Config", "Config JSON is valid.", "info")

    def test_config_open_uses_environment_path(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"AUDIT_CONFIG_ROOT": folder}):
            config = Path(folder) / "base.json"
            config.write_text("{}")
            target = SimpleNamespace(config_path="$AUDIT_CONFIG_ROOT/base.json", _ui_is_indonesian=lambda: False, append_log=MagicMock())
            with (
                patch("gallery_dl_app.system_tools.QMessageBox.question") as missing,
                patch("gallery_dl_app.system_tools.open_path", return_value=True) as opened,
            ):
                SystemToolsMixin.open_or_create_config(target)
            missing.assert_not_called()
            opened.assert_called_once_with(config)

    def test_composer_reads_config_through_environment_path(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"AUDIT_CONFIG_ROOT": folder}):
            config = Path(folder) / "base.json"
            config.write_text('{"extractor":{"filename":"fixture"}}')
            data, error = _read_json_config("$AUDIT_CONFIG_ROOT/base.json")
            self.assertIsNone(error)
            self.assertEqual(data, {"extractor": {"filename": "fixture"}})

    def test_composer_argv_uses_expanded_config_path(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"AUDIT_CONFIG_ROOT": folder}):
            config = Path(folder) / "base.json"
            config.write_text("{}")
            argv = build_composer_argv(ComposerState(use_active_config=True), "https://example.com/a", config_path="$AUDIT_CONFIG_ROOT/base.json")
            self.assertIn("--config", argv)
            self.assertEqual(argv[argv.index("--config") + 1], str(config))

    def test_cookie_validator_accepts_environment_path(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"AUDIT_CONFIG_ROOT": folder}):
            (Path(folder) / "cookies.txt").write_text(".example.com\tTRUE\t/\tFALSE\t0\tsession\tfixture\n")
            self.assertTrue(validate_cookies_txt("$AUDIT_CONFIG_ROOT/cookies.txt")[0])


class AccountDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        class Harness(ManagementMixin):
            def gallery_dl_site_preset_definitions(self):
                return SystemToolsMixin.gallery_dl_site_preset_definitions(self)

            def autosave_session(self):
                pass

        self.temp = tempfile.TemporaryDirectory()
        self.harness = Harness()
        self.store = self.harness.feature_store = FeatureStore(Path(self.temp.name) / "features.sqlite3")
        self.harness.secret_vault = MagicMock(backend_name="System keyring", available=True)
        self.harness.active_account_profile_id = None
        self.harness.gdl_cmd = "gallery-dl"
        self.harness.config_path = None
        self.account_id = self.store.save_account({"name": "Fixture", "site": "pixiv", "auth_kind": "oauth", "cache_file": str(Path(self.temp.name) / "cache.sqlite3")})
        self.page = self.harness._build_accounts_tab()
        self.table = self.page.findChild(QTableWidget)
        self.table.selectRow(0)

    def tearDown(self):
        self.page.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_account_database_errors_are_reported_and_preserve_form(self):
        for action, method in (("accountUseButton", "account"), ("accountDeleteButton", "delete_accounts"), ("accountOAuthButton", "account")):
            with (
                self.subTest(action=action),
                patch.object(self.store, method, side_effect=sqlite3.OperationalError("locked password=fixture-secret")),
                patch("gallery_dl_app.management.QMessageBox.question", return_value=QMessageBox.Yes),
                patch("gallery_dl_app.management.QMessageBox.warning") as warning,
                patch("sys.excepthook") as uncaught,
            ):
                self.page.findChild(QPushButton, action).click()
                uncaught.assert_not_called()
                warning.assert_called_once()
                self.assertNotIn("fixture-secret", str(warning.call_args))
                self.assertEqual(self.page.findChild(QLineEdit, "accountName").text(), "Fixture")
                self.harness.secret_vault.delete.assert_not_called()
        self.assertEqual(len(self.store.list_accounts()), 1)

    def test_account_selection_read_error_does_not_escape_slot(self):
        self.table.clearSelection()
        with (
            patch.object(self.store, "account", side_effect=sqlite3.OperationalError("locked")),
            patch("gallery_dl_app.management.QMessageBox.warning") as warning,
            patch("sys.excepthook") as uncaught,
        ):
            self.table.selectRow(0)
        uncaught.assert_not_called()
        warning.assert_called_once()

    def test_account_refresh_failure_after_save_is_reported(self):
        with (
            patch.object(self.store, "list_accounts", side_effect=sqlite3.OperationalError("locked")),
            patch("gallery_dl_app.management.QMessageBox.warning") as warning,
            patch("sys.excepthook") as uncaught,
        ):
            self.page.findChild(QPushButton, "accountSaveButton").click()
        uncaught.assert_not_called()
        warning.assert_called_once()
        self.assertEqual(self.table.rowCount(), 1)

    def test_account_save_preserves_selection_by_id_when_names_reorder(self):
        self.store.save_account({"name": "Other", "site": "pixiv", "auth_kind": "browser"})
        self.page.findChild(QLineEdit, "accountName").setText("Z renamed")
        self.page.findChild(QPushButton, "accountSaveButton").click()
        self.assertEqual(int(self.table.item(self.table.currentRow(), 0).text()), self.account_id)
        self.assertEqual(self.page.findChild(QLineEdit, "accountName").text(), "Z renamed")

    def test_failed_save_removes_new_secret_when_old_reference_had_no_value(self):
        self.store.save_account({
            "name": "Fixture", "site": "danbooru", "auth_kind": "api_key",
            "secret_key": "password", "secret_ref": "fixture-ref",
        }, self.account_id)
        self.table.clearSelection()
        self.table.selectRow(0)
        self.page.findChild(QLineEdit, "accountApiSecret").setText("fixture-new-secret")
        self.harness.secret_vault.get.return_value = None
        with (
            patch.object(self.store, "save_account", side_effect=ValueError("Account profile no longer exists")),
            patch("gallery_dl_app.management.QMessageBox.warning") as warning,
        ):
            self.page.findChild(QPushButton, "accountSaveButton").click()
        warning.assert_called_once()
        self.harness.secret_vault.delete.assert_called_once_with("fixture-ref")

    def test_oauth_action_passes_environment_config(self):
        with patch.dict(os.environ, {"AUDIT_CONFIG_ROOT": self.temp.name}):
            config = Path(self.temp.name) / "base.json"
            config.write_text("{}")
            self.harness.config_path = "$AUDIT_CONFIG_ROOT/base.json"
            with patch.object(QProcess, "start"):
                self.page.findChild(QPushButton, "accountOAuthButton").click()
            argv = self.page.findChild(QProcess).arguments()
            self.assertIn("--config", argv)
            self.assertEqual(argv[argv.index("--config") + 1], str(config))

    def test_cookie_profile_saves_expanded_environment_path(self):
        self.page.findChild(QPushButton, "accountNewButton").click()
        kind = self.page.findChild(QComboBox, "accountMethodCombo")
        kind.setCurrentIndex(kind.findData("cookies_file"))
        self.page.findChild(QLineEdit, "accountName").setText("Cookies fixture")
        self.page.findChild(QLineEdit, "accountCookieFile").setText("$AUDIT_CONFIG_ROOT/cookies.txt")
        with patch.dict(os.environ, {"AUDIT_CONFIG_ROOT": self.temp.name}):
            cookie = Path(self.temp.name) / "cookies.txt"
            cookie.write_text(".example.com\tTRUE\t/\tFALSE\t0\tsession\tfixture\n")
            with patch("gallery_dl_app.management.QMessageBox.warning") as warning:
                self.page.findChild(QPushButton, "accountSaveButton").click()
            warning.assert_not_called()
        record = next(row for row in self.store.list_accounts() if row["name"] == "Cookies fixture")
        self.assertEqual(record["cookie_source"], str(cookie))


class DeletedRecordTests(unittest.TestCase):
    def test_edit_of_deleted_account_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            store = FeatureStore(Path(folder) / "features.sqlite3")
            values = {"name": "Fixture", "site": "pixiv", "auth_kind": "browser"}
            account_id = store.save_account(values)
            store.delete_accounts([account_id])
            with self.assertRaisesRegex(ValueError, "no longer exists"):
                store.save_account(values, account_id)
            self.assertEqual(store.list_accounts(), [])

    def test_edit_of_deleted_schedule_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            store = FeatureStore(Path(folder) / "features.sqlite3")
            values = {"name": "Fixture", "command_text": "https://example.com/a"}
            schedule_id = store.save_schedule(values)
            store.delete_schedules([schedule_id])
            with self.assertRaisesRegex(ValueError, "no longer exists"):
                store.save_schedule(values, schedule_id)
            self.assertEqual(store.list_schedules(), [])


if __name__ == "__main__":
    unittest.main()
