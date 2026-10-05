"""Regression coverage for persistence, backup integrity, and queue review."""

import os
import json
import sqlite3
import subprocess
import tempfile
import threading
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from gallery_dl_app.core import parse_text_database
from gallery_dl_app.feature_store import FeatureStore
from gallery_dl_app.management import interrupted_recovery_commands, library_records_to_database_text
from gallery_dl_app.queue_controller import QueueControllerMixin
from gallery_dl_app.reports import write_app_data_backup


class PersistenceFollowupTests(unittest.TestCase):
    def test_history_migration_tolerates_integer_counts_beyond_sqlite_range(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            history = root / "history.jsonl"
            history.write_text(json.dumps({
                "url": "https://example.com/a", "downloaded": 10 ** 100, "skipped": -(10 ** 100),
            }) + "\n", encoding="utf-8")
            store = FeatureStore(root / "features.sqlite3")
            self.assertEqual(store.migrate_jsonl_history(history), 1)
            row = store.list_history()[0]
            self.assertEqual(row["downloaded"], (1 << 63) - 1)
            self.assertEqual(row["skipped"], 0)

    def test_history_writer_tolerates_integer_counts_beyond_sqlite_range(self):
        with tempfile.TemporaryDirectory() as folder:
            store = FeatureStore(Path(folder) / "features.sqlite3")
            store.record_history({"url": "https://example.com/a", "downloaded": 10 ** 100})
            self.assertEqual(store.list_history()[0]["downloaded"], (1 << 63) - 1)

    def test_existing_database_migrates_metadata_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "features.sqlite3"
            with closing(sqlite3.connect(path)) as db:
                db.executescript("""
                    CREATE TABLE library_entries (
                        id INTEGER PRIMARY KEY, title TEXT DEFAULT '', url TEXT UNIQUE,
                        command TEXT DEFAULT '', service TEXT DEFAULT '-', tag TEXT DEFAULT '',
                        output_dir TEXT DEFAULT '', account_profile_id INTEGER, enabled INTEGER DEFAULT 1,
                        created_at REAL, updated_at REAL, last_run_at REAL,
                        last_status TEXT DEFAULT 'never', last_downloaded INTEGER DEFAULT 0);
                    INSERT INTO library_entries(id, url) VALUES(1, 'https://example.com/a');
                    CREATE TABLE runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, started_at REAL,
                        finished_at REAL, status TEXT, source TEXT DEFAULT 'manual');
                    CREATE TABLE run_items (
                        run_id INTEGER, job_index INTEGER, command TEXT, url TEXT,
                        status TEXT DEFAULT 'queued', downloaded INTEGER DEFAULT 0,
                        skipped INTEGER DEFAULT 0, return_code INTEGER, message TEXT DEFAULT '',
                        updated_at REAL, PRIMARY KEY(run_id, job_index));
                """)
            store = FeatureStore(path)
            self.assertEqual(store.list_library()[0]["notes"], "")
            store.add_library_entry(url="https://example.com/a", notes="Restored")
            self.assertEqual(store.list_library()[0]["id"], 1)
            store.begin_run(parse_text_database("# Group\n#@notes Note\nhttps://example.com/a"), [0])
            self.assertEqual(store.interrupted_runs()[0]["notes"], "Note")

    def test_metadata_storage_redacts_inline_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            store = FeatureStore(Path(folder) / "features.sqlite3")
            note = "gallery-dl --password fixture-password https://example.com/a"
            store.add_library_entry(url="https://example.com/a", notes=note)
            jobs = parse_text_database(f"#@notes {note}\nhttps://example.com/a")
            store.begin_run(jobs, [0])
            for record in [store.list_library()[0], store.interrupted_runs()[0]]:
                self.assertNotIn("fixture-password", record["notes"])

    def test_library_notes_survive_store_and_queue_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            store = FeatureStore(Path(folder) / "features.sqlite3")
            store.add_library_entry(url="https://example.com/a", tag="Art", notes="Keep original")
            jobs = parse_text_database(library_records_to_database_text(store.list_library()))
            self.assertEqual((jobs[0].tag, jobs[0].notes), ("Art", "Keep original"))

    def test_recovery_preserves_tags_notes_resets_and_duplicates(self):
        source = "# Art\n#@notes Keep original\nhttps://example.com/a\n#\nhttps://example.com/a"
        jobs = parse_text_database(source)
        with tempfile.TemporaryDirectory() as folder:
            store = FeatureStore(Path(folder) / "features.sqlite3")
            store.begin_run(jobs, [0, 1])
            recovered = parse_text_database("\n".join(interrupted_recovery_commands(store.interrupted_runs())))
            self.assertEqual([(j.raw, j.tag, j.notes) for j in recovered],
                             [(j.raw, j.tag, j.notes) for j in jobs])

    def test_legacy_library_output_directory_survives_queue_restore(self):
        rows = [{"url": "https://example.com/a", "output_dir": "D:/Creator  Name"}]
        jobs = parse_text_database(library_records_to_database_text(rows))
        self.assertEqual(jobs[0].dest, "D:/Creator  Name")

    def test_empty_library_rows_do_not_change_next_jobs_tag(self):
        rows = [{"tag": "Invalid"}, {"url": "https://example.com/a"}]
        self.assertEqual(library_records_to_database_text(rows), "https://example.com/a")


class BackupFollowupTests(unittest.TestCase):
    def test_live_sqlite_backup_remains_restorable_after_wal_checkpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / "library.sqlite3"
            target = root / "backups" / "data.zip"
            with closing(sqlite3.connect(database)) as source:
                source.execute("PRAGMA journal_mode=WAL")
                source.execute("PRAGMA wal_autocheckpoint=0")
                source.execute("CREATE TABLE fixture(value TEXT)")
                source.execute("INSERT INTO fixture VALUES('saved before backup')")
                source.commit()
                original_write = zipfile.ZipFile.write

                def write(archive, filename, arcname=None, *args, **kwargs):
                    result = original_write(archive, filename, arcname, *args, **kwargs)
                    if str(arcname) == "library.sqlite3":
                        source.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    return result

                with patch("gallery_dl_app.reports.zipfile.ZipFile.write", new=write):
                    write_app_data_backup(root, target.parent, target)
                restored = root / "restored"
                with zipfile.ZipFile(target) as archive:
                    archive.extractall(restored)
                    members = archive.namelist()
                with closing(sqlite3.connect(restored / "library.sqlite3")) as copy:
                    self.assertEqual(copy.execute("SELECT value FROM fixture").fetchall(),
                                     [("saved before backup",)])
                self.assertNotIn("library.sqlite3-wal", members)
                self.assertNotIn("library.sqlite3-shm", members)

    @unittest.skipUnless(os.name == "nt", "Windows directory junction")
    def test_backup_does_not_follow_directory_junction_outside_source(self):
        import zipfile

        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / "source"
            source.mkdir()
            outside = base / "outside"
            outside.mkdir()
            (outside / "external.txt").write_text("outside fixture")
            junction = source / "linked"
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                                    capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            target = base / "backups" / "data.zip"
            write_app_data_backup(source, target.parent, target)
            with zipfile.ZipFile(target) as archive:
                self.assertEqual(archive.namelist(), [])
            self.assertTrue((outside / "external.txt").exists())

    def test_unreadable_source_preserves_previous_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            blocked = root / "data"
            blocked.mkdir()
            target = root / "backups" / "latest.zip"
            target.parent.mkdir()
            target.write_bytes(b"previous backup")
            original_scandir = os.scandir

            def scandir(path):
                if Path(path) == blocked:
                    raise PermissionError("fixture directory denied")
                return original_scandir(path)

            with patch("gallery_dl_app.reports.os.scandir", side_effect=scandir):
                with self.assertRaises(PermissionError):
                    write_app_data_backup(root, target.parent, target)
            self.assertEqual(target.read_bytes(), b"previous backup")

    def test_missing_source_does_not_create_successful_empty_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "backups" / "latest.zip"
            with self.assertRaises(FileNotFoundError):
                write_app_data_backup(root / "missing", target.parent, target)
            self.assertFalse(target.exists())

    def test_backup_directory_is_pruned_before_scanning(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "backups" / "latest.zip"
            target.parent.mkdir()
            (root / "settings.json").write_text("{}")
            original_scandir = os.scandir

            def scandir(path):
                self.assertNotEqual(Path(path), target.parent)
                return original_scandir(path)

            with patch("gallery_dl_app.reports.os.scandir", side_effect=scandir):
                write_app_data_backup(root, target.parent, target)

    def test_precancelled_backup_does_not_scan_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stop = threading.Event()
            stop.set()
            with patch("gallery_dl_app.reports.os.scandir") as scan:
                with self.assertRaises(InterruptedError):
                    write_app_data_backup(root, root / "backups", root / "backup.zip", stop)
                scan.assert_not_called()


class QueueReviewFollowupTests(unittest.TestCase):
    def test_unchanged_queue_review_preserves_disabled_jobs(self):
        source = "https://example.com/a"
        jobs = parse_text_database(source)
        jobs[0].enabled = False
        target = SimpleNamespace(
            active_workers=0, jobs=jobs,
            txt_commands=SimpleNamespace(toPlainText=lambda: source),
            _clear_result_state=MagicMock(), populate_queue_table=MagicMock(),
            _update_counts=MagicMock(), autosave_session=MagicMock(),
        )
        self.assertTrue(QueueControllerMixin._rebuild_from_text(target))
        self.assertFalse(target.jobs[0].enabled)

    def test_unchanged_queue_review_preserves_download_results(self):
        source = "https://example.com/a"
        target = SimpleNamespace(
            active_workers=0, jobs=parse_text_database(source),
            txt_commands=SimpleNamespace(toPlainText=lambda: source),
            _clear_result_state=MagicMock(), populate_queue_table=MagicMock(),
            _update_counts=MagicMock(), autosave_session=MagicMock(),
        )
        self.assertTrue(QueueControllerMixin._rebuild_from_text(target))
        target._clear_result_state.assert_not_called()

    def test_changed_queue_review_clears_stale_results(self):
        target = SimpleNamespace(
            active_workers=0, jobs=parse_text_database("https://example.com/a"),
            txt_commands=SimpleNamespace(toPlainText=lambda: "https://example.com/b"),
            _clear_result_state=MagicMock(), populate_queue_table=MagicMock(),
            _update_counts=MagicMock(), autosave_session=MagicMock(),
        )
        self.assertTrue(QueueControllerMixin._rebuild_from_text(target))
        target._clear_result_state.assert_called_once()
