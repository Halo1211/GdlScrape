import copy
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QPushButton

from gallery_dl_app.path_rules_editor import PathRulesEditor, read_rule_condition, rule_condition
from gallery_dl_app.postprocessor_editor import PostprocessorEditor, postprocessor_site_scope
from tests.test_config_guided_media import isolated_window


def test_generated_rules_match_the_runtime_and_missing_fields_fall_back():
    from gallery_dl.path import PathFormat
    settings = {
        "directory": {rule_condition("content", "nature"): ["Nature"], rule_condition("subcategory", "favorite"): ["Favorites"], "": ["Other"]},
        "filename": {rule_condition("images", ""): "{id}_image.{extension}", rule_condition("videos", ""): "{id}_video.{extension}"},
    }
    extractor = SimpleNamespace(config=lambda key, default=None: settings.get(key, default), directory_fmt=["Default"], filename_fmt="{id}.{extension}", _parentdir=None)
    formatter = PathFormat(extractor)
    assert formatter.build_directory({"content": "A nature post", "subcategory": "post"}) == ["Nature"]
    assert formatter.build_directory({"subcategory": "favorite"}) == ["Favorites"]
    assert formatter.build_directory({"subcategory": "post"}) == ["Other"]
    assert formatter.build_directory({"content": None}) == ["Other"]
    assert formatter.build_filename({"id": "42", "extension": "jpg"}) == "42_image.jpg"
    assert formatter.build_filename({"id": "42", "extension": "mp4"}) == "42_video.mp4"
    assert formatter.build_filename({"id": "42", "extension": "zip"}) == "42.zip"
    for kind, argument in (("title", "quote ' and \" inside"), ("content", "nature"), ("description", "hello"), ("subcategory", "favorite")):
        assert read_rule_condition(rule_condition(kind, argument)) == (kind, argument)
    assert read_rule_condition("extension == 'mp4'") == ("extension", "mp4")


def test_folder_rule_form_preserves_advanced_conditions_and_handles_order_deletion_and_fallback():
    app = QApplication.instance() or QApplication([])
    original = {"'nature' in content": ["imgur", "Nature"], "dangerous_call()": ["Advanced"], "": ["Other"]}
    changes = []
    # Neither the GUI nor its preview compiles/evaluates imported conditions.
    with patch("gallery_dl.util.compile_filter", side_effect=AssertionError("Unexpected condition execution")):
        editor = PathRulesEditor("directory", original, True, lambda value, remove: changes.append((value, remove)), site="imgur")
        try:
            assert not changes
            assert editor.condition.currentData() == "content"
            editor.destination.setPlainText("imgur\nNature photos")
            editor.update_rule()
            assert changes[-1][0]["'nature' in content"] == ["imgur", "Nature photos"]
            editor.destination.setPlainText("imgur\nSaved on selecting next rule")
            editor.list.setCurrentRow(1)
            assert changes[-1][0]["'nature' in content"] == ["imgur", "Saved on selecting next rule"]
            assert "Saved on selecting next rule" in editor.list.item(0).text()
            assert editor.condition.currentData() == "advanced"
            editor.destination.setPlainText("Advanced\nChanged")
            editor.update_rule()
            assert changes[-1][0]["dangerous_call()"] == ["Advanced", "Changed"]
            editor.move(-1)
            assert next(iter(changes[-1][0])) == "dangerous_call()"
            editor.remove_rule()
            assert "dangerous_call()" not in changes[-1][0]
            before = copy.deepcopy(changes[-1])
            editor.new_rule()
            editor.condition.setCurrentIndex(editor.condition.findData("otherwise"))
            editor.update_rule()
            with pytest.raises(ValueError, match="only one Otherwise"):
                PathRulesEditor.parse_snapshot(changes[-1][0])
            assert "only one Otherwise" in editor.preview.text()
            editor.discard_edit()
            assert changes[-1] == before
            editor.list.setCurrentRow(editor.list.count() - 1)
            editor.destination.setPlainText("")
            editor.update_rule()
            assert changes[-1][0][""] == []
            assert original["'nature' in content"] == ["imgur", "Nature"]
        finally:
            editor.close()
            app.processEvents()


