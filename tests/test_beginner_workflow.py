import copy
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QPlainTextEdit, QPushButton, QTabWidget
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from gallery_dl_app.config_examples import EXAMPLES
from gallery_dl_app.config_maker import edit_config_path
from gallery_dl_app.link_builder import LinkBuilder
from gallery_dl_app.postprocessor_editor import PostprocessorEditor
from tests.test_config_guided_media import isolated_window


def test_recipe_switching_keeps_separate_drafts_and_explicit_clear():
    app = QApplication.instance() or QApplication([])
    editor = LinkBuilder(lambda links: len(links))
    try:
        editor.site.setCurrentIndex(editor.site.findData("bluesky"))
        editor.mode.setCurrentIndex(editor.mode.findData("post"))
        editor.fill_example()
        original = editor.generated_links()
        editor.site.setCurrentIndex(editor.site.findData("instagram"))
        editor.mode.setCurrentIndex(editor.mode.findData("url"))
        editor.batch.setChecked(True)
        editor.batch_input.setPlainText("https://www.instagram.com/example/\nhttps://www.instagram.com/other/")
        batch = editor.generated_links()
        editor.site.setCurrentIndex(editor.site.findData("bluesky"))
        editor.mode.setCurrentIndex(editor.mode.findData("post"))
        assert editor.generated_links() == original
        editor.site.setCurrentIndex(editor.site.findData("instagram"))
        editor.mode.setCurrentIndex(editor.mode.findData("url"))
        assert editor.batch.isChecked() and editor.generated_links() == batch
        editor.clear.click()
        assert not editor.add.isEnabled()
        editor.site.setCurrentIndex(editor.site.findData("bluesky"))
        editor.mode.setCurrentIndex(editor.mode.findData("post"))
        assert editor.generated_links() == original
    finally:
        editor.close()
        app.processEvents()


def test_dashboard_groups_queue_tools_and_manage_has_no_option_editor():
    with isolated_window() as (app, window):
        assert window.btn_action_preview.isHidden() and window.btn_action_dedupe.isHidden()
        menu = window.btn_action_tools.menu()
        assert len(menu.actions()) == 3
        assert "filter" in menu.actions()[2].text().lower()
        window.open_management_center()
        dialog = window._management_dialog
        tabs = dialog.findChild(QTabWidget, "managementTabs")
        assert tabs.count() == 5
        assert all("Options" not in tabs.tabText(i) and "Runtime" not in tabs.tabText(i) for i in range(tabs.count()))
        dialog.accept()
        app.processEvents()


def test_download_config_shortcut_has_own_draft_and_reloads_saved_config(tmp_path):
    path = tmp_path / "config.json"
    original = {"extractor": {"instagram": {"videos": False}}}
    path.write_text(json.dumps(original))
    with isolated_window() as (_app, window):
        window.config_path = str(path)

        def save_site(site):
            assert site == "instagram"
            # Simulate saving in the separate Config Maker workspace.
            assert json.loads(path.read_text()) == original
            path.write_text(json.dumps({"extractor": {"instagram": {"videos": True}}}))

        def inspect(dialog):
            tabs = dialog.findChild(QTabWidget, "composerTabs")
            assert "1." in tabs.tabText(tabs.currentIndex())
            assert all(tabs.tabText(i) != "Sites" or not tabs.isTabVisible(i) for i in range(tabs.count()))
            save_buttons = [button for button in dialog.findChildren(QPushButton) if button.text() in {"Save Defaults", "Save As..."}]
            assert len(save_buttons) == 2 and all(button.isHidden() for button in save_buttons)
            destination = next(field for field in dialog.findChildren(QLineEdit) if field.placeholderText() == "D:/Downloads/gallery-dl")
            destination.setText(str(tmp_path / "only-this-job"))
            builder = dialog.findChild(LinkBuilder, "linkBuilder")
            builder.inputs[0].setText("https://www.instagram.com/example/")
            builder.configure_website()
            assert destination.text().endswith("only-this-job")
            previews = [field.toPlainText() for field in dialog.findChildren(QPlainTextEdit) if field.isReadOnly()]
            assert any('"videos": true' in text for text in previews)
            return QDialog.Rejected

        with patch.object(window, "open_config_builder", side_effect=save_site) as open_config, patch.object(QDialog, "exec", inspect):
            window.open_download_composer()
        open_config.assert_called_once_with("instagram")
        assert "base-directory" not in json.loads(path.read_text())["extractor"]


