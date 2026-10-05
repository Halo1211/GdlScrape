import json
import os
import copy
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QFileDialog, QLineEdit, QPlainTextEdit, QPushButton

from gallery_dl_app.composer import _read_json_config
from gallery_dl_app.link_builder import LinkBuilder
from gallery_dl_app.url_builder import build_site_url
from gallery_dl_app.config_helper import config_helper_text, inspect_config
from gallery_dl_app.config_examples import EXAMPLES
from tests.test_config_guided_media import isolated_window


def test_import_requires_source_and_rejects_duplicate_settings(tmp_path):
    missing = tmp_path / "missing.json"
    assert _read_json_config(str(missing), required=True)[1]
    assert _read_json_config(str(missing)) == ({}, None)
    duplicate = tmp_path / "duplicate.conf"
    duplicate.write_text('{"extractor":{"reddit":{"videos":false},"reddit":{"timeout":30}}}')
    data, error = _read_json_config(str(duplicate))
    assert data == {} and "Duplicate" in error
    # The official examples use repeated explanatory '#' keys, not settings.
    duplicate.write_text('{"#":"one","#":"two","extractor":{"timeout":30}}')
    assert _read_json_config(str(duplicate))[0]["extractor"]["timeout"] == 30


def test_missing_import_keeps_existing_draft(tmp_path):
    path = tmp_path / "saved.json"
    original = {"extractor": {"reddit": {"timeout": 42}}}
    path.write_text(json.dumps(original))
    with isolated_window() as (_app, window):
        window.config_path = str(path)

        def inspect(dialog):
            dialog.findChild(QPushButton, "configStarterImport").click()
            next(button for button in dialog.findChildren(QPushButton) if button.text() == "Save config file").click()
            return QDialog.Rejected

        with patch.object(QDialog, "exec", inspect), patch.object(QFileDialog, "getOpenFileName", return_value=(str(tmp_path / "gone.json"), "")), patch.object(window, "show_compact_message"):
            window.open_config_builder()
        assert json.loads(path.read_text()) == original


@pytest.mark.parametrize("mode,target,suffix", [
    ("user", "did:plc:abc123", "did:plc:abc123"),
    ("post", "did:plc:abc123:3lxyzabc12345", "did:plc:abc123/post/3lxyzabc12345"),
    ("post", "@artist.bsky.social:3lxyzabc12345", "artist.bsky.social/post/3lxyzabc12345"),
])
def test_bluesky_accepts_did_and_at_handles(mode, target, suffix):
    assert build_site_url("bluesky", mode, target)[0].endswith(suffix)


def test_multifield_batch_is_atomic_and_keeps_original_line_numbers():
    app = QApplication.instance() or QApplication([])
    added = []
    editor = LinkBuilder(lambda links: added.extend(links) or len(links))
    try:
        editor.site.setCurrentIndex(editor.site.findData("bluesky"))
        editor.mode.setCurrentIndex(editor.mode.findData("post"))
        assert not editor.batch.isHidden()
        editor.batch.setChecked(True)
        editor.batch_input.setPlainText("did:plc:abc123 | 3lxyzabc12345\n\nartist.bsky.social | 3lxyzabc12345")
        assert len(editor.generated_links()) == 2
        assert all(not field.isEnabled() for field in editor.inputs[:2])
        editor.fill_example()
        assert editor.batch_input.toPlainText() == "artist.bsky.social | 3lxyzabc12345"
        editor.batch_input.setPlainText("did:plc:abc123 | 3lxyzabc12345\n\nartist.bsky.social | 3lxyzabc12345")
        editor.batch_input.appendPlainText("only-one-field")
        assert "Line 4" in editor.note.text() and not editor.add.isEnabled()
        editor.add_links()
        assert added == []
    finally:
        editor.close()
        app.processEvents()


def test_helper_reports_actions_and_filters_without_values_or_execution(tmp_path):
    marker = tmp_path / "never-created"
    secret = "private-token-must-stay-hidden"
    data = {"filename": "{id}", "extractor": {"pixiv": {"refresh-token": secret, "postprocessors": ["missing", "animation"],
            "file-filter": f"__import__('pathlib').Path({str(marker)!r}).touch()"},
            "reddit": {"file-filter": "extension in (", "archive": ""}},
            "postprocessor": {"animation": {"name": "ugoira", "extension": "mp4"}}}
    original = copy.deepcopy(data)
    paths = {item.path for item in inspect_config(data, dependencies={"FFmpeg": False})}
    assert {"filename", "extractor.pixiv.postprocessors[0]", "extractor.pixiv.postprocessors[1]", "extractor.reddit.file-filter", "extractor.reddit.archive"} <= paths
    text = config_helper_text(data, indonesian=True, dependencies={"FFmpeg": False})
    assert secret not in text and "belum ada" in text
    assert data == original and not marker.exists()
    for value in (False, 42, [{}], {"type": []}, {"name": 42}):
        assert inspect_config({"extractor": {"postprocessors": value}})