def test_filename_rules_preserve_equality_conditions_and_folder_extension_rules_stay_advanced():
    app = QApplication.instance() or QApplication([])
    updates = []
    editor = PathRulesEditor("filename", {"extension == 'mp4'": "{id}.mp4"}, True, lambda value, remove: updates.append(value), site="instagram")
    folder = PathRulesEditor("directory", {rule_condition("images", ""): ["Images"]}, True, lambda value, remove: None, site="instagram")
    try:
        assert folder.condition.currentData() == "advanced"  # Extension is not normally in folder metadata.
        assert editor.condition.currentData() == "extension"
        editor.destination.setText("{id}_video.{extension}")
        editor.update_rule()
        assert updates[-1] == {"extension == 'mp4'": "{id}_video.{extension}"}
        editor.fill_example()
        assert len(updates[-1]) == 3
        editor.list.setCurrentRow(0)
        editor.destination.clear()
        before = copy.deepcopy(updates[-1])
        editor.update_rule()
        assert updates[-1] == before and "Enter a file name" in editor.preview.text()
    finally:
        editor.close()
        folder.close()
        app.processEvents()


def test_shared_action_status_matches_site_family_page_restrictions_and_option_precedence():
    config = {"postprocessor": {"tags": {"name": "metadata", "mode": "tags", "whitelist": ["Danbooru"]}}}
    assert postprocessor_site_scope(config, "tags", "aibooru")[0] == "all"
    assert postprocessor_site_scope(config, "tags", "instagram")[0] == "excluded"
    assert postprocessor_site_scope({}, {"name": "mtime", "whitelist": ["pixiv:favorite"]}, "pixiv") == ("some", ("favorite",))
    assert postprocessor_site_scope({}, {"name": "mtime", "blacklist": ["pixiv"]}, "pixiv")[0] == "excluded"
    assert postprocessor_site_scope({}, {"name": "mtime", "whitelist": []}, "pixiv")[0] == "all"
    action = {"name": "mtime", "whitelist": ["instagram"]}
    # The root override replaces local postprocessor-options rather than merging them.
    config = {"postprocessor-options": {"keep-files": True}, "extractor": {"pixiv": {"postprocessor-options": {"whitelist": ["pixiv"]}}}}
    assert postprocessor_site_scope(config, action, "pixiv")[0] == "excluded"
    config["postprocessor-options"] = None
    assert postprocessor_site_scope(config, action, "pixiv")[0] == "excluded"


def test_shared_action_panel_explains_excluded_and_conditional_actions_without_changing_config():
    app = QApplication.instance() or QApplication([])
    state = {"extractor": {"postprocessors": [{"name": "metadata", "mode": "tags", "whitelist": ["danbooru"]}, {"name": "mtime", "filter": "condition_not_executed()"}, {"name": "zip", "whitelist": ["pixiv:favorite"]}]}}
    original = copy.deepcopy(state)
    editor = PostprocessorEditor(["pixiv", "instagram"], lambda: state, lambda site, actions: None, site="instagram")
    try:
        assert "Excluded by saved website restrictions" in editor.shared.text()
        assert "also depends on its saved condition" in editor.shared.text()
        editor.site.setCurrentIndex(editor.site.findData("pixiv"))
        assert "Only on page types: favorite" in editor.shared.text()
        assert state == original
    finally:
        editor.close()
        app.processEvents()


def test_guided_rules_save_only_the_edited_site_and_visiting_defaults_does_not_create_values(tmp_path):
    source = tmp_path / "config.json"
    original = {"extractor": {"imgur": {"directory": {"'nature' in content": ["Nature"], "": ["Other"]}}, "pixiv": {"metadata": True}}, "output": {"progress": False}}
    source.write_text(json.dumps(original))
    with isolated_window() as (_app, window):
        window.config_path = str(source)
        def inspect(dialog):
            if dialog.objectName() != "siteConfigStudio":
                next(button for button in dialog.findChildren(QPushButton) if button.text() == "2. Choose a website…").click()
                return QDialog.Rejected
            sites = dialog.findChild(QComboBox, "siteOptionsSiteCombo")
            sites.setCurrentIndex(sites.findText("pixiv"))
            goal = dialog.findChild(QComboBox, "siteOptionsGoal")
            goal.setCurrentIndex(goal.findData("paths"))
            assert dialog.findChild(PathRulesEditor, "pathRules_directory").rules == []
            assert dialog.findChild(PathRulesEditor, "pathRules_filename").rules == []
            sites.setCurrentIndex(sites.findText("imgur"))
            editor = dialog.findChild(PathRulesEditor, "pathRules_directory")
            editor.destination.setPlainText("imgur\nNature photos")
            editor.update_rule()
            sites.setCurrentIndex(sites.findText("pixiv"))
            dialog.findChild(QPushButton, "siteConfigSave").click()
            return QDialog.Accepted
        with patch.object(QDialog, "exec", inspect), patch.object(window, "show_compact_message"), patch.object(window, "_refresh_env"):
            window.open_config_builder()
        expected = copy.deepcopy(original)
        expected["extractor"]["imgur"]["directory"]["'nature' in content"] = ["imgur", "Nature photos"]
        assert json.loads(source.read_text()) == expected


