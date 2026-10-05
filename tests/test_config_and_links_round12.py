"""Regression checks for effective config advice and link-file workflows."""

import copy
import json
import os
import zipfile
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog

from gallery_dl_app.config_helper import inspect_config
from gallery_dl_app.url_builder import validate_supported_url
from gallery_dl_app.link_builder import LinkBuilder
from gallery_dl_app.postprocessor_config import postprocessor_options, resolved_postprocessor_action
from gallery_dl_app.config_examples import EXAMPLES


def test_helper_uses_inherited_postprocessor_options():
    data = {"extractor": {"postprocessor-options": {"keep-files": True},
                          "mangadex": {"postprocessors": [{"name": "zip"}]}}}
    original = copy.deepcopy(data)
    assert not inspect_config(data)
    assert data == original
    # A website override replaces the shared dictionary, as in gallery-dl.
    data["extractor"]["mangadex"]["postprocessor-options"] = {"keep-files": False}
    assert any("original media" in item.message[0] for item in inspect_config(data))
    data["postprocessor-options"] = {"keep-files": True}
    assert not inspect_config(data)


def test_helper_checks_postprocessor_options_before_tool_advice():
    data = {"extractor": {"pixiv": {
        "postprocessor-options": {"mode": "archive", "extension": "zip"},
        "postprocessors": [{"name": "ugoira", "extension": "mp4"}],
    }}}
    assert not inspect_config(data, dependencies={"FFmpeg": False})


@pytest.mark.parametrize("suffix", ["\x7f", "\x01"])
def test_link_validator_rejects_control_characters(suffix):
    with pytest.raises(ValueError):
        validate_supported_url("https://imgur.com/abc12" + suffix)


@pytest.fixture
def editor():
    app = QApplication.instance() or QApplication([])
    widget = LinkBuilder(lambda links: len(links))
    yield widget
    widget.close()
    app.processEvents()


def test_import_link_file_accepts_unicode_text_and_deduplicates(tmp_path, editor):
    path = tmp_path / "links.txt"
    links = ["https://imgur.com/abc12", "https://www.pixiv.net/artworks/12345"]
    path.write_text("\n" + "\n\n".join([*links, links[0]]), encoding="utf-16")
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")):
        editor.import_links()
    assert editor.site_key() == "" and editor.mode.currentData() == "url"
    assert editor.batch.isChecked() and editor.generated_links() == links
    assert editor.export.isEnabled()


def test_invalid_or_missing_link_import_keeps_draft(tmp_path, editor):
    editor.site.setCurrentIndex(editor.site.findData("pixiv"))
    editor.mode.setCurrentIndex(editor.mode.findData("artwork"))
    editor.fill_example()
    before = editor.generated_links()
    path = tmp_path / "links.txt"
    path.write_text("https://imgur.com/abc12\n\nnot-a-link")
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")):
        editor.import_links()
    assert "Line 3" in editor.note.text()
    assert editor.generated_links() == before and editor.site_key() == "pixiv"
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(tmp_path / "missing.txt"), "")):
        editor.import_links()
    assert editor.generated_links() == before


def test_export_link_file_roundtrip_and_failed_write_preserves_file(tmp_path, editor):
    editor.site.setCurrentIndex(editor.site.findData("pixiv"))
    editor.mode.setCurrentIndex(editor.mode.findData("artwork"))
    editor.fill_example()
    path = tmp_path / "links.txt"
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(path), "")):
        editor.export_links()
    assert path.read_text(encoding="utf-8").splitlines() == editor.generated_links()
    old = path.read_bytes()
    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(path), "")), patch(
            "gallery_dl_app.core.os.replace", side_effect=PermissionError("fixture locked")):
        editor.export_links()
    assert path.read_bytes() == old and "fixture locked" in editor.note.text()
    assert list(tmp_path.glob("*.tmp")) == []


def test_import_link_limit_is_atomic(tmp_path, editor):
    editor.site.setCurrentIndex(editor.site.findData("pixiv"))
    editor.mode.setCurrentIndex(editor.mode.findData("artwork"))
    editor.fill_example()
    before = editor.generated_links()
    path = tmp_path / "links.txt"
    path.write_text("\n".join("https://imgur.com/abc12" for _ in range(201)))
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")):
        editor.import_links()
    assert "200" in editor.note.text() and editor.generated_links() == before


def test_archive_mode_needs_no_encoder_but_zip_extension_alone_does():
    def check(action, dependencies):
        return inspect_config({"extractor": {"pixiv": {"postprocessors": [action]}}}, dependencies=dependencies)
    assert not check({"name": "ugoira/archive"}, {"FFmpeg": False})
    assert check({"name": "ugoira", "extension": "zip", "mode": "concat"}, {"FFmpeg": False})
    assert not check({"name": "ugoira", "extension": "mkv"}, {"FFmpeg": False, "mkvmerge": True})
    assert check({"name": "ugoira/mkvmerge", "extension": "mkv"}, {"FFmpeg": True, "mkvmerge": False})


