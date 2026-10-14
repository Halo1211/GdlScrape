"""Regression checks for simulated runs, argv boundaries and Qt ownership."""

import io
import os
import queue
import subprocess
import sys
import threading
import time
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox
from shiboken6 import isValid

from gallery_dl_app.advanced_tools import AdvancedToolsMixin
from gallery_dl_app.core import parse_line
from gallery_dl_app.feature_store import FeatureStore
from gallery_dl_app.queue_controller import QueueControllerMixin
from tests.test_core import make_worker
from tests.test_config_guided_media import isolated_window


@pytest.mark.parametrize("flag", ["--simulate", "-s", "-vs", "--no-download", "--get-urls", "-g", "--dump-json", "-j"])
def test_read_only_job_does_not_run_gui_postprocessing(flag):
    tasks = queue.Queue()
    tasks.put((0, parse_line(f"gallery-dl {flag} -d output https://example.com/item")))
    worker = make_worker(task_queue=tasks, compress_enabled=True, convert_png_webp=True)
    process = MagicMock(stdout=io.StringIO(""), stderr=None, returncode=0)
    process.poll.return_value = 0
    completed = []
    worker.job_done.connect(lambda *values: completed.append(values))
    with patch("gallery_dl_app.workers.subprocess.Popen", return_value=process), patch.object(worker, "maybe_compress", return_value="") as post:
        worker.run()
    assert completed[0][2] == "done"
    post.assert_not_called()
    assert tasks.unfinished_tasks == 0


def test_close_can_wait_for_finished_workers_until_next_run():
    app = QApplication.instance() or QApplication([])
    worker = make_worker()
    controller = MagicMock()
    controller.jobs = [parse_line("https://example.com/item")]
    controller.active_workers = 0
    controller.workers = []
    controller.service_policy = {}
    controller._worker_cancel_lock = threading.Lock()
    controller._worker_cancel = set()
    controller.spin_workers.value.return_value = 1
    controller.prepare_active_account_config.return_value = None
    controller.feature_store.begin_run.return_value = 1
    with patch("gallery_dl_app.queue_controller.DownloadWorker", return_value=worker), patch.object(worker, "start"):
        assert QueueControllerMixin._start_indices(controller, [0], True)
    worker.finished.emit()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert isValid(worker), "the controller must retain completed workers until the batch ends"
    target = SimpleNamespace(
        active_workers=1, workers=[worker], stop_download=MagicMock(),
        current_run_id=None, version_worker=None, tray_icon=None,
        cleanup_run_account_config=MagicMock(), _restore_queue_after_scheduled_run=MagicMock(),
        autosave_session=MagicMock(),
    )
    event = QCloseEvent()
    with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
        AdvancedToolsMixin.closeEvent(target, event)
    assert event.isAccepted()
    worker.deleteLater()


def test_queue_editor_keeps_first_jobs_in_large_valid_commented_import():
    text = "https://example.com/first\n" + "# comment\n" * 20_000 + "https://example.com/last"
    with isolated_window() as (_app, window):
        window.txt_commands.setPlainText(text)
        assert window.txt_commands.toPlainText() == text
        assert window._rebuild_from_text()
        assert [job.url for job in window.jobs] == ["https://example.com/first", "https://example.com/last"]
        assert window.session_data()["commands"] == text


def test_queue_action_does_not_overwrite_input_rejected_by_finished_debounce():
    with isolated_window() as (_app, window):
        window.txt_commands.setPlainText("https://example.com/first\nhttps://example.com/last")
        assert window._rebuild_from_text()
        window.tbl_queue.selectRow(0)
        invalid = "https://example.com/" + "x" * 70_000
        window.txt_commands.setPlainText(invalid)
        with patch.object(window, "show_compact_message"):
            assert not window._rebuild_from_text()
            assert not window._text_debounce.isActive()
            window.delete_selected()
        assert window.txt_commands.toPlainText() == invalid
        assert len(window.jobs) == 2


