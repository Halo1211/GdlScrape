import copy
import json
import os
from contextlib import contextmanager
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QMessageBox, QPushButton

from gallery_dl_app.config_examples import EXAMPLES
from gallery_dl_app.config_maker import edit_config_path
from gallery_dl_app.content_filter_editor import ContentFilterEditor, FILE_TYPES, extension_filter, filter_extensions
from gallery_dl_app.link_builder import LinkBuilder
from gallery_dl_app.postprocessor_editor import PostprocessorEditor, resolved_postprocessor_action, ugoira_settings


@contextmanager
def isolated_window():
    import gallery_dl_app as package
    from gallery_dl_app.management import ManagementMixin
    from gallery_dl_app.reports import ReportsMixin
    from gallery_dl_app.system_tools import SystemToolsMixin
    with (
        patch.object(ReportsMixin, "_load_autosave_silently", lambda self: None),
        patch.object(SystemToolsMixin, "autosave_session", lambda self: None),
        patch.object(ManagementMixin, "_start_management_services", lambda self: None),
        patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
    ):
        app, window = package.create_application([])
        try:
            yield app, window
        finally:
            window.config_path = None
            window.close()
            app.processEvents()


def test_extension_rules_reject_code_and_keep_imported_filters_until_explicitly_changed():
    app = QApplication.instance() or QApplication([])
    expression = extension_filter(".JPG, png; JPG WEBP")
    assert filter_extensions(expression) == ("jpg", "png", "webp")
    for value in ("", "jpg' or True", "../png", "*.jpg", "png/../../gif"):
        with pytest.raises(ValueError):
            extension_filter(value)
    for value in ("extension in __import__('os').listdir('.')", "score > 100", "extension not in ('mp4',)", "extension in ('JPG',)", "extension in ('png',) or True"):
        assert filter_extensions(value) is None
    changes = []
    editor = ContentFilterEditor("score > 100", True, lambda value, remove: changes.append((value, remove)))
    try:
        assert changes == [] and editor.choice.currentData() == "advanced"
        assert not editor.apply.isEnabled()
        editor.choice.setCurrentIndex(editor.choice.findData("custom"))
        editor.extensions.setText("jpg' or True")
        editor.apply_filter()
        assert changes == []
        editor.extensions.setText("jpg, png")
        editor.apply_filter()
        assert changes[-1] == ("extension in ('jpg', 'png')", False)
        editor.choice.setCurrentIndex(editor.choice.findData("all"))
        editor.apply_filter()
        assert changes[-1] == (None, False)  # Explicitly disable; shared filter is not inherited.
        editor.reset.click()
        assert changes[-1] == (None, True)  # Remove local filter to inherit instead.
    finally:
        editor.close()
        app.processEvents()


def test_animation_formats_match_cli_and_named_actions_preserve_source_and_unknown_fields():
    from argparse import Namespace
    from gallery_dl.option import UgoiraAction
    for key in ("mp4", "webm", "gif", "zip"):
        namespace = Namespace(postprocessors=[], options=[])
        UgoiraAction([], "ugoira")(None, namespace, key)
        expected = namespace.postprocessors[0]
        actual = ugoira_settings(key)
        if key == "zip":
            assert actual["mode"] == expected["mode"] == "archive"
        else:
            assert actual["extension"] == expected["extension"]
            assert actual["ffmpeg-args"] == list(expected["ffmpeg-args"])
    app = QApplication.instance() or QApplication([])
    state = json.loads(next(item.path for item in EXAMPLES if item.key == "pixiv-animation").read_text())
    original = copy.deepcopy(state)

    def change(site, actions):
        nonlocal state
        state = edit_config_path(state, ("extractor", *([site] if site else []), "postprocessors"), actions)

    editor = PostprocessorEditor(["pixiv", "danbooru", "instagram"], lambda: state, change, site="pixiv")
    try:
        assert state == original
        assert editor.animation_format.currentData() == "mp4"
        editor.animation_format.setCurrentIndex(editor.animation_format.findData("gif"))
        action = resolved_postprocessor_action(state, state["extractor"]["pixiv"]["postprocessors"][0])
        assert action["extension"] == "gif" and action["repeat-last-frame"] is False
        assert state["postprocessor"] == original["postprocessor"]
        editor.animation_keep.setChecked(False)
        assert state["extractor"]["pixiv"]["postprocessors"][0]["keep-files"] is False
        editor.animation_format.setCurrentIndex(editor.animation_format.findData("zip"))
        action = resolved_postprocessor_action(state, state["extractor"]["pixiv"]["postprocessors"][0])
        assert action["mode"] == "archive" and action["extension"] == "zip"
        assert action["ffmpeg-args"] == []
        editor.site.setCurrentIndex(editor.site.findData("instagram"))
        editor.task.setCurrentIndex(editor.task.findData("ugoira"))
        editor.add_action()
        assert "instagram" not in state["extractor"]  # Unsupported target is explained, not changed.
        editor.task.setCurrentIndex(editor.task.findData("mtime"))
        editor.add_action()
        editor.edit_date("{status[date]}")
        assert state["extractor"]["instagram"]["postprocessors"] == [{"name": "mtime", "value": "{status[date]}"}]
    finally:
        editor.close()
        app.processEvents()


