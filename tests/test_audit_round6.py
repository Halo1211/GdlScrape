"""Differential CLI, cancellation, input-boundary and numeric regressions."""

import io
import os
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gallery_dl.option import build_parser
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QPushButton, QTableWidget

from gallery_dl_app.composer import parse_typed_config_value
from gallery_dl_app.core import REDACTED, ensure_no_option, normalize_destination_argv, parse_line, redact_sensitive_argv, redact_sensitive_text
from gallery_dl_app.feature_logic import build_filter_expression
from gallery_dl_app.feature_store import FeatureStore
from gallery_dl_app.management import ManagementMixin, account_action_argv
from gallery_dl_app.workers import CommandProbeWorker, DownloadWorker


def make_worker(**changes):
    values = dict(worker_id=0, task_queue=queue.Queue(), gdl_cmd="gallery-dl", config_path=None,
                  output_dir="", cookies_browser="none", retries=0, extra_args="",
                  pause_event=threading.Event(), stop_event=threading.Event(), cancelled_indices=set(),
                  compress_enabled=False, compress_format="zip", convert_png_webp=False)
    values.update(changes)
    return DownloadWorker(**values)


class CombinedShortOptionTests(unittest.TestCase):
    def test_combined_destinations_match_upstream_parser(self):
        for option in ("-vd", "-qD", "-vqd", "-qvd"):
            with self.subTest(option=option):
                tokens = [option, "fixture-folder", "https://example.com/a"]
                upstream = build_parser().parse_args(tokens)
                if option.endswith("D"):
                    self.assertEqual(upstream.directory, "fixture-folder")
                else:
                    self.assertIn(((), "base-directory", "fixture-folder"), upstream.options)
                self.assertEqual(parse_line("gallery-dl " + " ".join(tokens)).dest, "fixture-folder")
                self.assertFalse(ensure_no_option(tokens, ("-d", "-D")))

    def test_attached_combined_destination_is_normalized_on_windows(self):
        with patch("gallery_dl_app.core.IS_WINDOWS", True):
            normalized = normalize_destination_argv(["-vqdfixture-folder...", "https://example.com/a"])
        self.assertEqual(parse_line("gallery-dl " + " ".join(normalized)).dest, "fixture-folder")

    def test_url_values_in_combined_options_are_not_job_targets(self):
        for option in ("-vo", "-vN", "-qa"):
            with self.subTest(option=option):
                tokens = [option, "https://example.com/option-value", "https://example.com/real"]
                self.assertEqual(build_parser().parse_args(tokens).urls, ["https://example.com/real"])
                self.assertEqual(parse_line("gallery-dl " + " ".join(tokens)).url, "https://example.com/real")

    def test_destination_template_with_combined_option_is_not_runnable(self):
        self.assertIsNone(parse_line("gallery-dl -vd fixture-folder"))

    def test_worker_does_not_override_combined_cookie_or_retry_options(self):
        job = parse_line("gallery-dl -qR7 -vC fixture-cookies.txt https://example.com/a")
        argv = make_worker(retries=2, cookies_browser="chrome").build_command(job)
        self.assertNotIn("--retries", argv)
        self.assertNotIn("--cookies-from-browser", argv)

    def test_combined_values_are_redacted_in_argv_text_and_repr(self):
        for flag, value in (("-vp", "fixture-password"), ("-qu", "fixture-user"), ("-qC", "fixture-cookies"), ("-vqp", "fixture-password")):
            for attached in (False, True):
                with self.subTest(flag=flag, attached=attached):
                    tokens = [flag + value] if attached else [flag, value]
                    tokens.append("https://example.com/a")
                    for redacted in (str(redact_sensitive_argv(tokens)), redact_sensitive_text("gallery-dl " + " ".join(tokens)), redact_sensitive_text(repr(tokens))):
                        self.assertNotIn(value, redacted)
                        self.assertIn(REDACTED, redacted)
                        self.assertIn("https://example.com/a", redacted)

    def test_combined_option_payloads_are_not_reinterpreted_as_flags(self):
        job = parse_line('gallery-dl -f "-vd" https://example.com/a')
        self.assertEqual(job.dest, "-")
        self.assertEqual(normalize_destination_argv(["--", "-vd"]), ["--", "-vd"])

    def test_combined_config_options_redact_entire_quoted_secrets(self):
        secret = "fixture secret phrase"
        commands = [
            f'gallery-dl -vo "password={secret}" https://example.com/a',
            f'gallery-dl -vopassword="{secret}" https://example.com/a',
            repr(["-vo", f"password={secret}", "https://example.com/a"]),
            repr(["-vopassword=" + secret, "https://example.com/a"]),
            'gallery-dl -vopassword=fixture-secret https://example.com/a',
            'gallery-dl -vo "password=fixture secret phrase https://example.com/a',
        ]
        for command in commands:
            with self.subTest(command=command):
                redacted = redact_sensitive_text(command)
                for word in secret.split():
                    self.assertNotIn(word, redacted)
                if '"password=fixture secret phrase https://' not in command:
                    self.assertIn("https://example.com/a", redacted)
                self.assertIn(REDACTED, redacted)


