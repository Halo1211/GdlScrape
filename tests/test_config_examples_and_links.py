import copy
import json
import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QFileDialog, QLineEdit, QMessageBox, QPushButton

from gallery_dl_app.composer import parse_typed_config_value
from gallery_dl_app.config_maker import edit_config_path
from gallery_dl_app.config_value_editor import StructuredConfigEditor
from gallery_dl_app.link_builder import LinkBuilder
from gallery_dl_app.postprocessor_editor import PostprocessorEditor, resolved_postprocessor_action
from gallery_dl_app.url_builder import URL_RECIPE_MODES, build_site_url, url_recipe_fields, validate_supported_url


def online_config():
    return json.loads((Path(__file__).parent / "fixtures/configs/online-patterns.json").read_text(encoding="utf-8"))


def test_named_actions_and_inline_edits_keep_definitions_and_shared_actions():
    app = QApplication.instance() or QApplication([])
    state = online_config()
    original = copy.deepcopy(state)

    def change(site, actions):
        nonlocal state
        state = edit_config_path(state, ("extractor", *([site] if site else []), "postprocessors"), actions, remove=actions is None)

    editor = PostprocessorEditor(["twitter", "pixiv", "mangadex", "deviantart", "instagram"], lambda: state, change, site="twitter")
    try:
        assert state == original
        assert editor.event.currentData() == "post"
        assert editor.content.toPlainText() == "{content|description}\n"
        assert "shared actions" in editor.shared.text()
        editor.content.setPlainText("Caption: {content|description}\n")
        assert state["extractor"]["twitter"]["postprocessors"][0] == {"type": "content", "format": "Caption: {content|description}\n"}
        editor.list.setCurrentRow(1)
        assert editor.indent.value() == 2
        assert editor.event.currentData() == "post"
        editor.indent.setValue(4)
        assert state["extractor"]["twitter"]["postprocessors"][1] == {"type": "meta", "filename": "{id}.json", "indent": 4}
        assert state["postprocessor"] == original["postprocessor"]
        editor.site.setCurrentIndex(editor.site.findData("mangadex"))
        assert "CBZ" in editor.list.item(0).text()
        editor.keep.setChecked(True)
        assert state["extractor"]["mangadex"]["postprocessors"] == [{"type": "cbz", "keep-files": True}]
        editor.site.setCurrentIndex(editor.site.findData("deviantart"))
        assert editor.content.toPlainText() == "{description}\n"
        editor.content.setPlainText("{description}\nSource: {url}\n")
        assert state["extractor"]["deviantart"]["postprocessors"][0]["content-format"].endswith("Source: {url}\n")
        editor.site.setCurrentIndex(editor.site.findData("instagram"))
        assert editor.actions == []
        editor.task.setCurrentIndex(editor.task.findData("preset:content"))
        editor.add_action()
        assert state["extractor"]["instagram"]["postprocessors"] == ["content"]
        editor.use_default()
        assert "postprocessors" not in state["extractor"]["instagram"]
        assert state["extractor"]["postprocessors"] == original["extractor"]["postprocessors"]
        assert state["extractor"]["pixiv"] == original["extractor"]["pixiv"]
        # The runtime combines these levels; adding a local JSON action must not clone mtime.
        from gallery_dl import config, extractor
        with patch.dict(config._config, state, clear=True):
            actions = extractor.find("https://x.com/artist").config_accumulate("postprocessors")
            assert len(actions) == 3
            assert actions[-1] == original["extractor"]["postprocessors"][0]
    finally:
        editor.close()
        app.processEvents()


def test_conditional_folder_fallback_can_be_edited_without_evaluating_conditions():
    app = QApplication.instance() or QApplication([])
    editor = StructuredConfigEditor(parse_typed_config_value, allowed_types=("object",), allow_default_key=True)
    try:
        value = online_config()["extractor"]["tbib"]["directory"]
        editor.set_value(value)
        assert editor.value() == value
        editor.table.cellWidget(1, 2).setText('["TBIB", "Other"]')
        assert editor.value()[""] == ["TBIB", "Other"]
        assert "Otherwise" in editor.result.text()
        editor.add_row("", "duplicate")
        try:
            editor.value()
            assert False, "Duplicate default conditions must be rejected"
        except ValueError as exc:
            assert "only one default" in str(exc)
        state = edit_config_path(online_config(), ("extractor", "reddit>imgur", "filename"), "{id}.jpg")
        assert state["extractor"]["reddit>imgur"]["filename"] == "{id}.jpg"
    finally:
        editor.close()
        app.processEvents()


