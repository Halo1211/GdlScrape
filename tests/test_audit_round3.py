"""Third audit pass: linked output trees, secrets, metadata, and scheduled state."""

import base64
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gallery_dl_app as package
from gallery_dl_app.advanced_tools import AdvancedToolsMixin
from gallery_dl_app.core import parse_line, parse_text_database, process_is_running
from gallery_dl_app.feature_store import FeatureStore
from gallery_dl_app.management import ManagementMixin
from gallery_dl_app.models import JobResult
from gallery_dl_app.queue_controller import QueueControllerMixin
from gallery_dl_app.reports import ReportsMixin, scan_output_tree
from gallery_dl_app.secure_vault import SecretVault
from gallery_dl_app.system_tools import SystemToolsMixin
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


def add_junction(link, target):
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if result.returncode:
        raise AssertionError(result.stderr.decode(errors="replace"))


class OutputTreeTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows junction")
    def test_external_archiver_is_not_started_on_tree_containing_junction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            destination = root / "gallery"
            outside = root / "outside"
            destination.mkdir()
            outside.mkdir()
            add_junction(destination / "linked", outside)
            job = parse_line(f'gallery-dl -d "{destination}" https://example.com/a')
            child = MagicMock()
            child.returncode = 1
            child.communicate.return_value = ("", "fixture")
            with (
                patch("gallery_dl_app.workers.shutil.which", return_value="fixture-7z"),
                patch("gallery_dl_app.workers.subprocess.Popen", return_value=child) as spawn,
            ):
                result = worker_for(compress_enabled=True, compress_format="7z").maybe_compress(job)
            self.assertIn("link", result.lower())
            spawn.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows junction")
    def test_zip_does_not_archive_files_outside_destination_through_junction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            destination = root / "gallery"
            outside = root / "outside"
            destination.mkdir()
            outside.mkdir()
            (destination / "own.txt").write_text("own")
            (outside / "private.txt").write_text("outside fixture")
            add_junction(destination / "linked", outside)
            job = parse_line(f'gallery-dl -d "{destination}" https://example.com/a')
            result = worker_for(compress_enabled=True).maybe_compress(job)
            self.assertIn("compressed:", result)
            with zipfile.ZipFile(root / "gallery.zip") as archive:
                self.assertEqual(archive.namelist(), ["gallery/own.txt"])

    @unittest.skipUnless(os.name == "nt", "Windows junction")
    def test_conversion_does_not_write_outside_destination_through_junction(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            destination = root / "gallery"
            outside = root / "outside"
            destination.mkdir()
            outside.mkdir()
            Image.new("RGB", (2, 2)).save(outside / "image.png")
            add_junction(destination / "linked", outside)
            job = parse_line(f'gallery-dl -d "{destination}" https://example.com/a')
            worker_for(convert_png_webp=True).maybe_compress(job)
            self.assertFalse((outside / "image.webp").exists())

    @unittest.skipUnless(os.name == "nt", "Windows junction")
    def test_output_scan_does_not_count_outside_files_through_junction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            destination = root / "gallery"
            outside = root / "outside"
            destination.mkdir()
            outside.mkdir()
            (destination / "own.txt").write_bytes(b"own")
            (outside / "private.txt").write_bytes(b"outside fixture")
            add_junction(destination / "linked", outside)
            result = scan_output_tree(destination)
            self.assertEqual((result["files"], result["total"]), (1, 3))

    def test_postprocessing_expands_destination_environment_variables(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "gallery"
            destination.mkdir()
            (destination / "own.txt").write_text("own")
            with patch.dict(os.environ, {"AUDIT_OUTPUT_ROOT": folder}):
                path = "%AUDIT_OUTPUT_ROOT%/gallery" if os.name == "nt" else "$AUDIT_OUTPUT_ROOT/gallery"
                job = parse_line(f'gallery-dl -d "{path}" https://example.com/a')
                result = worker_for(compress_enabled=True).maybe_compress(job)
            self.assertIn("compressed:", result)
            self.assertTrue(Path(folder, "gallery.zip").exists())


class ProcessOwnershipTests(unittest.TestCase):
    def test_exited_child_with_windows_still_active_exit_code_is_not_live(self):
        child = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(259)"],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        child.wait(timeout=5)
        self.assertFalse(process_is_running(child.pid))

    def test_process_probe_detects_live_and_exited_child_without_killing_it(self):
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            self.assertTrue(process_is_running(child.pid))
            self.assertIsNone(child.poll())
        finally:
            child.terminate()
            child.wait(timeout=5)
        self.assertFalse(process_is_running(child.pid))

    def test_exited_owner_config_is_removed_and_run_remains_recoverable(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        child.wait(timeout=5)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime = root / "runtime"
            runtime.mkdir()
            config = runtime / f"secure-config-{child.pid}-{'a' * 32}.json"
            config.write_text("{}")
            with patch("gallery_dl_app.management.SECURE_RUNTIME_DIR", runtime):
                ManagementMixin._cleanup_stale_secure_configs(SimpleNamespace())
            self.assertFalse(config.exists())
            store = FeatureStore(root / "features.sqlite3")
            run_id = store.begin_run(parse_text_database("https://example.com/a"), [0])
            with store._connect() as db:
                db.execute("UPDATE runs SET owner_pid=? WHERE id=?", (child.pid, run_id))
            target = SimpleNamespace(active_workers=0, feature_store=store)
            with patch("gallery_dl_app.management.QMessageBox.question", return_value=0) as question:
                ManagementMixin._offer_crash_recovery(target)
            question.assert_called_once()
            self.assertEqual(store.interrupted_runs(), [])


class VaultBackendTests(unittest.TestCase):
    def test_new_keyring_secret_removes_older_dpapi_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "vault.json"
            path.write_text(json.dumps({"account:old": "old encrypted fixture", "account:other": "keep"}))
            vault = SecretVault(path)
            vault._keyring = MagicMock()
            vault._keyring.get_password.return_value = "previous keyring fixture"
            vault.set("account:old", "new fixture")
            self.assertEqual(json.loads(path.read_text()), {"account:other": "keep"})

    def test_keyring_update_rolls_back_if_legacy_cleanup_cannot_be_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "vault.json"
            original = json.dumps({"account:old": "old encrypted fixture"})
            path.write_text(original)
            vault = SecretVault(path)
            vault._keyring = MagicMock()
            vault._keyring.get_password.return_value = "previous keyring fixture"
            with patch("gallery_dl_app.secure_vault.atomic_write_text", side_effect=OSError("fixture locked")):
                with self.assertRaises(OSError):
                    vault.set("account:old", "new fixture")
            self.assertEqual(path.read_text(), original)
            self.assertEqual(vault._keyring.set_password.call_args.args[-1], "previous keyring fixture")

    @unittest.skipUnless(os.name == "nt", "DPAPI backend")
    def test_dpapi_secret_remains_readable_when_keyring_becomes_available(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "vault.json"
            path.write_text(json.dumps({"account:old": base64.b64encode(b"fixture-ciphertext").decode()}))
            vault = SecretVault(path)
            vault._keyring = MagicMock()
            vault._keyring.get_password.return_value = None
            with patch.object(vault, "_dpapi_unprotect", return_value=b"fixture secret"):
                self.assertEqual(vault.get("account:old"), "fixture secret")

    def test_delete_removes_secret_from_both_backends(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "vault.json"
            path.write_text(json.dumps({"account:old": "encrypted fixture", "account:other": "keep"}))
            vault = SecretVault(path)
            vault._keyring = MagicMock()
            vault._keyring.get_password.return_value = "keyring fixture"
            vault.delete("account:old")
            self.assertEqual(json.loads(path.read_text()), {"account:other": "keep"})
            vault._keyring.delete_password.assert_called_once()


class ImportExportTests(unittest.TestCase):
    def test_headerless_xlsx_preserves_tag_headers_notes_and_resets(self):
        from openpyxl import Workbook

        source = "# Project\n#@notes First note\nhttps://example.com/a\n#\nhttps://example.com/b"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.xlsx"
            book = Workbook()
            for line in source.splitlines():
                book.active.append([line])
            book.save(path)
            book.close()
            output = QueueControllerMixin().read_xlsx_as_commands(str(path))
        self.assertEqual([(j.url, j.tag, j.notes) for j in parse_text_database(output)],
                         [(j.url, j.tag, j.notes) for j in parse_text_database(source)])

    def test_export_failed_preserves_job_tags_notes_and_resets(self):
        jobs = parse_text_database("# Project\n#@notes First note\nhttps://example.com/a\n#\nhttps://example.com/b")
        target = SimpleNamespace(jobs=jobs, failed_indices={0, 1}, stopped_indices=set(),
                                 cancelled_indices=set(), _safe_write_file=MagicMock())
        with patch("gallery_dl_app.reports.QFileDialog.getSaveFileName", return_value=("fixture.txt", "")):
            ReportsMixin.export_failed(target)
        recovered = parse_text_database(target._safe_write_file.call_args.args[1])
        self.assertEqual([(j.url, j.tag, j.notes) for j in recovered],
                         [(j.url, j.tag, j.notes) for j in jobs])


class AuditUiTests(unittest.TestCase):
    def test_second_instance_does_not_remove_live_account_config(self):
        with (
            patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None),
            patch.object(SystemToolsMixin, "autosave_session", lambda _self: None),
            patch.object(ManagementMixin, "_start_management_services", lambda _self: None),
            tempfile.TemporaryDirectory() as folder,
        ):
            root = Path(folder)
            app, window = package.create_application([])
            try:
                window.feature_store = FeatureStore(root / "features.sqlite3")
                window.active_account_profile_id = window.feature_store.save_account({
                    "name": "Active profile", "site": "example", "auth_kind": "browser",
                    "cookie_source": "firefox",
                })
                window.config_path = None
                with patch("gallery_dl_app.management.SECURE_RUNTIME_DIR", root / "runtime"):
                    config = window.prepare_active_account_config()
                    ManagementMixin._cleanup_stale_secure_configs(SimpleNamespace())
                    self.assertTrue(Path(config).exists())
                window.cleanup_run_account_config()
                self.assertFalse(Path(config).exists())
            finally:
                window.close()
                app.processEvents()

    def test_live_run_is_not_offered_as_crash_recovery_to_second_instance(self):
        with tempfile.TemporaryDirectory() as folder:
            store = FeatureStore(Path(folder) / "features.sqlite3")
            store.begin_run(parse_text_database("https://example.com/active"), [0])
            target = SimpleNamespace(active_workers=0, feature_store=store)
            with patch("gallery_dl_app.management.QMessageBox.question", return_value=0) as question:
                ManagementMixin._offer_crash_recovery(target)
            question.assert_not_called()
            self.assertEqual(len(store.interrupted_runs()), 1)

    def test_validator_config_is_cleaned_if_worker_construction_fails(self):
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
                    "name": "Validator account", "site": "example", "auth_kind": "browser",
                    "cookie_source": "firefox",
                })
                window.config_path = None
                window.txt_commands.setPlainText("https://example.com/a")
                with (
                    patch("gallery_dl_app.management.SECURE_RUNTIME_DIR", runtime),
                    patch("gallery_dl_app.advanced_tools.DownloadWorker", side_effect=RuntimeError("fixture failure")),
                    patch.object(window, "show_compact_message") as show_error,
                ):
                    window.dry_run_validator()
                show_error.assert_called_once()
                self.assertEqual(list(runtime.glob("secure-config-*.json")), [])
            finally:
                # Dispose any fixture config leaked by the pre-fix code.
                window.close()
                app.processEvents()

    def test_schedule_runs_its_snapshot_even_when_identical_manual_jobs_are_disabled(self):
        with (
            patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None),
            patch.object(SystemToolsMixin, "autosave_session", lambda _self: None),
            patch.object(ManagementMixin, "_start_management_services", lambda _self: None),
            tempfile.TemporaryDirectory() as folder,
        ):
            app, window = package.create_application([])
            try:
                window.feature_store = FeatureStore(Path(folder) / "features.sqlite3")
                window.gdl_cmd = subprocess.list2cmdline([sys.executable, "-c", "print('C:/fixture/image.jpg')"])
                source = "https://example.com/same"
                window.txt_commands.setPlainText(source)
                window._rebuild_from_text()
                window.jobs[0].enabled = False
                window.notify_finished = lambda: None
                window.show_compact_message = MagicMock()
                window.feature_store.save_schedule({
                    "name": "Same snapshot", "command_text": source, "frequency": "interval",
                    "interval_minutes": 60, "next_run_at": time.time() - 1,
                })
                window._poll_schedules()
                started = window.active_workers > 0
                deadline = time.monotonic() + 10
                while window.active_workers > 0 and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                app.processEvents()
                self.assertTrue(started)
                self.assertFalse(window.jobs[0].enabled)
                self.assertEqual(window.feature_store.list_history()[0]["status"], "done")
            finally:
                window.close()
                app.processEvents()

    def test_scheduled_run_preserves_manual_results_disabled_jobs_and_selection(self):
        with (
            patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None),
            patch.object(SystemToolsMixin, "autosave_session", lambda _self: None),
            patch.object(ManagementMixin, "_start_management_services", lambda _self: None),
            tempfile.TemporaryDirectory() as folder,
        ):
            app, window = package.create_application([])
            try:
                window.feature_store = FeatureStore(Path(folder) / "features.sqlite3")
                window.gdl_cmd = subprocess.list2cmdline([sys.executable, "-c", "print('C:/fixture/image.jpg')"])
                window.txt_commands.setPlainText("https://example.com/manual-a\nhttps://example.com/manual-b")
                window._rebuild_from_text()
                window.results[0] = JobResult(0, "failed", message="network fixture")
                window.failed_indices.add(0)
                window.jobs[1].enabled = False
                window.populate_queue_table()
                window.tbl_queue.selectRow(1)
                notifications = []
                window.notify_finished = lambda: notifications.append((len(window.done_indices), len(window.failed_indices)))
                window.feature_store.save_schedule({
                    "name": "State preservation", "command_text": "https://example.com/scheduled",
                    "frequency": "interval", "interval_minutes": 60, "next_run_at": time.time() - 1,
                })
                window._poll_schedules()
                deadline = time.monotonic() + 10
                while window.active_workers > 0 and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                app.processEvents()
                self.assertEqual(window.active_workers, 0)
                self.assertEqual(window.failed_indices, {0})
                self.assertEqual(window.results[0].message, "network fixture")
                self.assertFalse(window.jobs[1].enabled)
                self.assertEqual(window.selected_indices(), [1])
                self.assertEqual(notifications, [(1, 0)])
            finally:
                window.close()
                app.processEvents()

    def test_dry_run_validator_uses_selected_account_and_cleans_preview_config(self):
        with (
            patch.object(ReportsMixin, "_load_autosave_silently", lambda _self: None),
            patch.object(SystemToolsMixin, "autosave_session", lambda _self: None),
            patch.object(ManagementMixin, "_start_management_services", lambda _self: None),
            tempfile.TemporaryDirectory() as folder,
        ):
            root = Path(folder)
            runtime = root / "runtime"
            app, window = package.create_application([])
            captured = {}
            try:
                window.feature_store = FeatureStore(root / "features.sqlite3")
                window.active_account_profile_id = window.feature_store.save_account({
                    "name": "Validator account", "site": "example", "auth_kind": "browser",
                    "cookie_source": "firefox",
                })
                window.combo_cookies.setCurrentText("chrome")
                window.config_path = None
                window.txt_commands.setPlainText("https://example.com/a")

                def capture(_title, message, _level):
                    captured["message"] = message
                    captured["configs"] = [json.loads(p.read_text()) for p in runtime.glob("secure-config-*.json")]

                with (
                    patch("gallery_dl_app.management.SECURE_RUNTIME_DIR", runtime),
                    patch.object(window, "show_scroll_message", side_effect=capture),
                ):
                    AdvancedToolsMixin.dry_run_validator(window)
                self.assertEqual(len(captured["configs"]), 1)
                self.assertEqual(captured["configs"][0]["extractor"]["example"]["cookies"], ["firefox"])
                self.assertNotIn("--cookies-from-browser chrome", captured["message"])
                self.assertEqual(list(runtime.glob("secure-config-*.json")), [])
            finally:
                window.close()
                app.processEvents()
