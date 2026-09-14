"""Pure helpers for schedules, option discovery, and visual filters."""

from __future__ import annotations

import ast
import re
import time
from datetime import datetime, timedelta
from typing import Iterable


FALLBACK_OPTIONS = [
    ("--range", "File index range", True),
    ("--chapter-range", "Chapter range", True),
    ("--filter", "Metadata filter expression", True),
    ("--chapter-filter", "Chapter filter expression", True),
    ("--download-archive", "SQLite archive path", True),
    ("--cookies-from-browser", "Browser cookie source", True),
    ("--cookies", "Netscape cookies.txt file", True),
    ("--proxy", "HTTP/SOCKS proxy", True),
    ("--user-agent", "HTTP User-Agent", True),
    ("--retries", "Retry count", True),
    ("--http-timeout", "Connection timeout", True),
    ("--sleep", "Delay between downloads", True),
    ("--sleep-request", "Delay between requests", True),
    ("--filesize-min", "Minimum file size", True),
    ("--filesize-max", "Maximum file size", True),
    ("--write-metadata", "Write metadata JSON", False),
    ("--write-info-json", "Write gallery info JSON", False),
    ("--zip", "Create ZIP archive", False),
    ("--cbz", "Create CBZ archive", False),
    ("--simulate", "Do not download", False),
    ("--no-skip", "Do not skip existing files", False),
]


def _option_requires_value(signature_tail: str) -> bool:
    """Distinguish required metavariables from fully optional ones."""
    remainder = signature_tail.strip()
    return bool(remainder) and re.fullmatch(r"\[[^\]]+\]", remainder) is None


def parse_help_options(text: str) -> list[tuple[str, str, bool]]:
    """Parse the installed gallery-dl help output into a searchable catalog."""
    options: dict[str, tuple[str, str, bool]] = {}
    current: str | None = None
    for raw_line in str(text).splitlines():
        line = raw_line.rstrip()
        match = re.match(r"^\s{2,}(.+?)\s{2,}(\S.*)$", line)
        if match:
            flag_blob, description = match.groups()
            flag_match = re.search(r"--[\w-]+", flag_blob)
            if not flag_match:
                if current and line.startswith(" " * 8):
                    flag, old_description, requires_value = options[current]
                    options[current] = (
                        flag,
                        f"{old_description} {line.strip()}".strip(),
                        requires_value,
                    )
                else:
                    current = None
                continue
            current = flag_match.group(0)
            remainder = flag_blob[flag_match.end():].strip()
            options[current] = (
                current,
                description.strip(),
                _option_requires_value(remainder),
            )
        elif line.startswith("  ") and "--" in line:
            # Long signatures such as --cookies-from-browser have no room for
            # a same-line description; argparse wraps the description onto the
            # following indented line. Keep the declaration so continuation
            # lines can populate its description instead of dropping the flag.
            flag_match = re.search(r"--[\w-]+", line)
            if not flag_match:
                current = None
                continue
            current = flag_match.group(0)
            remainder = line[flag_match.end():].strip()
            options[current] = (current, "", _option_requires_value(remainder))
        elif current and line.startswith(" " * 8) and line.strip():
            flag, description, requires_value = options[current]
            options[current] = (flag, f"{description} {line.strip()}".strip(), requires_value)
        else:
            current = None
    return sorted(options.values()) or list(FALLBACK_OPTIONS)


def build_filter_expression(rules: Iterable[tuple[str, str, str]], joiner: str = "and") -> str:
    """Build a gallery-dl/Python-style expression from validated UI rules."""
    conjunction = "or" if str(joiner).lower() == "or" else "and"
    allowed_operators = {"==", "!=", ">", ">=", "<", "<=", "in", "not in", "contains"}
    expressions: list[str] = []
    for field, operator, raw_value in rules:
        field = str(field).strip()
        operator = str(operator).strip().lower()
        raw_value = str(raw_value).strip()
        if not field and not raw_value:
            continue
        if not _is_valid_filter_field(field):
            raise ValueError(f"Invalid metadata field: {field}")
        if operator not in allowed_operators:
            raise ValueError(f"Unsupported operator: {operator}")
        if not raw_value:
            raise ValueError(f"A value is required for {field}")
        value = _filter_value(raw_value)
        if operator == "contains":
            expressions.append(f"{value} in {field}")
        else:
            expressions.append(f"{field} {operator} {value}")
    return f" {conjunction} ".join(expressions)


def _is_valid_filter_field(field: str) -> bool:
    """Accept metadata lookups without allowing them to become expressions.

    The visual builder supports normal keys, dotted attributes, and indexed
    lookups such as ``items[-1]``.  A character-only regular expression is not
    enough here: it also accepted ``width-height`` as subtraction and malformed
    values such as ``metadata[`` that gallery-dl later rejected.  Validate the
    parsed expression shape and reject Python's introspection attributes.
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\[\]-]*", field):
        return False
    try:
        node = ast.parse(field, mode="eval").body
    except SyntaxError:
        return False

    def valid(part: ast.AST) -> bool:
        if isinstance(part, ast.Name):
            return not part.id.startswith("__")
        if isinstance(part, ast.Attribute):
            return not part.attr.startswith("__") and valid(part.value)
        if isinstance(part, ast.Subscript):
            return valid(part.value) and valid_index(part.slice)
        return False

    def valid_index(part: ast.AST) -> bool:
        if isinstance(part, ast.Name):
            return not part.id.startswith("__")
        if isinstance(part, ast.Constant):
            return isinstance(part.value, int) and not isinstance(part.value, bool)
        return (
            isinstance(part, ast.UnaryOp)
            and isinstance(part.op, ast.USub)
            and isinstance(part.operand, ast.Constant)
            and isinstance(part.operand.value, int)
            and not isinstance(part.operand.value, bool)
        )

    return valid(node)


def _filter_value(raw: str) -> str:
    lowered = raw.lower()
    if lowered in {"true", "false", "none"}:
        return {"true": "True", "false": "False", "none": "None"}[lowered]
    try:
        value = ast.literal_eval(raw)
    except (SyntaxError, ValueError):
        if re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
            return raw
        return repr(raw)
    return repr(value)


def next_schedule_time(schedule: dict[str, object], now: float | None = None) -> float:
    """Calculate the next occurrence strictly after *now*."""
    moment = datetime.fromtimestamp(float(now if now is not None else time.time()))
    frequency = str(schedule.get("frequency") or "daily").lower()
    if frequency == "interval":
        try:
            minutes = max(1, min(int(schedule.get("interval_minutes") or 60), 10080))
        except (TypeError, ValueError, OverflowError):
            minutes = 60
        return (moment + timedelta(minutes=minutes)).timestamp()

    time_text = str(schedule.get("time_of_day") or "02:00")
    try:
        hour, minute = (int(part) for part in time_text.split(":", 1))
    except (TypeError, ValueError):
        hour, minute = 2, 0
    hour = max(0, min(hour, 23))
    minute = max(0, min(minute, 59))
    candidate = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= moment:
        candidate += timedelta(days=1)
    if frequency == "weekly":
        try:
            weekdays = {
                int(value.strip())
                for value in str(schedule.get("weekdays") or "0").split(",")
                if value.strip()
            }
        except ValueError:
            weekdays = {0}
        weekdays = {day for day in weekdays if 0 <= day <= 6} or {0}
        while candidate.weekday() not in weekdays:
            candidate += timedelta(days=1)
    return candidate.timestamp()


__all__ = ["FALLBACK_OPTIONS", "build_filter_expression", "next_schedule_time", "parse_help_options"]
