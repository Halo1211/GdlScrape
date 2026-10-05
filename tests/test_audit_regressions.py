"""Behavioral regressions found during the broad program audit."""

import csv
import io
import json
import queue
import sqlite3
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from gallery_dl_app.composer import ComposerState, build_composer_argv, build_composer_config
from gallery_dl_app.core import extract_destination, parse_line, parse_text_database
from gallery_dl_app.queue_controller import QueueControllerMixin
from gallery_dl_app.management import ManagementMixin
from gallery_dl_app.oauth_flow import local_oauth_callback_url
from gallery_dl_app.reports import write_app_data_backup
from gallery_dl_app.system_tools import SystemToolsMixin
from gallery_dl_app.workers import DownloadWorker


def worker_for(**changes):
    settings = dict(
        worker_id=0, task_queue=queue.Queue(), gdl_cmd="gallery-dl", config_path=None,
        output_dir="", cookies_browser="none", retries=0, extra_args="",
        pause_event=threading.Event(), stop_event=threading.Event(), cancelled_indices=set(),
        compress_enabled=False, compress_format="zip", convert_png_webp=False,
    )
    settings.update(changes)
    return DownloadWorker(**settings)


class DestinationAndImportTests(unittest.TestCase):
    def test_destination_matches_last_value_and_exact_directory_priority(self):
        cases = [
            (["-d", "first", "-d", "last"], "last"),
            (["--destination=first", "-dlast"], "last"),
            (["-D", "exact", "-d", "base"], "exact"),
            (["-d", "base", "-Dfirst", "--directory=last"], "last"),
            (["-d", "first", "--", "-d", "ignored"], "first"),
            (["--print-to-file", "-d", "ignored", "-d", "real"], "real"),
        ]
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                self.assertEqual(extract_destination(arguments), expected)

    def test_headerless_csv_preserves_spaces_inside_command_arguments(self):
        command = 'gallery-dl -d "D:/Creator  Name" https://example.com/a'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.csv"
            stream = io.StringIO()
            csv.writer(stream).writerow([command])
            path.write_text(stream.getvalue(), encoding="utf-8")
            text = QueueControllerMixin().read_csv_as_commands(str(path))
            self.assertEqual(parse_text_database(text)[0].dest, "D:/Creator  Name")

    def test_headerless_xlsx_preserves_spaces_in_every_command_row(self):
        from openpyxl import Workbook

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.xlsx"
            book = Workbook()
            for index in range(2):
                book.active.append([f'gallery-dl -d "D:/Creator  Name" https://example.com/{index}'])
            book.save(path)
            book.close()
            text = QueueControllerMixin().read_xlsx_as_commands(str(path))
            self.assertEqual([job.dest for job in parse_text_database(text)], ["D:/Creator  Name"] * 2)