def test_config_helper_and_example_search_use_current_draft(tmp_path):
    path = tmp_path / "config.json"
    original = {"extractor": {"pixiv": {"postprocessors": ["undefined-preset"]}}}
    path.write_text(json.dumps(original))
    with isolated_window() as (_app, window):
        window.config_path = str(path)

        def inspect(dialog):
            dialog.findChild(QPushButton, "configHelperCheck").click()
            assert "not defined" in dialog.findChild(QPlainTextEdit, "configHelperResult").toPlainText()
            picker = dialog.findChild(QComboBox, "configStarterExample")
            search = dialog.findChild(QLineEdit, "configStarterExampleSearch")
            search.setText("instagram reels")
            assert picker.count() == 1 and picker.currentData() == "instagram-selected-media"
            search.setText("no-such-example")
            assert picker.count() == 0 and not dialog.findChild(QPushButton, "configStarterLoadExample").isEnabled()
            search.clear()
            assert picker.count() == len(EXAMPLES)
            return QDialog.Rejected

        with patch.object(QDialog, "exec", inspect):
            window.open_config_builder()
        assert json.loads(path.read_text()) == original


def test_new_online_examples_match_runtime_routing_and_formatter():
    from gallery_dl import config, extractor, formatter
    for example in EXAMPLES:
        assert not inspect_config(json.loads(example.path.read_text(encoding="utf-8")))
    reddit = json.loads(next(item.path for item in EXAMPLES if item.key == "reddit-connected-media").read_text())
    with patch.dict(config._config, reddit, clear=True):
        direct = extractor.find("https://imgur.com/abc12")
        assert direct.config("directory") is None
        child = extractor.find("https://imgur.com/abc12")
        child._cfgpath = ("extractor", "reddit>imgur", "image")
        assert child.config("directory") == []
        assert formatter.StringFormatter(child.config("filename")).format_map({"_reddit": {"id": "abc123"}, "id": "abc12", "extension": "jpg"}) == "abc123_abc12.jpg"
    windows = json.loads(next(item.path for item in EXAMPLES if item.key == "windows-short-paths").read_text())
    pattern = windows["extractor"]["artstation"]["directory"][-1]
    assert len(formatter.StringFormatter(pattern).format_map({"id": 42, "title": "x" * 1000})) == 63
    instagram = json.loads(next(item.path for item in EXAMPLES if item.key == "instagram-selected-media").read_text())
    with patch.dict(config._config, instagram, clear=True):
        messages = list(extractor.find("https://www.instagram.com/artist/").items())
        assert {message[2]["_extractor"].subcategory for message in messages} == {"posts", "reels", "highlights"}


def test_dependency_helper_matches_packaged_mode_and_redacts_config(tmp_path):
    path = tmp_path / "config.conf"
    path.write_text(json.dumps({"extractor": {"pixiv": {"refresh-token": "never-display-this-token", "postprocessors": ["unknown-preset"]}}}))
    with isolated_window() as (_app, window):
        window.config_path = str(path)
        with patch("gallery_dl_app.window.sys.frozen", True, create=True), patch.object(window, "show_scroll_message") as shown:
            window.show_dependency_helper()
        text = shown.call_args.args[1]
        assert "complete release folder" in text
        assert "pip install" not in text
        assert "not defined" in text and "never-display-this-token" not in text


def test_conf_validator_reads_json_and_detects_duplicate_settings(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from gallery_dl_app.system_tools import SystemToolsMixin
    path = tmp_path / "gallery-dl.conf"
    path.write_text('{"extractor": {"timeout": 30, "timeout": 42}}')
    target = SimpleNamespace(config_path=str(path), show_compact_message=MagicMock())
    SystemToolsMixin.validate_config(target)
    assert target.show_compact_message.call_args.args[0] == "Config duplicate keys"


@pytest.mark.parametrize("site,mode,target", [
    ("mangadex", "chapter", "not-a-uuid"), ("imgur", "album", "abc"),
    ("artstation", "album", "artist:abc"), ("patreon", "post", "0"),
    ("coomer", "post", "onlyfans:artist"), ("bluesky", "post", "artist.bsky.social:bad/id"),
])
def test_new_recipes_reject_incomplete_or_malformed_targets(site, mode, target):
    with pytest.raises(ValueError):
        build_site_url(site, mode, target)
