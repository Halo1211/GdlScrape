import json
import math
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
)

from gallery_dl_app.feature_logic import (
    FALLBACK_OPTIONS,
    build_filter_expression,
    next_schedule_time,
    parse_help_options,
)
from gallery_dl_app.feature_store import FeatureStore
from gallery_dl_app.models import DownloadJob
from gallery_dl_app.system_tools import SystemToolsMixin
from gallery_dl_app.core import HISTORY_MAX_BYTES, MAX_COMMAND_LINE_CHARS, REDACTED
from gallery_dl_app.management import (
    ACCOUNT_CACHE_DIR,
    OAUTH_SITES,
    ManagementMixin,
    account_action_argv,
    account_site_error,
    browser_cookie_source,
    common_account_profile_id,
    default_account_cache_path,
    format_schedule_timestamp,
    interrupted_recovery_commands,
    library_records_to_database_text,
    normalize_clipboard_url_candidate,
    oauth_target,
    redact_oauth_output,
    split_browser_cookie_source,
)


class FeatureStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = FeatureStore(self.root / "features.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_legacy_history_migrates_once(self):
        history = self.root / "history.jsonl"
        history.write_text(
            json.dumps({
                "time": "2026-08-20 10:00:00",
                "status": "done",
                "service": "pixiv",
                "id": "42",
                "url": "https://www.pixiv.net/artworks/42",
                "rc": 0,
                "error_type": "none",
            }) + "\nnot-json\n",
            encoding="utf-8",
        )
        self.assertEqual(self.store.migrate_jsonl_history(history), 1)
        self.assertEqual(self.store.migrate_jsonl_history(history), 0)
        rows = self.store.list_history()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["service"], "pixiv")

    def test_corrupt_database_is_preserved_and_recreated(self):
        path = self.root / "corrupt.sqlite3"
        path.write_bytes(b"this is not sqlite")

        store = FeatureStore(path)

        self.assertIsNotNone(store.recovered_corrupt_path)
        self.assertTrue(store.recovered_corrupt_path.exists())
        self.assertEqual(store.recovered_corrupt_path.read_bytes(), b"this is not sqlite")
        self.assertEqual(store.list_history(), [])

    def test_legacy_library_schema_adds_account_profile_column(self):
        path = self.root / "legacy.sqlite3"
        database = sqlite3.connect(path)
        try:
            database.execute(
                """CREATE TABLE library_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL UNIQUE,
                    service TEXT NOT NULL DEFAULT '-',
                    tag TEXT NOT NULL DEFAULT '',
                    output_dir TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_run_at REAL,
                    last_status TEXT NOT NULL DEFAULT 'never',
                    last_downloaded INTEGER NOT NULL DEFAULT 0
                )"""
            )
            database.commit()
        finally:
            database.close()

        store = FeatureStore(path)
        account_id = store.save_account({
            "name": "Migrated account",
            "site": "pixiv",
            "auth_kind": "browser",
        })
        library_id = store.add_library_entry(
            url="https://example.com/migrated",
            account_profile_id=account_id,
            command="https://example.com/migrated",
        )

        row = next(item for item in store.list_library() if item["id"] == library_id)
        self.assertEqual(row["account_profile_id"], account_id)
        self.assertEqual(row["command"], "https://example.com/migrated")

    def test_legacy_account_schema_adds_oauth_and_private_cache_columns(self):
        path = self.root / "legacy-accounts.sqlite3"
        database = sqlite3.connect(path)
        try:
            database.execute(
                """CREATE TABLE account_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    site TEXT NOT NULL,
                    auth_kind TEXT NOT NULL,
                    username TEXT NOT NULL DEFAULT '',
                    cookie_source TEXT NOT NULL DEFAULT '',
                    secret_key TEXT NOT NULL DEFAULT 'password',
                    secret_ref TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )"""
            )
            database.commit()
        finally:
            database.close()

        store = FeatureStore(path)
        account_id = store.save_account({
            "name": "Pixiv OAuth",
            "site": "pixiv",
            "auth_kind": "oauth",
            "oauth_instance": "",
            "cache_file": "C:/private/pixiv.sqlite3",
        })

        account = store.account(account_id)
        self.assertEqual(account["oauth_instance"], "")
        self.assertEqual(account["cache_file"], "C:/private/pixiv.sqlite3")

    def test_legacy_history_tolerates_invalid_counts(self):
        history = self.root / "history.jsonl"
        history.write_text(json.dumps({
            "url": "https://example.com/a",
            "downloaded": "not-a-number",
            "skipped": None,
        }) + "\n", encoding="utf-8")

        self.assertEqual(self.store.migrate_jsonl_history(history), 1)
        row = self.store.list_history()[0]
        self.assertEqual(row["downloaded"], 0)
        self.assertEqual(row["skipped"], 0)

    def test_legacy_history_tolerates_non_scalar_return_code(self):
        history = self.root / "invalid-return-code.jsonl"
        history.write_text(
            json.dumps({
                "url": "https://example.com/a",
                "status": "failed",
                "rc": {"unexpected": "container"},
            }) + "\n",
            encoding="utf-8",
        )
        store = FeatureStore(self.root / "return-code.sqlite3")

        imported = store.migrate_jsonl_history(history)

        self.assertEqual(imported, 1)
        self.assertEqual(store.list_history()[0]["return_code"], -1)

    def test_history_writer_tolerates_non_scalar_return_code(self):
        self.store.record_history({
            "url": "https://example.com/a",
            "status": "failed",
            "rc": ["unexpected", "container"],
        })

        self.assertEqual(self.store.list_history()[0]["return_code"], -1)

    def test_history_tail_keeps_first_row_when_limit_starts_at_line_boundary(self):
        history = self.root / "history.jsonl"
        rows = [
            json.dumps({"url": f"https://example.com/{index}"}) + "\n"
            for index in range(1, 4)
        ]
        history.write_bytes("".join(rows).encode("utf-8"))
        tail_bytes = len((rows[1] + rows[2]).encode("utf-8"))

        with patch("gallery_dl_app.feature_store.HISTORY_MAX_BYTES", tail_bytes):
            self.assertEqual(self.store.migrate_jsonl_history(history), 2)

        self.assertEqual(
            [row["url"] for row in self.store.list_history()],
            ["https://example.com/2", "https://example.com/3"],
        )

    def test_legacy_history_migration_bounds_oversized_rows_and_keeps_recent_data(self):
        history = self.root / "oversized-history.jsonl"
        recent = json.dumps({
            "time": "2026-08-30 12:00:00",
            "status": "done",
            "url": "https://example.com/recent",
        }).encode("utf-8")
        history.write_bytes(
            b"x" * (HISTORY_MAX_BYTES + 1024) + b"\n" + recent + b"\n"
        )

        self.assertEqual(self.store.migrate_jsonl_history(history), 1)
        rows = self.store.list_history()
        self.assertEqual(
            [row["url"] for row in rows],
            ["https://example.com/recent"],
        )

    def test_legacy_history_directory_does_not_break_startup_or_mark_migrated(self):
        history = self.root / "history.jsonl"
        history.mkdir()

        self.assertEqual(self.store.migrate_jsonl_history(history), 0)
        self.assertIsNone(self.store._meta("jsonl_history_migrated"))

    def test_run_item_message_is_redacted_at_storage_boundary(self):
        jobs = [DownloadJob("https://example.com/a", False, "https://example.com/a")]
        run_id = self.store.begin_run(jobs, [0])
        self.store.mark_run_item(
            run_id,
            0,
            "failed",
            message="--password super-secret; config {'api-key': 'structured-secret'}",
        )
        # interrupted_runs intentionally omits the message, so inspect the
        # persistence boundary directly for this regression.
        with self.store._connect() as db:
            message = db.execute(
                "SELECT message FROM run_items WHERE run_id=?", (run_id,)
            ).fetchone()[0]
        self.assertNotIn("super-secret", message)
        self.assertNotIn("structured-secret", message)
        self.assertIn(REDACTED, message)

    def test_library_upsert_and_result(self):
        url = "https://example.com/gallery/1"
        first_id = self.store.add_library_entry(url=url, title="Old", service="example", command=url + " --range 1-5")
        second_id = self.store.add_library_entry(url=url, title="New", service="example", tag="art")
        self.assertEqual(first_id, second_id)
        self.store.update_library_result(url, "done", 12)
        rows = self.store.list_library("art")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "New")
        self.assertEqual(rows[0]["last_downloaded"], 12)
        self.assertEqual(rows[0]["command"], url + " --range 1-5")

    def test_library_and_schedule_strip_inline_credentials(self):
        private_url = "https://user:pass@example.com/a?token=url-secret"
        entry_id = self.store.add_library_entry(
            url=private_url,
            command=f"gallery-dl --password command-secret {private_url}",
        )
        library_row = next(
            row for row in self.store.list_library() if row["id"] == entry_id
        )
        persisted_library = str(library_row["url"]) + str(library_row["command"])
        self.assertNotIn("user:pass", persisted_library)
        self.assertNotIn("url-secret", persisted_library)
        self.assertNotIn("command-secret", persisted_library)
        self.store.update_library_result(private_url, "done", 4)
        library_row = next(
            row for row in self.store.list_library() if row["id"] == entry_id
        )
        self.assertEqual(library_row["last_status"], "done")
        self.assertEqual(library_row["last_downloaded"], 4)

        schedule_id = self.store.save_schedule({
            "command_text": f"gallery-dl --password schedule-secret {private_url}",
        })
        schedule = next(
            row for row in self.store.list_schedules() if row["id"] == schedule_id
        )
        self.assertNotIn("schedule-secret", str(schedule["command_text"]))
        self.assertNotIn("url-secret", str(schedule["command_text"]))
        self.assertIn(REDACTED, str(schedule["command_text"]))
        self.assertEqual(schedule["enabled"], 0)

    def test_interrupted_run_tracks_only_unfinished_items(self):
        jobs = [
            DownloadJob("https://example.com/a", False, "https://example.com/a"),
            DownloadJob("https://example.com/b", False, "https://example.com/b"),
        ]
        run_id = self.store.begin_run(jobs, [0, 1])
        self.store.mark_run_item(run_id, 0, "done", downloaded=3)
        rows = self.store.interrupted_runs()
        self.assertEqual([row["job_index"] for row in rows], [1])
        self.store.resolve_interrupted_runs([run_id])
        self.assertEqual(self.store.interrupted_runs(), [])

    def test_run_recovery_records_do_not_persist_inline_credentials(self):
        raw = (
            "gallery-dl -psecret "
            "https://user:pass@example.com/a?access_token=url-secret"
        )
        jobs = [DownloadJob(raw, True, "https://user:pass@example.com/a?access_token=url-secret")]
        self.store.begin_run(jobs, [0])

        row = self.store.interrupted_runs()[0]
        persisted = str(row["command"]) + str(row["url"])
        self.assertNotIn("-psecret", persisted)
        self.assertNotIn("user:pass", persisted)
        self.assertNotIn("url-secret", persisted)
        self.assertIn(REDACTED, persisted)

    def test_crash_recovery_preserves_duplicate_unfinished_jobs(self):
        rows = [
            {"command": "https://example.com/a", "job_index": 0},
            {"command": "https://example.com/a", "job_index": 1},
            {"command": "", "job_index": 2},
        ]
        self.assertEqual(
            interrupted_recovery_commands(rows),
            ["https://example.com/a", "https://example.com/a"],
        )
    def test_schedule_due_and_advance(self):
        schedule_id = self.store.save_schedule({
            "name": "Hourly",
            "command_text": "https://example.com/a",
            "frequency": "interval",
            "interval_minutes": 60,
            "next_run_at": time.time() - 1,
            "enabled": True,
        })
        self.assertEqual(self.store.due_schedules()[0]["id"], schedule_id)
        self.store.advance_schedule(schedule_id, time.time() + 3600)
        self.assertEqual(self.store.due_schedules(), [])

    def test_failed_schedule_defer_does_not_claim_a_run(self):
        schedule_id = self.store.save_schedule({
            "command_text": "https://example.com/a",
            "frequency": "interval",
            "next_run_at": time.time() - 1,
            "enabled": True,
        })
        retry_at = time.time() + 300

        self.store.defer_schedule(schedule_id, retry_at)

        row = next(item for item in self.store.list_schedules() if item["id"] == schedule_id)
        self.assertAlmostEqual(row["next_run_at"], retry_at, delta=0.01)
        self.assertIsNone(row["last_run_at"])

    def test_schedule_string_false_is_not_enabled(self):
        schedule_id = self.store.save_schedule({
            "command_text": "https://example.com/a",
            "enabled": "false",
            "next_run_at": time.time() - 1,
        })
        row = next(item for item in self.store.list_schedules() if item["id"] == schedule_id)
        self.assertEqual(row["enabled"], 0)
        self.assertEqual(self.store.due_schedules(), [])

    def test_schedule_pins_account_profile(self):
        account_id = self.store.save_account({
            "name": "Scheduled account",
            "site": "pixiv",
            "auth_kind": "browser",
        })
        schedule_id = self.store.save_schedule({
            "command_text": "https://example.com/a",
            "account_profile_id": account_id,
        })

        row = next(item for item in self.store.list_schedules() if item["id"] == schedule_id)
        self.assertEqual(row["account_profile_id"], account_id)

    def test_common_account_profile_ignores_malformed_persisted_id(self):
        self.assertIsNone(common_account_profile_id([
            {"account_profile_id": "not-an-id"},
        ]))

    def test_deleting_account_clears_library_reference_and_disables_schedule(self):
        account_id = self.store.save_account({
            "name": "Temporary account",
            "site": "pixiv",
            "auth_kind": "browser",
        })
        library_id = self.store.add_library_entry(
            url="https://example.com/account-bound",
            account_profile_id=account_id,
        )
        schedule_id = self.store.save_schedule({
            "command_text": "https://example.com/account-bound",
            "account_profile_id": account_id,
            "enabled": True,
        })

        self.store.delete_accounts([account_id])

        library = next(row for row in self.store.list_library() if row["id"] == library_id)
        schedule = next(row for row in self.store.list_schedules() if row["id"] == schedule_id)
        self.assertIsNone(library["account_profile_id"])
        self.assertIsNone(schedule["account_profile_id"])
        self.assertEqual(schedule["enabled"], 0)

    def test_schedule_store_rejects_non_finite_timestamps(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "finite"):
                self.store.save_schedule({
                    "command_text": "https://example.com/a",
                    "next_run_at": value,
                })

        schedule_id = self.store.save_schedule({
            "command_text": "https://example.com/a",
            "next_run_at": time.time(),
        })
        stored = next(row for row in self.store.list_schedules() if row["id"] == schedule_id)
        self.assertTrue(math.isfinite(float(stored["next_run_at"])))
        with self.assertRaisesRegex(ValueError, "finite"):
            self.store.advance_schedule(schedule_id, float("inf"))
        with self.assertRaisesRegex(ValueError, "finite"):
            self.store.defer_schedule(schedule_id, float("nan"))

    def test_account_metadata_never_contains_secret_value(self):
        account_id = self.store.save_account({
            "name": "Pixiv main",
            "site": "pixiv",
            "auth_kind": "username_password",
            "username": "user",
            "secret_ref": "account:opaque-reference",
        })
        account = self.store.account(account_id)
        self.assertEqual(account["secret_ref"], "account:opaque-reference")
        self.assertNotIn("password", account)


