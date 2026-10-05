import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QPushButton, QTabWidget, QWidget

from gallery_dl_app.application_tools import build_application_tools
from gallery_dl_app.config_examples import EXAMPLES
from gallery_dl_app.config_maker import edit_config_path
from gallery_dl_app.postprocessor_editor import PostprocessorEditor
from tests.test_config_guided_media import isolated_window


def test_information_locations_and_tabs_change_only_the_target_action():
    app = QApplication.instance() or QApplication([])
    state = {"postprocessor": {"info": {"name": "metadata", "mode": "json", "base-directory": ["root", "metadata"], "directory": "info", "indent": "\t"}},
             "extractor": {"danbooru": {"postprocessors": ["info"]}, "pixiv": {"metadata": True}}}
    original = copy.deepcopy(state)

    def change(site, actions):
        nonlocal state
        state = edit_config_path(state, ("extractor", site, "postprocessors"), actions)

    editor = PostprocessorEditor(["danbooru", "pixiv"], lambda: state, change, site="danbooru")
    try:
        assert state == original
        assert editor.location.currentData() == "advanced"
        assert editor.metadata_directory.toPlainText() == "info"
        assert editor.indent_style.currentData() == "tabs" and not editor.indent.isEnabled()
        editor.ascii.setChecked(True)
        assert state["extractor"]["danbooru"]["postprocessors"] == [{"type": "info", "ascii": True}]
        with patch.object(QFileDialog, "getExistingDirectory", return_value=""):
            editor.location.setCurrentIndex(editor.location.findData("custom"))
        assert editor.location.currentData() == "advanced"
        editor.location.setCurrentIndex(editor.location.findData("base"))
        editor.metadata_directory.setPlainText("metadata\n{category}")
        action = state["extractor"]["danbooru"]["postprocessors"][0]
        assert action["base-directory"] is True and action["directory"] == ["metadata", "{category}"]
        assert "Download root / metadata / {category}" in editor.location_note.text()
        editor.indent_style.setCurrentIndex(editor.indent_style.findData("spaces"))
        editor.indent.setValue(2)
        assert state["extractor"]["danbooru"]["postprocessors"][0]["indent"] == 2
        editor.indent_style.setCurrentIndex(editor.indent_style.findData("tabs"))
        assert state["extractor"]["danbooru"]["postprocessors"][0]["indent"] == "\t"
        with patch.object(QFileDialog, "getExistingDirectory", return_value="D:/Information"):
            editor.choose_metadata_root()
        assert state["extractor"]["danbooru"]["postprocessors"][0]["base-directory"] == "D:/Information"
        editor.location.setCurrentIndex(editor.location.findData("download"))
        editor.metadata_directory.clear()
        assert state["extractor"]["danbooru"]["postprocessors"][0]["base-directory"] is False
        assert state["extractor"]["danbooru"]["postprocessors"][0]["directory"] == []
        assert state["postprocessor"] == original["postprocessor"] and state["extractor"]["pixiv"] == original["extractor"]["pixiv"]
    finally:
        editor.close()
        app.processEvents()


def test_new_examples_formats_and_metadata_paths_match_gallery_dl(tmp_path):
    from gallery_dl import formatter
    from gallery_dl.postprocessor.metadata import MetadataPP
    example = json.loads(next(item.path for item in EXAMPLES if item.key == "separate-information").read_text())
    options = example["extractor"]["danbooru"]["postprocessors"][0]
    job = SimpleNamespace(get_logger=MagicMock(), register_hooks=MagicMock(), extractor=SimpleNamespace(config=lambda key, default=None: default))
    pp = MetadataPP(job, options)
    assert pp._base(SimpleNamespace(basedirectory=str(tmp_path))) == str(tmp_path)
    assert '\n\t"id"' in pp._json_encode({"id": 42})
    from gallery_dl.path import PathFormat
    extractor = SimpleNamespace(config=lambda key, default=None: str(tmp_path) if key == "base-directory" else default, directory_fmt=["images", "{category}"], filename_fmt="{id}.{extension}", _parentdir=None)
    path = PathFormat(extractor)
    path.kwdict = {"category": "danbooru", "id": 42, "extension": "jpg"}
    assert Path(pp._directory(path)) == tmp_path / "metadata" / "danbooru"
    assert path.build_directory(path.kwdict) == ["images", "danbooru"]
    social = json.loads(next(item.path for item in EXAMPLES if item.key == "social-post-text").read_text())
    pattern = social["extractor"]["tumblr"]["postprocessors"][0]["format"]
    text = formatter.StringFormatter(pattern)
    assert text.format_map({"body": "<b>Hello</b>"}) == "Hello\n"
    assert text.format_map({"answer": "Reply", "summary": "ignored"}) == "Reply\n"
    assert text.format_map({}) == "\n"


def test_application_summary_actions_are_explicit_and_refresh_locations(tmp_path):
    app = QApplication.instance() or QApplication([])
    edit = SimpleNamespace(text=lambda: str(tmp_path / "downloads"))
    methods = {key: MagicMock() for key in ("open_config_builder", "open_output_folder", "open_app_data_dir", "backup_config_file", "backup_app_data", "health_check", "check_version", "export_audit_report", "show_compact_message")}
    window = SimpleNamespace(_ui_is_indonesian=lambda: False, gdl_cmd="", config_path=None, edit_output=edit, **methods)
    with patch("gallery_dl_app.application_tools.shutil.which", return_value=None), patch("gallery_dl_app.application_tools.sys.frozen", True, create=True):
        page = build_application_tools(window)
        try:
            assert "not found; needed only for media conversion" in page.findChild(QLabel, "applicationStatus").text()
            assert "Not selected" in page.findChild(QLabel, "applicationLocations").text()
            assert not any(method.called for method in methods.values())
            page.findChild(QPushButton, "applicationTools_config").click()
            page.findChild(QPushButton, "applicationTools_backupConfig").click()
            page.findChild(QPushButton, "applicationTools_backupData").click()
            methods["open_config_builder"].assert_called_once()
            methods["backup_config_file"].assert_called_once()
            methods["backup_app_data"].assert_called_once()
            page.findChild(QPushButton, "applicationTools_configFolder").click()
            methods["show_compact_message"].assert_called_once()
            folder = tmp_path / "config-folder"
            folder.mkdir()
            window.config_path = str(folder / "config.json")
            page.findChild(QPushButton, "applicationTools_refresh").click()
            assert "not created yet" in page.findChild(QLabel, "applicationLocations").text()
            with patch("gallery_dl_app.application_tools.open_path", return_value=True) as opened:
                page.findChild(QPushButton, "applicationTools_configFolder").click()
            opened.assert_called_once_with(folder)
            assert not (folder / "config.json").exists()
        finally:
            page.close()
            app.processEvents()


@pytest.mark.parametrize("frozen", [True, False])
def test_manage_starts_with_useful_application_page_and_hides_runtime_in_exe(frozen):
    with isolated_window() as (app, window), patch("gallery_dl_app.management.sys.frozen", frozen, create=True), patch.object(window, "_build_options_tab", return_value=QWidget()):
        window.open_management_center()
        dialog = window._management_dialog
        try:
            tabs = dialog.findChild(QTabWidget, "managementTabs")
            assert tabs.currentWidget().objectName() == "applicationTools"
            assert not any("Runtime" in tabs.tabText(index) for index in range(tabs.count()))
            install = [button for button in dialog.findChildren(QPushButton) if button.text() == "Install / Update Managed Runtime"]
            assert not install
            assert dialog.findChild(QPushButton, "applicationTools_report") is not None
        finally:
            dialog.accept()
            app.processEvents()
