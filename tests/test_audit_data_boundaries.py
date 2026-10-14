"""Data-loss and validation regressions found in the comprehensive audit."""

import ast
import copy
from types import SimpleNamespace

import openpyxl
import pytest

from gallery_dl_app.composer import apply_config_editor_drafts
from gallery_dl_app.config_maker import apply_config_delta, config_changes
from gallery_dl_app.core import validate_cookies_txt
from gallery_dl_app.feature_logic import build_filter_expression
from gallery_dl_app.queue_controller import QueueControllerMixin
from gallery_dl_app.url_builder import validate_supported_url


@pytest.mark.parametrize("raw, expected", [("001", 1), ("-001", -1), ("08", 8)])
def test_visual_filter_accepts_decimal_ids_with_leading_zeroes(raw, expected):
    expression = build_filter_expression([("id", "==", raw)])
    tree = ast.parse(expression, mode="eval")
    assert ast.literal_eval(tree.body.comparators[0]) == expected


@pytest.mark.parametrize("key", ["directory", "filename", "cookies", "headers", "extension-map"])
def test_site_option_replacement_removes_old_dictionary_entries(key):
    original = {"extractor": {"imgur": {key: {"old": "a", "new": "b"}, "metadata": True}}}
    before = copy.deepcopy(original)
    updated = apply_config_editor_drafts(original, site_overrides={"imgur": {key: {"new": "b"}}})
    assert updated["extractor"]["imgur"][key] == {"new": "b"}
    assert updated["extractor"]["imgur"]["metadata"] is True
    assert original == before


@pytest.mark.parametrize("key", ["directory", "filename"])
def test_reordering_path_rules_is_reported_as_unsaved_change(key):
    before = {"extractor": {"imgur": {key: {"width > 100": "wide", "height > 100": "tall"}}}}
    after = copy.deepcopy(before)
    after["extractor"]["imgur"][key] = dict(reversed(list(before["extractor"]["imgur"][key].items())))
    assert config_changes(before, after) == [(("extractor", "imgur", key), False)]


def test_config_delta_preserves_reordered_rules_through_parent_sections():
    original = {"extractor": {"imgur": {"directory": {"width > 100": ["Wide"], "height > 100": ["Tall"]}}}}
    edited = copy.deepcopy(original)
    edited["extractor"]["imgur"]["directory"] = dict(reversed(list(original["extractor"]["imgur"]["directory"].items())))
    updated = apply_config_delta(original, original, edited)
    assert list(updated["extractor"]["imgur"]["directory"]) == ["height > 100", "width > 100"]
    assert apply_config_delta({}, original, original) == {}


def test_explicit_empty_object_appears_in_config_change_review():
    assert config_changes({}, {"extractor": {"postprocessor-options": {}}}) == [
        (("extractor", "postprocessor-options"), False)
    ]


@pytest.mark.parametrize("format", ["csv", "xlsx"])
def test_duplicate_spreadsheet_headers_do_not_silently_drop_urls(tmp_path, format):
    path = tmp_path / ("links." + format)
    rows = [["url", " URL "], ["https://example.com/one", "https://example.com/two"]]
    if format == "csv":
        path.write_text("\n".join(",".join(row) for row in rows), encoding="utf-8")
        read = QueueControllerMixin.read_csv_as_commands
    else:
        workbook = openpyxl.Workbook()
        for row in rows:
            workbook.active.append(row)
        workbook.save(path)
        workbook.close()
        read = QueueControllerMixin.read_xlsx_as_commands
    with pytest.raises(ValueError, match="(?i)duplicate"):
        read(SimpleNamespace(), str(path))


@pytest.mark.parametrize("prefix", ["", "#HttpOnly_"])
def test_netscape_cookies_with_empty_values_are_valid(tmp_path, prefix):
    path = tmp_path / "cookies.txt"
    path.write_text(f"{prefix}.example.com\tTRUE\t/\tFALSE\t0\tname\t\n", encoding="utf-8")
    assert validate_cookies_txt(str(path))[0]


@pytest.mark.parametrize("port", ["bad", "99999", "-1", ""])
def test_complete_link_validation_rejects_invalid_explicit_ports(port):
    with pytest.raises(ValueError):
        validate_supported_url(f"https://example.com:{port}/file.jpg")