class AccountManagementHelperTests(unittest.TestCase):
    def test_site_validation_rejects_typos_with_a_suggestion(self):
        message = account_site_error("pixvi", ["danbooru", "pixiv", "twitter"])

        self.assertIn("pixiv", message)
        self.assertEqual(account_site_error("Pixiv", ["pixiv"]), "")

    def test_browser_cookie_source_round_trips_profile_and_domain(self):
        source = browser_cookie_source("edge", "Profile 2", ".twitter.com")

        self.assertEqual(source, "edge/.twitter.com:Profile 2")
        self.assertEqual(
            split_browser_cookie_source(source),
            ("edge", "Profile 2", ".twitter.com"),
        )

    def test_oauth_target_only_allows_documented_sites_and_valid_mastodon_instance(self):
        self.assertIn("pixiv", OAUTH_SITES)
        self.assertEqual(oauth_target("pixiv"), "oauth:pixiv")
        self.assertEqual(
            oauth_target("mastodon", "https://mastodon.social/"),
            "oauth:mastodon:https://mastodon.social/",
        )
        with self.assertRaisesRegex(ValueError, "does not support OAuth"):
            oauth_target("danbooru")
        with self.assertRaisesRegex(ValueError, "Mastodon instance"):
            oauth_target("mastodon", "not a host")

    def test_account_action_uses_isolated_cache_and_active_config(self):
        argv = account_action_argv(
            'py -m gallery_dl',
            "oauth",
            site="mastodon",
            cache_file="C:/private/account.sqlite3",
            oauth_instance="mastodon.social",
            config_path="C:/config/gallery-dl.conf",
        )

        self.assertEqual(argv[:3], ["py", "-m", "gallery_dl"])
        self.assertIn("--config", argv)
        self.assertIn("--cache-file", argv)
        self.assertEqual(argv[-1], "oauth:mastodon:mastodon.social")
        self.assertEqual(
            account_action_argv(
                "gallery-dl",
                "clear_site",
                site="pixiv",
                cache_file="C:/cache.sqlite3",
            )[-2:],
            ["--cache-clear", "pixiv"],
        )
        self.assertEqual(
            account_action_argv(
                "gallery-dl",
                "clear_all",
                site="pixiv",
                cache_file="C:/cache.sqlite3",
            )[-2:],
            ["--cache-clear", "ALL"],
        )

    def test_default_account_cache_is_inside_managed_directory(self):
        path = Path(default_account_cache_path("pixiv"))

        self.assertEqual(path.parent, ACCOUNT_CACHE_DIR)
        self.assertTrue(path.name.startswith("pixiv-"))
        self.assertEqual(path.suffix, ".sqlite3")

    def test_oauth_output_masks_bare_tokens_printed_on_following_lines(self):
        output = (
            "Your 'access-token' and 'access-token-secret' are\n\n"
            "first-bare-token\nsecond-bare-token\n\n"
            "These values have been cached and will automatically be used.\n"
        )

        redacted = redact_oauth_output(output)

        self.assertNotIn("first-bare-token", redacted)
        self.assertNotIn("second-bare-token", redacted)
        self.assertIn(REDACTED, redacted)
        self.assertIn("automatically be used", redacted)


class AccountManagementUiTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.temp = tempfile.TemporaryDirectory()

        class Harness(ManagementMixin):
            def gallery_dl_site_preset_definitions(inner_self):
                return SystemToolsMixin.gallery_dl_site_preset_definitions(inner_self)

            def autosave_session(inner_self):
                return None

        self.harness = Harness()
        self.harness.feature_store = FeatureStore(
            Path(self.temp.name) / "features.sqlite3"
        )
        self.harness.secret_vault = MagicMock()
        self.harness.secret_vault.backend_name = "System keyring"
        self.harness.secret_vault.available = True
        self.harness.secret_vault.new_reference.return_value = (
            "account:test-reference"
        )
        self.harness.active_account_profile_id = None
        self.harness.gdl_cmd = "gallery-dl"
        self.harness.config_path = None
        self.page = self.harness._build_accounts_tab()

    def tearDown(self):
        self.page.close()
        self.temp.cleanup()

    def test_account_editor_uses_site_picker_and_method_specific_panels(self):
        scroll = self.page.findChild(QScrollArea, "accountScrollArea")
        sites = self.page.findChild(QComboBox, "accountSiteCombo")
        methods = self.page.findChild(QComboBox, "accountMethodCombo")
        stack = self.page.findChild(QStackedWidget, "accountMethodStack")
        oauth_button = self.page.findChild(QPushButton, "accountOAuthButton")

        self.assertIsNotNone(scroll)
        self.assertTrue(scroll.widgetResizable())
        self.page.resize(900, 420)
        self.page.show()
        self.app.processEvents()
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        self.assertEqual(
            scroll.verticalScrollBar().value(),
            scroll.verticalScrollBar().maximum(),
        )
        self.assertGreater(sites.count(), 100)
        self.assertGreaterEqual(sites.findData("pixiv"), 0)
        methods.setCurrentIndex(methods.findData("oauth"))
        self.app.processEvents()
        self.assertEqual(stack.currentIndex(), methods.currentIndex())
        self.assertEqual(sites.count(), len(OAUTH_SITES))
        self.assertTrue(oauth_button.isEnabled())

    def test_username_password_profile_saves_password_only_to_vault(self):
        sites = self.page.findChild(QComboBox, "accountSiteCombo")
        methods = self.page.findChild(QComboBox, "accountMethodCombo")
        username = self.page.findChild(QLineEdit, "accountUsername")
        password = self.page.findChild(QLineEdit, "accountPassword")
        save = self.page.findChild(QPushButton, "accountSaveButton")
        sites.setCurrentIndex(sites.findData("danbooru"))
        methods.setCurrentIndex(methods.findData("username_password"))
        username.setText("alice")
        password.setText("vault-only-password")

        save.click()
        self.app.processEvents()

        account = self.harness.feature_store.list_accounts()[0]
        self.assertEqual(account["site"], "danbooru")
        self.assertEqual(account["username"], "alice")
        self.assertEqual(account["secret_ref"], "account:test-reference")
        self.assertNotIn("vault-only-password", str(account))
        self.harness.secret_vault.set.assert_called_once_with(
            "account:test-reference", "vault-only-password"
        )


