"""Catalog routing and configuration edits shared by the guided config maker."""

from __future__ import annotations

import copy
import re
import string
from functools import lru_cache

from .core import is_sensitive_option_key

ConfigPath = tuple[str | int, ...]


def filename_example(pattern: str, site: str) -> str:
    """Preview basic filename tokens with sample metadata, without evaluation."""
    values = {"category": site, "subcategory": "post", "filename": "photo", "id": "12345", "title": "Artwork",
              "num": 1, "extension": "jpg", "user": {"name": "alice", "id": "42"}}
    result = []
    for literal, name, spec, conversion in string.Formatter().parse(pattern):
        result.append(literal)
        if name is None:
            continue
        if conversion or not re.fullmatch(r"[<>^]?0?\d{0,3}(?:\.\d{1,2})?[sdf]?", spec):
            raise ValueError("This advanced pattern needs real download metadata for a preview")
        selected = None
        for candidate in name.split("|"):
            match = re.fullmatch(r"([a-z][a-z0-9_-]*)(?:\[([a-z][a-z0-9_-]*)\])?", candidate)
            if not match:
                raise ValueError("This advanced pattern needs real download metadata for a preview")
            selected = values.get(match[1])
            if match[2]:
                selected = selected.get(match[2]) if isinstance(selected, dict) else None
            if selected is not None:
                break
        if selected is None:
            raise ValueError(f"Sample data has no field named {name}")
        result.append(format(selected, spec))
    return "".join(result)


def apply_config_delta(original: dict, baseline: dict, edited: dict) -> dict:
    """Apply only GUI changes, keeping imported values that controls normalize."""
    result = copy.deepcopy(original)
    for key in set(baseline) | set(edited):
        if key not in edited:
            result.pop(key, None)
        elif key not in baseline:
            result[key] = copy.deepcopy(edited[key])
        elif type(baseline[key]) is type(edited[key]) and baseline[key] == edited[key]:
            continue
        elif isinstance(baseline[key], dict) and isinstance(edited[key], dict):
            previous = original.get(key)
            nested = apply_config_delta(previous if isinstance(previous, dict) else {}, baseline[key], edited[key])
            if nested != previous:
                result[key] = nested
        elif type(baseline[key]) is not type(edited[key]) or baseline[key] != edited[key]:
            result[key] = copy.deepcopy(edited[key])
    return result


def contains_config_secrets(value: object) -> bool:
    if isinstance(value, dict):
        return any(is_sensitive_option_key(key) or contains_config_secrets(item) for key, item in value.items())
    if isinstance(value, list):
        return any(contains_config_secrets(item) for item in value)
    return False


@lru_cache(maxsize=1)
def installed_site_catalog() -> dict[str, tuple[str, ...]]:
    """Include named instances of shared extractors, such as Danbooru sites."""
    from gallery_dl import extractor

    families: dict[str, set[str]] = {}
    for cls in extractor.extractors():
        names = {str(getattr(cls, "category", "")), str(getattr(cls, "basecategory", ""))}
        names.update(str(instance[0]) for instance in getattr(cls, "instances", ()))
        bases = {
            base.__name__.lower().removesuffix("extractor").replace("-", "")
            for base in cls.__mro__
        }
        for name in names - {""}:
            families.setdefault(name.lower(), set()).update(bases)
    return {name: tuple(sorted(bases)) for name, bases in sorted(families.items())}


@lru_cache(maxsize=512)
def installed_page_types(site: str) -> tuple[str, ...]:
    from gallery_dl import extractor
    result = set()
    for cls in extractor.extractors():
        names = {str(getattr(cls, "category", "")), str(getattr(cls, "basecategory", ""))}
        names.update(str(instance[0]) for instance in getattr(cls, "instances", ()))
        if site in names and getattr(cls, "subcategory", ""):
            result.add(cls.subcategory)
    return tuple(sorted(result))