def test_cbz_information_shortcut_edits_only_this_site_and_runs_with_gallery_dl(tmp_path):
    app = QApplication.instance() or QApplication([])
    state = {"extractor": {"pixiv": {"metadata": True}}}

    def change(site, actions):
        nonlocal state
        state = edit_config_path(state, ("extractor", site, "postprocessors"), actions)

    editor = PostprocessorEditor(["mangadex", "pixiv"], lambda: state, change, site="mangadex")
    try:
        editor.task.setCurrentIndex(editor.task.findData("cbz-info"))
        editor.add_action()
        assert len(editor.actions) == 2
        assert editor.zip_files.toPlainText() == "info.json"
        editor.zip_files.setPlainText("info.json\ntags.txt\ninfo.json")
        actions = state["extractor"]["mangadex"]["postprocessors"]
        assert actions[1]["files"] == ["info.json", "tags.txt"]
        assert state["extractor"]["pixiv"] == {"metadata": True}
        example = json.loads(next(item.path for item in EXAMPLES if item.key == "manga-cbz-information").read_text())
        assert actions[0] == example["extractor"]["mangadex"]["postprocessors"][0]

        from gallery_dl.postprocessor.metadata import MetadataPP
        from gallery_dl.postprocessor.zip import ZipPP
        gallery = tmp_path / "chapter"
        gallery.mkdir()
        image = gallery / "001.jpg"
        image.write_bytes(b"sample image bytes")
        (gallery / "tags.txt").write_text("sample")
        pathfmt = SimpleNamespace(realdirectory=str(gallery) + os.sep, directory=str(gallery) + os.sep,
                                  extended=False, kwdict={"title": "Sample chapter", "id": 42},
                                  filename=image.name, temppath=str(image), delete=False,
                                  clean_path=lambda value: value, clean_segment=lambda value: value)
        job = SimpleNamespace(pathfmt=pathfmt, get_logger=MagicMock(), register_hooks=MagicMock(),
                              hooks={"finalize": []}, extractor=SimpleNamespace(config=lambda key, default=None: default))
        MetadataPP(job, actions[0]).run(pathfmt)
        archive = ZipPP(job, actions[1])
        archive.write_fast(pathfmt)
        archive.finalize(pathfmt)
        with zipfile.ZipFile(tmp_path / "chapter.cbz") as result:
            assert set(result.namelist()) == {"001.jpg", "info.json", "tags.txt"}
            assert json.loads(result.read("info.json"))["title"] == "Sample chapter"
        assert image.exists() and (gallery / "info.json").exists() and not pathfmt.delete
    finally:
        editor.close()
        app.processEvents()


def test_pixiv_profile_and_search_use_same_history_identity():
    from gallery_dl import config, extractor, formatter
    example = json.loads(next(item.path for item in EXAMPLES if item.key == "pixiv-consistent-history").read_text())
    with patch.dict(config._config, example, clear=True):
        identities = []
        for url in ("https://www.pixiv.net/artworks/42", "https://www.pixiv.net/en/tags/Original/artworks"):
            found = extractor.find(url)
            assert found.config("archive") == "./pixiv-history.sqlite3"
            identities.append(formatter.StringFormatter(found.config("archive-format")).format_map({"id": 42, "suffix": "_p0", "extension": "jpg"}))
        assert identities == ["42_p0.jpg", "42_p0.jpg"]


def test_editing_named_zip_preserves_preset_and_unknown_options():
    app = QApplication.instance() or QApplication([])
    state = {"postprocessor": {"pack": {"name": "zip", "files": ["info.json"], "mode": "safe"}}, "extractor": {"mangadex": {"postprocessors": ["pack"]}}}
    original = copy.deepcopy(state)

    def change(site, actions):
        nonlocal state
        state = edit_config_path(state, ("extractor", site, "postprocessors"), actions)

    editor = PostprocessorEditor(["mangadex"], lambda: state, change, site="mangadex")
    try:
        assert state == original
        editor.zip_files.setPlainText("info.json\nnotes.txt")
        assert state["extractor"]["mangadex"]["postprocessors"] == [{"type": "pack", "files": ["info.json", "notes.txt"]}]
        assert state["postprocessor"] == original["postprocessor"]
    finally:
        editor.close()
        app.processEvents()


def test_multiline_fields_accept_enter_while_typing():
    app = QApplication.instance() or QApplication([])
    state = {"extractor": {"mangadex": {"postprocessors": [{"name": "zip"}, {"name": "metadata"}]}}}

    def change(site, actions):
        nonlocal state
        state = edit_config_path(state, ("extractor", site, "postprocessors"), actions)

    editor = PostprocessorEditor(["mangadex"], lambda: state, change, site="mangadex")
    try:
        QTest.keyClicks(editor.zip_files, "info.json")
        QTest.keyClick(editor.zip_files, Qt.Key_Return)
        assert editor.zip_files.toPlainText() == "info.json\n"
        QTest.keyClicks(editor.zip_files, "tags.txt")
        assert state["extractor"]["mangadex"]["postprocessors"][0]["files"] == ["info.json", "tags.txt"]
        editor.list.setCurrentRow(1)
        editor.metadata_directory.selectAll()
        QTest.keyClicks(editor.metadata_directory, "metadata")
        QTest.keyClick(editor.metadata_directory, Qt.Key_Return)
        assert editor.metadata_directory.toPlainText() == "metadata\n"
        QTest.keyClicks(editor.metadata_directory, "{category}")
        assert state["extractor"]["mangadex"]["postprocessors"][1]["directory"] == ["metadata", "{category}"]
    finally:
        editor.close()
        app.processEvents()


def test_single_link_goes_straight_to_queue_and_invalid_batch_is_atomic(tmp_path):
    with isolated_window() as (_app, window):
        path = tmp_path / "config.json"
        path.write_text('{}')
        window.config_path = str(path)
        window.txt_commands.clear()

        def inspect(dialog):
            builder = dialog.findChild(LinkBuilder, "linkBuilder")
            builder.batch.setChecked(True)
            builder.batch_input.setPlainText("https://www.instagram.com/example/\nNOT-A-LINK")
            add = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Add to Queue")
            add.click()
            assert not window.txt_commands.toPlainText()
            builder.batch.setChecked(False)
            builder.inputs[0].setText("https://www.instagram.com/example/")
            add.click()  # No intermediate Keep button is needed.
            assert len(window.jobs) == 1
            assert window.jobs[0].url == "https://www.instagram.com/example/"
            assert dialog.result() == QDialog.Accepted
            return QDialog.Accepted

        with patch.object(QDialog, "exec", inspect), patch.object(window, "show_compact_message"):
            window.open_download_composer()
        assert path.read_text() == '{}'