def test_paste_mixed_links_reports_real_line_numbers_and_configures_the_selected_website():
    app = QApplication.instance() or QApplication([])
    editor = LinkBuilder(lambda urls: len(urls))
    previous_clipboard = app.clipboard().text()
    configured = []
    editor.configureRequested.connect(configured.append)
    try:
        editor.site.setCurrentIndex(editor.site.findData("pixiv"))
        editor.fill_example()
        app.clipboard().setText("https://x.com/artist\n\nBAD\nhttps://www.instagram.com/p/ABC123xyz/")
        editor.paste_links()
        assert "Line 3" in editor.note.text()
        assert not editor.add.isEnabled() and editor.detected_site.count() == 0
        app.clipboard().setText("https://x.com/artist\n\nhttps://www.instagram.com/p/ABC123xyz/\nhttps://x.com/artist")
        editor.paste_links()
        assert editor.batch.isChecked() and editor.mode.currentData() == "url"
        assert len(editor.generated_links()) == 2
        assert editor.detected_site.count() == 2 and editor.configure.isEnabled()
        editor.detected_site.setCurrentIndex(editor.detected_site.findData("instagram"))
        editor.configure_website()
        assert configured == ["instagram"]
        editor.batch_input.setPlainText("https://x.com/artist\nhttps://x.com/other")
        assert editor.detected_site.count() == 1
        assert editor.detected_site.currentData() == "twitter"
    finally:
        app.clipboard().setText(previous_clipboard)
        editor.close()
        app.processEvents()


def test_file_type_controls_save_one_site_and_reset_to_shared_filter(tmp_path):
    source = tmp_path / "config.json"
    original = {"extractor": {"file-filter": "score >= 100", "instagram": {"include": ["posts"]}, "pixiv": {"metadata": True}}}
    source.write_text(json.dumps(original))
    with isolated_window() as (_app, window):
        window.config_path = str(source)
        def inspect(dialog):
            if dialog.objectName() != "siteConfigStudio":
                next(button for button in dialog.findChildren(QPushButton) if button.text() == "2. Choose a website…").click()
                return QDialog.Rejected
            picker = dialog.findChild(QComboBox, "siteOptionsSiteCombo")
            picker.setCurrentIndex(picker.findText("instagram"))
            goal = dialog.findChild(QComboBox, "siteOptionsGoal")
            goal.setCurrentIndex(goal.findData("content"))
            editor = dialog.findChild(ContentFilterEditor, "contentFilterEditor")
            assert editor.choice.currentData() == "advanced"
            editor.choice.setCurrentIndex(editor.choice.findData("images"))
            editor.apply.click()
            dialog.findChild(QPushButton, "siteConfigSave").click()
            assert dialog.result() == QDialog.Accepted
            return QDialog.Accepted
        with patch.object(QDialog, "exec", inspect), patch.object(window, "show_compact_message"), patch.object(window, "_refresh_env"):
            window.open_config_builder()
        expected = copy.deepcopy(original)
        expected["extractor"]["instagram"]["file-filter"] = extension_filter(",".join(FILE_TYPES["images"]))
        assert json.loads(source.read_text()) == expected
        def reset(dialog):
            if dialog.objectName() != "siteConfigStudio":
                next(button for button in dialog.findChildren(QPushButton) if button.text() == "2. Choose a website…").click()
                return QDialog.Rejected
            picker = dialog.findChild(QComboBox, "siteOptionsSiteCombo")
            picker.setCurrentIndex(picker.findText("instagram"))
            dialog.findChild(QPushButton, "contentFilterReset").click()
            dialog.findChild(QPushButton, "siteConfigSave").click()
            return QDialog.Accepted
        with patch.object(QDialog, "exec", reset), patch.object(window, "show_compact_message"), patch.object(window, "_refresh_env"):
            window.open_config_builder()
        assert json.loads(source.read_text()) == original


@pytest.mark.parametrize("site", ["pixiv", ""])
def test_adding_animation_action_sets_original_mode_at_the_right_scope(tmp_path, site):
    source = tmp_path / "config.json"
    original = {"extractor": {"instagram": {"metadata": True}, "pixiv": {"ugoira": True}}, "output": {"progress": False}}
    source.write_text(json.dumps(original))
    with isolated_window() as (_app, window):
        window.config_path = str(source)
        def inspect(dialog):
            if dialog.objectName() != "siteConfigStudio":
                next(button for button in dialog.findChildren(QPushButton) if button.text() == "2. Choose a website…").click()
                return QDialog.Rejected
            editor = dialog.findChild(PostprocessorEditor, "postprocessorEditor")
            editor.site.setCurrentIndex(editor.site.findData(site))
            editor.task.setCurrentIndex(editor.task.findData("ugoira"))
            editor.add_action()
            dialog.findChild(QPushButton, "siteConfigSave").click()
            return QDialog.Accepted
        with patch.object(QDialog, "exec", inspect), patch.object(window, "show_compact_message"), patch.object(window, "_refresh_env"):
            window.open_config_builder()
        result = json.loads(source.read_text())
        block = result["extractor"][site] if site else result["extractor"]
        assert block["ugoira"] == "original"
        assert block["postprocessors"][0]["name"] == "ugoira"
        assert block["postprocessors"][0]["keep-files"] is True
        assert "" not in result["extractor"]
        assert result["extractor"]["instagram"] == original["extractor"]["instagram"]
        assert result["extractor"]["pixiv"]["ugoira"] == "original"
        assert result["output"] == original["output"]