def test_every_recipe_example_matches_a_local_extractor_and_handles_search_encoding():
    for site, modes in URL_RECIPE_MODES.items():
        for mode in modes:
            url, subcategory = build_site_url(site, mode, ":".join(field[2] for field in url_recipe_fields(site, mode)))
            assert url.startswith("https://") and subcategory
    assert build_site_url("instagram", "reels", "@artist")[0] == "https://www.instagram.com/artist/reels/"
    assert "landscape+sky" in build_site_url("pinterest", "search", "landscape sky")[0]
    assert build_site_url("bluesky", "post", "artist.bsky.social:3lxyzabc12345")[0].endswith("/post/3lxyzabc12345")
    for invalid in ("file:///tmp/photo", "https://user:password@example.com/photo", "https://x.com/artist\nhttps://x.com/other"):
        try:
            validate_supported_url(invalid)
            assert False, "Invalid URL must be rejected"
        except ValueError:
            pass
    assert validate_supported_url("https://www.instagram.com/p/ABC123xyz/", "instagram")[1] == "post"
    try:
        validate_supported_url("https://x.com/artist", "pixiv")
        assert False, "Wrong website must be rejected"
    except ValueError as exc:
        assert "twitter" in str(exc)


def test_link_builder_split_fields_batch_errors_and_duplicate_handling():
    app = QApplication.instance() or QApplication([])
    targets = []

    def add_urls(links):
        new = [link for link in links if link not in targets]
        targets.extend(new)
        return len(new)

    editor = LinkBuilder(add_urls)
    try:
        editor.site.setCurrentIndex(editor.site.findData("bluesky"))
        editor.mode.setCurrentIndex(editor.mode.findData("post"))
        editor.fill_example()
        assert len(url_recipe_fields("bluesky", "post")) == 2
        assert editor.add.isEnabled()
        assert editor.preview.toPlainText().endswith("/post/3lxyzabc12345")
        editor.add_links()
        editor.add_links()
        assert len(targets) == 1
        assert "Added 0" in editor.note.text()
        configured = []
        editor.configureRequested.connect(configured.append)
        editor.configure_website()
        assert configured == ["bluesky"]
        editor.site.setCurrentIndex(editor.site.findData("hypnohub"))
        editor.mode.setCurrentIndex(editor.mode.findData("post"))
        editor.batch.setChecked(True)
        editor.batch_input.setPlainText("12345\nBAD\n67890")
        assert "Line 2" in editor.note.text()
        assert not editor.add.isEnabled()
        editor.add_links()
        assert len(targets) == 1  # No partial batch was added.
        editor.batch_input.setPlainText("12345\n67890\n12345")
        editor.add_links()
        assert len(targets) == 3
        editor.site.setCurrentIndex(0)
        editor.inputs[0].setText("https://www.flickr.com/photos/artist/12345")
        assert editor.add.isEnabled()
        assert editor.mode.currentData() == "url"
        editor.site.setCurrentIndex(editor.site.findData("imagefap"))
        assert editor.mode.currentData() == "url"
        assert resolved_postprocessor_action(online_config(), "ugoira-copy")["name"] == "ugoira"
    finally:
        editor.close()
        app.processEvents()