def test_empty_filters_disable_checks_and_filter_lists_are_checked(tmp_path):
    assert not inspect_config({"extractor": {"file-filter": ""}})
    assert not inspect_config({"extractor": {"file-filter": ["extension == 'jpg'", "width > 0"]}})
    assert inspect_config({"extractor": {"file-filter": ["extension == 'jpg'", "("]}})
    marker = tmp_path / "never-run"
    data = {"extractor": {"postprocessors": [{"name": "mtime", "filter": [
        f"__import__('pathlib').Path({str(marker)!r}).touch()", "("
    ]}]}}
    assert any(item.path.endswith(".filter") for item in inspect_config(data))
    assert not marker.exists()


def test_option_resolution_matches_runtime_inheritance_and_does_not_mutate():
    from gallery_dl import config, extractor
    shared = {"postprocessor-options": {"keep-files": True}, "pixiv": {
        "postprocessor-options": None, "work": {"postprocessor-options": {"keep-files": False}},
    }}
    data = {"extractor": shared}
    with patch.dict(config._config, data, clear=True):
        found = extractor.find("https://www.pixiv.net/artworks/12345")
        assert postprocessor_options(data, (shared, shared["pixiv"], shared["pixiv"]["work"])) == found.config("postprocessor-options")
    del shared["pixiv"]["work"]["postprocessor-options"]
    assert postprocessor_options(data, (shared, shared["pixiv"], shared["pixiv"]["work"])) == {}
    data["postprocessor"] = {"animate": {"name": "ugoira/concat@after", "keep-files": False}}
    original = copy.deepcopy(data)
    action = resolved_postprocessor_action(data, "animate", overrides={"name": "ugoira/archive@file", "keep-files": True})
    assert action == {"name": "ugoira", "mode": "archive", "event": "file", "keep-files": True}
    assert data == original


def test_new_animation_example_creates_zip_with_frame_metadata(tmp_path):
    from gallery_dl.postprocessor.ugoira import UgoiraPP
    data = json.loads(next(item.path for item in EXAMPLES if item.key == "pixiv-animation-archive").read_text())
    shared = data["extractor"]
    site = shared["pixiv"]
    options = resolved_postprocessor_action(data, site["postprocessors"][0],
                                           overrides=postprocessor_options(data, (shared, site)))
    job = SimpleNamespace(get_logger=MagicMock(), register_hooks=MagicMock())
    pp = UgoiraPP(job, options)
    assert pp.delete is False
    frames = []
    for index, delay in enumerate((50, 100)):
        path = tmp_path / f"{index}.jpg"
        path.write_bytes(f"source-frame-{index}".encode())
        frames.append({"file": path.name, "path": str(path), "delay": delay})
    pp._frames = frames
    pp._zip_source = False
    output = tmp_path / "animation.zip"
    pathfmt = SimpleNamespace(realpath=str(output), kwdict={"date_url": None, "date": datetime(2026, 10, 5)})
    with patch("subprocess.Popen", side_effect=AssertionError("must not run an encoder")):
        assert pp.convert_to_archive(pathfmt, None)
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {"0.jpg", "1.jpg", "animation.json"}
        assert json.loads(archive.read("animation.json")) == [{"file": "0.jpg", "delay": 50}, {"file": "1.jpg", "delay": 100}]
        assert archive.read("0.jpg") == b"source-frame-0"
    assert all((tmp_path / frame["file"]).exists() for frame in frames)
    assert not inspect_config(data, dependencies={"FFmpeg": False, "mkvmerge": False})


def test_import_export_cancellation_does_not_change_form(editor):
    editor.site.setCurrentIndex(editor.site.findData("pixiv"))
    editor.mode.setCurrentIndex(editor.mode.findData("artwork"))
    editor.fill_example()
    before = editor.generated_links()
    with patch.object(QFileDialog, "getOpenFileName", return_value=("", "")):
        editor.import_links()
    with patch.object(QFileDialog, "getSaveFileName", return_value=("", "")), patch("gallery_dl_app.link_builder.atomic_write_text") as write:
        editor.export_links()
    assert not write.called and editor.generated_links() == before


def test_helper_reports_malformed_overrides_and_respects_disabled_processing():
    advice = inspect_config({"extractor": {"postprocessor-options": ["keep-files"]}})
    assert any(item.path == "extractor.postprocessor-options" for item in advice)
    data = {"extractor": {"postprocess": False, "pixiv": {
        "postprocessors": [{"name": "zip"}, {"name": "ugoira"}],
    }}}
    assert not inspect_config(data, dependencies={"FFmpeg": False, "mkvmerge": False})
    data["extractor"]["pixiv"]["postprocess"] = True
    assert len(inspect_config(data, dependencies={"FFmpeg": False, "mkvmerge": False})) == 2
    data["postprocess"] = False
    assert not inspect_config(data, dependencies={"FFmpeg": False, "mkvmerge": False})


def test_invalid_preview_blocks_export_dialog(editor):
    assert not editor.export.isEnabled()
    with patch.object(QFileDialog, "getSaveFileName") as dialog:
        editor.export_links()
    assert not dialog.called