class NumericInputTests(unittest.TestCase):
    def test_nonfinite_config_numbers_are_rejected(self):
        for value in ("nan", "inf", "-inf", "1e309"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_typed_config_value(value, "number")

    def test_nonfinite_json_numbers_are_rejected_recursively(self):
        for value in ('{"timeout": NaN}', '[Infinity]', '{"nested":[1e309]}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_typed_config_value(value, "json")

    def test_finite_scientific_config_number_is_preserved(self):
        self.assertEqual(parse_typed_config_value("1.5e3", "number"), 1500.0)
        self.assertEqual(parse_typed_config_value('{"nested":[1.5e3]}', "json"), {"nested": [1500.0]})

    def test_filter_overflow_does_not_generate_an_undefined_inf_name(self):
        for value in ("1e309", "[1e309]", "(1, 1e309)"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_filter_expression([("width", ">", value)])


class ProcessLifecycleTests(unittest.TestCase):
    def test_account_actions_insert_options_before_base_boundary(self):
        for action in ("status", "clear_all", "vacuum", "oauth"):
            with self.subTest(action=action):
                argv = account_action_argv("gallery-dl --", action, site="pixiv", cache_file="fixture-cache.sqlite3", config_path="fixture-config.json")
                ns = build_parser().parse_args(argv[1:])
                self.assertEqual(ns.cache_file, "fixture-cache.sqlite3")
                self.assertIn("fixture-config.json", str(ns))
                self.assertEqual(ns.urls, ["oauth:pixiv"] if action == "oauth" else [])

    def test_probe_options_go_before_gallery_dl_boundary(self):
        worker = CommandProbeWorker("gallery-dl --", ["--version"])
        proc = MagicMock(returncode=0)
        proc.communicate.return_value = ("1.32.12", "")
        proc.poll.return_value = 0
        with patch("gallery_dl_app.workers.subprocess.Popen", return_value=proc) as popen:
            worker.run()
        argv = popen.call_args.args[0]
        self.assertLess(argv.index("--version"), argv.index("--"))

    def test_probe_communication_error_does_not_orphan_child_or_pipes(self):
        worker = CommandProbeWorker("gallery-dl", ["--version"])
        proc = MagicMock(returncode=None)
        proc.communicate.side_effect = OSError("fixture broken pipe")
        proc.poll.return_value = None
        with patch("gallery_dl_app.workers.subprocess.Popen", return_value=proc):
            worker.run()
        proc.kill.assert_called_once()
        proc.wait.assert_called_once()
        proc.stdout.close.assert_called_once()
        proc.stderr.close.assert_called_once()
        self.assertIsNone(worker._probe_proc)

    def test_cancel_interrupts_wait_for_postprocessing_lock(self):
        lock = threading.Lock()
        lock.acquire()
        started = threading.Event()
        worker = make_worker(compress_enabled=True)
        worker.task_queue.put((0, parse_line("gallery-dl -d fixture-folder https://example.com/a")))
        proc = MagicMock(returncode=0, stdout=io.StringIO(""), stderr=None)
        proc.poll.return_value = 0
        proc.wait.side_effect = lambda **kwargs: started.set() or 0
        thread = threading.Thread(target=worker.run)
        with patch.object(DownloadWorker, "_postprocess_lock", lock), patch("gallery_dl_app.workers.subprocess.Popen", return_value=proc):
            try:
                thread.start()
                self.assertTrue(started.wait(2))
                worker.stop_event.set()
                thread.join(1)
                exited_while_locked = not thread.is_alive()
            finally:
                lock.release()
                thread.join(2)
        self.assertTrue(exited_while_locked, "Cancelled job stayed blocked behind another job's postprocessing")
        self.assertEqual(worker.task_queue.unfinished_tasks, 0)


class SchedulerInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_invalid_schedule_is_rejected_before_database_write(self):
        for commands_text in ("# comments only", "gallery-dl -d fixture-folder", "https://example.com/" + "a" * 40):
            with self.subTest(commands=commands_text), tempfile.TemporaryDirectory() as folder:
                store = FeatureStore(Path(folder) / "fixture.sqlite3")
                editor = QPlainTextEdit(commands_text)
                target = SimpleNamespace(feature_store=store, txt_commands=editor, _table=ManagementMixin._table)
                page = ManagementMixin._build_scheduler_tab(target)
                try:
                    with patch("gallery_dl_app.core.MAX_COMMAND_LINE_CHARS", 48), patch("gallery_dl_app.management.QMessageBox.warning") as warning:
                        next(button for button in page.findChildren(QPushButton) if button.text() == "Save").click()
                    warning.assert_called_once()
                    self.assertEqual(store.list_schedules(), [])
                    self.assertEqual(page.findChild(QPlainTextEdit).toPlainText(), commands_text)
                finally:
                    page.deleteLater()
                    editor.deleteLater()
                    self.app.processEvents()

    def test_schedule_save_keeps_selection_when_due_order_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            store = FeatureStore(Path(folder) / "fixture.sqlite3")
            first_id = store.save_schedule({"name": "First", "command_text": "https://example.com/first", "next_run_at": 1})
            store.save_schedule({"name": "Second", "command_text": "https://example.com/second", "next_run_at": 2})
            editor = QPlainTextEdit()
            target = SimpleNamespace(feature_store=store, txt_commands=editor, _table=ManagementMixin._table)
            page = ManagementMixin._build_scheduler_tab(target)
            try:
                table = page.findChild(QTableWidget)
                table.selectRow(0)
                next(button for button in page.findChildren(QPushButton) if button.text() == "Save").click()
                self.assertEqual(int(table.item(table.currentRow(), 0).text()), first_id)
                self.assertEqual(page.findChild(QPlainTextEdit).toPlainText(), "https://example.com/first")
            finally:
                page.deleteLater()
                editor.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