def test_unapplied_rule_form_survives_navigation_and_is_saved_without_update_click(tmp_path):
    source = tmp_path / "config.json"
    original = {"extractor": {"imgur": {"directory": {"'nature' in content": ["Nature"], "": ["Other"]}}}}
    source.write_text(json.dumps(original))
    with isolated_window() as (_app, window):
        window.config_path = str(source)
        def inspect(dialog):
            if dialog.objectName() != "siteConfigStudio":
                next(button for button in dialog.findChildren(QPushButton) if button.text() == "2. Choose a website…").click()
                return QDialog.Rejected
            picker = dialog.findChild(QComboBox, "siteOptionsSiteCombo")
            picker.setCurrentIndex(picker.findText("imgur"))
            goal = dialog.findChild(QComboBox, "siteOptionsGoal")
            goal.setCurrentIndex(goal.findData("paths"))
            editor = dialog.findChild(PathRulesEditor, "pathRules_directory")
            editor.destination.setPlainText("imgur\nEdited without Update")
            goal.setCurrentIndex(goal.findData("content"))
            goal.setCurrentIndex(goal.findData("paths"))
            editor = dialog.findChild(PathRulesEditor, "pathRules_directory")
            assert editor.destination.toPlainText().endswith("Edited without Update")
            dialog.findChild(QPushButton, "siteConfigSave").click()
            assert dialog.result() == QDialog.Accepted
            return QDialog.Accepted
        with patch.object(QDialog, "exec", inspect), patch.object(window, "show_compact_message"), patch.object(window, "_refresh_env"):
            window.open_config_builder()
        expected = copy.deepcopy(original)
        expected["extractor"]["imgur"]["directory"]["'nature' in content"] = ["imgur", "Edited without Update"]
        assert json.loads(source.read_text()) == expected


def test_invalid_unapplied_rule_blocks_save_and_can_be_discarded(tmp_path):
    source = tmp_path / "config.json"
    original = {"extractor": {"imgur": {"directory": ["imgur"]}}}
    source.write_text(json.dumps(original))
    with isolated_window() as (_app, window):
        window.config_path = str(source)
        def inspect(dialog):
            if dialog.objectName() != "siteConfigStudio":
                next(button for button in dialog.findChildren(QPushButton) if button.text() == "2. Choose a website…").click()
                return QDialog.Rejected
            picker = dialog.findChild(QComboBox, "siteOptionsSiteCombo")
            picker.setCurrentIndex(picker.findText("imgur"))
            goal = dialog.findChild(QComboBox, "siteOptionsGoal")
            goal.setCurrentIndex(goal.findData("paths"))
            editor = dialog.findChild(PathRulesEditor, "pathRules_filename")
            editor.new_rule()
            editor.destination.setText("{id}.jpg")
            dialog.findChild(QPushButton, "siteConfigSave").click()
            assert dialog.result() != QDialog.Accepted
            assert json.loads(source.read_text()) == original
            assert goal.currentData() == "paths"
            editor = dialog.findChild(PathRulesEditor, "pathRules_filename")
            assert editor.destination.text() == "{id}.jpg"
            editor.discard_edit()
            dialog.findChild(QPushButton, "siteConfigSave").click()
            assert dialog.result() == QDialog.Accepted
            return QDialog.Accepted
        with patch.object(QDialog, "exec", inspect), patch.object(window, "show_compact_message"), patch.object(window, "_refresh_env"):
            window.open_config_builder()
        assert json.loads(source.read_text()) == original
