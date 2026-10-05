import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QTableWidget

from gallery_dl_app.composer import config_option_definitions, documented_config_options, parse_typed_config_value
from gallery_dl_app.themes import DARK_QSS
from gallery_dl_app.config_maker import (
    apply_config_delta, config_changes, config_path_value, edit_config_path, filename_example, installed_site_catalog, resolve_config_path,
)
from gallery_dl_app.config_value_editor import StructuredConfigEditor
from gallery_dl_app.postprocessor_editor import PostprocessorEditor, postprocessor_actions


class ConfigMakerTests(unittest.TestCase):
    def test_imported_values_are_kept_until_the_gui_changes_them(self):
        original = {"extractor": {"timeout": 30.5, "filename": {"": "{id}.jpg"}, "archive-pragma": ["busy_timeout=5000"]},
                    "output": {"progress": True}, "netrc": False}
        baseline = {"extractor": {"timeout": 30, "filename": "{'': '{id}.jpg'}", "archive-pragma": ["journal_mode=WAL"], "input": False},
                    "output": {"progress": True}, "netrc": False}
        self.assertEqual(apply_config_delta(original, baseline, baseline), original)
        self.assertEqual(apply_config_delta({}, baseline, baseline), {})
        edited = {**baseline, "extractor": {**baseline["extractor"], "timeout": 45}}
        self.assertEqual(apply_config_delta(original, baseline, edited)["extractor"],
                         {**original["extractor"], "timeout": 45})
        self.assertEqual(filename_example("{title}/{num:>03}.{extension}", "pixiv"), "Artwork/001.jpg")
        self.assertEqual(filename_example("{title} [{id}].json", "pixiv"), "Artwork [12345].json")
        with self.assertRaises(ValueError):
            filename_example("{filename!E}", "pixiv")
        with self.assertRaises(ValueError):
            filename_example("{filename:999999999s}", "pixiv")

    def test_postprocessor_tasks_keep_site_lists_separate_and_preserve_custom_fields(self):
        app = QApplication.instance() or QApplication([])
        from PySide6.QtWidgets import QPushButton
        state = {"extractor": {
            "postprocessors": [{"name": "hash", "algorithm": "sha256"}],
            "pixiv": {"postprocessors": [{"name": "metadata", "mode": "json", "event": "post",
                       "filename": "{title} [{id}].json", "indent": 2, "ascii": False, "custom-field": "keep"}]},
            "pawchive": {"postprocessors": [{"name": "metadata", "mode": "json", "event": "post", "indent": 2}]},
        }}
        original = __import__('copy').deepcopy(state)
        changes = []
        def change(site, actions):
            nonlocal state
            path = ("extractor", *([site] if site else []), "postprocessors")
            state = edit_config_path(state, path, actions, remove=actions is None)
            changes.append(site)
        editor = PostprocessorEditor(["pixiv", "pawchive", "instagram"], lambda: state, change, site="pixiv")
        try:
            self.assertEqual(changes, [])
            self.assertEqual(editor.event.currentData(), "post")
            self.assertEqual(editor.filename.text(), "{title} [{id}].json")
            self.assertEqual(editor.indent.value(), 2)
            self.assertFalse(editor.ascii.isChecked())
            self.assertIn("Artwork [12345].json", editor.name_preview.text())
            editor.indent.setValue(4)
            self.assertEqual(state["extractor"]["pixiv"]["postprocessors"][0]["custom-field"], "keep")
            self.assertEqual(state["extractor"]["pawchive"], original["extractor"]["pawchive"])
            editor.task.setCurrentIndex(editor.task.findData("zip"))
            editor.findChild(QPushButton, "postprocessorAdd").click()
            self.assertTrue(state["extractor"]["pixiv"]["postprocessors"][1]["keep-files"])
            editor.keep.setChecked(False)
            self.assertIn("removed", editor.result.text())
            editor.move(-1)
            self.assertEqual(state["extractor"]["pixiv"]["postprocessors"][0]["name"], "zip")
            editor.remove_action()
            editor.site.setCurrentIndex(editor.site.findData("instagram"))
            self.assertEqual(editor.actions, [])
            self.assertIn("shared actions", editor.shared.text())
            editor.task.setCurrentIndex(editor.task.findData("json"))
            editor.add_action()
            editor.event.setCurrentIndex(editor.event.findData("post"))
            editor.findChild(QPushButton, "postprocessorTitleFilename").click()
            self.assertEqual(state["extractor"]["instagram"]["postprocessors"][0],
                             {"name": "metadata", "mode": "json", "event": "post", "indent": 2, "ascii": False, "filename": "{title} [{id}].json"})
            self.assertEqual(state["extractor"]["postprocessors"], original["extractor"]["postprocessors"])
            editor.use_default()
            self.assertNotIn("postprocessors", state["extractor"]["instagram"])
            self.assertEqual(postprocessor_actions(state, "instagram"), ([], False))
            editor.site.setCurrentIndex(editor.site.findData("pawchive"))
            self.assertEqual(editor.actions, original["extractor"]["pawchive"]["postprocessors"])
        finally:
            editor.close()
            app.processEvents()

    def test_manual_choices_preserve_unquoted_names_aliases_and_container_types(self):
        specs = {spec.key: spec for spec in config_option_definitions("instagram")}
        self.assertEqual(specs["include"].choices, ("posts", "reels", "tagged", "stories", "highlights", "info", "avatar"))
        self.assertEqual(specs["videos"].choices, (True, "dash", "ytdl", "merged", False))
        self.assertIn("pre-merged", specs["videos"].choice_help['"merged"'])
        self.assertEqual(specs["archive-event"].choices, ("file", "after", "skip"))
        self.assertEqual(specs["archive-pragma"].allowed_types, ("list",))
        self.assertEqual(specs["extension-map"].default["jpeg"], "jpg")
        self.assertEqual(specs["previews"].suggestions, ("video", "audio"))

    def test_string_lists_and_mixed_boolean_lists_have_clear_controls_and_no_clipped_rows(self):
        app = QApplication.instance() or QApplication([])
        editor = StructuredConfigEditor(parse_typed_config_value, allowed_types=("boolean", "text", "list"),
                                        string_items=True, item_choices=("audio", "video"))
        messages = []
        previous = qInstallMessageHandler(lambda _kind, _context, message: messages.append(message))
        try:
            editor.setStyleSheet(DARK_QSS)
            editor.set_value(False)
            self.assertIs(editor.value(), False)
            editor.kind.setCurrentIndex(editor.kind.findData("list"))
            editor.add_row(value="video")
            editor.add_row(value="audio")
            editor.resize(420, 360)
            editor.show()
            app.processEvents()
            self.assertEqual(editor.value(), ["video", "audio"])
            self.assertTrue(editor.table.isColumnHidden(0))
            self.assertTrue(editor.table.isColumnHidden(1))
            self.assertIsInstance(editor.table.cellWidget(0, 2), QComboBox)
            for row in range(2):
                self.assertGreaterEqual(editor.table.rowHeight(row), editor.table.cellWidget(row, 2).sizeHint().height())
            editor.configure(allowed_types=("object",), string_items=True, name_hint="jpeg", value_hint="jpg")
            editor.set_value({"jpeg": "jpg"})
            editor.table.cellWidget(0, 0).setText("jpe")
            self.assertEqual(editor.value(), {"jpe": "jpg"})
            self.assertEqual(editor.table.editTriggers(), QTableWidget.NoEditTriggers)
            app.processEvents()
            self.assertFalse(any("QFont::setPointSize" in message for message in messages), messages)
        finally:
            qInstallMessageHandler(previous)
            editor.close()
            app.processEvents()

    def test_every_documented_option_can_resolve_to_an_editable_location(self):
        sites = installed_site_catalog()
        self.assertIn("danbooru", sites)
        self.assertIn("mastodon", sites)
        self.assertIn("hypnohub", sites)
        for entry in documented_config_options():
            raw = entry["path"]
            site = ""
            parts = raw.split(".")
            if parts[1].startswith("["):
                family = parts[1].strip("[]").lower().replace("-", "").removesuffix("extractor")
                site = next((name for name, families in sites.items() if family in families), "")
                self.assertTrue(site, raw)
            with self.subTest(path=raw):
                path = resolve_config_path(raw, site=site)
                changed = edit_config_path({}, path, entry.get("default"))
                self.assertEqual(config_path_value(changed, path), (entry.get("default"), True))

    def test_shared_settings_can_target_a_site_page_type_or_downloader(self):
        self.assertEqual(resolve_config_path("extractor.*.timeout"), ("extractor", "timeout"))
        self.assertEqual(resolve_config_path("extractor.*.timeout", site="twitter", subcategory="user"),
                         ("extractor", "twitter", "user", "timeout"))
        self.assertEqual(resolve_config_path("downloader.*.retries", downloader="http"),
                         ("downloader", "http", "retries"))
        with self.assertRaises(ValueError):
            resolve_config_path("extractor.*.timeout", subcategory="user")
        with self.assertRaises(ValueError):
            resolve_config_path("extractor.[Danbooru].threshold", site="imagefap")

    def test_postprocessor_edits_preserve_other_actions_and_original_data(self):
        original = {"extractor": {"postprocessors": [{"name": "metadata", "filename": "keep.json"}]}}
        path = resolve_config_path("postprocessor.zip.compression", postprocessor_index=1)
        updated = edit_config_path(original, path, "deflate")
        updated = edit_config_path(updated, path[:-1] + ("name",), "zip")
        self.assertEqual(len(original["extractor"]["postprocessors"]), 1)
        self.assertEqual(updated["extractor"]["postprocessors"][0], original["extractor"]["postprocessors"][0])
        removed = edit_config_path(updated, path, remove=True)
        self.assertEqual(removed["extractor"]["postprocessors"][1], {"name": "zip"})
        with self.assertRaises(ValueError):
            edit_config_path(original, ("extractor", "postprocessors", 8, "name"), "zip")
        with self.assertRaises(ValueError):
            edit_config_path({"output": "invalid"}, ("output", "progress"), False)

    def test_summary_tracks_secret_changes_without_returning_secret_values(self):
        changes = config_changes({"secret": "old", "count": 1, "removed": True},
                                 {"secret": "new", "count": True})
        self.assertIn((("secret",), False), changes)
        self.assertIn((("count",), False), changes)
        self.assertIn((("removed",), True), changes)
        self.assertNotIn("new", str(changes))

    def test_structured_values_round_trip_types_and_keep_invalid_edits(self):
        app = QApplication.instance() or QApplication([])
        editor = StructuredConfigEditor(parse_typed_config_value)
        try:
            value = {"label": "001", "count": 2, "enabled": False, "nested": [1, None], "password": "private"}
            editor.set_value(value)
            self.assertEqual(editor.value(), value)
            self.assertEqual(editor.table.cellWidget(4, 2).echoMode(), QLineEdit.Password)
            editor.table.cellWidget(1, 2).setText("invalid")
            snapshot = editor.snapshot()
            editor.set_value([])
            editor.set_snapshot(snapshot)
            with self.assertRaises(ValueError):
                editor.value()
            self.assertEqual(editor.table.cellWidget(1, 2).text(), "invalid")
            editor.table.cellWidget(1, 2).setText("3")
            self.assertEqual(editor.value()["count"], 3)
            editor.table.item(0, 0).setText("count")
            with self.assertRaises(ValueError):
                editor.value()
        finally:
            editor.close()
            app.processEvents()
