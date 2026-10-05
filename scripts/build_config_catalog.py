"""Regenerate the offline option index from the pinned gallery-dl manual.

Run manually when upgrading the bundled gallery-dl version. The application
never downloads documentation at runtime.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.request import urlopen


VERSION = "v1.32.14"
SOURCE = f"https://raw.githubusercontent.com/mikf/gallery-dl/{VERSION}/docs/configuration.rst"
TARGET = Path(__file__).resolve().parents[1] / "gallery_dl_app" / "assets" / "config-options.json"
HEADING = re.compile(r"^(extractor|downloader|output|cache|jinja)\.[\w*\[\].& -]+$")
POSTPROCESSOR_HEADING = re.compile(r"^[a-z][a-z0-9-]*\.[a-z][a-z0-9-]*$")
FIELD = re.compile(r"^(Type|Default|Description|Example|Note|Supported Values|Available Events|Special Values|Implementation Detail)$")


def clean(value: str) -> str:
    value = re.sub(r"`([^`<>]+) <[^`]+>`__?", r"\1", value)
    value = value.replace("``", "").replace("*", "")
    return " ".join(value.split())


def parse(source: str) -> list[dict[str, object]]:
    lines = source.splitlines()
    catalog: list[dict[str, object]] = []
    postprocessor_start = lines.index("Postprocessor Options")
    postprocessor_end = lines.index("Miscellaneous Options")
    for index, title in enumerate(lines[:-1]):
        is_postprocessor = postprocessor_start < index < postprocessor_end and bool(POSTPROCESSOR_HEADING.fullmatch(title))
        if not (HEADING.fullmatch(title) or is_postprocessor) or not re.fullmatch(r"-+", lines[index + 1]):
            continue
        if len(lines[index + 1]) < len(title):
            continue
        next_heading = index + 2
        while next_heading < len(lines) - 1:
            if lines[next_heading] and re.fullmatch(r"-+", lines[next_heading + 1]) and len(lines[next_heading + 1]) >= len(lines[next_heading]):
                break
            next_heading += 1
        chunk = lines[index + 2 : next_heading]
        fields: dict[str, list[str]] = {}
        raw_fields: dict[str, list[str]] = {}
        current = ""
        for line in chunk:
            if FIELD.fullmatch(line):
                current = line
                fields.setdefault(current, [])
                raw_fields.setdefault(current, [])
            elif current and line.strip():
                fields[current].append(clean(line.strip()))
                raw_fields[current].append(line)
        types = " ".join(fields.get("Type", [])).lower()
        allowed_types = []
        for line in raw_fields.get("Type", []):
            match = re.match(r"\s*(?:[*+]\s*)?``(bool|string|integer|number|float|list|object)``", line)
            if match:
                kind = {"bool": "boolean", "string": "text", "float": "number"}.get(match[1], match[1])
                if kind not in allowed_types:
                    allowed_types.append(kind)
        value_type = "text"
        if "bool" in types and "string" not in types and "integer" not in types:
            value_type = "boolean"
        elif "integer" in types and "string" not in types:
            value_type = "integer"
        elif "number" in types and "string" not in types:
            value_type = "number"
        elif "list" in types or "object" in types:
            value_type = "json"
        default = None
        if fields.get("Default"):
            candidate = fields["Default"][0]
            try:
                default = json.loads(candidate)
            except (ValueError, TypeError):
                block = "\n".join(raw_fields.get("Default", []))
                start = next((pos for pos, char in enumerate(block) if char in "[{"), None)
                if start is not None:
                    try:
                        default, _ = json.JSONDecoder().raw_decode(block[start:])
                    except ValueError:
                        pass
        description = clean(" ".join(fields.get("Description", [])))
        choices = []
        choice_help: dict[str, str] = {}
        for section in ("Description", "Supported Values", "Available Events"):
            active_choices = []
            for line in raw_fields.get(section, []):
                stripped = line.strip().removeprefix("* ")
                tokens = re.findall(r"``([^`]+)``", stripped)
                remainder = re.sub(r"``[^`]+``", "", stripped).replace("|", "").strip()
                if not tokens or remainder:
                    for token in active_choices:
                        choice_help[token] = (choice_help.get(token, "") + " " + clean(stripped)).strip()
                    continue
                active_choices = []
                for token in tokens:
                    try:
                        choice = json.loads(token)
                    except ValueError:
                        if section == "Description":
                            continue
                        choice = token
                    if isinstance(choice, (dict, list)):
                        continue
                    if not any(type(choice) is type(existing) and choice == existing for existing in choices):
                        choices.append(choice)
                    active_choices.append(json.dumps(choice, ensure_ascii=False))
        first, *siblings = title.split(" & ")
        suggestions = []
        if "list" in allowed_types:
            for line in raw_fields.get("Example", []):
                tokens = re.findall(r"``([^`]+)``", line)
                for token in tokens:
                    try:
                        values = json.loads(token)
                    except ValueError:
                        continue
                    values = values if isinstance(values, list) else [values]
                    for value in values:
                        if isinstance(value, str) and value not in suggestions:
                            suggestions.append(value)
        prefix = first.rsplit(".", 1)[0]
        paths = [first] + [prefix + sibling for sibling in siblings]
        for path in paths:
            catalog.append({
                "path": f"postprocessor.{path}" if is_postprocessor else path,
                "type": value_type,
                "default": default,
                "default_text": " ".join(fields.get("Default", [])),
                "description": description,
                "declared_type": types,
                "allowed_types": allowed_types,
                "string_items": bool(re.search(r"``list``\s+of\s+``strings``", " ".join(raw_fields.get("Type", [])))),
                "choices": choices,
                "choice_help": choice_help,
                "suggestions": suggestions,
                "example": "\n".join(line.strip() for line in raw_fields.get("Example", []) if not line.strip().startswith(".. code")),
                "notes": clean(" ".join(fields.get("Note", []))),
            })
    return catalog


if __name__ == "__main__":
    with urlopen(SOURCE, timeout=30) as response:
        entries = parse(response.read().decode("utf-8"))
    TARGET.write_text(
        json.dumps({"version": VERSION, "source": SOURCE, "options": entries}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Saved {len(entries)} documented options to {TARGET}")
