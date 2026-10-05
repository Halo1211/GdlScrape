"""End-to-end preparation checks before downloads enter the queue."""

import os
import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QDialog, QLineEdit, QPlainTextEdit, QPushButton

from gallery_dl_app.application_tools import build_application_tools
from gallery_dl_app.core import command_string_to_argv
from gallery_dl_app.link_builder import LinkBuilder

from tests.test_config_guided_media import isolated_window


@pytest.mark.parametrize("invalid", ["not-a-link", "https://example.invalid/unknown", "https://imgur.com/abc12\x7f"])
def test_prepared_link_validation_blocks_whole_queue(tmp_path, invalid):
    path = tmp_path / "config.json"
    path.write_text("{}")
    with isolated_window() as (_app, window):
        window.config_path = str(path)
        window.txt_commands.clear()

        def inspect(dialog):
            prepared = next(field for field in dialog.findChildren(QPlainTextEdit) if field.placeholderText() == "One URL per line")
            prepared.setPlainText("https://imgur.com/abc12\n\n" + invalid)
            next(button for button in dialog.findChildren(QPushButton) if button.text() == "Add to Queue").click()
            assert not window.txt_commands.toPlainText()
            assert not window.jobs
            assert dialog.result() != QDialog.Accepted
            return QDialog.Rejected

        with patch.object(QDialog, "exec", inspect), patch.object(window, "show_compact_message") as message:
            window.open_download_composer()
        assert any("Line 3" in str(call.args) for call in message.call_args_list)
    assert path.read_text() == "{}"


class ProbeFixture(QObject):
    done = Signal(str, int, str)

    def __init__(self, parent):
        super().__init__(parent)
        self.running = False
        self.started = 0

    def isRunning(self):
        return self.running

    def start(self):
        self.running = True
        self.started += 1

    def complete(self, output, code=0, error=""):
        self.running = False
        self.done.emit(output, code, error)


@pytest.mark.parametrize("output,code,error,title", [
    ("1.32.14", 0, "", "Installed gallery-dl version"),
    ("", 1, "fixture error", "Check gallery-dl version failed"),
])
def test_version_check_uses_current_tools_button_without_sidebar(output, code, error, title):
    with isolated_window() as (_app, window):
        assert not hasattr(window, "btn_version_side")
        page = build_application_tools(window)
        page.setParent(window)
        button = page.findChild(QPushButton, "applicationTools_version")
        window.gdl_cmd = "gallery-dl"
        worker = ProbeFixture(window)
        with patch("gallery_dl_app.system_tools.CommandProbeWorker", return_value=worker) as factory, patch.object(window, "show_compact_message") as shown:
            window.check_version()
            assert worker.started == 1 and not button.isEnabled()
            second_page = build_application_tools(window)
            second_page.setParent(window)
            second_button = second_page.findChild(QPushButton, "applicationTools_version")
            assert not second_button.isEnabled()
            window.check_version()
            assert worker.started == 1 and factory.call_count == 1
            worker.complete(output, code, error)
            assert button.isEnabled()
            assert second_button.isEnabled()
            assert shown.call_args.args[0] == title
            second_page.deleteLater()
        page.deleteLater()


def test_portable_build_includes_dynamic_downloaders_and_actions():
    pytest.importorskip("PyInstaller")
    from gallery_dl import downloader, postprocessor
    spec = Path(__file__).resolve().parents[1] / "packaging/GdlScrape.spec"
    captured = {}

    def analysis(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(binaries=[], pure=[], scripts=[], datas=[])

    runpy.run_path(str(spec), init_globals={
        "SPECPATH": str(spec.parent), "Analysis": analysis,
        "PYZ": lambda *args, **kwargs: None, "EXE": lambda *args, **kwargs: None,
        "COLLECT": lambda *args, **kwargs: None,
    })
    included = set(captured["hiddenimports"])
    required = {"gallery_dl.postprocessor." + name for name in postprocessor.modules}
    required.update("gallery_dl.downloader." + name for name in downloader.modules)
    assert required <= included, "Missing dynamically loaded modules: " + ", ".join(sorted(required - included))


@pytest.mark.parametrize("shared_naming", [False, True, "empty-shared"])
def test_queue_keeps_website_naming_and_deduplicates_prepared_links(tmp_path, shared_naming):
    path = tmp_path / "config.json"
    original = {"extractor": {"imgur": {"directory": ["imgur"], "filename": "{id}.{extension}"}}}
    if shared_naming:
        original["extractor"].update(directory=[] if shared_naming == "empty-shared" else ["shared"], filename="shared_{id}.{extension}")
    path.write_text(json.dumps(original))
    with isolated_window() as (_app, window):
        window.config_path = str(path)
        window.txt_commands.clear()

        def inspect(dialog):
            prepared = dialog.findChild(QPlainTextEdit, "composerPreparedLinks")
            prepared.setPlainText("https://imgur.com/abc12\n\nhttps://imgur.com/abc12")
            builder = dialog.findChild(LinkBuilder)
            builder.inputs[0].setText("https://imgur.com/def34")
            destination = next(field for field in dialog.findChildren(QLineEdit) if field.placeholderText() == "D:/Downloads/gallery-dl")
            destination.setText(str(tmp_path / "job-only"))
            next(button for button in dialog.findChildren(QPushButton) if button.text() == "Add to Queue").click()
            assert len(window.jobs) == 2
            assert {job.url for job in window.jobs} == {"https://imgur.com/abc12", "https://imgur.com/def34"}
            for job in window.jobs:
                argv = command_string_to_argv(job.raw)
                assert "-f" not in argv
                assert not any(value.startswith("directory=") for value in argv)
                assert argv[argv.index("-d") + 1] == str(tmp_path / "job-only")
            return QDialog.Accepted

        with patch.object(QDialog, "exec", inspect), patch.object(window, "show_compact_message"):
            window.open_download_composer()
    assert json.loads(path.read_text()) == original