class WorkerLifecycleTests(unittest.TestCase):
    def test_pipe_read_failure_does_not_leave_child_or_open_pipe_behind(self):
        worker = worker_for()
        worker.task_queue.put((0, parse_line("https://example.com/a")))
        child = MagicMock()
        child.poll.return_value = None
        child.stdout.readline.side_effect = OSError("pipe read failed")
        results = []
        worker.job_done.connect(lambda *values: results.append(values))
        with patch("gallery_dl_app.workers.subprocess.Popen", return_value=child), patch.object(
            worker, "terminate_current_process"
        ) as terminate:
            worker.run()
        terminate.assert_called_once()
        child.stdout.close.assert_called()
        self.assertEqual(results[0][2], "failed")
        self.assertEqual(worker.task_queue.unfinished_tasks, 0)

    def test_interruption_during_spawn_terminates_child_before_reading_output(self):
        for action in ("stop", "cancel"):
            with self.subTest(action=action):
                worker = worker_for()
                worker.task_queue.put((0, parse_line("https://example.com/a")))
                child = MagicMock()
                child.returncode = 0
                terminated = threading.Event()

                def spawn(*_args, **_kwargs):
                    if action == "stop":
                        worker.stop()
                    else:
                        worker.cancelled_indices.add(0)
                        worker.cancel_job(0)
                    return child

                def read(*_args):
                    self.assertTrue(terminated.is_set(), "interrupted child reached a blocking read")
                    return ""

                child.stdout.readline.side_effect = read
                results = []
                worker.job_done.connect(lambda *values: results.append(values))
                with patch("gallery_dl_app.workers.subprocess.Popen", side_effect=spawn), patch.object(
                    worker, "terminate_current_process", side_effect=lambda: terminated.set()
                ), patch.object(worker, "_terminate_current_process_async"):
                    worker.run()
                self.assertTrue(terminated.is_set())
                self.assertEqual(results[0][2], "stopped" if action == "stop" else "cancelled")
                self.assertEqual(worker.task_queue.unfinished_tasks, 0)

    def test_async_termination_cannot_kill_the_next_job_process(self):
        worker = worker_for()
        old_child, new_child = MagicMock(), MagicMock()
        old_child.pid, new_child.pid = 111, 222
        old_child.poll.return_value = new_child.poll.return_value = None
        old_child.terminate.side_effect = lambda: setattr(old_child.poll, "return_value", 0)
        new_child.terminate.side_effect = lambda: setattr(new_child.poll, "return_value", 0)
        worker._proc = old_child
        with patch("gallery_dl_app.workers.threading.Thread") as thread, patch(
            "gallery_dl_app.workers.os.kill", side_effect=OSError
        ), patch("gallery_dl_app.workers.os.killpg", side_effect=OSError, create=True):
            worker._terminate_current_process_async()
            invocation = thread.call_args.kwargs
            worker._proc = new_child
            invocation["target"](*invocation.get("args", ()))
        old_child.terminate.assert_called_once()
        new_child.terminate.assert_not_called()

    def test_successful_paths_containing_error_words_are_downloads(self):
        worker = worker_for()
        worker.task_queue.put((0, parse_line("https://example.com/a")))
        child = MagicMock()
        child.returncode = 0
        child.stdout.readline.side_effect = [
            "D:/Media/error/one.jpg\n", "./Media/warning/two.jpg\n", "# D:/Media/skip.jpg\n", "",
        ]
        results = []
        worker.job_done.connect(lambda *values: results.append(values))
        with patch("gallery_dl_app.workers.subprocess.Popen", return_value=child):
            worker.run()
        self.assertEqual(results[0][3:7], (2, 1, 0, 0))

    def test_global_extra_arguments_override_gui_fallbacks(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "config.json"
            config.write_text("{}", encoding="utf-8")
            worker = worker_for(
                config_path=str(config), cookies_browser="chrome", output_dir="fallback", retries=3,
                extra_args='--config-ignore -C cookies.txt -R 0 -d "D:/Creator  Name"',
            )
            command = worker.build_command(parse_line("https://example.com/a"))
            self.assertNotIn("--config", command)
            self.assertNotIn("--cookies-from-browser", command)
            self.assertNotIn("--retries", command)
            self.assertNotIn("fallback", command)
            self.assertEqual(extract_destination(command), "D:/Creator  Name")


class ComposerPersistenceTests(unittest.TestCase):
    def test_cookie_update_choice_round_trips_for_file_and_browser(self):
        for source in ({"cookies_file": "cookies.txt"}, {"cookies_browser": "firefox"}):
            with self.subTest(source=source):
                state = ComposerState(cookies_update=False, **source)
                config = build_composer_config(state)
                self.assertIs(config["extractor"].get("cookies-update"), False)
                command = build_composer_argv(state, "https://example.com/a")
                self.assertIn("cookies-update=false", command)

    def test_enabled_postprocessors_preserve_custom_options(self):
        processors = [
            {"name": "metadata", "filename": "custom.json", "mode": "custom", "event": "post"},
            {"name": "metadata", "filename": "info.json", "event": "init", "indent": 4},
            {"name": "zip", "extension": "cbz", "compression": "store", "keep-files": True},
            {"name": "exec", "command": "notify"},
        ]
        existing = {"extractor": {"postprocessors": processors}}
        result = build_composer_config(
            ComposerState(metadata_json=True, info_json=True, archive_format="cbz"), existing=existing,
        )
        actual = result["extractor"]["postprocessors"]
        for processor in processors:
            self.assertIn(processor, actual)
        self.assertEqual(existing["extractor"]["postprocessors"], processors)

    def test_zip_postprocessor_preserves_custom_extension_until_format_changes(self):
        processor = {"name": "zip", "extension": "bundle", "keep-files": True}
        result = build_composer_config(
            ComposerState(archive_format="zip"), existing={"extractor": {"postprocessors": [processor]}},
        )
        self.assertEqual(result["extractor"]["postprocessors"], [processor])

    def test_session_can_explicitly_clear_previously_active_config(self):
        target = MagicMock()
        target.config_path = "old-config.json"
        target.combo_cookies.findText.return_value = -1
        target.combo_archive.findText.return_value = -1
        target.current_theme = "dark"
        target.compact_mode = False
        SystemToolsMixin.apply_session_data(target, {"commands": "", "config_path": None})
        self.assertIsNone(target.config_path)

    def test_missing_session_config_field_preserves_detected_config(self):
        target = MagicMock()
        target.config_path = "detected-config.json"
        target.combo_cookies.findText.return_value = -1
        target.combo_archive.findText.return_value = -1
        target.current_theme = "dark"
        target.compact_mode = False
        SystemToolsMixin.apply_session_data(target, {"commands": ""})
        self.assertEqual(target.config_path, "detected-config.json")


class BackupAndAuthenticationTests(unittest.TestCase):
    def test_backup_cancelled_during_last_file_keeps_existing_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app_data = root / "app"
            app_data.mkdir()
            (app_data / "settings.json").write_text("{}", encoding="utf-8")
            target = root / "backup.zip"
            target.write_bytes(b"original backup")
            stop = threading.Event()
            write = zipfile.ZipFile.write

            def write_and_cancel(archive, *args, **kwargs):
                write(archive, *args, **kwargs)
                stop.set()

            with patch.object(zipfile.ZipFile, "write", write_and_cancel):
                with self.assertRaises(InterruptedError):
                    write_app_data_backup(app_data, app_data / "backups", target, stop)
            self.assertEqual(target.read_bytes(), b"original backup")
            self.assertFalse(list(root.glob("*.tmp")))

    def test_zip_postprocess_cancelled_during_last_file_preserves_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            destination = root / "media"
            destination.mkdir()
            (destination / "image.jpg").write_bytes(b"image")
            target = root / "media.zip"
            target.write_bytes(b"original archive")
            worker = worker_for(compress_enabled=True)
            write = zipfile.ZipFile.write

            def write_and_cancel(archive, *args, **kwargs):
                write(archive, *args, **kwargs)
                worker.stop_event.set()

            job = parse_line(f'gallery-dl -d "{destination}" https://example.com/a')
            with patch.object(zipfile.ZipFile, "write", write_and_cancel):
                self.assertIn("stopped", worker.maybe_compress(job))
            self.assertEqual(target.read_bytes(), b"original archive")

    def test_oauth_callback_rejects_blank_or_duplicate_required_values(self):
        for query in ("state=&code=sample", "state=sample&code=", "state=sample&code=one&code=two"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                local_oauth_callback_url("reddit", "http://localhost:6414/?" + query)

    def test_oauth_profile_preserves_application_keys_needed_by_token_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "active.json"
            config.write_text(json.dumps({"extractor": {"flickr": {
                "api-key": "test-application-key", "api-secret": "test-application-secret",
                "access-token": "stale-user-token", "access-token-secret": "stale-user-secret",
                "size-max": "Original",
            }}}), encoding="utf-8")
            target = MagicMock()
            target.config_path = str(config)
            target.active_account_profile_id = 1
            target.feature_store.account.return_value = {
                "site": "flickr", "auth_kind": "oauth", "cache_file": str(root / "cache.sqlite3"),
            }
            with patch("gallery_dl_app.management.SECURE_RUNTIME_DIR", root / "runtime"):
                result = ManagementMixin.prepare_active_account_config(target)
                saved = json.loads(Path(result).read_text(encoding="utf-8"))
            site = saved["extractor"]["flickr"]
            self.assertEqual(site.get("api-key"), "test-application-key")
            self.assertEqual(site.get("api-secret"), "test-application-secret")
            self.assertNotIn("access-token", site)
            self.assertNotIn("access-token-secret", site)
            target.secret_vault.get.assert_not_called()


class SchedulerFailureTests(unittest.TestCase):
    def test_database_failure_does_not_escape_scheduler_timer(self):
        target = MagicMock()
        target.active_workers = 0
        target.delay_timer = None
        target._scheduled_queue_restore = None
        target.feature_store.due_schedules.side_effect = sqlite3.OperationalError("database is locked")
        ManagementMixin._poll_schedules(target)
        target.append_log.assert_called()
        target.txt_commands.setPlainText.assert_not_called()

    def test_failure_advancing_active_schedule_does_not_restore_manual_queue(self):
        target = MagicMock()
        target.active_workers = 0
        target.delay_timer = None
        target.txt_commands.toPlainText.return_value = "manual queue"
        target.feature_store.due_schedules.return_value = [{
            "id": 1, "name": "job", "frequency": "interval", "interval_minutes": 1,
            "command_text": "https://example.com/a",
        }]

        def start(**_kwargs):
            target.active_workers = 1
            return True

        target._start_download_now.side_effect = start
        target.feature_store.advance_schedule.side_effect = sqlite3.OperationalError("database is locked")
        ManagementMixin._poll_schedules(target)
        self.assertEqual(target._scheduled_queue_restore, "manual queue")
        target._restore_queue_after_scheduled_run.assert_not_called()
        target.active_workers = 0
        ManagementMixin._poll_schedules(target)
        self.assertEqual(target._start_download_now.call_count, 1)
        self.assertEqual(target.feature_store.due_schedules.call_count, 1)
        target.feature_store.advance_schedule.side_effect = None
        target.feature_store.due_schedules.return_value = []
        ManagementMixin._poll_schedules(target)
        self.assertEqual(target._pending_schedule_advances, {})
        self.assertIn("ran_at", target.feature_store.advance_schedule.call_args.kwargs)


class QueueEditingTests(unittest.TestCase):
    def test_queue_edits_do_not_discard_text_waiting_for_debounce(self):
        class Harness(QueueControllerMixin):
            def __init__(self):
                self.active_workers = 0
                self.jobs = parse_text_database("https://example.com/a\nhttps://example.com/b")
                self.txt_commands = MagicMock()
                self.txt_commands.toPlainText.return_value = (
                    "https://example.com/a\nhttps://example.com/b\nhttps://example.com/new"
                )
                self._text_debounce = MagicMock()
                self._text_debounce.isActive.return_value = True

            def selected_indices(self):
                return [0]

            def _clear_result_state(self):
                pass

            def populate_queue_table(self):
                pass

            def _update_counts(self):
                pass

            def autosave_session(self):
                pass

        target = Harness()
        target.delete_selected()
        saved = target.txt_commands.setPlainText.call_args.args[0]
        self.assertEqual([job.url for job in parse_text_database(saved)], [
            "https://example.com/b", "https://example.com/new",
        ])

    def test_failed_reparse_preserves_queue_and_does_not_delete_any_row(self):
        target = MagicMock(spec=QueueControllerMixin)
        target.active_workers = 0
        target._text_debounce = MagicMock()
        target._text_debounce.isActive.return_value = True
        target._rebuild_from_text.return_value = False
        target._flush_pending_text_edits.side_effect = lambda: QueueControllerMixin._flush_pending_text_edits(target)
        target.selected_indices.return_value = [0]
        target.jobs = [parse_line("https://example.com/a")]
        QueueControllerMixin.delete_selected(target)
        self.assertEqual(len(target.jobs), 1)
        target.sync_editor_from_jobs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