@pytest.mark.parametrize("action", ["copy_selected_url", "copy_selected_command", "open_selected_destination"])
def test_selected_row_actions_revalidate_before_using_old_model(tmp_path, action):
    target = SimpleNamespace(
        _flush_pending_text_edits=MagicMock(return_value=False),
        selected_indices=MagicMock(return_value=[0]),
        jobs=[parse_line(subprocess.list2cmdline(["gallery-dl", "-D", str(tmp_path), "https://example.com/old"]))],
        show_compact_message=MagicMock(),
    )
    clipboard = MagicMock()
    with patch("gallery_dl_app.queue_controller.QApplication.clipboard", return_value=clipboard), patch("gallery_dl_app.queue_controller.open_path") as open_folder:
        getattr(QueueControllerMixin, action)(target)
    target._flush_pending_text_edits.assert_called_once()
    target.selected_indices.assert_not_called()
    clipboard.setText.assert_not_called()
    open_folder.assert_not_called()


@pytest.mark.parametrize("raw", [
    "gallery-dl -f --simulate https://example.com/item",
    "gallery-dl --filename=--simulate https://example.com/item",
    "gallery-dl -- https://example.com/item --simulate",
])
def test_simulation_text_inside_values_or_after_boundary_is_not_a_mode(raw):
    worker = make_worker()
    assert worker.command_allows_postprocessing(worker.build_command(parse_line(raw)))


def test_close_mixed_finished_and_running_batch_with_real_qthreads(tmp_path):
    with isolated_window() as (app, window):
        window.feature_store = FeatureStore(tmp_path / "features.sqlite3")
        window.config_path = None
        window.notifications_enabled = False
        window.gdl_cmd = subprocess.list2cmdline([
            sys.executable, "-c",
            "import sys,time; time.sleep(1 if sys.argv[-1].endswith('/slow') else 0.02)",
        ])
        window.spin_workers.setValue(2)
        window.txt_commands.setPlainText("https://example.com/fast\nhttps://example.com/slow")
        assert window._rebuild_from_text()
        assert window._start_indices([0, 1], True)
        deadline = time.monotonic() + 5
        while 0 not in window.results and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert 0 in window.results and window.active_workers == 1
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert all(isValid(worker) for worker in window.workers)
        assert window.close()
        assert all(not worker.isRunning() for worker in window.workers)
        assert window.feature_store.interrupted_runs() == []


@pytest.mark.parametrize("simulate", [False, True])
def test_real_local_gallery_dl_download_and_simulation_preserve_mode(tmp_path, simulate):
    from PIL import Image

    source = tmp_path / "source"
    source.mkdir()
    Image.new("RGB", (2, 2), "red").save(source / "sample.png")
    payload = (source / "sample.png").read_bytes()
    destination = tmp_path / "download"
    destination.mkdir()
    if simulate:
        (destination / "existing.png").write_bytes(payload)

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(source)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/sample.png"
        args = ["gallery-dl", "--config-ignore", "--no-input", "-D", str(destination), "-f", "{filename}.{extension}"]
        if simulate:
            args.append("--simulate")
        args.append(url)
        tasks = queue.Queue()
        tasks.put((0, parse_line(subprocess.list2cmdline(args))))
        worker = make_worker(
            task_queue=tasks, gdl_cmd=subprocess.list2cmdline([sys.executable, "-m", "gallery_dl"]),
            output_dir="", retries=0, compress_enabled=True, convert_png_webp=True,
        )
        completed = []
        worker.job_done.connect(lambda *values: completed.append(values))
        worker.run()
        assert completed[0][2] == "done", completed[0][8]
        if simulate:
            assert sorted(path.name for path in destination.iterdir()) == ["existing.png"]
            assert (destination / "existing.png").read_bytes() == payload
            assert not (tmp_path / "download.zip").exists()
        else:
            assert (destination / "sample.png").read_bytes() == payload
            assert (destination / "sample.webp").is_file()
            with zipfile.ZipFile(tmp_path / "download.zip") as archive:
                assert archive.read("download/sample.png") == payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