class CrashRecoveryTests(unittest.TestCase):
    def test_recovery_record_is_not_resolved_before_user_decision(self):
        harness = MagicMock()
        harness.active_workers = 0
        harness.feature_store.interrupted_runs.return_value = [{
            "run_id": 7,
            "command": "https://example.com/a",
        }]

        with patch(
            "gallery_dl_app.management.QMessageBox.question",
            side_effect=RuntimeError("dialog failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "dialog failed"):
                ManagementMixin._offer_crash_recovery(harness)

        harness.feature_store.resolve_interrupted_runs.assert_not_called()


class SchedulerDispatchTests(unittest.TestCase):
    def test_corrupt_account_profile_id_defers_without_running_publicly(self):
        harness = MagicMock()
        harness.active_workers = 0
        harness.delay_timer = None
        harness.txt_commands.toPlainText.return_value = "manual queue"
        harness.feature_store.due_schedules.return_value = [{
            "id": 13,
            "name": "Corrupt account reference",
            "command_text": "https://example.com/a",
            "frequency": "interval",
            "interval_minutes": 60,
            "account_profile_id": "not-an-id",
        }]
        harness._start_download_now.return_value = True

        ManagementMixin._poll_schedules(harness)

        harness._start_download_now.assert_not_called()
        harness.feature_store.advance_schedule.assert_not_called()
        harness.feature_store.defer_schedule.assert_called_once()
        harness.txt_commands.setPlainText.assert_not_called()
        self.assertIn("invalid account profile", harness.append_log.call_args.args[0])

    def test_redacted_legacy_schedule_is_disabled_without_touching_queue(self):
        harness = MagicMock()
        harness.active_workers = 0
        harness.delay_timer = None
        harness.feature_store.due_schedules.return_value = [{
            "id": 11,
            "name": "Legacy secret",
            "command_text": f"gallery-dl --password {REDACTED} https://example.com/a",
            "frequency": "interval",
            "interval_minutes": 60,
            "account_profile_id": 7,
        }]

        ManagementMixin._poll_schedules(harness)

        harness.feature_store.set_schedule_enabled.assert_called_once_with(11, False)
        harness._start_download_now.assert_not_called()
        harness.txt_commands.setPlainText.assert_not_called()
        harness.feature_store.advance_schedule.assert_not_called()
        harness.feature_store.defer_schedule.assert_not_called()
        self.assertIn("disabled", harness.append_log.call_args.args[0])

    def test_due_schedule_passes_source_and_pinned_account_without_pending_state(self):
        harness = MagicMock()
        harness.active_workers = 0
        harness.delay_timer = None
        harness.feature_store.due_schedules.return_value = [{
            "id": 12,
            "name": "Pinned",
            "command_text": "https://example.com/a",
            "frequency": "interval",
            "interval_minutes": 60,
            "account_profile_id": 7,
        }]
        harness._start_download_now.return_value = True

        ManagementMixin._poll_schedules(harness)

        harness._start_download_now.assert_called_once_with(
            run_source="schedule:12",
            account_profile_id=7,
            use_account_override=True,
        )
        self.assertNotIn("_pending_run_source", harness.__dict__)
        harness.feature_store.advance_schedule.assert_called_once()
        harness.feature_store.defer_schedule.assert_not_called()

    def test_failed_schedule_start_is_deferred_instead_of_skipped(self):
        harness = MagicMock()
        harness.active_workers = 0
        harness.delay_timer = None
        harness.feature_store.due_schedules.return_value = [{
            "id": 12,
            "name": "Pinned",
            "command_text": "https://example.com/a",
            "frequency": "interval",
            "interval_minutes": 60,
            "account_profile_id": 7,
        }]
        harness._start_download_now.return_value = False

        before = time.time()
        ManagementMixin._poll_schedules(harness)
        after = time.time()

        harness.feature_store.advance_schedule.assert_not_called()
        harness.feature_store.defer_schedule.assert_called_once()
        harness._restore_queue_after_scheduled_run.assert_called_once_with()
        schedule_id, retry_at = harness.feature_store.defer_schedule.call_args.args
        self.assertEqual(schedule_id, 12)
        self.assertGreaterEqual(retry_at, before + 300)
        self.assertLessEqual(retry_at, after + 300)
        self.assertIn("retrying in 5 minutes", harness.append_log.call_args.args[0])

    def test_unexpected_schedule_start_error_restores_queue_and_defers(self):
        harness = MagicMock()
        harness.active_workers = 0
        harness.delay_timer = None
        harness.feature_store.due_schedules.return_value = [{
            "id": 13,
            "name": "Broken start",
            "command_text": "https://example.com/a",
            "frequency": "interval",
            "interval_minutes": 60,
            "account_profile_id": None,
        }]
        harness._start_download_now.side_effect = RuntimeError("simulated failure")

        ManagementMixin._poll_schedules(harness)

        harness.feature_store.advance_schedule.assert_not_called()
        harness.feature_store.defer_schedule.assert_called_once()
        harness._restore_queue_after_scheduled_run.assert_called_once_with()
        messages = [call.args[0] for call in harness.append_log.call_args_list]
        self.assertTrue(any("unexpected start error" in message for message in messages))
        self.assertTrue(any("retrying in 5 minutes" in message for message in messages))

    def test_schedule_error_after_workers_activate_is_committed_without_queue_restore(self):
        harness = MagicMock()
        harness.active_workers = 1
        harness.delay_timer = None
        # The poll normally exits when workers are already active, so emulate
        # the transition from zero to one during the failing start call.
        type(harness).active_workers = PropertyMock(side_effect=[0, 1])
        harness.feature_store.due_schedules.return_value = [{
            "id": 14,
            "name": "Late UI failure",
            "command_text": "https://example.com/a",
            "frequency": "interval",
            "interval_minutes": 60,
            "account_profile_id": None,
        }]
        harness._start_download_now.side_effect = RuntimeError("late failure")

        ManagementMixin._poll_schedules(harness)

        harness.feature_store.advance_schedule.assert_called_once()
        harness.feature_store.defer_schedule.assert_not_called()
        harness._restore_queue_after_scheduled_run.assert_not_called()

    def test_missing_explicit_account_profile_blocks_config_creation(self):
        harness = MagicMock()
        harness.active_account_profile_id = 99
        harness.feature_store.account.return_value = None

        with self.assertRaisesRegex(RuntimeError, "no longer exists"):
            ManagementMixin.prepare_active_account_config(
                harness,
                7,
                use_active_default=False,
            )
        harness.feature_store.account.assert_called_once_with(7)

    def test_account_profile_replaces_stale_auth_but_preserves_site_options(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "gallery-dl.conf"
            config_path.write_text(json.dumps({
                "extractor": {
                    "pixiv": {
                        "cookies": ["firefox"],
                        "username": "stale-user",
                        "password": "stale-password",
                        "access-token": "stale-token",
                        "oauth-token": "stale-custom-token",
                        "parent-session": True,
                        "username-display": "public-label",
                        "custom-option": "keep-me",
                    }
                }
            }), encoding="utf-8")

            harness = MagicMock()
            harness.config_path = str(config_path)
            harness._run_config_path = None
            harness.active_account_profile_id = 7
            harness.cleanup_run_account_config.side_effect = (
                lambda: ManagementMixin.cleanup_run_account_config(harness)
            )
            harness.feature_store.account.return_value = {
                "id": 7,
                "site": "pixiv",
                "auth_kind": "browser",
                "cookie_source": "edge",
            }

            runtime_dir = root / "runtime"
            with patch("gallery_dl_app.management.SECURE_RUNTIME_DIR", runtime_dir):
                result = ManagementMixin.prepare_active_account_config(harness)
                merged = json.loads(Path(result).read_text(encoding="utf-8"))

            site = merged["extractor"]["pixiv"]
            self.assertEqual(site["cookies"], ["edge"])
            self.assertEqual(site["custom-option"], "keep-me")
            self.assertIs(site["parent-session"], True)
            self.assertEqual(site["username-display"], "public-label")
            self.assertNotIn("username", site)
            self.assertNotIn("password", site)
            self.assertNotIn("access-token", site)
        self.assertNotIn("oauth-token", site)

    def test_account_profile_merges_home_relative_active_config(self):
        with tempfile.TemporaryDirectory() as home:
            config_path = Path(home) / "gallery-dl" / "config.json"
            config_path.parent.mkdir()
            config_path.write_text(
                json.dumps({"extractor": {"pixiv": {"custom-option": "keep"}}}),
                encoding="utf-8",
            )
            harness = MagicMock()
            harness.config_path = "~/gallery-dl/config.json"
            harness._run_config_path = None
            harness.active_account_profile_id = 7
            harness.cleanup_run_account_config.side_effect = (
                lambda: ManagementMixin.cleanup_run_account_config(harness)
            )
            harness.feature_store.account.return_value = {
                "id": 7,
                "site": "pixiv",
                "auth_kind": "browser",
                "cookie_source": "edge",
            }
            runtime_dir = Path(home) / "runtime"

            with (
                patch.dict(os.environ, {"HOME": home, "USERPROFILE": home}),
                patch("gallery_dl_app.management.SECURE_RUNTIME_DIR", runtime_dir),
            ):
                result = ManagementMixin.prepare_active_account_config(harness)
                merged = json.loads(Path(result).read_text(encoding="utf-8"))

        self.assertEqual(merged["extractor"]["pixiv"]["custom-option"], "keep")
        self.assertEqual(merged["extractor"]["pixiv"]["cookies"], ["edge"])

    def test_oauth_profile_uses_its_private_cache_without_reading_secret_vault(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_file = root / "accounts" / "pixiv.sqlite3"
            harness = MagicMock()
            harness.config_path = None
            harness._run_config_path = None
            harness.active_account_profile_id = 7
            harness.cleanup_run_account_config.side_effect = (
                lambda: ManagementMixin.cleanup_run_account_config(harness)
            )
            harness.feature_store.account.return_value = {
                "id": 7,
                "site": "pixiv",
                "auth_kind": "oauth",
                "cache_file": str(cache_file),
            }
            runtime_dir = root / "runtime"

            with patch("gallery_dl_app.management.SECURE_RUNTIME_DIR", runtime_dir):
                result = ManagementMixin.prepare_active_account_config(harness)
                merged = json.loads(Path(result).read_text(encoding="utf-8"))

        self.assertEqual(merged["cache"]["file"], str(cache_file))
        self.assertEqual(merged["extractor"]["pixiv"], {})
        harness.secret_vault.get.assert_not_called()


class ClipboardInboxTests(unittest.TestCase):
    def test_oversized_existing_queue_does_not_escape_clipboard_callback(self):
        harness = MagicMock()
        harness.clipboard_inbox_enabled = True
        harness.active_workers = 0
        harness._last_clipboard_text = ""
        harness.clipboard_allowed_hosts = ""
        harness.txt_commands.toPlainText.return_value = "x" * (MAX_COMMAND_LINE_CHARS + 1)
        clipboard = MagicMock()
        clipboard.text.return_value = "https://example.com/new"

        with patch(
            "gallery_dl_app.management.QApplication.clipboard",
            return_value=clipboard,
        ):
            ManagementMixin._on_clipboard_changed(harness)

        harness.txt_commands.setPlainText.assert_not_called()
        self.assertIn("current queue", harness.append_log.call_args.args[0])

    def test_malformed_ipv6_candidate_is_ignored_without_crashing(self):
        harness = MagicMock()
        harness.clipboard_inbox_enabled = True
        harness.active_workers = 0
        harness._last_clipboard_text = ""
        harness.clipboard_allowed_hosts = ""
        clipboard = MagicMock()
        clipboard.text.return_value = "Look at https://[invalid"

        with patch(
            "gallery_dl_app.management.QApplication.clipboard",
            return_value=clipboard,
        ):
            ManagementMixin._on_clipboard_changed(harness)

        harness.txt_commands.setPlainText.assert_not_called()


class FeatureLogicTests(unittest.TestCase):
    def test_clipboard_url_trimming_preserves_balanced_parentheses(self):
        wikipedia = (
            "https://en.wikipedia.org/wiki/Python_(programming_language)"
        )
        self.assertEqual(normalize_clipboard_url_candidate(wikipedia), wikipedia)
        self.assertEqual(
            normalize_clipboard_url_candidate("https://example.com/article)."),
            "https://example.com/article",
        )
        self.assertEqual(
            normalize_clipboard_url_candidate("https://example.com/list_(a))."),
            "https://example.com/list_(a)",
        )

    def test_fallback_catalog_uses_supported_http_timeout_flag(self):
        flags = {flag for flag, _description, _needs_value in FALLBACK_OPTIONS}
        self.assertIn("--http-timeout", flags)
        self.assertNotIn("--timeout", flags)

    def test_visual_filter_quotes_strings_and_preserves_numbers(self):
        expression = build_filter_expression([
            ("extension", "in", '("jpg", "png")'),
            ("width", ">=", "1920"),
            ("artist", "==", "alice"),
        ])
        self.assertEqual(
            expression,
            "extension in ('jpg', 'png') and width >= 1920 and artist == 'alice'",
        )

    def test_visual_filter_rejects_code_like_field(self):
        with self.assertRaises(ValueError):
            build_filter_expression([("__import__('os')", "==", "1")])

    def test_visual_filter_rejects_arithmetic_and_malformed_fields(self):
        for field in (
            "width-height",
            "metadata[",
            "metadata]",
            "metadata..width",
            "metadata.__class__",
        ):
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError,
                "Invalid metadata field",
            ):
                build_filter_expression([(field, "==", "1")])

        self.assertEqual(
            build_filter_expression([("items[-1]", "==", "last")]),
            "items[-1] == 'last'",
        )

    def test_help_catalog_tracks_installed_style_output(self):
        output = """
  -R, --retries RETRIES     Maximum number of retries
      --simulate            Do not download files
      --print [EVENT:]FORMAT  Print a metadata value
"""
        parsed = parse_help_options(output)
        self.assertIn(("--retries", "Maximum number of retries", True), parsed)
        self.assertIn(("--simulate", "Do not download files", False), parsed)
        self.assertIn(("--print", "Print a metadata value", True), parsed)

    def test_help_catalog_keeps_wrapped_long_option_signatures(self):
        output = """
  --cookies-from-browser BROWSER[/DOMAIN][+KEYRING][:PROFILE][::CONTAINER]
                              Name of the browser to load cookies from
"""
        parsed = parse_help_options(output)
        self.assertIn(
            (
                "--cookies-from-browser",
                "Name of the browser to load cookies from",
                True,
            ),
            parsed,
        )

    def test_help_catalog_does_not_require_fully_optional_value(self):
        output = """
  --list-extractors [CATEGORIES]
                              Print a list of extractor classes
  -N, --print [EVENT:]FORMAT  Print a metadata value
"""
        parsed = parse_help_options(output)
        self.assertIn(
            ("--list-extractors", "Print a list of extractor classes", False),
            parsed,
        )
        self.assertIn(("--print", "Print a metadata value", True), parsed)

    def test_daily_schedule_is_strictly_in_future(self):
        now = time.time()
        result = next_schedule_time({"frequency": "daily", "time_of_day": "00:00"}, now)
        self.assertGreater(result, now)
        self.assertLessEqual(result - now, 24 * 60 * 60)

    def test_weekly_schedule_tolerates_trailing_weekday_separator(self):
        monday = datetime(2026, 9, 7, 3, 0, 0).timestamp()
        result = next_schedule_time(
            {
                "frequency": "weekly",
                "time_of_day": "02:00",
                "weekdays": "1,2,",
            },
            monday,
        )
        self.assertEqual(datetime.fromtimestamp(result).weekday(), 1)
        self.assertEqual(datetime.fromtimestamp(result).hour, 2)

    def test_invalid_interval_falls_back_without_crashing(self):
        now = time.time()
        result = next_schedule_time(
            {"frequency": "interval", "interval_minutes": "not-a-number"},
            now,
        )
        self.assertAlmostEqual(result - now, 60 * 60, delta=1)

    def test_overflowing_interval_falls_back_without_crashing(self):
        now = time.time()
        result = next_schedule_time(
            {"frequency": "interval", "interval_minutes": float("inf")},
            now,
        )
        self.assertAlmostEqual(result - now, 60 * 60, delta=1)

    def test_library_queue_restore_preserves_tag_resets(self):
        rows = [
            {"command": "https://example.com/a", "tag": "Project A"},
            {"command": "https://example.com/b", "tag": ""},
        ]
        text = library_records_to_database_text(rows)
        self.assertEqual(
            text,
            "# Project A\nhttps://example.com/a\n#\nhttps://example.com/b",
        )

    def test_mixed_library_accounts_clear_active_profile(self):
        self.assertEqual(
            common_account_profile_id([
                {"account_profile_id": 7},
                {"account_profile_id": 7},
            ]),
            7,
        )
        self.assertIsNone(common_account_profile_id([
            {"account_profile_id": 7},
            {"account_profile_id": None},
        ]))

    def test_invalid_schedule_timestamp_does_not_crash_management(self):
        self.assertEqual(format_schedule_timestamp("not-a-time"), "Invalid timestamp")
        self.assertEqual(format_schedule_timestamp(float("inf")), "Invalid timestamp")


if __name__ == "__main__":
    unittest.main()
