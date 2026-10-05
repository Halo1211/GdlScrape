"""Resolve postprocessor settings without initializing or executing actions."""

from __future__ import annotations

import copy


def postprocessor_options(config: dict, scopes: tuple[dict, ...]) -> dict:
    """Root overrides win; otherwise the nearest extractor scope wins.

    An explicit null or empty object stops inheritance, matching gallery-dl.
    Scopes are ordered from shared extractor settings to the current page.
    """
    for block in (config, *reversed(scopes)):
        if "postprocessor-options" in block:
            value = block["postprocessor-options"]
            return copy.deepcopy(value) if isinstance(value, dict) else {}
    return {}


def resolved_postprocessor_action(config: dict, action: dict | str, *, overrides: dict | None = None) -> dict:
    """Match preset, override and name/mode/event precedence in DownloadJob."""
    presets = config.get("postprocessor")
    presets = presets if isinstance(presets, dict) else {}
    if isinstance(action, str):
        preset = presets.get(action)
        result = copy.deepcopy(preset) if isinstance(preset, dict) and preset else {"name": action}
    elif isinstance(action, dict):
        result = copy.deepcopy(action)
        if isinstance(result.get("type"), str):
            preset = presets.get(result["type"])
            if isinstance(preset, dict):
                result = {**copy.deepcopy(preset), **result}
            result.setdefault("name", result["type"])
    else:
        return {}
    if overrides:
        result.update(copy.deepcopy(overrides))
    name = result.get("name")
    if not isinstance(name, str):
        return result
    name, sep, event = name.rpartition("@")
    if sep:
        result["name"] = name
        result.setdefault("event", event)
    name, sep, mode = result.get("name", "").rpartition("/")
    if sep:
        result["name"] = name
        result.setdefault("mode", mode)
    return result