def resolve_config_path(
    raw: str, *, site: str = "", subcategory: str = "", downloader: str = "",
    postprocessor_index: int = 0,
) -> ConfigPath:
    """Resolve manual placeholders into the real dictionary/list location."""
    parts = raw.split(".")
    if parts[0] == "postprocessor":
        if len(parts) != 3:
            raise ValueError("Choose a documented postprocessor field")
        return ("extractor", *([site] if site else []), "postprocessors", postprocessor_index, parts[2])
    if len(parts) > 1 and parts[1].startswith("["):
        if not site:
            raise ValueError("Choose the website that should use this setting")
        family = parts[1].strip("[]").lower().replace("-", "").removesuffix("extractor")
        if family not in installed_site_catalog().get(site, ()):
            raise ValueError(f"{site} does not belong to the {parts[1]} family")
        parts[1] = site
    elif len(parts) > 1 and parts[1] == "*":
        if subcategory and parts[0] == "extractor" and not site:
            raise ValueError("Choose a website before a page type")
        target = site if parts[0] == "extractor" else downloader
        parts[1:2] = [target] if target else []
        if subcategory and parts[0] == "extractor":
            parts.insert(2 if site else 1, subcategory)
    path = tuple(parts)
    validate_config_path(path)
    return path


def validate_config_path(path: ConfigPath) -> None:
    if not path or any(
        (isinstance(part, int) and (isinstance(part, bool) or part < 0))
        or (not isinstance(part, int) and (
            not isinstance(part, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*(?:>[A-Za-z][A-Za-z0-9_-]*)?", part)
        ))
        for part in path
    ):
        raise ValueError("Choose a concrete documented JSON path")


def config_path_value(data: dict, path: ConfigPath, default: object = None) -> tuple[object, bool]:
    node: object = data
    for part in path:
        if isinstance(node, dict) and isinstance(part, str) and part in node:
            node = node[part]
        elif isinstance(node, list) and isinstance(part, int) and 0 <= part < len(node):
            node = node[part]
        else:
            return default, False
    return node, True


def edit_config_path(data: dict, path: ConfigPath, value: object = None, *, remove: bool = False) -> dict:
    """Preserve peers and safely append a new postprocessor object when needed."""
    validate_config_path(path)
    result = copy.deepcopy(data)
    node = result
    for index, part in enumerate(path[:-1]):
        next_part = path[index + 1]
        container = [] if isinstance(next_part, int) else {}
        if isinstance(node, dict) and isinstance(part, str):
            if part not in node:
                if remove:
                    return result
                node[part] = container
            elif part == "postprocessors" and isinstance(next_part, int) and isinstance(node[part], (dict, str)):
                node[part] = [node[part]]
            node = node[part]
        elif isinstance(node, list) and isinstance(part, int):
            if part == len(node) and not remove:
                node.append(container)
            if not 0 <= part < len(node):
                if remove:
                    return result
                raise ValueError("Choose an existing item or the next new item")
            if isinstance(node[part], str) and index > 0 and path[index - 1] == "postprocessors":
                node[part] = {"type": node[part]}
            node = node[part]
        else:
            raise ValueError(f"Cannot edit nested option under non-object {part}")
        if not isinstance(node, (dict, list)):
            raise ValueError(f"Cannot edit nested option under non-object {part}")
    key = path[-1]
    if isinstance(node, dict) and isinstance(key, str):
        if remove:
            node.pop(key, None)
        else:
            node[key] = copy.deepcopy(value)
    elif isinstance(node, list) and isinstance(key, int):
        if remove and key < len(node):
            node.pop(key)
        elif not remove and key == len(node):
            node.append(copy.deepcopy(value))
        elif not remove and key < len(node):
            node[key] = copy.deepcopy(value)
        elif not remove:
            raise ValueError("Choose an existing item or the next new item")
    else:
        raise ValueError("The selected path does not match this config's structure")
    return result


def config_changes(original: dict, updated: dict, prefix: ConfigPath = ()) -> list[tuple[ConfigPath, bool]]:
    """Describe changed paths without exposing their values or login secrets."""
    changes = []
    for key in sorted(set(original) | set(updated)):
        path = prefix + (key,)
        if key not in updated:
            changes.append((path, True))
        elif key not in original:
            if isinstance(updated[key], dict):
                changes.extend(config_changes({}, updated[key], path))
            else:
                changes.append((path, False))
        elif isinstance(original[key], dict) and isinstance(updated[key], dict):
            changes.extend(config_changes(original[key], updated[key], path))
        elif type(original[key]) is not type(updated[key]) or original[key] != updated[key]:
            changes.append((path, False))
    return changes