def test_imported_online_fixture_edits_preserve_named_definitions_and_page_settings(tmp_path):
    import gallery_dl_app as package
    from gallery_dl_app.management import ManagementMixin
    from gallery_dl_app.reports import ReportsMixin
    from gallery_dl_app.system_tools import SystemToolsMixin

    original = online_config()
    source = tmp_path / "example.json"
    target = tmp_path / "copy.json"
    source.write_text(json.dumps(original), encoding="utf-8")
    target.write_text("{}", encoding="utf-8")
    with (
        patch.object(ReportsMixin, "_load_autosave_silently", lambda self: None),
        patch.object(SystemToolsMixin, "autosave_session", lambda self: None),
        patch.object(ManagementMixin, "_start_management_services", lambda self: None),
    ):
        app, window = package.create_application([])
        window.config_path = str(target)

        def inspect(dialog):
            if dialog.objectName() != "siteConfigStudio":
                dialog.findChild(QPushButton, "configStarterImport").click()
                next(button for button in dialog.findChildren(QPushButton) if button.text() == "Save config file").click()
                assert json.loads(target.read_text(encoding="utf-8")) == original
                dialog.findChild(QPushButton, "configWebsiteSettingsButton").click()
                return QDialog.Rejected
            site = dialog.findChild(QComboBox, "siteOptionsSiteCombo")
            search = dialog.findChild(QLineEdit, "siteOptionsSearch")
            site.setCurrentText("tbib")
            search.setText("directory")
            conditional = dialog.findChild(StructuredConfigEditor, "siteOptionStructured_directory")
            assert conditional.value()[""] == ["TBIB", "Unsorted"]
            conditional.table.cellWidget(1, 2).setText('["TBIB", "Other"]')
            conditional.table.selectRow(0)
            conditional.remove_row()
            site.setCurrentText("reddit>imgur")
            search.setText("filename")
            dialog.findChild(QLineEdit, "siteOptionValue_filename").setText("{id}.jpg")
            site.setCurrentText("twitter")
            goal = dialog.findChild(QComboBox, "siteOptionsGoal")
            goal.setCurrentIndex(goal.findData("after"))
            pp = dialog.findChild(PostprocessorEditor, "postprocessorEditor")
            assert pp.actions[0] == "content"
            pp.content.setPlainText("{description}\n")
            pp.list.setCurrentRow(1)
            pp.open_advanced()
            ref_search = dialog.findChild(QLineEdit, "configReferenceSearch")
            ref_search.setText("postprocessor.metadata.indent")
            assert dialog.findChild(QLineEdit, "configReferenceValue").text() == "2"
            dialog.findChild(QLineEdit, "configReferenceValue").setText("8")
            dialog.findChild(QPushButton, "setReferenceOption").click()
            group = dialog.findChild(QComboBox, "configReferenceGroup")
            group.setCurrentIndex(group.findData(""))
            ref_search.setText("extractor.*.filename")
            ref_site = dialog.findChild(QComboBox, "configReferenceSite")
            ref_site.setCurrentText("pixiv")
            pages = dialog.findChild(QComboBox, "configReferencePageType")
            assert pages.findData("favorite") >= 0
            pages.setCurrentIndex(pages.findData("favorite"))
            dialog.findChild(QComboBox, "configReferenceType").setCurrentIndex(dialog.findChild(QComboBox, "configReferenceType").findData("text"))
            dialog.findChild(QLineEdit, "configReferenceValue").setText("{id}.{extension}")
            dialog.findChild(QPushButton, "setReferenceOption").click()
            dialog.findChild(QPushButton, "siteConfigSave").click()
            assert dialog.result() == QDialog.Accepted
            return QDialog.Accepted

        try:
            with (
                patch.object(QDialog, "exec", inspect),
                patch.object(QFileDialog, "getOpenFileName", return_value=(str(source), "")),
                patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
                patch.object(window, "show_compact_message"),
                patch.object(window, "_refresh_env"),
            ):
                window.open_config_builder()
            expected = copy.deepcopy(original)
            expected["extractor"]["tbib"]["directory"] = {"": ["TBIB", "Other"]}
            expected["extractor"]["reddit>imgur"]["filename"] = "{id}.jpg"
            expected["extractor"]["twitter"]["postprocessors"] = [{"type": "content", "format": "{description}\n"}, {"type": "meta", "filename": "{id}.json", "indent": 8}]
            expected["extractor"]["pixiv"]["favorite"]["filename"] = "{id}.{extension}"
            assert json.loads(target.read_text(encoding="utf-8")) == expected
            assert json.loads(source.read_text(encoding="utf-8")) == original
        finally:
            window.config_path = None
            window.close()
            app.processEvents()


def test_starter_examples_load_from_assets_and_malformed_import_keeps_draft(tmp_path):
    import gallery_dl_app as package
    from gallery_dl_app import composer
    from gallery_dl_app.management import ManagementMixin
    from gallery_dl_app.reports import ReportsMixin
    from gallery_dl_app.system_tools import SystemToolsMixin
    bad = tmp_path / "bad.json"
    bad.write_text('{"extractor": {"timeout": 30 "skip": true}}', encoding="utf-8")
    target = tmp_path / "copy.json"
    target.write_text("{}", encoding="utf-8")
    assert "line 1, column" in composer._read_json_config(str(bad))[1]
    with (
        patch.object(ReportsMixin, "_load_autosave_silently", lambda self: None),
        patch.object(SystemToolsMixin, "autosave_session", lambda self: None),
        patch.object(ManagementMixin, "_start_management_services", lambda self: None),
    ):
        app, window = package.create_application([])
        window.config_path = str(target)
        def inspect(dialog):
            picker = dialog.findChild(QComboBox, "configStarterExample")
            save = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Save config file")
            for index in range(picker.count()):
                picker.setCurrentIndex(index)
                dialog.findChild(QPushButton, "configStarterLoadExample").click()
                save.click()
                example = Path(composer.__file__).parent / "assets/config-examples" / (picker.currentData() + ".json")
                assert json.loads(target.read_text(encoding="utf-8")) == json.loads(example.read_text(encoding="utf-8"))
            before = target.read_bytes()
            dialog.findChild(QPushButton, "configStarterImport").click()
            save.click()
            assert target.read_bytes() == before
            return QDialog.Rejected
        try:
            with (
                patch.object(QDialog, "exec", inspect),
                patch.object(QFileDialog, "getOpenFileName", return_value=(str(bad), "")),
                patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
                patch.object(window, "show_compact_message") as message,
                patch.object(window, "_refresh_env"),
            ):
                window.open_config_builder()
                assert any("line 1, column" in str(call.args) for call in message.call_args_list)
        finally:
            window.config_path = None
            window.close()
            app.processEvents()


def test_link_builder_opens_matching_site_without_changing_shared_config(tmp_path):
    import gallery_dl_app as package
    from gallery_dl_app.management import ManagementMixin
    from gallery_dl_app.reports import ReportsMixin
    from gallery_dl_app.system_tools import SystemToolsMixin
    original = online_config()
    source = tmp_path / "config.json"
    source.write_text(json.dumps(original), encoding="utf-8")
    with (
        patch.object(ReportsMixin, "_load_autosave_silently", lambda self: None),
        patch.object(SystemToolsMixin, "autosave_session", lambda self: None),
        patch.object(ManagementMixin, "_start_management_services", lambda self: None),
    ):
        app, window = package.create_application([])
        window.config_path = str(source)
        def inspect(dialog):
            if dialog.objectName() == "configBuilderDialog":
                # The Link Builder now opens a separate saved-config workspace.
                # Let its initial website shortcut run before returning.
                app.processEvents()
                return QDialog.Rejected
            if dialog.objectName() != "siteConfigStudio":
                builder = dialog.findChild(LinkBuilder, "linkBuilder")
                builder.site.setCurrentIndex(builder.site.findData("bluesky"))
                builder.mode.setCurrentIndex(builder.mode.findData("user"))
                builder.fill_example()
                builder.configure_website()
                return QDialog.Rejected
            assert dialog.findChild(QComboBox, "siteOptionsSiteCombo").currentText() == "bluesky"
            dialog.findChild(QLineEdit, "siteOptionsSearch").setText("timeout")
            dialog.findChild(QLineEdit, "siteOptionValue_timeout").setText("45")
            dialog.findChild(QPushButton, "siteConfigSave").click()
            assert dialog.result() == QDialog.Accepted
            return QDialog.Accepted
        try:
            with (
                patch.object(QDialog, "exec", inspect),
                patch.object(QMessageBox, "question", return_value=QMessageBox.Yes),
                patch.object(window, "show_compact_message"),
                patch.object(window, "_refresh_env"),
            ):
                window.open_download_composer()
            expected = copy.deepcopy(original)
            expected["extractor"]["bluesky"] = {"timeout": 45}
            assert json.loads(source.read_text(encoding="utf-8")) == expected
        finally:
            window.config_path = None
            window.close()
            app.processEvents()
