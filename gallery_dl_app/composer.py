"""Unified gallery-dl download and configuration composer.

The official gallery-dl model is simple: configuration provides reusable
defaults and command-line options override those defaults for a single run.
This module mirrors that model in one UI instead of exposing separate command
and config builders with overlapping fields.
"""

from __future__ import annotations

import copy
import dis
import json
import re
import shutil
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

from PySide6.QtCore import QPointF, QProcess, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .core import (
    APP_DIR,
    IS_WINDOWS,
    MANAGED_AUTH_KEYS,
    atomic_write_text,
    command_string_to_argv,
    detect_config_path,
    is_sensitive_option_key,
    quote_arg_for_preview,
    read_text_safely,
    redact_oauth_output,
    redact_sensitive_argv,
    safe_bool,
    split_command,
    timestamp_slug,
    unique_path,
    validate_cookies_txt,
)


@dataclass
class ComposerState:
    """Values shared by command preview and persistent config generation."""

    urls: list[str] = field(default_factory=list)
    apply_to_config: bool = False
    use_active_config: bool = True
    destination: str = ""
    exact_destination: bool = False
    directory: list[str] = field(default_factory=list)
    filename: str = ""
    cookies_browser: str = "none"
    cookies_profile: str = ""
    cookies_domain: str = ""
    cookies_file: str = ""
    cookies_update: bool = True
    auth_category: str = ""
    username: str = ""
    secret_key: str = ""
    secret_value: str = ""
    extra_auth_key: str = ""
    extra_auth_value: str = ""
    archive_enabled: bool = False
    archive_path: str = ""
    retries: int = 0
    timeout: int = 0
    sleep_request: str = ""
    sleep_429: str = ""
    file_range: str = ""
    date_after: str = ""
    date_before: str = ""
    filter_expr: str = ""
    proxy: str = ""
    user_agent: str = ""
    mode: str = "download"
    metadata_json: bool = False
    info_json: bool = False
    archive_format: str = "none"
    windows_filenames: bool = False
    no_input: bool = True
    extra_args: str = ""


OAUTH_SITE_CHOICES = (
    "pixiv",
    "deviantart",
    "flickr",
    "reddit",
    "smugmug",
    "tumblr",
    "mastodon",
)


@dataclass(frozen=True)
class ConfigOptionSpec:
    """One user-editable gallery-dl extractor option."""

    key: str
    value_type: str
    default: object = None
    group: str = "Extractor-specific"
    description: str = "Extractor option discovered from the installed gallery-dl runtime."
    choices: tuple[object, ...] = ()
    sensitive: bool = False
    scope: str = "site"


_GENERAL_OPTION_DEFAULTS: dict[str, tuple[object, str, str]] = {
    "filename": ("{filename}.{extension}", "Paths", "Filename format string."),
    "directory": (["{category}"], "Paths", "Directory path segments as a JSON list."),
    "base-directory": ("./gallery-dl/", "Paths", "Root directory for downloaded files."),
    "path-restrict": ("auto", "Paths", "Characters to replace in generated path names."),
    "path-replace": ("_", "Paths", "Replacement text for restricted path characters."),
    "path-remove": ("\\u0000-\\u001f\\u007f", "Paths", "Characters removed from generated paths."),
    "path-strip": ("auto", "Paths", "Characters stripped from the end of path segments."),
    "path-convert": (None, "Paths", "Conversions applied to generated path segments."),
    "path-extended": (True, "Paths", "Use Windows extended-length paths."),
    "extension-map": ({}, "Paths", "Map file extensions to replacements."),
    "archive": (None, "Archive", "SQLite download archive path."),
    "archive-format": (None, "Archive", "Format string used as the archive item ID."),
    "archive-prefix": (None, "Archive", "Prefix prepended to archive IDs."),
    "archive-pragma": ([], "Archive", "SQLite PRAGMA statements as a JSON list."),
    "archive-event": (["file"], "Archive", "Events that write entries to the archive."),
    "archive-mode": ("file", "Archive", "Archive lookup/write mode."),
    "archive-table": (None, "Archive", "Custom SQLite table name."),
    "skip": (True, "Download", "Behavior for files already present or archived."),
    "skip-filter": (None, "Download", "Condition controlling which skipped files count."),
    "download": (True, "Download", "Enable or disable file downloads."),
    "fallback": (True, "Download", "Use fallback URLs when the primary URL fails."),
    "file-filter": (None, "Filters", "Condition applied to individual files."),
    "file-range": (None, "Filters", "Range applied to extracted files."),
    "file-unique": (False, "Filters", "Remove duplicate files."),
    "post-filter": (None, "Filters", "Condition applied to posts."),
    "post-range": (None, "Filters", "Range applied to posts."),
    "child-filter": (None, "Filters", "Condition applied to child extractors."),
    "child-range": (None, "Filters", "Range applied to child extractors."),
    "child-unique": (False, "Filters", "Remove duplicate child extractor targets."),
    "date-before": (None, "Filters", "Ignore entries newer than this date."),
    "date-after": (None, "Filters", "Ignore entries older than this date."),
    "blacklist": (None, "Filters", "Blocked child extractor categories."),
    "whitelist": (None, "Filters", "Allowed child extractor categories."),
    "tags-blacklist": (None, "Filters", "Blocked metadata tags."),
    "tags-whitelist": (None, "Filters", "Allowed metadata tags."),
    "user-agent": ("auto", "Network", "HTTP User-Agent header."),
    "referer": (True, "Network", "Send extractor-derived Referer headers."),
    "headers": ({}, "Network", "Additional HTTP headers as a JSON object."),
    "browser": (None, "Network", "Browser/TLS impersonation preset."),
    "proxy": (None, "Network", "HTTP/SOCKS proxy URL."),
    "proxy-env": (True, "Network", "Read proxy settings from environment variables."),
    "retries": (4, "Network", "Maximum retry count for failed requests."),
    "retry-codes": ([], "Network", "Additional HTTP status codes to retry."),
    "timeout": (30.0, "Network", "Request timeout in seconds."),
    "verify": (True, "Network", "Verify TLS certificates."),
    "truststore": (False, "Network", "Use the operating-system certificate store."),
    "sleep": (0, "Rate limits", "Delay before each download."),
    "sleep-skip": (0, "Rate limits", "Delay after skipping a file."),
    "sleep-request": (0, "Rate limits", "Minimum interval between HTTP requests."),
    "sleep-extractor": (0, "Rate limits", "Delay before starting an extractor."),
    "sleep-retries": ("lin=1", "Rate limits", "Delay/backoff before retrying requests."),
    "sleep-429": (60.0, "Rate limits", "Delay/backoff after HTTP 429."),
    "parent": (False, "Hierarchy", "Mark the extractor as a parent."),
    "parent-directory": (False, "Hierarchy", "Use the parent directory for child extractors."),
    "parent-metadata": (False, "Hierarchy", "Forward parent metadata to child extractors."),
    "parent-session": (False, "Hierarchy", "Share the HTTP session with child extractors."),
    "parent-skip": (False, "Hierarchy", "Share skip counts with parent extractors."),
    "keywords": ({}, "Metadata", "Custom metadata values as a JSON object."),
    "keywords-default": (None, "Metadata", "Fallback for missing metadata fields."),
    "metadata-extractor": ("_extr", "Metadata", "Field name for extractor metadata."),
    "metadata-parent": ("_parent", "Metadata", "Field name for parent metadata."),
    "metadata-path": ("_path", "Metadata", "Field name for path metadata."),
    "metadata-url": ("_url", "Metadata", "Field name for URL metadata."),
    "postprocessors": (None, "Post-processing", "Postprocessor definitions as JSON."),
    "cookies": (None, "Authentication", "Cookie file, browser source, or cookie object."),
    "cookies-update": (True, "Authentication", "Write refreshed cookies back to their source."),
    "username": (None, "Authentication", "Login username."),
    "password": (None, "Authentication", "Login password or API key for supported sites."),
    "netrc": (False, "Authentication", "Enable .netrc authentication."),
    "input": (True, "Behavior", "Allow interactive input prompts."),
    "init": ("lazy", "Behavior", "When extractor internals are initialized."),
}


_SITE_OPTION_DEFAULTS: dict[str, dict[str, tuple[object, str, str, tuple[object, ...]]]] = {
    "pixiv": {
        "captions": (False, "Content", "Download artwork captions.", (True, False)),
        "comments": (False, "Content", "Fetch comments.", (True, False)),
        "include": (["artworks"], "Content", "Related profile sections to include.", ()),
        "max-posts": (None, "Limits", "Maximum number of posts.", ()),
        "metadata": (False, "Metadata", "Fetch extended metadata.", (True, False)),
        "metadata-bookmark": (False, "Metadata", "Fetch bookmark metadata.", (True, False)),
        "sanity": (True, "Behavior", "Enable Pixiv sanity checks.", (True, False)),
        "tags": ("japanese", "Metadata", "Tag translation/language mode.", ("japanese", "translated")),
        "ugoira": (True, "Content", "Download Ugoira or original frame archive.", (True, False, "original")),
        "covers": (False, "Novels", "Download novel covers.", (True, False)),
        "embeds": (False, "Novels", "Download embedded novel media.", (True, False)),
        "full-series": (False, "Novels", "Download full novel series.", (True, False)),
        "refresh-token": (None, "Authentication", "Pixiv OAuth refresh token.", ()),
    },
    "reddit": {
        "api": ("auto", "API", "Choose auto, OAuth, or REST endpoints.", ("auto", "oauth", "rest")),
        "comments": (0, "Content", "Approximate number of comments to fetch.", ()),
        "morecomments": (False, "Content", "Resolve additional comment stubs.", (True, False)),
        "embeds": (True, "Content", "Download media embedded in comments.", (True, False)),
        "date-min": (0, "Filters", "Minimum submission date/timestamp.", ()),
        "date-max": (253402210800, "Filters", "Maximum submission date/timestamp.", ()),
        "id-min": (None, "Filters", "Minimum Reddit submission ID.", ()),
        "id-max": (None, "Filters", "Maximum Reddit submission ID.", ()),
        "limit": (None, "Limits", "Results per API query.", ()),
        "pinned": (True, "Content", "Process pinned submissions.", (True, False)),
        "previews": (True, "Content", "Use Reddit previews when child downloads fail.", (True, False)),
        "recursion": (0, "Hierarchy", "Maximum recursive submission depth.", ()),
        "selftext": (None, "Content", "Follow links in submission selftext.", (True, False, None)),
        "videos": ("dash", "Content", "Video handling mode.", (True, False, "ytdl", "dash")),
        "client-id": (None, "Authentication", "Reddit installed-app client ID.", ()),
        "user-agent-oauth": (None, "Authentication", "OAuth API User-Agent identity.", ()),
        "refresh-token": (None, "Authentication", "Reddit OAuth refresh token.", ()),
    },
    "kemono": {
        "files": (None, "Content", "File types/generators to download.", ()),
        "duplicates": (False, "Content", "Allow duplicate file hashes by type.", (True, False)),
        "revisions": (False, "Content", "Download post revisions.", (True, False, "unique")),
        "order-revisions": ("desc", "Ordering", "Revision order.", ("asc", "desc")),
        "order-posts": (None, "Ordering", "Post order.", ("asc", "desc")),
        "comments": (False, "Content", "Fetch comments.", (True, False)),
        "dms": (False, "Content", "Fetch direct messages where supported.", (True, False)),
        "announcements": (False, "Content", "Fetch creator announcements.", (True, False)),
        "archives": (False, "Content", "Fetch archive-file metadata.", (True, False)),
        "archives-format": ("list", "Content", "Archive metadata container format.", ("list", "dict", "object")),
        "metadata": (True, "Metadata", "Fetch creator profile metadata.", (True, False)),
        "original": (False, "Content", "Prefer original files over thumbnails.", (True, False)),
        "max-posts": (None, "Limits", "Maximum number of posts.", ()),
    },
    "coomer": {},
    "deviantart": {
        "include": ("gallery", "Content", "Profile sections to include.", ()),
        "folders": (False, "Content", "Process gallery folders.", (True, False)),
        "journals": ("html", "Content", "Journal output mode.", (False, "html", "text")),
        "mature": (True, "Content", "Include mature content when authorized.", (True, False)),
        "original": (False, "Content", "Prefer original files.", (True, False)),
        "quality": (100, "Content", "Image quality percentage.", ()),
        "client-id": (None, "Authentication", "DeviantArt API client ID.", ()),
        "client-secret": (None, "Authentication", "DeviantArt API client secret.", ()),
        "refresh-token": (None, "Authentication", "OAuth refresh token.", ()),
    },
}
_SITE_OPTION_DEFAULTS["coomer"] = _SITE_OPTION_DEFAULTS["kemono"]


def _option_value_type(key: str, default: object) -> str:
    if isinstance(default, bool):
        return "boolean"
    if isinstance(default, int):
        return "integer"
    if isinstance(default, float):
        return "number"
    if isinstance(default, (list, dict)):
        return "json"
    if key in {"include", "files", "formats", "format", "retry-codes", "postprocessors"}:
        return "json"
    return "text"


@lru_cache(maxsize=512)
def _runtime_site_options(category: str) -> tuple[tuple[str, object], ...]:
    """Discover literal ``self.config('key', default)`` calls from bytecode."""
    wanted = "kemono" if category == "coomer" else category
    options: dict[str, object] = {}
    try:
        from gallery_dl import extractor

        classes = [item for item in extractor.extractors() if item.category == wanted]
        for cls in classes:
            for base in cls.__mro__:
                if not str(getattr(base, "__module__", "")).startswith("gallery_dl.extractor"):
                    continue
                for member in vars(base).values():
                    code = getattr(member, "__code__", None)
                    if code is None:
                        continue
                    awaiting_key = False
                    current_key: str | None = None
                    current_default: object = None
                    for instruction in dis.get_instructions(code):
                        if instruction.opname in {"LOAD_METHOD", "LOAD_ATTR"} and instruction.argval == "config":
                            awaiting_key = True
                            current_key = None
                            current_default = None
                        elif awaiting_key and instruction.opname == "LOAD_CONST" and isinstance(instruction.argval, str):
                            current_key = instruction.argval
                            awaiting_key = False
                        elif current_key and instruction.opname == "LOAD_CONST" and isinstance(
                            instruction.argval, (type(None), bool, int, float, str)
                        ):
                            current_default = instruction.argval
                        elif current_key and instruction.opname.startswith("CALL"):
                            options.setdefault(current_key, current_default)
                            current_key = None
                            current_default = None
                        elif awaiting_key and instruction.opname.startswith("CALL"):
                            awaiting_key = False
    except Exception:
        pass
    return tuple(sorted(options.items()))


def config_option_definitions(category: str = "") -> tuple[ConfigOptionSpec, ...]:
    """Return general options plus details for one installed extractor."""
    specs: dict[str, ConfigOptionSpec] = {}
    for key, (default, group, description) in _GENERAL_OPTION_DEFAULTS.items():
        specs[key] = ConfigOptionSpec(
            key=key,
            value_type=_option_value_type(key, default),
            default=default,
            group=group,
            description=description,
            sensitive=is_sensitive_option_key(key),
            scope="general",
        )
    site = str(category or "").strip().lower()
    if site:
        for key, values in _SITE_OPTION_DEFAULTS.get(site, {}).items():
            default, group, description, choices = values
            specs[key] = ConfigOptionSpec(
                key=key,
                value_type=_option_value_type(key, default),
                default=default,
                group=group,
                description=description,
                choices=choices,
                sensitive=is_sensitive_option_key(key),
                scope="site",
            )
        for key, default in _runtime_site_options(site):
            if key and key not in specs and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", key):
                specs[key] = ConfigOptionSpec(
                    key=key,
                    value_type=_option_value_type(key, default),
                    default=default,
                    sensitive=is_sensitive_option_key(key),
                )
    return tuple(sorted(specs.values(), key=lambda item: (item.group, item.key)))


def composer_oauth_target(site: str, instance: str = "") -> str:
    """Build a typo-proof OAuth target from guided Composer controls."""
    category = str(site or "").strip().lower()
    if category not in OAUTH_SITE_CHOICES:
        raise ValueError("Choose a supported OAuth site from the list")
    if category != "mastodon":
        return f"oauth:{category}"
    host = str(instance or "").strip()
    if not host:
        raise ValueError("Enter the Mastodon instance hostname or URL")
    if any(char.isspace() for char in host):
        raise ValueError("Enter a valid Mastodon instance hostname or URL")
    is_url = "://" in host
    candidate = urlsplit(host if is_url else f"//{host}")
    try:
        port = candidate.port
    except ValueError as exc:
        raise ValueError("Enter a valid Mastodon instance hostname or URL") from exc
    hostname = candidate.hostname or ""
    if (
        (is_url and candidate.scheme.lower() not in {"http", "https"})
        or candidate.username is not None
        or candidate.password is not None
        or candidate.query
        or candidate.fragment
        or (is_url and candidate.path not in {"", "/"})
        or (not is_url and (candidate.path or candidate.scheme))
        or not hostname
        or port is not None and not 1 <= port <= 65535
    ):
        raise ValueError("Enter a valid Mastodon instance hostname or URL")
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Enter a valid Mastodon instance hostname or URL") from exc
    labels = ascii_hostname.rstrip(".").split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not re.fullmatch(r"[A-Za-z0-9-]+", label)
        for label in labels
    ):
        raise ValueError("Enter a valid Mastodon instance hostname or URL")
    return f"oauth:mastodon:{host}"


def apply_config_editor_drafts(
    data: dict,
    *,
    general_overrides: dict[str, object] | None = None,
    site_overrides: dict[str, dict[str, object]] | None = None,
    general_removals: set[str] | tuple[str, ...] = (),
    site_removals: dict[str, set[str] | tuple[str, ...]] | None = None,
) -> dict:
    """Apply GUI config edits while preserving unrelated config sections."""
    result = copy.deepcopy(data) if isinstance(data, dict) else {}
    extractor = result.get("extractor")
    if not isinstance(extractor, dict):
        extractor = {}
        result["extractor"] = extractor
    for key in general_removals:
        extractor.pop(str(key), None)
    for key, value in (general_overrides or {}).items():
        extractor[str(key)] = copy.deepcopy(value)
    removals = site_removals or {}
    categories = set(site_overrides or {}) | set(removals)
    for category in categories:
        current = extractor.get(category)
        block = copy.deepcopy(current) if isinstance(current, dict) else {}
        for key in removals.get(category, ()):
            block.pop(str(key), None)
        block = _merge_dict(block, (site_overrides or {}).get(category, {}))
        if block:
            extractor[category] = block
        else:
            extractor.pop(category, None)
    return result


def _add_value(parts: list[str], flag: str, value: str) -> None:
    value = str(value or "").strip()
    if value:
        parts.extend([flag, value])


def site_archive_options(
    path: str,
    *,
    duplicates: bool = False,
    archive_format: str = "",
) -> dict[str, object]:
    """Build a per-extractor archive block from guided GUI values."""
    archive = str(path or "").strip()
    if not archive:
        raise ValueError("Choose an archive SQLite database path")
    result: dict[str, object] = {
        "archive": archive,
        "duplicates": bool(duplicates),
    }
    identifier = str(archive_format or "").strip().replace(r"\_", "_")
    if identifier:
        result["archive-format"] = identifier
    return result


def reddit_site_options(client_id: str, user_agent: str) -> dict[str, object]:
    """Build the current gallery-dl Reddit OAuth application settings."""
    result: dict[str, object] = {}
    if value := str(client_id or "").strip():
        result["client-id"] = value
    if value := str(user_agent or "").strip():
        result["user-agent-oauth"] = value
    return result


def pixiv_site_options(
    *,
    include: Iterable[str] = (),
    embeds: bool = False,
    covers: bool = False,
    full_series: bool = False,
    metadata: bool = False,
    ugoira: bool | str = True,
) -> dict[str, object]:
    """Build guided Pixiv and Pixiv-novel settings accepted by gallery-dl."""
    allowed = {
        "artworks",
        "avatar",
        "background",
        "favorite",
        "novel-user",
        "novel-bookmark",
        "sketch",
    }
    selected: list[str] = []
    for raw in include:
        value = str(raw or "").strip().lower()
        if value and value not in selected:
            if value not in allowed:
                raise ValueError(f"Unsupported Pixiv include value: {value}")
            selected.append(value)
    if isinstance(ugoira, str):
        normalized_ugoira: bool | str
        lowered = ugoira.strip().lower()
        if lowered in {"true", "yes", "on"}:
            normalized_ugoira = True
        elif lowered in {"false", "no", "off"}:
            normalized_ugoira = False
        elif lowered == "original":
            normalized_ugoira = "original"
        else:
            raise ValueError("Ugoira must be enabled, disabled, or original")
    else:
        normalized_ugoira = bool(ugoira)
    result: dict[str, object] = {
        "embeds": bool(embeds),
        "covers": bool(covers),
        "full-series": bool(full_series),
        "metadata": bool(metadata),
        "ugoira": normalized_ugoira,
    }
    result["include"] = selected
    return result


def parse_typed_config_value(text: str, value_type: str) -> object:
    """Convert one advanced GUI field without executing user-provided text."""
    kind = str(value_type or "text").strip().lower()
    value = str(text or "")
    if kind == "text":
        return value
    if kind == "boolean":
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        raise ValueError("Boolean value must be true or false")
    if kind == "integer":
        try:
            return int(value.strip())
        except ValueError as exc:
            raise ValueError("Enter a valid integer") from exc
    if kind == "number":
        try:
            return float(value.strip())
        except ValueError as exc:
            raise ValueError("Enter a valid number") from exc
    if kind == "null":
        return None
    if kind == "json":
        try:
            return json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Enter valid JSON for a list or object") from exc
    raise ValueError(f"Unsupported value type: {value_type}")


def build_composer_argv(
    state: ComposerState,
    url: str,
    *,
    config_path: str | None = None,
) -> list[str]:
    """Build one safe argv list.

    Shared settings become CLI overrides only in ``job`` scope. In ``config``
    scope they are intentionally omitted so the saved config remains the
    single source of truth.
    """

    parts = ["gallery-dl"]
    config_exists = bool(config_path and Path(config_path).expanduser().is_file())
    if state.use_active_config and config_exists:
        parts.extend(["--config", str(config_path)])
    elif not state.use_active_config:
        parts.append("--config-ignore")

    if not state.apply_to_config:
        if state.destination:
            parts.extend(["-D" if state.exact_destination else "-d", state.destination])
        if state.directory and not state.exact_destination:
            directory_json = json.dumps(state.directory, ensure_ascii=False, separators=(",", ":"))
            parts.extend(["-o", f"directory={directory_json}"])
        _add_value(parts, "-f", state.filename)
        if state.cookies_file:
            parts.extend(["-C", state.cookies_file])
        elif state.cookies_browser and state.cookies_browser != "none":
            browser_spec = state.cookies_browser
            if state.cookies_domain:
                browser_spec += "/" + state.cookies_domain
            if state.cookies_profile:
                browser_spec += ":" + state.cookies_profile
            parts.extend(["--cookies-from-browser", browser_spec])
        if state.archive_enabled:
            _add_value(parts, "--download-archive", state.archive_path)
        if state.retries > 0:
            parts.extend(["-R", str(state.retries)])
        if state.timeout > 0:
            parts.extend(["--http-timeout", str(state.timeout)])
        _add_value(parts, "--sleep-request", state.sleep_request)
        if state.sleep_429:
            # Config override form also works on gallery-dl releases from
            # before the dedicated --sleep-429 CLI flag was added.
            parts.extend(["-o", f"sleep-429={state.sleep_429}"])
        _add_value(parts, "--proxy", state.proxy)
        # Keep the descriptive long form in generated previews. The minimum
        # supported gallery-dl also accepts ``-a`` as an alias.
        _add_value(parts, "--user-agent", state.user_agent)
        if state.windows_filenames:
            parts.extend(["-o", "path-restrict=windows", "-o", "path-strip=windows"])

    _add_value(parts, "--range", state.file_range)
    # gallery-dl has no --date-after/--date-before CLI flags. Date bounds are
    # extractor configuration keys (supported by extractors such as Reddit,
    # Tumblr, and Misskey), so one-job overrides must use the stable -o form.
    if state.date_after:
        parts.extend(["-o", f"date-min={state.date_after}"])
    if state.date_before:
        parts.extend(["-o", f"date-max={state.date_before}"])
    _add_value(parts, "--filter", state.filter_expr)

    mode_flags = {
        "simulate": "-s",
        "urls": "-g",
        "json": "-j",
        "keywords": "-K",
        "extractor": "-E",
    }
    if state.mode in mode_flags:
        parts.append(mode_flags[state.mode])
    if state.metadata_json:
        parts.append("--write-metadata")
    if state.info_json:
        parts.append("--write-info-json")
    if state.archive_format == "zip":
        parts.append("--zip")
    elif state.archive_format == "cbz":
        parts.append("--cbz")
    if state.no_input:
        parts.append("--no-input")
    if state.extra_args.strip():
        parts.extend(split_command(state.extra_args.strip()))
    if url.strip():
        parts.append(url.strip())
    return parts


def _merge_dict(base: dict, update: dict) -> dict:
    """Recursively merge config sections without discarding unknown options."""

    result = copy.deepcopy(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def redact_auth_config(data: object, *, extra_keys: Iterable[str] = ()) -> object:
    """Return a preview-safe copy with credentials and tokens hidden."""

    explicit = {str(key).strip().lower() for key in extra_keys if str(key).strip()}
    if isinstance(data, dict):
        redacted: dict[str, object] = {}
        for key, value in data.items():
            normalized = str(key).strip().lower()
            if normalized == "cookies" and isinstance(value, dict):
                redacted[str(key)] = "<hidden cookie values>"
            elif normalized in explicit or is_sensitive_option_key(normalized):
                redacted[str(key)] = "<hidden>"
            else:
                redacted[str(key)] = redact_auth_config(value, extra_keys=explicit)
        return redacted
    if isinstance(data, list):
        return [redact_auth_config(value, extra_keys=explicit) for value in data]
    return copy.deepcopy(data)


def build_composer_config(
    state: ComposerState,
    *,
    site_blocks: dict[str, dict] | None = None,
    existing: dict | None = None,
) -> dict:
    """Create a valid gallery-dl config while preserving unmanaged sections."""

    existing_extractor = (existing or {}).get("extractor", {}) if isinstance(existing, dict) else {}
    if not isinstance(existing_extractor, dict):
        existing_extractor = {}

    # Start with the user's extractor config, then clear only settings owned by
    # Composer.  Building a sparse update and recursively merging it cannot
    # represent deletion: clearing Destination or unchecking Archive/Metadata
    # would silently leave the old value active in the saved file.
    extractor: dict[str, object] = copy.deepcopy(existing_extractor)
    managed_keys = {
        "base-directory",
        "directory",
        "filename",
        "archive",
        "archive-pragma",
        "retries",
        "timeout",
        "sleep-request",
        "sleep-429",
        "proxy",
        "user-agent",
        "path-restrict",
        "path-replace",
        "path-strip",
    }
    for key in managed_keys:
        extractor.pop(key, None)

    if state.destination:
        extractor["base-directory"] = state.destination
    if state.exact_destination:
        extractor["directory"] = []
    elif state.directory:
        extractor["directory"] = list(state.directory)
    if state.filename:
        extractor["filename"] = state.filename
    auth_options: dict[str, object] = {}
    if state.cookies_file:
        auth_options["cookies"] = state.cookies_file
        if state.cookies_update:
            auth_options["cookies-update"] = True
    elif state.cookies_browser and state.cookies_browser != "none":
        browser_source: list[object | None] = [state.cookies_browser]
        if state.cookies_profile or state.cookies_domain:
            browser_source.append(state.cookies_profile or None)
        if state.cookies_domain:
            browser_source.extend([None, None, state.cookies_domain])
        auth_options["cookies"] = browser_source
    if state.username:
        auth_options["username"] = state.username
    if state.secret_key and state.secret_value:
        auth_options[state.secret_key] = state.secret_value
    if state.extra_auth_key and state.extra_auth_value:
        auth_options[state.extra_auth_key] = state.extra_auth_value
    auth_category = state.auth_category.strip().lower()
    if auth_options:
        # The UI presents login methods as mutually exclusive. Clear only the
        # authentication keys managed by Composer in this exact scope before
        # applying the new method, while retaining unrelated site settings.
        auth_keys = set(MANAGED_AUTH_KEYS)
        auth_keys.update(
            key for key in (state.secret_key, state.extra_auth_key) if key
        )
        if auth_category:
            current_auth = extractor.get(auth_category, {})
            clean_auth = copy.deepcopy(current_auth) if isinstance(current_auth, dict) else {}
            for key in list(clean_auth):
                if key in auth_keys or is_sensitive_option_key(key):
                    clean_auth.pop(key, None)
            extractor[auth_category] = _merge_dict(clean_auth, auth_options)
        else:
            for key in list(extractor):
                if key in auth_keys or is_sensitive_option_key(key):
                    extractor.pop(key, None)
            extractor.update(auth_options)
    if state.archive_enabled and state.archive_path:
        extractor["archive"] = state.archive_path
        extractor["archive-pragma"] = ["journal_mode=WAL", "synchronous=NORMAL"]
    if state.retries > 0:
        extractor["retries"] = state.retries
    if state.timeout > 0:
        extractor["timeout"] = state.timeout
    if state.sleep_request:
        extractor["sleep-request"] = state.sleep_request
    if state.sleep_429:
        extractor["sleep-429"] = state.sleep_429
    if state.proxy:
        extractor["proxy"] = state.proxy
    if state.user_agent:
        extractor["user-agent"] = state.user_agent
    if state.windows_filenames:
        extractor["path-restrict"] = "windows"
        extractor["path-replace"] = "_"
        extractor["path-strip"] = "windows"
    existing_pp = existing_extractor.get("postprocessors", []) if isinstance(existing_extractor, dict) else []
    # Metadata/zip entries are represented by Composer checkboxes.  Remove the
    # old managed entries first so unchecking them actually removes them, while
    # preserving unrelated user postprocessors such as exec/classify.
    postprocessors = [
        copy.deepcopy(item)
        for item in existing_pp
        if not (isinstance(item, dict) and item.get("name") in {"metadata", "zip"})
    ] if isinstance(existing_pp, list) else []
    if state.metadata_json and not any(
        isinstance(item, dict) and item.get("name") == "metadata" and item.get("filename") != "info.json"
        for item in postprocessors
    ):
        postprocessors.append({"name": "metadata"})
    if state.info_json and not any(
        isinstance(item, dict) and item.get("name") == "metadata" and item.get("filename") == "info.json"
        for item in postprocessors
    ):
        postprocessors.append({"name": "metadata", "event": "init", "filename": "info.json"})
    if state.archive_format in {"zip", "cbz"} and not any(
        isinstance(item, dict) and item.get("name") == "zip"
        for item in postprocessors
    ):
        archive_pp: dict[str, object] = {"name": "zip"}
        if state.archive_format == "cbz":
            archive_pp["extension"] = "cbz"
        postprocessors.append(archive_pp)
    if postprocessors:
        extractor["postprocessors"] = postprocessors
    else:
        extractor.pop("postprocessors", None)
    for key, block in (site_blocks or {}).items():
        current = extractor.get(key)
        extractor[key] = _merge_dict(current, block) if isinstance(current, dict) else copy.deepcopy(block)

    result = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    result["extractor"] = extractor
    return result


def config_defaults(data: dict) -> dict[str, object]:
    """Extract fields managed by the Composer from an existing config."""

    extractor = data.get("extractor", {}) if isinstance(data, dict) else {}
    if not isinstance(extractor, dict):
        extractor = {}
    cookies = extractor.get("cookies")
    browser = "none"
    cookie_file = ""
    if isinstance(cookies, list) and cookies:
        browser = str(cookies[0]).lower()
        browser_profile = str(cookies[1]) if len(cookies) > 1 and cookies[1] else ""
        browser_domain = str(cookies[4]) if len(cookies) > 4 and cookies[4] else ""
    elif isinstance(cookies, str):
        cookie_file = cookies
        browser_profile = ""
        browser_domain = ""
    else:
        browser_profile = ""
        browser_domain = ""
    directory = extractor.get("directory")
    exact_destination = "directory" in extractor and isinstance(directory, list) and not directory
    if isinstance(directory, str):
        directory = [directory]
    if not isinstance(directory, list):
        directory = []
    postprocessors = extractor.get("postprocessors", [])
    if not isinstance(postprocessors, list):
        postprocessors = []
    metadata_json = any(
        isinstance(item, dict) and item.get("name") == "metadata" and item.get("filename") != "info.json"
        for item in postprocessors
    )
    info_json = any(
        isinstance(item, dict) and item.get("name") == "metadata" and item.get("filename") == "info.json"
        for item in postprocessors
    )
    archive_format = "none"
    for item in postprocessors:
        if isinstance(item, dict) and item.get("name") == "zip":
            archive_format = "cbz" if item.get("extension") == "cbz" else "zip"
            break
    return {
        "destination": extractor.get("base-directory", ""),
        "exact_destination": exact_destination,
        "directory": [str(item) for item in directory],
        "filename": extractor.get("filename", ""),
        "cookies_browser": browser,
        "cookies_profile": browser_profile,
        "cookies_domain": browser_domain,
        "cookies_file": cookie_file,
        "cookies_update": safe_bool(extractor.get("cookies-update"), True),
        "archive_enabled": bool(extractor.get("archive")),
        "archive_path": extractor.get("archive", ""),
        "retries": extractor.get("retries", 0),
        "timeout": extractor.get("timeout", 0),
        "sleep_request": extractor.get("sleep-request", ""),
        "sleep_429": extractor.get("sleep-429", ""),
        "proxy": extractor.get("proxy", ""),
        "user_agent": extractor.get("user-agent", ""),
        "windows_filenames": extractor.get("path-restrict") == "windows",
        "metadata_json": metadata_json,
        "info_json": info_json,
        "archive_format": archive_format,
    }


def _read_json_config(path: str | None) -> tuple[dict, str | None]:
    if not path:
        return {}, None
    config = Path(path).expanduser()
    if not config.exists():
        return {}, None
    if not config.is_file():
        return {}, "Config path is not a file."
    try:
        data = json.loads(read_text_safely(config))
        if not isinstance(data, dict):
            return {}, "Config root must be a JSON object."
        return data, None
    except Exception as exc:
        return {}, str(exc)


class ComposerFlowchart(QWidget):
    """Compact, theme-aware overview of the Composer workflow."""

    def __init__(self, *, indonesian: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.indonesian = indonesian
        self.setMinimumHeight(310)
        self.setAccessibleName(
            "Alur penggunaan Download Composer" if indonesian else "Download Composer usage flow"
        )
        self.setToolTip(
            "Ikuti alur dari kiri ke kanan. Pilihan scope bercabang lalu bergabung kembali di Preview."
            if indonesian else
            "Follow the flow from left to right. The scope choice branches and joins again at Preview."
        )

    @staticmethod
    def _arrow(painter: QPainter, start: QPointF, end: QPointF, color: QColor) -> None:
        pen = QPen(color, 2)
        painter.setPen(pen)
        painter.drawLine(start, end)
        delta = start - end
        length = max(1.0, (delta.x() ** 2 + delta.y() ** 2) ** 0.5)
        unit = QPointF(delta.x() / length, delta.y() / length)
        normal = QPointF(-unit.y(), unit.x())
        tip = end
        left = end + unit * 10 + normal * 5
        right = end + unit * 10 - normal * 5
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([tip, left, right]))

    @staticmethod
    def _node(
        painter: QPainter,
        rect: QRectF,
        step: str,
        title: str,
        detail: str,
        *,
        fill: QColor,
        border: QColor,
        text: QColor,
        muted: QColor,
    ) -> None:
        painter.setPen(QPen(border, 1.5))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 10, 10)
        painter.setPen(text)
        title_font = painter.font()
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(rect.adjusted(10, 7, -10, -27), Qt.AlignLeft | Qt.AlignTop, f"{step}  {title}")
        body_font = painter.font()
        body_font.setBold(False)
        painter.setFont(body_font)
        painter.setPen(muted)
        painter.drawText(rect.adjusted(10, 28, -10, -6), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, detail)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        dialog = self.window()
        owner = dialog.parentWidget() if dialog else None
        dark = getattr(owner, "current_theme", "dark") != "light"
        if dark:
            neutral = QColor("#151b23")
            branch = QColor("#102a35")
            text = QColor("#eaf1ff")
            muted = QColor("#9aa7ba")
            border = QColor("#354153")
            accent = QColor("#35c8d0")
        else:
            neutral = QColor("#ffffff")
            branch = QColor("#e6f7f6")
            text = QColor("#172033")
            muted = QColor("#5f6f84")
            border = QColor("#cbd9e7")
            accent = QColor("#087f7b")

        margin = 12.0
        width = max(700.0, float(self.width()) - margin * 2)
        x0 = margin
        top_y = 18.0
        node_w = min(205.0, (width - 54.0) / 4.0)
        node_h = 68.0
        gap = (width - node_w * 4) / 3.0
        xs = [x0 + index * (node_w + gap) for index in range(4)]
        url_box = QRectF(xs[0], top_y, node_w, node_h)
        scope_box = QRectF(xs[1], top_y, node_w, node_h)
        preview_box = QRectF(xs[2], top_y + 94, node_w, node_h)
        queue_box = QRectF(xs[3], top_y + 94, node_w, node_h)
        job_box = QRectF(xs[2], top_y, node_w, node_h)
        config_box = QRectF(xs[2], top_y + 188, node_w, node_h)
        download_box = QRectF(xs[3], top_y + 188, node_w, node_h)

        self._arrow(painter, url_box.topRight() + QPointF(0, node_h / 2), scope_box.topLeft() + QPointF(0, node_h / 2), accent)
        self._arrow(painter, scope_box.topRight() + QPointF(0, node_h / 2), job_box.topLeft() + QPointF(0, node_h / 2), accent)
        self._arrow(painter, scope_box.bottomRight(), config_box.topLeft(), accent)
        self._arrow(painter, job_box.bottomLeft() + QPointF(node_w / 2, 0), preview_box.topLeft() + QPointF(node_w / 2, 0), accent)
        self._arrow(painter, config_box.topLeft() + QPointF(node_w / 2, 0), preview_box.bottomLeft() + QPointF(node_w / 2, 0), accent)
        self._arrow(painter, preview_box.topRight() + QPointF(0, node_h / 2), queue_box.topLeft() + QPointF(0, node_h / 2), accent)
        self._arrow(painter, queue_box.bottomLeft() + QPointF(node_w / 2, 0), download_box.topLeft() + QPointF(node_w / 2, 0), accent)

        if self.indonesian:
            labels = [
                (url_box, "1", "Masukkan URL", "Satu URL per baris."),
                (scope_box, "2", "Pilih scope", "Sekali pakai atau default."),
                (job_box, "A", "Override job", "Nilai masuk ke command."),
                (config_box, "B", "Default tersimpan", "Simpan config + backup."),
                (preview_box, "3", "Periksa Preview", "Command dan JSON final."),
                (queue_box, "4", "Tambah antrean", "Kirim job ke input utama."),
                (download_box, "5", "Mulai download", "Tekan Ctrl+Enter."),
            ]
        else:
            labels = [
                (url_box, "1", "Add URLs", "One URL per line."),
                (scope_box, "2", "Choose scope", "One job or saved defaults."),
                (job_box, "A", "Job override", "Values go into the command."),
                (config_box, "B", "Saved defaults", "Save config + backup."),
                (preview_box, "3", "Check Preview", "Final command and JSON."),
                (queue_box, "4", "Add to queue", "Send jobs to main input."),
                (download_box, "5", "Start download", "Press Ctrl+Enter."),
            ]
        for rect, step, title, detail in labels:
            is_branch = rect in (job_box, config_box)
            self._node(
                painter,
                rect,
                step,
                title,
                detail,
                fill=branch if is_branch else neutral,
                border=accent if is_branch else border,
                text=text,
                muted=muted,
            )

        painter.setPen(muted)
        legend = (
            "Opsi, Login & Cookies, dan preset situs bersifat opsional; gunakan hanya jika diperlukan."
            if self.indonesian else
            "Options, Login & Cookies, and site presets are optional; use them only when needed."
        )
        painter.drawText(QRectF(margin, self.height() - 30, width, 24), Qt.AlignCenter, legend)


class ComposerMixin:
    """Single entry point for creating jobs and maintaining defaults."""

    def open_command_builder(self) -> None:
        self.open_download_composer()

    def open_config_guide_dialog(self) -> None:
        self.open_download_composer()

    def open_download_composer(self) -> None:  # noqa: C901 - UI composition is intentionally local
        if self.active_workers > 0:
            self.show_compact_message(
                "Download Composer",
                "Wait for the current download to finish before changing the queue or config.",
                "info",
            )
            return
        ind = self._ui_is_indonesian()

        def tr(en: str, id_text: str) -> str:
            return id_text if ind else en

        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Download Composer", "Perancang Download"))
        dlg.resize(980, 740)
        dlg.setMinimumSize(820, 620)
        root = QVBoxLayout(dlg)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(9)

        title_row = QHBoxLayout()
        title = QLabel(tr("Download Composer", "Perancang Download"))
        title.setObjectName("title")
        title_row.addWidget(title)
        title_row.addStretch(1)
        scope_badge = QLabel()
        scope_badge.setObjectName("statusPill")
        title_row.addWidget(scope_badge)
        root.addLayout(title_row)
        intro = QLabel(tr(
            "One workspace for a download job, reusable defaults, and site presets. Command options override saved defaults only when you choose Job override.",
            "Satu tempat untuk job download, default yang dapat dipakai ulang, dan preset situs. Opsi command hanya menimpa default saat memilih Override job.",
        ))
        intro.setObjectName("subtle")
        intro.setWordWrap(True)
        root.addWidget(intro)

        tabs = QTabWidget()
        root.addWidget(tabs, 1)
        definitions = self.gallery_dl_site_preset_definitions()
        definition_names = {
            str(item.get("key") or "").strip().lower(): str(item.get("name") or "").strip()
            for item in definitions
            if str(item.get("key") or "").strip()
        }
        runtime_categories: set[str] = set()
        try:
            from gallery_dl import extractor

            runtime_categories.update(
                str(item.category).strip().lower()
                for item in extractor.extractors()
                if str(getattr(item, "category", "")).strip()
            )
        except Exception:
            pass
        # Coomer changes category dynamically after URL matching, so it is not
        # represented by the class-level category returned above.
        site_catalog = sorted(runtime_categories | set(definition_names) | {"coomer"})
        gui_browser = self.combo_cookies.currentText().strip().lower()

        # Built-in guide --------------------------------------------------------------
        guide_scroll = QScrollArea()
        guide_scroll.setWidgetResizable(True)
        guide_scroll.setFrameShape(QFrame.NoFrame)
        guide_page = QWidget()
        guide_l = QVBoxLayout(guide_page)
        guide_l.setContentsMargins(8, 8, 8, 8)
        guide_l.setSpacing(10)
        guide_scroll.setWidget(guide_page)
        tabs.addTab(guide_scroll, tr("How to use", "Cara Pakai"))

        guide_heading = QLabel(tr("Start here", "Mulai dari sini"))
        guide_heading.setStyleSheet("font-size: 17px; font-weight: 800;")
        guide_l.addWidget(guide_heading)
        guide_summary = QLabel(tr(
            "Composer has one set of fields. You decide whether those values belong only to the current job or become reusable defaults. Follow the diagram, then use the detailed steps below.",
            "Composer hanya memiliki satu set field. Anda menentukan apakah nilainya hanya untuk job saat ini atau menjadi default yang dapat dipakai ulang. Ikuti diagram, lalu baca langkah detail di bawahnya.",
        ))
        guide_summary.setObjectName("subtle")
        guide_summary.setWordWrap(True)
        guide_l.addWidget(guide_summary)
        guide_l.addWidget(ComposerFlowchart(indonesian=ind))

        choice_card = QFrame()
        choice_card.setObjectName("card")
        choice_l = QGridLayout(choice_card)
        choice_l.setContentsMargins(10, 10, 10, 10)
        choice_l.setHorizontalSpacing(14)
        choice_l.setVerticalSpacing(7)
        choice_headers = [
            tr("YOUR GOAL", "TUJUAN ANDA"),
            tr("CHOOSE", "PILIH"),
            tr("RESULT", "HASIL"),
        ]
        for column, header in enumerate(choice_headers):
            label = QLabel(header)
            label.setObjectName("fieldLabel")
            choice_l.addWidget(label, 0, column)
        choice_rows = [
            (
                tr("Change folder/options for these URLs only", "Ubah folder/opsi hanya untuk URL ini"),
                tr("Job override", "Override job"),
                tr("Values are written into the generated command", "Nilai ditulis ke command yang dibuat"),
            ),
            (
                tr("Reuse the same settings in future downloads", "Pakai pengaturan yang sama untuk download berikutnya"),
                tr("Saved defaults", "Default tersimpan"),
                tr("Values are merged into the active config", "Nilai digabung ke config aktif"),
            ),
            (
                tr("Give Pixiv, Twitter, booru, etc. special patterns", "Beri Pixiv, Twitter, booru, dll. pola khusus"),
                tr("Sites tab", "Tab Situs"),
                tr("Extractor-specific blocks are added to that same config", "Blok khusus extractor ditambahkan ke config yang sama"),
            ),
            (
                tr("Access private, account-only, or age-gated content", "Akses konten privat, khusus akun, atau terbatas usia"),
                tr("Login & Cookies tab", "Tab Login & Cookies"),
                tr("Guided browser cookies, cookies.txt, credentials, or OAuth", "Cookies browser, cookies.txt, credential, atau OAuth terpandu"),
            ),
            (
                tr("Check what will happen without downloading", "Periksa hasil tanpa melakukan download"),
                tr("Preview tab", "Tab Pratinjau"),
                tr("Review the final command and merged JSON", "Periksa command final dan JSON gabungan"),
            ),
        ]
        for row, values in enumerate(choice_rows, start=1):
            for column, value in enumerate(values):
                label = QLabel(value)
                label.setWordWrap(True)
                if column == 1:
                    label.setObjectName("good")
                choice_l.addWidget(label, row, column)
        choice_l.setColumnStretch(0, 3)
        choice_l.setColumnStretch(1, 2)
        choice_l.setColumnStretch(2, 4)
        guide_l.addWidget(choice_card)

        steps_card = QFrame()
        steps_card.setObjectName("card")
        steps_l = QVBoxLayout(steps_card)
        steps_l.setContentsMargins(12, 10, 12, 10)
        steps_title = QLabel(tr("Detailed steps", "Langkah lengkap"))
        steps_title.setStyleSheet("font-weight: 800;")
        steps_l.addWidget(steps_title)
        steps_text = QLabel(tr(
            """
            <ol>
              <li><b>Open the Download tab.</b> Enter one supported URL per line. Multiple lines create multiple queue jobs with the same settings.</li>
              <li><b>Choose a recipe.</b> Basic changes nothing; Safe &amp; slow adds polite delays; Logged-in selects browser cookies; Archive duplicates enables the SQLite archive; Metadata writes JSON; Inspect keywords changes the job to <code>-K</code>.</li>
              <li><b>Choose the scope.</b> Use <i>Job override</i> for a temporary change. Use <i>Saved defaults</i> when folder, filename, login source, archive, retry, and delay should be reused.</li>
              <li><b>Set files and reliability.</b> Download folder is the base location. Subfolders create levels below it. Exact folder ignores the subfolder pattern. Archive skips known IDs; retry and delays reduce transient failures.</li>
              <li><b>Open Options only when needed.</b> Range limits file numbers; dates and Filter select content; Mode can simulate, print URLs/JSON, list keywords, or inspect an extractor; ZIP/CBZ and metadata are post-processing options.</li>
              <li><b>Use Login &amp; Cookies for private content.</b> Select the site and exactly one method: browser cookies, Netscape cookies.txt, username plus password/API key, or OAuth. Use Prepare safe test to switch to simulation. Credentials require Saved defaults and explicit plain-text storage confirmation.</li>
              <li><b>Open Sites only for site-specific behavior.</b> You do not need to select a site for normal downloads. A selected preset only adds an extractor-specific config block.</li>
              <li><b>Check Preview.</b> Final command shows what enters the queue. Merged config shows the active config plus proposed defaults and site blocks. Passwords, API keys, and tokens appear only as <code>&lt;hidden&gt;</code>.</li>
              <li><b>Finish the workflow.</b> With Saved defaults, click Save Defaults first (Composer will prompt if needed). Click Add to Queue, close Composer, then press DOWNLOAD or Ctrl+Enter in the main window.</li>
            </ol>
            """,
            """
            <ol>
              <li><b>Buka tab Download.</b> Masukkan satu URL yang didukung per baris. Beberapa baris menghasilkan beberapa job antrean dengan pengaturan yang sama.</li>
              <li><b>Pilih recipe.</b> Dasar tidak mengubah apa pun; Aman &amp; pelan menambah jeda; Dengan login memilih cookies browser; Arsip duplikat mengaktifkan archive SQLite; Metadata menulis JSON; Periksa keyword mengubah job menjadi <code>-K</code>.</li>
              <li><b>Pilih scope.</b> Gunakan <i>Override job</i> untuk perubahan sementara. Gunakan <i>Default tersimpan</i> jika folder, nama file, sumber login, archive, retry, dan jeda ingin dipakai ulang.</li>
              <li><b>Atur file dan ketahanan.</b> Folder download adalah lokasi dasar. Subfolder membuat tingkat di bawahnya. Folder persis mengabaikan pola subfolder. Archive melewati ID yang sudah dikenal; retry dan jeda mengurangi kegagalan sementara.</li>
              <li><b>Buka Opsi hanya bila perlu.</b> Rentang membatasi nomor file; tanggal dan Filter memilih konten; Mode dapat melakukan simulasi, mencetak URL/JSON, melihat keyword, atau memeriksa extractor; ZIP/CBZ dan metadata adalah post-processing.</li>
              <li><b>Gunakan Login &amp; Cookies untuk konten privat.</b> Pilih situs dan tepat satu metode: cookies browser, cookies.txt format Netscape, username plus password/API key, atau OAuth. Gunakan Siapkan uji aman untuk beralih ke simulasi. Credential memerlukan Default tersimpan dan konfirmasi penyimpanan teks biasa.</li>
              <li><b>Buka Situs hanya untuk perilaku khusus situs.</b> Anda tidak perlu memilih situs untuk download normal. Preset yang dipilih hanya menambahkan blok config khusus extractor.</li>
              <li><b>Periksa Pratinjau.</b> Command final menunjukkan isi yang masuk ke antrean. Config gabungan menunjukkan config aktif ditambah default dan blok situs yang diusulkan. Password, API key, dan token hanya tampil sebagai <code>&lt;hidden&gt;</code>.</li>
              <li><b>Selesaikan alur.</b> Untuk Default tersimpan, klik Simpan Default terlebih dahulu (Composer akan mengingatkan bila belum). Klik Tambah ke Antrean, tutup Composer, lalu tekan DOWNLOAD atau Ctrl+Enter pada jendela utama.</li>
            </ol>
            """,
        ))
        steps_text.setWordWrap(True)
        steps_text.setTextFormat(Qt.RichText)
        steps_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        steps_l.addWidget(steps_text)
        guide_l.addWidget(steps_card)

        examples_card = QFrame()
        examples_card.setObjectName("card")
        examples_l = QGridLayout(examples_card)
        examples_l.setContentsMargins(12, 10, 12, 10)
        examples_l.setHorizontalSpacing(18)
        examples_l.addWidget(QLabel(tr("EXAMPLE 1 — ONE-TIME DOWNLOAD", "CONTOH 1 — DOWNLOAD SEKALI")), 0, 0)
        examples_l.addWidget(QLabel(tr("EXAMPLE 2 — REUSABLE SETUP", "CONTOH 2 — PENGATURAN BERULANG")), 0, 1)
        example_once = QLabel(tr(
            "1. Paste URL<br>2. Choose <b>Job override</b><br>3. Set folder/range<br>4. Check Preview<br>5. Add to Queue",
            "1. Tempel URL<br>2. Pilih <b>Override job</b><br>3. Atur folder/rentang<br>4. Periksa Pratinjau<br>5. Tambah ke Antrean",
        ))
        example_saved = QLabel(tr(
            "1. Choose <b>Saved defaults</b><br>2. Set folder/archive<br>3. Configure Login &amp; Cookies if needed<br>4. Save Defaults<br>5. Add URLs to Queue",
            "1. Pilih <b>Default tersimpan</b><br>2. Atur folder/archive<br>3. Atur Login &amp; Cookies bila perlu<br>4. Simpan Default<br>5. Tambahkan URL ke Antrean",
        ))
        for column, label in enumerate((example_once, example_saved)):
            label.setWordWrap(True)
            label.setTextFormat(Qt.RichText)
            examples_l.addWidget(label, 1, column)
        examples_l.setColumnStretch(0, 1)
        examples_l.setColumnStretch(1, 1)
        guide_l.addWidget(examples_card)

        safety = QLabel(tr(
            "Safety: Add to Queue does not start a download. Saving defaults preserves unmanaged JSON sections and creates a timestamped backup before updating an existing config. Cookies and tokens may be sensitive—do not share config previews publicly.",
            "Keamanan: Tambah ke Antrean tidak langsung memulai download. Penyimpanan default mempertahankan bagian JSON yang tidak dikelola dan membuat backup bertimestamp sebelum memperbarui config. Cookies dan token bersifat sensitif—jangan membagikan pratinjau config ke publik.",
        ))
        safety.setObjectName("subtle")
        safety.setWordWrap(True)
        guide_l.addWidget(safety)
        start_composing = QPushButton(tr("Continue to Download", "Lanjut ke Download"))
        start_composing.setObjectName("primary")
        start_composing.setMinimumHeight(34)
        guide_l.addWidget(start_composing, 0, Qt.AlignLeft)

        # Download and shared settings -------------------------------------------------
        main_scroll = QScrollArea()
        main_scroll.setWidgetResizable(True)
        main_scroll.setFrameShape(QFrame.NoFrame)
        main_page = QWidget()
        main_l = QVBoxLayout(main_page)
        main_l.setContentsMargins(8, 8, 8, 8)
        main_l.setSpacing(10)
        main_scroll.setWidget(main_page)
        tabs.addTab(main_scroll, tr("Download", "Download"))
        start_composing.clicked.connect(lambda: tabs.setCurrentWidget(main_scroll))

        job_card = QFrame()
        job_card.setObjectName("card")
        job_l = QGridLayout(job_card)
        job_l.setContentsMargins(10, 10, 10, 10)
        job_l.setHorizontalSpacing(10)
        job_l.setVerticalSpacing(7)
        job_l.addWidget(QLabel(tr("JOB", "JOB")), 0, 0)
        recipe = QComboBox()
        recipe.addItems([
            tr("Basic", "Dasar"),
            tr("Safe & slow", "Aman & pelan"),
            tr("Logged-in", "Dengan login"),
            tr("Archive duplicates", "Arsip duplikat"),
            tr("Metadata", "Metadata"),
            tr("Inspect keywords", "Periksa keyword"),
        ])
        recipe.setToolTip(tr(
            "Optional shortcut that fills a sensible group of settings. You can still edit every field afterward.",
            "Pintasan opsional yang mengisi sekelompok pengaturan. Semua field tetap dapat diubah setelahnya.",
        ))
        job_l.addWidget(recipe, 0, 1)
        scope = QComboBox()
        scope.addItem(tr("Job override", "Override job"), "job")
        scope.addItem(tr("Saved defaults", "Default tersimpan"), "config")
        scope.setToolTip(tr(
            "Job override writes shared values into this command. Saved defaults keeps them in the config instead.",
            "Override job menulis nilai bersama ke command ini. Default tersimpan menyimpannya di config.",
        ))
        job_l.addWidget(scope, 0, 2)
        urls = QPlainTextEdit()
        urls.setPlaceholderText(tr("One URL per line", "Satu URL per baris"))
        urls.setMinimumHeight(88)
        urls.setToolTip(tr(
            "Paste one gallery-dl-supported URL per line. Every line becomes a separate queue job.",
            "Tempel satu URL yang didukung gallery-dl per baris. Setiap baris menjadi satu job antrean.",
        ))
        job_l.addWidget(QLabel("URL"), 1, 0)
        job_l.addWidget(urls, 1, 1, 1, 2)
        config_path_label = QLabel(str(self.config_path or detect_config_path() or ""))
        config_path_label.setObjectName("subtle")
        config_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        job_l.addWidget(QLabel(tr("Active config", "Config aktif")), 2, 0)
        job_l.addWidget(config_path_label, 2, 1, 1, 2)
        job_l.setColumnStretch(1, 1)
        main_l.addWidget(job_card)

        storage_card = QFrame()
        storage_card.setObjectName("card")
        storage_l = QGridLayout(storage_card)
        storage_l.setContentsMargins(10, 10, 10, 10)
        storage_l.setHorizontalSpacing(8)
        storage_l.setVerticalSpacing(7)
        storage_l.addWidget(QLabel(tr("FILES & STORAGE", "FILE & PENYIMPANAN")), 0, 0, 1, 4)
        destination = QLineEdit(self.edit_output.text().strip() or "./downloads")
        destination.setPlaceholderText("D:/Downloads/gallery-dl")
        destination.setToolTip(tr(
            "Base download location. In Job override this becomes -d, or -D when Exact folder is enabled.",
            "Lokasi dasar download. Pada Override job nilainya menjadi -d, atau -D jika Folder persis aktif.",
        ))
        browse_destination = QPushButton(tr("Browse", "Pilih"))
        exact_destination = QCheckBox(tr("Exact folder (-D)", "Folder persis (-D)"))
        exact_destination.setToolTip(tr(
            "Put files directly in the selected folder and ignore the Subfolders pattern.",
            "Letakkan file langsung di folder terpilih dan abaikan pola Subfolder.",
        ))
        directory = QLineEdit("{category}; {subcategory}; {user[id]}")
        directory.setPlaceholderText(tr("Separate folder levels with ;", "Pisahkan tingkat folder dengan ;"))
        directory.setToolTip(tr(
            "Folder levels below Download folder. Separate each level with a semicolon. Available keywords vary by extractor; inspect them with Mode: List keywords.",
            "Tingkat folder di bawah Folder download. Pisahkan setiap tingkat dengan titik koma. Keyword berbeda per extractor; periksa melalui Mode: Lihat keyword.",
        ))
        filename = QLineEdit("{id}_{num}.{extension}")
        filename.setToolTip(tr(
            "gallery-dl filename format. Keep {extension}; use Preview or List keywords before using site-specific fields.",
            "Format nama file gallery-dl. Pertahankan {extension}; gunakan Pratinjau atau Lihat keyword sebelum memakai field khusus situs.",
        ))
        storage_l.addWidget(QLabel(tr("Download folder", "Folder download")), 1, 0)
        storage_l.addWidget(destination, 1, 1, 1, 2)
        storage_l.addWidget(browse_destination, 1, 3)
        storage_l.addWidget(QLabel(tr("Subfolders", "Subfolder")), 2, 0)
        storage_l.addWidget(directory, 2, 1, 1, 2)
        storage_l.addWidget(exact_destination, 2, 3)
        storage_l.addWidget(QLabel(tr("Filename", "Nama file")), 3, 0)
        storage_l.addWidget(filename, 3, 1, 1, 3)
        storage_l.setColumnStretch(1, 1)
        main_l.addWidget(storage_card)

        access_card = QFrame()
        access_card.setObjectName("card")
        access_l = QGridLayout(access_card)
        access_l.setContentsMargins(10, 10, 10, 10)
        access_l.setHorizontalSpacing(8)
        access_l.setVerticalSpacing(7)
        access_l.addWidget(QLabel(tr("RELIABILITY", "KETAHANAN")), 0, 0, 1, 4)
        archive_enabled = QCheckBox(tr("Skip previously downloaded files", "Lewati file yang pernah diunduh"))
        archive_enabled.setToolTip(tr(
            "Use gallery-dl's download archive database to avoid downloading the same item again.",
            "Gunakan database download archive gallery-dl agar item yang sama tidak diunduh kembali.",
        ))
        archive_path = QLineEdit(str(APP_DIR / "archive.sqlite3"))
        archive_path.setToolTip(tr(
            "SQLite archive path. This records gallery-dl item IDs, not the downloaded media itself.",
            "Path archive SQLite. File ini menyimpan ID item gallery-dl, bukan media hasil download.",
        ))
        browse_archive = QPushButton(tr("Browse", "Pilih"))
        retries = QSpinBox()
        retries.setRange(0, 99)
        retries.setValue(max(0, int(self.spin_retries.value())))
        retries.setToolTip(tr("Maximum retry attempts. Zero leaves the option unset.", "Jumlah maksimum percobaan ulang. Nol berarti opsi tidak diatur."))
        timeout = QSpinBox()
        timeout.setRange(0, 999)
        timeout.setValue(30)
        timeout.setSuffix(" s")
        timeout.setToolTip(tr("HTTP connection timeout in seconds.", "Timeout koneksi HTTP dalam detik."))
        sleep_request = QLineEdit("1.0-2.0")
        sleep_request.setToolTip(tr("Delay between extraction requests. A range picks a random value.", "Jeda antar-request ekstraksi. Rentang akan memilih nilai acak."))
        sleep_429 = QLineEdit("60-180")
        sleep_429.setToolTip(tr("Wait after an HTTP 429 rate-limit response. Longer values reduce repeated blocking.", "Jeda setelah respons rate-limit HTTP 429. Nilai lebih panjang mengurangi pemblokiran berulang."))
        access_l.addWidget(archive_enabled, 1, 0, 1, 2)
        archive_row = QHBoxLayout()
        archive_row.addWidget(archive_path, 1)
        archive_row.addWidget(browse_archive)
        access_l.addLayout(archive_row, 1, 2, 1, 2)
        access_l.addWidget(QLabel(tr("Retries", "Coba ulang")), 2, 0)
        access_l.addWidget(retries, 2, 1)
        access_l.addWidget(QLabel("Timeout"), 2, 2)
        access_l.addWidget(timeout, 2, 3)
        access_l.addWidget(QLabel(tr("Request delay", "Jeda request")), 3, 0)
        access_l.addWidget(sleep_request, 3, 1)
        access_l.addWidget(QLabel(tr("429 delay", "Jeda 429")), 3, 2)
        access_l.addWidget(sleep_429, 3, 3)
        access_l.setColumnStretch(1, 1)
        access_l.setColumnStretch(3, 1)
        main_l.addWidget(access_card)

        # Advanced job options ---------------------------------------------------------
        advanced_scroll = QScrollArea()
        advanced_scroll.setWidgetResizable(True)
        advanced_scroll.setFrameShape(QFrame.NoFrame)
        advanced_page = QWidget()
        advanced_l = QGridLayout(advanced_page)
        advanced_l.setContentsMargins(8, 8, 8, 8)
        advanced_l.setHorizontalSpacing(10)
        advanced_l.setVerticalSpacing(8)
        advanced_scroll.setWidget(advanced_page)
        tabs.addTab(advanced_scroll, tr("Options", "Opsi"))

        file_range = QLineEdit()
        file_range.setPlaceholderText("1-20, 5-, 1:50:2")
        file_range.setToolTip(tr("Select file indices, for example 1-20, 5-, or 1:50:2.", "Pilih indeks file, misalnya 1-20, 5-, atau 1:50:2."))
        date_after = QLineEdit()
        date_after.setPlaceholderText("2026-01-01")
        date_after.setToolTip(tr(
            "Set the extractor date-min override. This applies only to sites whose gallery-dl extractor supports date-min/date-max.",
            "Atur override date-min extractor. Ini hanya berlaku pada situs yang extractor gallery-dl-nya mendukung date-min/date-max.",
        ))
        date_before = QLineEdit()
        date_before.setPlaceholderText("2026-12-31")
        date_before.setToolTip(tr(
            "Set the extractor date-max override. This applies only to sites whose gallery-dl extractor supports date-min/date-max.",
            "Atur override date-max extractor. Ini hanya berlaku pada situs yang extractor gallery-dl-nya mendukung date-min/date-max.",
        ))
        filter_expr = QLineEdit()
        filter_expr.setPlaceholderText('extension in ("jpg", "png") and width >= 1000')
        filter_expr.setToolTip(tr("Advanced gallery-dl filter expression evaluated for each file.", "Ekspresi filter gallery-dl lanjutan yang dievaluasi untuk setiap file."))
        proxy = QLineEdit()
        proxy.setPlaceholderText("http://127.0.0.1:8080")
        proxy.setToolTip(tr("Optional HTTP or SOCKS proxy URL.", "URL proxy HTTP atau SOCKS opsional."))
        user_agent = QLineEdit()
        user_agent.setPlaceholderText("browser")
        user_agent.setToolTip(tr("Optional User-Agent value. The value browser asks gallery-dl to detect one.", "Nilai User-Agent opsional. Nilai browser meminta gallery-dl mendeteksinya."))
        mode = QComboBox()
        mode.addItem(tr("Download files", "Download file"), "download")
        mode.addItem(tr("Simulate only", "Hanya simulasi"), "simulate")
        mode.addItem(tr("Get direct URLs", "Ambil URL langsung"), "urls")
        mode.addItem(tr("Dump JSON", "Dump JSON"), "json")
        mode.addItem(tr("List keywords", "Lihat keyword"), "keywords")
        mode.addItem(tr("Extractor info", "Info extractor"), "extractor")
        mode.setToolTip(tr(
            "Download is normal operation. Other modes inspect or print information and usually do not download media.",
            "Download adalah operasi normal. Mode lain memeriksa atau mencetak informasi dan biasanya tidak mengunduh media.",
        ))
        archive_format = QComboBox()
        archive_format.addItem(tr("No archive", "Tanpa arsip"), "none")
        archive_format.addItem("ZIP", "zip")
        archive_format.addItem("CBZ", "cbz")
        archive_format.setToolTip(tr("Optionally package downloaded files as ZIP or CBZ.", "Opsional: kemas file hasil download sebagai ZIP atau CBZ."))
        metadata_json = QCheckBox(tr("Write metadata JSON", "Tulis metadata JSON"))
        metadata_json.setToolTip(tr("Write a JSON metadata file next to each downloaded item.", "Tulis file metadata JSON di samping setiap item hasil download."))
        info_json = QCheckBox(tr("Write info.json", "Tulis info.json"))
        info_json.setToolTip(tr("Write gallery-level metadata to info.json.", "Tulis metadata tingkat galeri ke info.json."))
        windows_filenames = QCheckBox(tr("Windows-safe filenames", "Nama file aman Windows"))
        windows_filenames.setChecked(IS_WINDOWS)
        windows_filenames.setToolTip(tr("Replace or strip characters Windows does not allow in paths.", "Ganti atau hapus karakter yang tidak diizinkan Windows dalam path."))
        no_input = QCheckBox(tr("Never prompt during queue runs", "Jangan tampilkan prompt saat antrean berjalan"))
        no_input.setChecked(True)
        no_input.setToolTip(tr("Prevent a background worker from waiting forever for terminal input.", "Cegah worker latar belakang menunggu input terminal tanpa batas."))
        extra_args = QLineEdit()
        extra_args.setPlaceholderText(tr("Only for options not available above", "Hanya untuk opsi yang belum tersedia di atas"))
        extra_args.setToolTip(tr("Raw gallery-dl arguments for advanced options not represented by a field.", "Argumen mentah gallery-dl untuk opsi lanjutan yang belum memiliki field."))
        advanced_fields = [
            (tr("File range", "Rentang file"), file_range),
            (tr("Mode", "Mode"), mode),
            (tr("Date after", "Tanggal setelah"), date_after),
            (tr("Date before", "Tanggal sebelum"), date_before),
            (tr("Filter", "Filter"), filter_expr),
            (tr("Output archive", "Arsip output"), archive_format),
            ("Proxy", proxy),
            ("User-Agent", user_agent),
        ]
        for index, (label_text, widget) in enumerate(advanced_fields):
            row = index // 2
            column = (index % 2) * 2
            advanced_l.addWidget(QLabel(label_text), row, column)
            advanced_l.addWidget(widget, row, column + 1)
        checks = QGridLayout()
        for index, widget in enumerate((metadata_json, info_json, windows_filenames, no_input)):
            checks.addWidget(widget, index // 2, index % 2)
        final_row = (len(advanced_fields) + 1) // 2
        advanced_l.addLayout(checks, final_row, 0, 1, 4)
        advanced_l.addWidget(QLabel(tr("Extra arguments", "Argumen ekstra")), final_row + 1, 0)
        advanced_l.addWidget(extra_args, final_row + 1, 1, 1, 3)
        tip = QLabel(tr(
            "Advanced values that are reusable (proxy, User-Agent, metadata and Windows filenames) are also included when saving defaults. Filters and modes remain job-specific.",
            "Nilai lanjutan yang bisa dipakai ulang (proxy, User-Agent, metadata, dan nama file Windows) ikut disimpan sebagai default. Filter dan mode tetap khusus job.",
        ))
        tip.setObjectName("subtle")
        tip.setWordWrap(True)
        advanced_l.addWidget(tip, final_row + 2, 0, 1, 4)
        advanced_l.setColumnStretch(1, 1)
        advanced_l.setColumnStretch(3, 1)
        advanced_l.setRowStretch(final_row + 3, 1)

        # Authentication -------------------------------------------------------------
        auth_scroll = QScrollArea()
        auth_scroll.setObjectName("authScroll")
        auth_scroll.setWidgetResizable(True)
        auth_scroll.setFrameShape(QFrame.NoFrame)
        auth_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        auth_page = QWidget()
        auth_page.setMinimumWidth(0)
        auth_page.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        auth_l = QVBoxLayout(auth_page)
        auth_l.setContentsMargins(8, 8, 8, 8)
        auth_l.setSpacing(10)
        auth_scroll.setWidget(auth_page)
        tabs.addTab(auth_scroll, tr("Login & Cookies", "Login & Cookies"))

        auth_intro = QLabel(tr(
            "Choose one login method. Public downloads need no login. Browser cookies are usually the easiest; credentials are saved only to config; OAuth opens the official authorization flow.",
            "Pilih satu metode login. Download publik tidak memerlukan login. Cookies browser biasanya paling mudah; credential hanya disimpan ke config; OAuth membuka alur otorisasi resmi.",
        ))
        auth_intro.setObjectName("subtle")
        auth_intro.setWordWrap(True)
        auth_l.addWidget(auth_intro)

        auth_flow = QFrame()
        auth_flow.setObjectName("card")
        auth_flow_l = QHBoxLayout(auth_flow)
        auth_flow_l.setContentsMargins(10, 8, 10, 8)
        auth_flow_l.setSpacing(8)
        auth_steps = [
            tr("1  Select site", "1  Pilih situs"),
            tr("2  Choose method", "2  Pilih metode"),
            tr("3  Enter data", "3  Isi data"),
            tr("4  Test safely", "4  Uji dengan aman"),
            tr("5  Save if needed", "5  Simpan bila perlu"),
        ]
        for index, step_text in enumerate(auth_steps):
            step_label = QLabel(step_text)
            step_label.setObjectName("fieldLabel")
            step_label.setAlignment(Qt.AlignCenter)
            step_label.setWordWrap(True)
            step_label.setMinimumWidth(0)
            step_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            auth_flow_l.addWidget(step_label, 1)
            if index < len(auth_steps) - 1:
                arrow = QLabel("→")
                arrow.setObjectName("good")
                auth_flow_l.addWidget(arrow)
        auth_l.addWidget(auth_flow)

        auth_choice = QFrame()
        auth_choice.setObjectName("card")
        auth_choice_l = QGridLayout(auth_choice)
        auth_choice_l.setContentsMargins(10, 10, 10, 10)
        auth_choice_l.setHorizontalSpacing(10)
        auth_choice_l.setVerticalSpacing(8)
        auth_site = QComboBox()
        auth_site.setObjectName("authSiteCombo")
        auth_site.setEditable(False)
        auth_site.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        auth_site.setMinimumContentsLength(18)
        auth_site.addItem(tr("All sites (global)", "Semua situs (global)"), "")
        for category in site_catalog:
            friendly = definition_names.get(category)
            auth_site.addItem(f"{friendly} ({category})" if friendly else category, category)
        auth_site.setToolTip(tr(
            "Select a specific extractor to keep login data scoped to that site. Global is useful only for one shared browser-cookie source.",
            "Pilih extractor tertentu agar data login hanya berlaku untuk situs itu. Global sebaiknya hanya untuk satu sumber cookies browser bersama.",
        ))
        auth_method = QComboBox()
        auth_method.setObjectName("authMethodCombo")
        auth_method.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        auth_method.setMinimumContentsLength(18)
        auth_method.addItem(tr("No login / public", "Tanpa login / publik"), "none")
        auth_method.addItem(tr("Browser cookies (recommended)", "Cookies browser (disarankan)"), "browser")
        auth_method.addItem("cookies.txt", "file")
        auth_method.addItem(tr("Username + password / API key", "Username + password / API key"), "credentials")
        auth_method.addItem("OAuth", "oauth")
        auth_method.setToolTip(tr(
            "Use exactly one method. The form below changes to show only fields needed by that method.",
            "Gunakan tepat satu metode. Form di bawah berubah dan hanya menampilkan field yang diperlukan.",
        ))
        auth_choice_l.addWidget(QLabel(tr("Site / extractor", "Situs / extractor")), 0, 0)
        auth_choice_l.addWidget(auth_site, 0, 1, 1, 3)
        auth_choice_l.addWidget(QLabel(tr("Login method", "Metode login")), 1, 0)
        auth_choice_l.addWidget(auth_method, 1, 1, 1, 3)
        auth_method_help = QLabel()
        auth_method_help.setObjectName("subtle")
        auth_method_help.setWordWrap(True)
        auth_choice_l.addWidget(auth_method_help, 2, 0, 1, 4)
        auth_choice_l.setColumnStretch(1, 1)
        auth_choice_l.setColumnStretch(3, 1)
        auth_l.addWidget(auth_choice)

        auth_stack = QStackedWidget()
        auth_stack.setObjectName("authMethodStack")
        auth_stack.setMinimumWidth(0)
        auth_stack.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        auth_l.addWidget(auth_stack)

        public_panel = QFrame()
        public_panel.setObjectName("card")
        public_l = QVBoxLayout(public_panel)
        public_l.setContentsMargins(12, 12, 12, 12)
        public_text = QLabel(tr(
            "Use this for public URLs. No browser profile, cookie file, password, or token will be added to the command or config.",
            "Gunakan untuk URL publik. Tidak ada profil browser, file cookie, password, atau token yang ditambahkan ke command maupun config.",
        ))
        public_text.setWordWrap(True)
        public_l.addWidget(public_text)
        auth_stack.addWidget(public_panel)

        browser_panel = QFrame()
        browser_panel.setObjectName("card")
        browser_l = QGridLayout(browser_panel)
        browser_l.setContentsMargins(12, 10, 12, 10)
        browser_l.setHorizontalSpacing(10)
        browser_l.setVerticalSpacing(8)
        cookies_browser = QComboBox()
        cookies_browser.addItems(["chrome", "firefox", "edge", "brave", "chromium", "opera"])
        if cookies_browser.findText(gui_browser) >= 0:
            cookies_browser.setCurrentText(gui_browser)
        elif cookies_browser.findText("firefox") >= 0:
            cookies_browser.setCurrentText("firefox")
        cookies_browser.setToolTip(tr(
            "The browser where you are already logged in. On Windows, close Chrome/Edge if their cookie database is locked.",
            "Browser tempat Anda sudah login. Di Windows, tutup Chrome/Edge jika database cookie sedang terkunci.",
        ))
        cookies_profile = QLineEdit()
        cookies_profile.setPlaceholderText(tr("Optional profile name or absolute profile path", "Nama profil atau path profil absolut (opsional)"))
        cookies_profile.setToolTip(tr(
            "Leave empty for the browser's default profile. Use this when you have multiple browser profiles.",
            "Kosongkan untuk profil default browser. Isi jika Anda memiliki beberapa profil browser.",
        ))
        cookies_domain = QLineEdit()
        cookies_domain.setPlaceholderText(tr("Optional domain, e.g. .twitter.com", "Domain opsional, misalnya .twitter.com"))
        cookies_domain.setToolTip(tr(
            "Limit extracted cookies to one domain. Prefix with a dot to include subdomains.",
            "Batasi cookies yang dibaca ke satu domain. Awali dengan titik untuk menyertakan subdomain.",
        ))
        browser_l.addWidget(QLabel(tr("Browser", "Browser")), 0, 0)
        browser_l.addWidget(cookies_browser, 0, 1, 1, 3)
        browser_l.addWidget(QLabel(tr("Profile", "Profil")), 1, 0)
        browser_l.addWidget(cookies_profile, 1, 1, 1, 3)
        browser_l.addWidget(QLabel(tr("Domain", "Domain")), 2, 0)
        browser_l.addWidget(cookies_domain, 2, 1, 1, 3)
        browser_note = QLabel(tr(
            "Nothing is copied into this app: gallery-dl reads the selected browser profile when the job runs. Use Job override for one task or Saved defaults to reuse this source.",
            "Tidak ada cookies yang disalin ke aplikasi: gallery-dl membaca profil browser saat job berjalan. Gunakan Override job untuk satu tugas atau Default tersimpan agar sumber ini dipakai ulang.",
        ))
        browser_note.setObjectName("subtle")
        browser_note.setWordWrap(True)
        browser_l.addWidget(browser_note, 3, 0, 1, 4)
        browser_l.setColumnStretch(1, 1)
        browser_l.setColumnStretch(3, 1)
        auth_stack.addWidget(browser_panel)

        file_panel = QFrame()
        file_panel.setObjectName("card")
        file_l = QGridLayout(file_panel)
        file_l.setContentsMargins(12, 10, 12, 10)
        file_l.setHorizontalSpacing(10)
        file_l.setVerticalSpacing(8)
        cookies_file = QLineEdit()
        cookies_file.setPlaceholderText(tr("Netscape-format cookies.txt", "cookies.txt format Netscape"))
        cookies_file.setToolTip(tr(
            "Must be Mozilla/Netscape cookies.txt format, not JSON. This file can grant account access; keep it private.",
            "Harus berupa cookies.txt format Mozilla/Netscape, bukan JSON. File ini dapat memberi akses akun; simpan secara privat.",
        ))
        browse_cookies = QPushButton(tr("Browse", "Pilih"))
        cookies_update = QCheckBox(tr("Let gallery-dl update refreshed cookies in this file", "Izinkan gallery-dl memperbarui cookies baru ke file ini"))
        cookies_update.setChecked(True)
        cookie_file_row = QHBoxLayout()
        cookie_file_row.addWidget(cookies_file, 1)
        cookie_file_row.addWidget(browse_cookies)
        file_l.addWidget(QLabel("cookies.txt"), 0, 0)
        file_l.addLayout(cookie_file_row, 0, 1, 1, 3)
        file_l.addWidget(cookies_update, 1, 1, 1, 3)
        file_warning = QLabel(tr(
            "Security: cookies.txt is plain text. Do not upload it, paste it into chat, or include it in a shared project folder.",
            "Keamanan: cookies.txt adalah teks biasa. Jangan mengunggahnya, menempelkannya ke chat, atau menyimpannya di folder proyek bersama.",
        ))
        file_warning.setObjectName("bad")
        file_warning.setWordWrap(True)
        file_l.addWidget(file_warning, 2, 0, 1, 4)
        file_l.setColumnStretch(1, 1)
        auth_stack.addWidget(file_panel)

        credentials_panel = QFrame()
        credentials_panel.setObjectName("card")
        credentials_l = QGridLayout(credentials_panel)
        credentials_l.setContentsMargins(12, 10, 12, 10)
        credentials_l.setHorizontalSpacing(10)
        credentials_l.setVerticalSpacing(8)
        auth_username = QLineEdit()
        auth_username.setPlaceholderText(tr("Optional for token-only sites", "Opsional untuk situs yang hanya memakai token"))
        secret_key = QComboBox()
        secret_key.setEditable(True)
        secret_key.addItems(["password", "api-key", "api-secret", "refresh-token", "access-token", "client-id", "client-secret"])
        secret_key.setToolTip(tr(
            "Config option name expected by this extractor. For Danbooru/E621 families, the API key is often stored under password.",
            "Nama opsi config yang diminta extractor. Untuk keluarga Danbooru/E621, API key sering disimpan pada password.",
        ))
        secret_value = QLineEdit()
        secret_value.setObjectName("authSecretValue")
        secret_value.setEchoMode(QLineEdit.Password)
        secret_value.setPlaceholderText(tr("Password, API key, or token", "Password, API key, atau token"))
        extra_auth_key = QComboBox()
        extra_auth_key.setEditable(True)
        extra_auth_key.addItem(tr("None", "Tidak ada"), "")
        extra_auth_key.addItems(["api-secret", "client-secret", "access-token-secret"])
        extra_auth_value = QLineEdit()
        extra_auth_value.setEchoMode(QLineEdit.Password)
        extra_auth_value.setPlaceholderText(tr("Optional second secret", "Secret kedua (opsional)"))
        show_secrets = QCheckBox(tr("Show secret values", "Tampilkan nilai secret"))
        credential_ack = QCheckBox(tr(
            "I understand secrets are stored as plain text",
            "Saya memahami secret disimpan sebagai teks biasa",
        ))
        credentials_l.addWidget(QLabel("Username"), 0, 0)
        credentials_l.addWidget(auth_username, 0, 1, 1, 3)
        credentials_l.addWidget(QLabel(tr("Secret option", "Opsi secret")), 1, 0)
        credentials_l.addWidget(secret_key, 1, 1)
        credentials_l.addWidget(QLabel(tr("Secret value", "Nilai secret")), 1, 2)
        credentials_l.addWidget(secret_value, 1, 3)
        credentials_l.addWidget(QLabel(tr("Extra option", "Opsi tambahan")), 2, 0)
        credentials_l.addWidget(extra_auth_key, 2, 1)
        credentials_l.addWidget(QLabel(tr("Extra value", "Nilai tambahan")), 2, 2)
        credentials_l.addWidget(extra_auth_value, 2, 3)
        credentials_l.addWidget(show_secrets, 3, 1)
        credentials_l.addWidget(credential_ack, 3, 2, 1, 2)
        credential_note = QLabel(tr(
            "Credentials are never placed in queue commands or logs. They are written only to the selected site's config block after confirmation. Preview replaces their values with <hidden>.",
            "Credential tidak pernah dimasukkan ke command antrean atau log. Data hanya ditulis ke blok config situs terpilih setelah konfirmasi. Pratinjau mengganti nilainya dengan <hidden>.",
        ))
        credential_note.setObjectName("subtle")
        credential_note.setWordWrap(True)
        credentials_l.addWidget(credential_note, 4, 0, 1, 4)
        credentials_l.setColumnStretch(1, 1)
        credentials_l.setColumnStretch(3, 1)
        auth_stack.addWidget(credentials_panel)

        oauth_panel = QFrame()
        oauth_panel.setObjectName("card")
        oauth_l = QGridLayout(oauth_panel)
        oauth_l.setContentsMargins(12, 10, 12, 10)
        oauth_l.setHorizontalSpacing(10)
        oauth_l.setVerticalSpacing(8)
        oauth_site = QComboBox()
        oauth_site.setObjectName("oauthSiteCombo")
        oauth_site.setEditable(False)
        for category in OAUTH_SITE_CHOICES:
            oauth_site.addItem(category, category)
        oauth_site.setToolTip(tr(
            "Choose a supported OAuth extractor. Free typing is disabled to prevent site-name mistakes.",
            "Pilih extractor OAuth yang didukung. Pengetikan bebas dinonaktifkan agar nama situs tidak salah.",
        ))
        oauth_instance = QLineEdit()
        oauth_instance.setObjectName("oauthMastodonInstance")
        oauth_instance.setPlaceholderText(tr(
            "Mastodon only: instance hostname or URL",
            "Khusus Mastodon: hostname atau URL instance",
        ))
        oauth_instance.setEnabled(False)
        oauth_start = QPushButton(tr("Start OAuth", "Mulai OAuth"))
        oauth_start.setObjectName("primary")
        oauth_stop = QPushButton(tr("Stop", "Hentikan"))
        oauth_stop.setEnabled(False)
        oauth_copy = QPushButton(tr("Copy command", "Salin command"))
        oauth_response = QLineEdit()
        oauth_response.setEchoMode(QLineEdit.Password)
        oauth_response.setPlaceholderText(tr(
            "Pixiv only: paste code or callback URL",
            "Khusus Pixiv: tempel code atau URL callback",
        ))
        oauth_send = QPushButton(tr("Send Pixiv code", "Kirim code Pixiv"))
        oauth_send.setEnabled(False)
        oauth_status = QPlainTextEdit()
        oauth_status.setReadOnly(True)
        oauth_status.setMinimumHeight(150)
        oauth_status.setPlaceholderText(tr(
            "OAuth progress appears here. Your browser should open for authorization.",
            "Progres OAuth muncul di sini. Browser akan terbuka untuk otorisasi.",
        ))
        oauth_l.addWidget(QLabel(tr("OAuth site", "Situs OAuth")), 0, 0)
        oauth_l.addWidget(oauth_site, 0, 1)
        oauth_buttons = QHBoxLayout()
        oauth_buttons.addWidget(oauth_start)
        oauth_buttons.addWidget(oauth_stop)
        oauth_buttons.addWidget(oauth_copy)
        oauth_l.addLayout(oauth_buttons, 0, 2, 1, 2)
        oauth_l.addWidget(QLabel(tr("Mastodon instance", "Instance Mastodon")), 1, 0)
        oauth_l.addWidget(oauth_instance, 1, 1, 1, 3)
        oauth_l.addWidget(QLabel(tr("Authorization response", "Respons otorisasi")), 2, 0)
        oauth_l.addWidget(oauth_response, 2, 1, 1, 2)
        oauth_l.addWidget(oauth_send, 2, 3)
        oauth_l.addWidget(oauth_status, 3, 0, 1, 4)
        oauth_note = QLabel(tr(
            "OAuth does not use the username/password fields above. gallery-dl opens the site's official authorization page and normally stores received tokens in its cache. Never share token output.",
            "OAuth tidak memakai field username/password di atas. gallery-dl membuka halaman otorisasi resmi situs dan biasanya menyimpan token yang diterima ke cache. Jangan pernah membagikan output token.",
        ))
        oauth_note.setObjectName("subtle")
        oauth_note.setWordWrap(True)
        oauth_l.addWidget(oauth_note, 4, 0, 1, 4)
        oauth_l.setColumnStretch(1, 1)
        auth_stack.addWidget(oauth_panel)

        auth_actions = QHBoxLayout()
        prepare_test = QPushButton(tr("Prepare safe test", "Siapkan uji aman"))
        prepare_test.setToolTip(tr(
            "Switch Mode to Simulate only and return to Download. Add a URL, then run the queue to test access without saving media.",
            "Ubah Mode menjadi Hanya simulasi dan kembali ke Download. Tambahkan URL, lalu jalankan antrean untuk menguji akses tanpa menyimpan media.",
        ))
        auth_actions.addWidget(prepare_test)
        remove_auth = QPushButton(tr("Remove saved config login", "Hapus login config tersimpan"))
        remove_auth.setToolTip(tr(
            "Remove common cookies and credential keys from the selected site's config block. OAuth cache is separate and is not cleared here.",
            "Hapus key cookies dan credential umum dari blok config situs terpilih. Cache OAuth terpisah dan tidak dihapus di sini.",
        ))
        auth_actions.addWidget(remove_auth)
        auth_actions.addStretch(1)
        auth_l.addLayout(auth_actions)

        # Site presets ---------------------------------------------------------------
        sites_page = QWidget()
        sites_l = QVBoxLayout(sites_page)
        sites_l.setContentsMargins(8, 8, 8, 8)
        sites_l.setSpacing(8)
        site_intro = QLabel(tr(
            "Optional extractor-specific folder, filename, cookie and rate-limit defaults. These are part of the same config, not a separate preset system.",
            "Default folder, nama file, cookies, dan rate-limit khusus extractor. Semua ini bagian dari config yang sama, bukan sistem preset terpisah.",
        ))
        site_intro.setObjectName("subtle")
        site_intro.setWordWrap(True)
        sites_l.addWidget(site_intro)
        site_tools = QHBoxLayout()
        site_search = QLineEdit()
        site_search.setPlaceholderText(tr("Search sites", "Cari situs"))
        site_search.setToolTip(tr("Filter the preset list by site name or extractor category.", "Saring daftar preset berdasarkan nama situs atau category extractor."))
        select_common = QPushButton(tr("Common", "Umum"))
        select_none = QPushButton(tr("Clear", "Bersihkan"))
        site_config_button = QPushButton(tr("Site Config Studio…", "Studio Config Situs…"))
        site_config_button.setObjectName("siteConfigStudioButton")
        site_config_button.setToolTip(tr(
            "Edit per-site archive databases, Reddit OAuth app settings, Pixiv options, and typed advanced options.",
            "Atur database archive per situs, aplikasi OAuth Reddit, opsi Pixiv, dan opsi lanjutan bertipe.",
        ))
        site_tools.addWidget(site_search, 1)
        site_tools.addWidget(select_common)
        site_tools.addWidget(select_none)
        site_tools.addWidget(site_config_button)
        sites_l.addLayout(site_tools)
        site_scroll = QScrollArea()
        site_scroll.setWidgetResizable(True)
        site_scroll.setFrameShape(QFrame.NoFrame)
        site_content = QWidget()
        site_grid = QGridLayout(site_content)
        site_grid.setContentsMargins(4, 4, 4, 4)
        site_grid.setHorizontalSpacing(12)
        site_grid.setVerticalSpacing(5)
        site_scroll.setWidget(site_content)
        sites_l.addWidget(site_scroll, 1)
        site_checks: dict[str, QCheckBox] = {}
        for index, item in enumerate(definitions):
            checkbox = QCheckBox(f"{item['name']}  ({item['key']})")
            checkbox.setToolTip(str(item.get("note", "")))
            site_checks[str(item["key"])] = checkbox
            site_grid.addWidget(checkbox, index // 2, index % 2)
        site_grid.setColumnStretch(0, 1)
        site_grid.setColumnStretch(1, 1)
        custom_sites = QLineEdit()
        custom_sites.setPlaceholderText(tr("Other extractor categories, separated by ;", "Category extractor lain, pisahkan dengan ;"))
        custom_sites.setToolTip(tr(
            "Add extractor category names not listed above. Use lowercase names from gallery-dl --list-extractors.",
            "Tambahkan nama category extractor yang tidak tersedia di atas. Gunakan nama lowercase dari gallery-dl --list-extractors.",
        ))
        sites_l.addWidget(custom_sites)
        tabs.addTab(sites_page, tr("Sites", "Situs"))

        # Preview --------------------------------------------------------------------
        preview_page = QWidget()
        preview_l = QVBoxLayout(preview_page)
        preview_l.setContentsMargins(8, 8, 8, 8)
        preview_tabs = QTabWidget()
        command_preview = QPlainTextEdit()
        command_preview.setReadOnly(True)
        command_preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        config_preview = QPlainTextEdit()
        config_preview.setReadOnly(True)
        config_preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        preview_tabs.addTab(command_preview, tr("Final command", "Command final"))
        preview_tabs.addTab(config_preview, tr("Merged config", "Config gabungan"))
        preview_l.addWidget(preview_tabs, 1)
        warnings = QLabel()
        warnings.setObjectName("subtle")
        warnings.setWordWrap(True)
        preview_l.addWidget(warnings)
        tabs.addTab(preview_page, tr("Preview", "Pratinjau"))

        existing_config, config_error = _read_json_config(self.config_path)
        # Only draft values created in Site Config Studio live here. They are
        # merged recursively into the existing config and are not persisted
        # until the user explicitly saves from the main Composer dialog.
        general_overrides: dict[str, object] = {}
        site_overrides: dict[str, dict[str, object]] = {}
        general_removals: set[str] = set()
        site_removals: dict[str, set[str]] = {}

        def apply_loaded_defaults() -> None:
            values = config_defaults(existing_config)
            if values.get("destination"):
                destination.setText(str(values["destination"]))
            if values.get("directory"):
                directory.setText("; ".join(values["directory"]))
            exact_destination.setChecked(bool(values.get("exact_destination", False)))
            if values.get("filename"):
                filename.setText(str(values["filename"]))
            browser_value = str(values.get("cookies_browser", "none"))
            if cookies_browser.findText(browser_value) >= 0:
                cookies_browser.setCurrentText(browser_value)
            cookies_profile.setText(str(values.get("cookies_profile", "")))
            cookies_domain.setText(str(values.get("cookies_domain", "")))
            cookies_file.setText(str(values.get("cookies_file", "")))
            cookies_update.setChecked(bool(values.get("cookies_update", True)))
            if cookies_file.text().strip():
                auth_method.setCurrentIndex(auth_method.findData("file"))
            elif browser_value != "none":
                auth_method.setCurrentIndex(auth_method.findData("browser"))
            archive_enabled.setChecked(bool(values.get("archive_enabled")))
            if values.get("archive_path"):
                archive_path.setText(str(values["archive_path"]))
            retries.setValue(_bounded_int(values.get("retries"), 0, 99))
            timeout.setValue(_bounded_int(values.get("timeout"), 0, 999))
            sleep_request.setText(str(values.get("sleep_request", "")))
            sleep_429.setText(str(values.get("sleep_429", "")))
            proxy.setText(str(values.get("proxy", "")))
            user_agent.setText(str(values.get("user_agent", "")))
            windows_filenames.setChecked(bool(values.get("windows_filenames", IS_WINDOWS)))
            metadata_json.setChecked(bool(values.get("metadata_json", False)))
            info_json.setChecked(bool(values.get("info_json", False)))
            archive_index = archive_format.findData(str(values.get("archive_format", "none")))
            if archive_index >= 0:
                archive_format.setCurrentIndex(archive_index)
            # Existing site blocks remain visible in the merged preview and are
            # preserved on save. Preset checkboxes start clear so merely opening
            # the Composer never replaces a user's hand-tuned site settings.

        def selected_auth_category() -> str:
            site_text = auth_site.currentText().strip().lower()
            index = auth_site.currentIndex()
            if index >= 0 and site_text == auth_site.itemText(index).strip().lower():
                return str(auth_site.itemData(index) or "").strip().lower()
            match = re.search(r"\(([^()]+)\)\s*$", site_text)
            return match.group(1) if match else re.sub(r"[^a-z0-9_.-]+", "", site_text)

        def populate_auth_sites(method: str, preserve: str = "") -> None:
            if method == "oauth":
                categories = list(OAUTH_SITE_CHOICES)
                include_global = False
            elif method == "credentials":
                categories = site_catalog
                include_global = False
            else:
                categories = site_catalog
                include_global = True
            wanted = str(preserve or selected_auth_category()).strip().lower()
            auth_site.blockSignals(True)
            try:
                auth_site.clear()
                if include_global:
                    auth_site.addItem(tr("All sites (global)", "Semua situs (global)"), "")
                for category in categories:
                    friendly = definition_names.get(category)
                    auth_site.addItem(
                        f"{friendly} ({category})" if friendly else category,
                        category,
                    )
                index = auth_site.findData(wanted)
                auth_site.setCurrentIndex(index if index >= 0 else 0)
            finally:
                auth_site.blockSignals(False)

        def current_state() -> ComposerState:
            auth_kind = str(auth_method.currentData())
            auth_category = selected_auth_category()
            browser = cookies_browser.currentText().strip().lower() if auth_kind == "browser" else "none"
            cookie_path = cookies_file.text().strip() if auth_kind == "file" else ""
            credential_mode = auth_kind == "credentials"
            extra_key = "" if extra_auth_key.currentIndex() == 0 else extra_auth_key.currentText().strip()
            return ComposerState(
                urls=[line.strip() for line in urls.toPlainText().splitlines() if line.strip()],
                apply_to_config=scope.currentData() == "config",
                use_active_config=True,
                destination=destination.text().strip(),
                exact_destination=exact_destination.isChecked(),
                directory=[part.strip() for part in re.split(r"[;\n]+", directory.text()) if part.strip()],
                filename=filename.text().strip(),
                cookies_browser=browser,
                cookies_profile=cookies_profile.text().strip() if auth_kind == "browser" else "",
                cookies_domain=cookies_domain.text().strip() if auth_kind == "browser" else "",
                cookies_file=cookie_path,
                cookies_update=cookies_update.isChecked(),
                auth_category=auth_category if auth_kind in {"browser", "file", "credentials"} else "",
                username=auth_username.text().strip() if credential_mode else "",
                secret_key=secret_key.currentText().strip() if credential_mode else "",
                secret_value=secret_value.text() if credential_mode else "",
                extra_auth_key=extra_key if credential_mode else "",
                extra_auth_value=extra_auth_value.text() if credential_mode and extra_key else "",
                archive_enabled=archive_enabled.isChecked(),
                archive_path=archive_path.text().strip(),
                retries=retries.value(),
                timeout=timeout.value(),
                sleep_request=sleep_request.text().strip(),
                sleep_429=sleep_429.text().strip(),
                file_range=file_range.text().strip(),
                date_after=date_after.text().strip(),
                date_before=date_before.text().strip(),
                filter_expr=filter_expr.text().strip(),
                proxy=proxy.text().strip(),
                user_agent=user_agent.text().strip(),
                mode=str(mode.currentData()),
                metadata_json=metadata_json.isChecked(),
                info_json=info_json.isChecked(),
                archive_format=str(archive_format.currentData()),
                windows_filenames=windows_filenames.isChecked(),
                no_input=no_input.isChecked(),
                extra_args=extra_args.text().strip(),
            )

        def selected_site_blocks(state: ComposerState) -> dict[str, dict]:
            selected = [key for key, checkbox in site_checks.items() if checkbox.isChecked()]
            blocks = self._selected_site_presets_to_config(
                selected,
                False,
                state.cookies_browser,
                state.cookies_file,
            )
            for category, block in self._custom_site_keys_to_config(custom_sites.text()).items():
                current = blocks.get(category, {})
                blocks[category] = _merge_dict(current, block)
            return blocks

        def proposed_config() -> dict:
            state = current_state()
            data = build_composer_config(
                state,
                site_blocks=selected_site_blocks(state),
                existing=existing_config,
            )
            return apply_config_editor_drafts(
                data,
                general_overrides=general_overrides,
                site_overrides=site_overrides,
                general_removals=general_removals,
                site_removals=site_removals,
            )

        def command_lines(*, redact: bool = False) -> list[str]:
            state = current_state()
            targets = state.urls or [""]
            lines: list[str] = []
            for target in targets:
                argv = build_composer_argv(state, target, config_path=self.config_path)
                if redact:
                    argv = redact_sensitive_argv(argv)
                lines.append(" ".join(quote_arg_for_preview(part) for part in argv))
            return lines

        def refresh_preview(*_args) -> None:
            state = current_state()
            command_preview.setPlainText("\n".join(command_lines(redact=True)))
            safe_config = redact_auth_config(
                proposed_config(),
                extra_keys=(state.secret_key, state.extra_auth_key),
            )
            config_preview.setPlainText(json.dumps(safe_config, indent=2, ensure_ascii=False))
            is_config = state.apply_to_config
            scope_badge.setText(tr("SAVED DEFAULTS", "DEFAULT TERSIMPAN") if is_config else tr("JOB OVERRIDE", "OVERRIDE JOB"))
            issues: list[str] = []
            if not state.urls:
                issues.append(tr("Add at least one URL before adding to the queue.", "Tambahkan minimal satu URL sebelum memasukkan ke antrean."))
            auth_kind = str(auth_method.currentData())
            if auth_kind == "file" and not state.cookies_file:
                issues.append(tr("Choose a valid cookies.txt file.", "Pilih file cookies.txt yang valid."))
            elif auth_kind == "file":
                cookies_ok, cookies_message = validate_cookies_txt(state.cookies_file)
                if not cookies_ok:
                    issues.append(tr("cookies.txt check: ", "Pemeriksaan cookies.txt: ") + cookies_message)
            if auth_kind == "credentials" and not state.auth_category:
                issues.append(tr("Credentials require a specific site/extractor, not Global.", "Credential memerlukan situs/extractor tertentu, bukan Global."))
            if auth_kind == "credentials" and not state.secret_value:
                issues.append(tr("Enter a password, API key, or token.", "Masukkan password, API key, atau token."))
            if auth_kind == "credentials" and not credential_ack.isChecked():
                issues.append(tr("Confirm the plain-text credential storage warning before saving.", "Konfirmasikan peringatan penyimpanan credential teks biasa sebelum menyimpan."))
            if state.archive_enabled and not state.archive_path:
                issues.append(tr("Archive is enabled but its path is empty.", "Archive aktif tetapi path-nya kosong."))
            if state.exact_destination and state.directory:
                issues.append(tr("Exact folder is enabled, so the subfolder pattern is ignored.", "Folder persis aktif, sehingga pola subfolder diabaikan."))
            if is_config and not self.config_path:
                issues.append(tr("Choose a config path before relying on saved defaults.", "Pilih path config sebelum memakai default tersimpan."))
            if config_error:
                issues.append(tr("The active config could not be merged: ", "Config aktif tidak dapat digabung: ") + config_error)
            warnings.setText("\n".join("! " + issue for issue in issues))

        def open_site_config_studio() -> None:  # noqa: C901 - guided tabbed editor
            studio = QDialog(dlg)
            studio.setObjectName("siteConfigStudio")
            studio.setWindowTitle(tr("Site Config Studio", "Studio Config Situs"))
            studio.resize(1040, 760)
            studio.setMinimumSize(860, 640)
            studio_l = QVBoxLayout(studio)
            studio_intro = QLabel(tr(
                "Build extractor-specific JSON without typing braces or option names. Changes remain a draft until you use Save Defaults in the Composer.",
                "Buat JSON khusus extractor tanpa mengetik kurung atau nama opsi. Perubahan tetap berupa draft sampai Anda menekan Simpan Default di Composer.",
            ))
            studio_intro.setWordWrap(True)
            studio_intro.setObjectName("subtle")
            studio_l.addWidget(studio_intro)

            studio_tabs = QTabWidget()
            studio_tabs.setObjectName("siteConfigTabs")
            studio_l.addWidget(studio_tabs, 1)
            existing_extractor = (
                existing_config.get("extractor", {})
                if isinstance(existing_config, dict)
                else {}
            )
            if not isinstance(existing_extractor, dict):
                existing_extractor = {}
            existing_site_names = {
                str(key).strip().lower()
                for key, value in existing_extractor.items()
                if isinstance(value, dict) and str(key).strip().lower() not in _GENERAL_OPTION_DEFAULTS
            }
            site_names = sorted(
                set(site_catalog)
                | {str(item.get("key") or "").strip().lower() for item in definitions}
                | existing_site_names
                | set(site_overrides)
                | {"kemono", "coomer", "reddit", "pixiv"}
                - {""}
            )

            def site_picker(object_name: str, default: str) -> QComboBox:
                picker = QComboBox()
                picker.setObjectName(object_name)
                picker.setEditable(False)
                picker.setInsertPolicy(QComboBox.NoInsert)
                picker.setMaxVisibleItems(24)
                picker.addItems(site_names)
                picker.setCurrentText(default)
                if picker.completer() is not None:
                    picker.completer().setCaseSensitivity(Qt.CaseInsensitive)
                    picker.completer().setFilterMode(Qt.MatchContains)
                return picker

            def normalized_site(picker: QComboBox) -> str:
                category = picker.currentText().strip().lower()
                if category not in site_names:
                    raise ValueError(tr(
                        "Choose an installed gallery-dl extractor from the list.",
                        "Pilih extractor gallery-dl terpasang dari daftar.",
                    ))
                return category

            def merge_override(category: str, block: dict[str, object]) -> None:
                site_overrides[category] = _merge_dict(site_overrides.get(category, {}), block)
                removed = site_removals.get(category)
                if removed:
                    removed.difference_update(block)
                    if not removed:
                        site_removals.pop(category, None)
                scope.setCurrentIndex(scope.findData("config"))
                refresh_preview()
                update_studio_preview()

            # Complete general/per-site option editor -------------------------
            all_options_page = QWidget()
            all_options_l = QVBoxLayout(all_options_page)
            all_options_l.setContentsMargins(10, 10, 10, 10)
            option_scope_row = QGridLayout()
            config_scope = QComboBox()
            config_scope.setObjectName("configScopeCombo")
            config_scope.addItem(tr("General defaults — all sites", "Default umum — semua situs"), "general")
            config_scope.addItem(tr("Per-site override", "Override per situs"), "site")
            config_site = site_picker("configEditorSiteCombo", "pixiv")
            config_option_search = QLineEdit()
            config_option_search.setObjectName("configOptionSearch")
            config_option_search.setPlaceholderText(tr(
                "Search option name, group, or description",
                "Cari nama opsi, grup, atau deskripsi",
            ))
            option_scope_row.addWidget(QLabel(tr("Level", "Tingkat")), 0, 0)
            option_scope_row.addWidget(config_scope, 0, 1)
            option_scope_row.addWidget(QLabel(tr("Site", "Situs")), 0, 2)
            option_scope_row.addWidget(config_site, 0, 3)
            option_scope_row.addWidget(config_option_search, 1, 0, 1, 4)
            option_scope_row.setColumnStretch(1, 1)
            option_scope_row.setColumnStretch(3, 1)
            all_options_l.addLayout(option_scope_row)

            config_option_table = QTableWidget(0, 6)
            config_option_table.setObjectName("configOptionTable")
            config_option_table.setHorizontalHeaderLabels([
                tr("Option", "Opsi"),
                tr("Current value", "Nilai saat ini"),
                tr("Source", "Sumber"),
                tr("Default", "Default"),
                tr("Type", "Tipe"),
                tr("Group", "Grup"),
            ])
            config_option_table.setSelectionBehavior(QTableWidget.SelectRows)
            config_option_table.setSelectionMode(QTableWidget.SingleSelection)
            config_option_table.setEditTriggers(QTableWidget.NoEditTriggers)
            config_option_table.verticalHeader().setVisible(False)
            option_header = config_option_table.horizontalHeader()
            option_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            option_header.setSectionResizeMode(1, QHeaderView.Stretch)
            option_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
            option_header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
            option_header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
            option_header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
            all_options_l.addWidget(config_option_table, 1)

            option_description = QLabel()
            option_description.setObjectName("configOptionDescription")
            option_description.setWordWrap(True)
            option_description.setMinimumHeight(38)
            all_options_l.addWidget(option_description)
            option_editor = QGridLayout()
            config_option_key = QComboBox()
            config_option_key.setObjectName("configOptionKey")
            config_option_key.setEditable(False)
            config_option_type = QComboBox()
            config_option_type.setObjectName("configOptionValueType")
            for label, value in (
                (tr("Text", "Teks"), "text"),
                (tr("Boolean", "Boolean"), "boolean"),
                (tr("Integer", "Integer"), "integer"),
                (tr("Number", "Angka"), "number"),
                ("JSON", "json"),
                ("Null", "null"),
            ):
                config_option_type.addItem(label, value)
            config_option_value = QComboBox()
            config_option_value.setObjectName("configOptionValue")
            config_option_value.setEditable(True)
            config_option_value.setInsertPolicy(QComboBox.NoInsert)
            config_option_value.lineEdit().setPlaceholderText(tr(
                "Choose or enter a typed value",
                "Pilih atau masukkan nilai sesuai tipe",
            ))
            config_option_show = QCheckBox(tr("Show sensitive value", "Tampilkan nilai sensitif"))
            config_option_ack = QCheckBox(tr(
                "Allow plain-text storage for this sensitive value",
                "Izinkan penyimpanan teks biasa untuk nilai sensitif ini",
            ))
            config_option_apply = QPushButton(tr("Set value", "Atur nilai"))
            config_option_apply.setObjectName("setConfigOption")
            config_option_remove = QPushButton(tr("Remove / inherit", "Hapus / warisi"))
            config_option_remove.setObjectName("removeConfigOption")
            option_editor.addWidget(QLabel(tr("Option", "Opsi")), 0, 0)
            option_editor.addWidget(config_option_key, 0, 1)
            option_editor.addWidget(config_option_type, 0, 2)
            option_editor.addWidget(QLabel(tr("Value", "Nilai")), 1, 0)
            option_editor.addWidget(config_option_value, 1, 1, 1, 2)
            option_editor.addWidget(config_option_show, 2, 1)
            option_editor.addWidget(config_option_ack, 2, 2)
            option_editor.addWidget(config_option_apply, 3, 1)
            option_editor.addWidget(config_option_remove, 3, 2)
            option_editor.setColumnStretch(1, 1)
            all_options_l.addLayout(option_editor)
            inheritance_note = QLabel(tr(
                "General values apply to every extractor. A per-site value overrides General only for that site. Remove / inherit deletes the site override so the General/default value is used again.",
                "Nilai Umum berlaku untuk semua extractor. Nilai per situs menimpa Umum hanya untuk situs tersebut. Hapus / warisi menghapus override situs sehingga nilai Umum/default dipakai lagi.",
            ))
            inheritance_note.setObjectName("subtle")
            inheritance_note.setWordWrap(True)
            all_options_l.addWidget(inheritance_note)
            studio_tabs.addTab(all_options_page, tr("All Options", "Semua Opsi"))

            # Archive database -------------------------------------------------
            archive_page = QWidget()
            archive_l = QGridLayout(archive_page)
            archive_l.setContentsMargins(12, 12, 12, 12)
            archive_site = site_picker("siteArchiveCategory", "kemono")
            site_archive_path = QLineEdit()
            site_archive_path.setObjectName("siteArchivePath")
            site_archive_path.setPlaceholderText("E:/RIPS/Database/kemono.sqlite3")
            site_archive_browse = QPushButton(tr("Browse…", "Pilih…"))
            site_duplicates = QCheckBox(tr(
                "Allow duplicate archive IDs (duplicates: true)",
                "Izinkan ID archive duplikat (duplicates: true)",
            ))
            site_archive_format = QLineEdit("{service}{user}{id}_{num}")
            site_archive_format.setObjectName("siteArchiveFormat")
            archive_apply = QPushButton(tr("Apply archive settings", "Terapkan pengaturan archive"))
            archive_apply.setObjectName("applySiteArchive")
            archive_load = QPushButton(tr("Load current", "Muat saat ini"))
            archive_note = QLabel(tr(
                "One SQLite file per site is supported. With duplicates unchecked, JSON writes \"duplicates\": false. The GUI also normalizes the copied \\_ example to _. Existing unknown site options are preserved.",
                "Satu file SQLite per situs didukung. Saat duplikat tidak dicentang, JSON menulis \"duplicates\": false. GUI juga menormalkan contoh \\_ yang disalin menjadi _. Opsi situs lain tetap dipertahankan.",
            ))
            archive_note.setWordWrap(True)
            archive_note.setObjectName("subtle")
            archive_l.addWidget(QLabel(tr("Site / extractor", "Situs / extractor")), 0, 0)
            archive_l.addWidget(archive_site, 0, 1, 1, 2)
            archive_l.addWidget(QLabel(tr("Archive database", "Database archive")), 1, 0)
            archive_l.addWidget(site_archive_path, 1, 1)
            archive_l.addWidget(site_archive_browse, 1, 2)
            archive_l.addWidget(QLabel("archive-format"), 2, 0)
            archive_l.addWidget(site_archive_format, 2, 1, 1, 2)
            archive_l.addWidget(site_duplicates, 3, 1, 1, 2)
            archive_l.addWidget(archive_note, 4, 0, 1, 3)
            archive_buttons = QHBoxLayout()
            archive_buttons.addWidget(archive_load)
            archive_buttons.addWidget(archive_apply)
            archive_buttons.addStretch(1)
            archive_l.addLayout(archive_buttons, 5, 0, 1, 3)
            archive_l.setColumnStretch(1, 1)
            archive_l.setRowStretch(6, 1)
            studio_tabs.addTab(archive_page, tr("Archive", "Archive"))

            # Reddit -----------------------------------------------------------
            reddit_page = QWidget()
            reddit_l = QGridLayout(reddit_page)
            reddit_l.setContentsMargins(12, 12, 12, 12)
            reddit_client_id = QLineEdit()
            reddit_client_id.setObjectName("redditClientId")
            reddit_client_id.setEchoMode(QLineEdit.Password)
            reddit_client_id.setPlaceholderText(tr(
                "Reddit application client ID (leave blank to keep existing)",
                "Client ID aplikasi Reddit (kosongkan untuk mempertahankan nilai lama)",
            ))
            reddit_user_agent = QLineEdit()
            reddit_user_agent.setObjectName("redditOAuthUserAgent")
            reddit_user_agent.setPlaceholderText("Python:Downloader:v1.01 (by /u/your_username)")
            reddit_show = QCheckBox(tr("Show client ID", "Tampilkan client ID"))
            reddit_apply = QPushButton(tr("Apply Reddit settings", "Terapkan pengaturan Reddit"))
            reddit_apply.setObjectName("applyRedditSettings")
            reddit_note = QLabel(tr(
                "Current gallery-dl uses client-id and user-agent-oauth for Reddit OAuth. The older plain user-agent key is not generated. After changing client-id, clear Reddit's cache from Accounts before reconnecting OAuth.",
                "gallery-dl saat ini memakai client-id dan user-agent-oauth untuk OAuth Reddit. Key user-agent lama tidak dibuat. Setelah mengubah client-id, bersihkan cache Reddit dari Accounts sebelum menyambungkan OAuth lagi.",
            ))
            reddit_note.setWordWrap(True)
            reddit_note.setObjectName("subtle")
            reddit_l.addWidget(QLabel("client-id"), 0, 0)
            reddit_l.addWidget(reddit_client_id, 0, 1)
            reddit_l.addWidget(reddit_show, 1, 1)
            reddit_l.addWidget(QLabel("user-agent-oauth"), 2, 0)
            reddit_l.addWidget(reddit_user_agent, 2, 1)
            reddit_l.addWidget(reddit_note, 3, 0, 1, 2)
            reddit_l.addWidget(reddit_apply, 4, 0, 1, 2, Qt.AlignLeft)
            reddit_l.setColumnStretch(1, 1)
            reddit_l.setRowStretch(5, 1)
            studio_tabs.addTab(reddit_page, "Reddit")

            # Pixiv ------------------------------------------------------------
            pixiv_scroll = QScrollArea()
            pixiv_scroll.setWidgetResizable(True)
            pixiv_scroll.setFrameShape(QFrame.NoFrame)
            pixiv_page = QWidget()
            pixiv_l = QVBoxLayout(pixiv_page)
            pixiv_l.setContentsMargins(12, 12, 12, 12)
            pixiv_scroll.setWidget(pixiv_page)
            include_label = QLabel(tr("Include", "Sertakan"))
            include_label.setObjectName("fieldLabel")
            pixiv_l.addWidget(include_label)
            pixiv_include_grid = QGridLayout()
            pixiv_include: dict[str, QCheckBox] = {}
            for index, value in enumerate((
                "artworks", "avatar", "background", "favorite",
                "novel-user", "novel-bookmark", "sketch",
            )):
                checkbox = QCheckBox(value)
                pixiv_include[value] = checkbox
                pixiv_include_grid.addWidget(checkbox, index // 3, index % 3)
            pixiv_l.addLayout(pixiv_include_grid)
            pixiv_flags = QGridLayout()
            pixiv_embeds = QCheckBox("embeds")
            pixiv_covers = QCheckBox("covers")
            pixiv_full_series = QCheckBox("full-series")
            pixiv_metadata = QCheckBox("metadata")
            for index, checkbox in enumerate((
                pixiv_embeds, pixiv_covers, pixiv_full_series, pixiv_metadata,
            )):
                pixiv_flags.addWidget(checkbox, index // 2, index % 2)
            pixiv_l.addLayout(pixiv_flags)
            pixiv_ugoira_row = QHBoxLayout()
            pixiv_ugoira_row.addWidget(QLabel("ugoira"))
            pixiv_ugoira = QComboBox()
            pixiv_ugoira.setObjectName("pixivUgoira")
            pixiv_ugoira.addItem(tr("Enabled", "Aktif"), True)
            pixiv_ugoira.addItem(tr("Disabled", "Nonaktif"), False)
            pixiv_ugoira.addItem(tr("Original frames", "Frame asli"), "original")
            pixiv_ugoira.setCurrentIndex(pixiv_ugoira.findData("original"))
            pixiv_ugoira_row.addWidget(pixiv_ugoira, 1)
            pixiv_l.addLayout(pixiv_ugoira_row)
            pixiv_secret_note = QLabel(tr(
                "Recommended: connect Pixiv through Accounts > OAuth so tokens stay in its private cache. The fields below exist only for importing a legacy manual config and will be stored as plain text in config.json.",
                "Disarankan: sambungkan Pixiv melalui Accounts > OAuth agar token tetap berada di cache privat. Field di bawah hanya untuk mengimpor config manual lama dan akan tersimpan sebagai teks biasa di config.json.",
            ))
            pixiv_secret_note.setWordWrap(True)
            pixiv_secret_note.setObjectName("subtle")
            pixiv_l.addWidget(pixiv_secret_note)
            pixiv_secret_grid = QGridLayout()
            pixiv_refresh_token = QLineEdit()
            pixiv_refresh_token.setObjectName("pixivRefreshToken")
            pixiv_refresh_token.setEchoMode(QLineEdit.Password)
            pixiv_refresh_token.setPlaceholderText(tr("Optional; blank keeps existing", "Opsional; kosong mempertahankan nilai lama"))
            pixiv_phpsessid = QLineEdit()
            pixiv_phpsessid.setObjectName("pixivPhpsessid")
            pixiv_phpsessid.setEchoMode(QLineEdit.Password)
            pixiv_phpsessid.setPlaceholderText(tr("Optional; blank keeps existing", "Opsional; kosong mempertahankan nilai lama"))
            pixiv_show = QCheckBox(tr("Show manual credentials", "Tampilkan credential manual"))
            pixiv_secret_ack = QCheckBox(tr(
                "I understand new values are saved in plain text",
                "Saya memahami nilai baru disimpan sebagai teks biasa",
            ))
            pixiv_secret_grid.addWidget(QLabel("refresh-token"), 0, 0)
            pixiv_secret_grid.addWidget(pixiv_refresh_token, 0, 1)
            pixiv_secret_grid.addWidget(QLabel("cookies.PHPSESSID"), 1, 0)
            pixiv_secret_grid.addWidget(pixiv_phpsessid, 1, 1)
            pixiv_secret_grid.addWidget(pixiv_show, 2, 1)
            pixiv_secret_grid.addWidget(pixiv_secret_ack, 3, 1)
            pixiv_secret_grid.setColumnStretch(1, 1)
            pixiv_l.addLayout(pixiv_secret_grid)
            pixiv_apply = QPushButton(tr("Apply Pixiv settings", "Terapkan pengaturan Pixiv"))
            pixiv_apply.setObjectName("applyPixivSettings")
            pixiv_l.addWidget(pixiv_apply, 0, Qt.AlignLeft)
            pixiv_l.addStretch(1)
            studio_tabs.addTab(pixiv_scroll, "Pixiv")

            # Typed advanced option -------------------------------------------
            advanced_page = QWidget()
            advanced_l = QGridLayout(advanced_page)
            advanced_l.setContentsMargins(12, 12, 12, 12)
            advanced_site = site_picker("advancedSiteCategory", "pixiv")
            advanced_key = QLineEdit()
            advanced_key.setObjectName("advancedSiteOptionKey")
            advanced_key.setPlaceholderText("option-name")
            advanced_type = QComboBox()
            advanced_type.setObjectName("advancedSiteOptionType")
            for label, value in (
                (tr("Text", "Teks"), "text"),
                (tr("Boolean", "Boolean"), "boolean"),
                (tr("Integer", "Integer"), "integer"),
                (tr("Number", "Angka"), "number"),
                ("JSON", "json"),
                ("Null", "null"),
            ):
                advanced_type.addItem(label, value)
            advanced_value = QLineEdit()
            advanced_value.setObjectName("advancedSiteOptionValue")
            advanced_value.setPlaceholderText(tr("Typed value", "Nilai sesuai tipe"))
            advanced_show = QCheckBox(tr("Show sensitive value", "Tampilkan nilai sensitif"))
            advanced_ack = QCheckBox(tr(
                "Allow plain-text storage when the option is sensitive",
                "Izinkan penyimpanan teks biasa jika opsi bersifat sensitif",
            ))
            advanced_apply = QPushButton(tr("Apply typed option", "Terapkan opsi bertipe"))
            advanced_apply.setObjectName("applyAdvancedSiteOption")
            advanced_note = QLabel(tr(
                "Use the exact option name from the official gallery-dl documentation. JSON accepts lists or objects and is parsed as data only; it is never executed.",
                "Gunakan nama opsi yang tepat dari dokumentasi resmi gallery-dl. JSON menerima list atau object dan hanya diparsing sebagai data; nilainya tidak pernah dieksekusi.",
            ))
            advanced_note.setWordWrap(True)
            advanced_note.setObjectName("subtle")
            advanced_l.addWidget(QLabel(tr("Site / extractor", "Situs / extractor")), 0, 0)
            advanced_l.addWidget(advanced_site, 0, 1, 1, 2)
            advanced_l.addWidget(QLabel(tr("Option key", "Nama opsi")), 1, 0)
            advanced_l.addWidget(advanced_key, 1, 1)
            advanced_l.addWidget(advanced_type, 1, 2)
            advanced_l.addWidget(QLabel(tr("Value", "Nilai")), 2, 0)
            advanced_l.addWidget(advanced_value, 2, 1, 1, 2)
            advanced_l.addWidget(advanced_show, 3, 1, 1, 2)
            advanced_l.addWidget(advanced_ack, 4, 1, 1, 2)
            advanced_l.addWidget(advanced_note, 5, 0, 1, 3)
            advanced_l.addWidget(advanced_apply, 6, 0, 1, 3, Qt.AlignLeft)
            advanced_l.setColumnStretch(1, 1)
            advanced_l.setRowStretch(7, 1)
            studio_tabs.addTab(advanced_page, tr("Advanced", "Lanjutan"))

            # Safe draft preview ----------------------------------------------
            draft_page = QWidget()
            draft_l = QVBoxLayout(draft_page)
            draft_preview = QPlainTextEdit()
            draft_preview.setObjectName("siteConfigDraftPreview")
            draft_preview.setReadOnly(True)
            draft_preview.setLineWrapMode(QPlainTextEdit.NoWrap)
            draft_l.addWidget(QLabel(tr(
                "Effective extractor config (credentials hidden)",
                "Config extractor efektif (credential disembunyikan)",
            )))
            draft_l.addWidget(draft_preview, 1)
            reset_draft = QPushButton(tr("Discard all Site Studio draft changes", "Buang semua perubahan draft Studio Situs"))
            draft_l.addWidget(reset_draft, 0, Qt.AlignLeft)
            studio_tabs.addTab(draft_page, tr("Safe Preview", "Preview Aman"))

            def update_studio_preview() -> None:
                effective = proposed_config().get("extractor", {})
                safe = redact_auth_config(effective)
                draft_preview.setPlainText(json.dumps(safe, indent=2, ensure_ascii=False))

            def selected_config_category() -> str:
                if config_scope.currentData() == "general":
                    return ""
                return normalized_site(config_site)

            def config_value_text(value: object) -> str:
                if value is None:
                    return ""
                if isinstance(value, bool):
                    return "true" if value else "false"
                if isinstance(value, (dict, list)):
                    return json.dumps(value, ensure_ascii=False)
                return str(value)

            def current_config_value(category: str, spec: ConfigOptionSpec) -> tuple[object, str, bool]:
                extractor = proposed_config().get("extractor", {})
                if not isinstance(extractor, dict):
                    extractor = {}
                if category:
                    block = extractor.get(category, {})
                    if isinstance(block, dict) and spec.key in block:
                        return block[spec.key], tr("Site override", "Override situs"), True
                    if spec.key in extractor:
                        return extractor[spec.key], tr("Inherited General", "Warisan Umum"), False
                elif spec.key in extractor:
                    return extractor[spec.key], tr("General", "Umum"), True
                return spec.default, tr("Built-in default", "Default bawaan"), False

            def selected_config_spec() -> ConfigOptionSpec | None:
                category = selected_config_category()
                key = str(config_option_key.currentData() or config_option_key.currentText()).strip()
                return next((item for item in config_option_definitions(category) if item.key == key), None)

            def configure_config_option(*_args) -> None:
                spec = selected_config_spec()
                if spec is None:
                    option_description.clear()
                    config_option_value.clear()
                    config_option_apply.setEnabled(False)
                    config_option_remove.setEnabled(False)
                    return
                config_option_apply.setEnabled(True)
                config_option_remove.setEnabled(True)
                category = selected_config_category()
                value, source, explicitly_set = current_config_value(category, spec)
                type_index = config_option_type.findData(spec.value_type)
                config_option_type.setCurrentIndex(max(0, type_index))
                config_option_value.blockSignals(True)
                try:
                    config_option_value.clear()
                    for choice in spec.choices:
                        config_option_value.addItem(config_value_text(choice), choice)
                    editor = config_option_value.lineEdit()
                    editor.clear()
                    if spec.sensitive:
                        editor.setPlaceholderText(tr(
                            "Stored value is hidden; enter a replacement or use Remove",
                            "Nilai tersimpan disembunyikan; masukkan pengganti atau gunakan Hapus",
                        ) if explicitly_set else tr(
                            "Sensitive value (stored as plain text)",
                            "Nilai sensitif (disimpan sebagai teks biasa)",
                        ))
                    else:
                        editor.setText(config_value_text(value))
                        editor.setPlaceholderText(tr("Enter a value", "Masukkan nilai"))
                finally:
                    config_option_value.blockSignals(False)
                config_option_show.setVisible(spec.sensitive)
                config_option_ack.setVisible(spec.sensitive)
                config_option_ack.setChecked(False)
                config_option_value.lineEdit().setEchoMode(
                    QLineEdit.Normal if not spec.sensitive or config_option_show.isChecked()
                    else QLineEdit.Password
                )
                scope_name = tr("General option", "Opsi Umum") if spec.scope == "general" else tr(
                    "Site-specific option", "Opsi khusus situs"
                )
                option_description.setText(
                    f"{scope_name} · {source} · {spec.group}\n{spec.description}"
                )

            def reload_config_options(*_args) -> None:
                try:
                    category = selected_config_category()
                except ValueError:
                    return
                config_site.setEnabled(bool(category))
                specs = config_option_definitions(category)
                previous = str(config_option_key.currentData() or config_option_key.currentText()).strip()
                config_option_key.blockSignals(True)
                try:
                    config_option_key.clear()
                    for spec in specs:
                        config_option_key.addItem(spec.key, spec.key)
                    previous_index = config_option_key.findData(previous)
                    if previous_index >= 0:
                        config_option_key.setCurrentIndex(previous_index)
                finally:
                    config_option_key.blockSignals(False)

                needle = config_option_search.text().strip().lower()
                visible_specs = [
                    spec for spec in specs
                    if not needle or needle in " ".join((
                        spec.key, spec.group, spec.description,
                    )).lower()
                ]
                config_option_table.setSortingEnabled(False)
                config_option_table.setRowCount(len(visible_specs))
                for row, spec in enumerate(visible_specs):
                    value, source, _explicit = current_config_value(category, spec)
                    default_text = config_value_text(spec.default)
                    current_text = "<hidden>" if spec.sensitive and value is not None else config_value_text(value)
                    values = (
                        spec.key,
                        current_text,
                        source,
                        default_text,
                        spec.value_type,
                        spec.group,
                    )
                    for column, cell in enumerate(values):
                        item = QTableWidgetItem(cell)
                        if column == 0:
                            item.setData(Qt.UserRole, spec.key)
                        if spec.sensitive and column in {1, 3}:
                            item.setToolTip(tr("Sensitive value is never revealed here.", "Nilai sensitif tidak pernah ditampilkan di sini."))
                        config_option_table.setItem(row, column, item)
                config_option_table.setSortingEnabled(True)
                configure_config_option()

            def choose_table_option(row: int, _column: int) -> None:
                item = config_option_table.item(row, 0)
                if item is None:
                    return
                key = str(item.data(Qt.UserRole) or item.text())
                index = config_option_key.findData(key)
                if index >= 0:
                    config_option_key.setCurrentIndex(index)

            def apply_config_option() -> None:
                try:
                    category = selected_config_category()
                    spec = selected_config_spec()
                    if spec is None:
                        raise ValueError(tr("Choose an option first.", "Pilih opsi terlebih dahulu."))
                    raw_value = config_option_value.currentText()
                    if spec.sensitive and not raw_value:
                        raise ValueError(tr(
                            "Enter a replacement value, or use Remove / inherit.",
                            "Masukkan nilai pengganti, atau gunakan Hapus / warisi.",
                        ))
                    if spec.sensitive and not config_option_ack.isChecked():
                        raise ValueError(tr(
                            "Confirm plain-text storage for this sensitive option.",
                            "Konfirmasikan penyimpanan teks biasa untuk opsi sensitif ini.",
                        ))
                    value = parse_typed_config_value(raw_value, str(config_option_type.currentData()))
                except ValueError as exc:
                    self.show_compact_message(tr("Config option", "Opsi config"), str(exc), "warning")
                    return
                if category:
                    block = site_overrides.setdefault(category, {})
                    block[spec.key] = value
                    removed = site_removals.get(category)
                    if removed:
                        removed.discard(spec.key)
                        if not removed:
                            site_removals.pop(category, None)
                else:
                    general_overrides[spec.key] = value
                    general_removals.discard(spec.key)
                scope.setCurrentIndex(scope.findData("config"))
                if spec.sensitive:
                    config_option_value.clear()
                    config_option_ack.setChecked(False)
                refresh_preview()
                update_studio_preview()
                reload_config_options()

            def remove_config_option() -> None:
                try:
                    category = selected_config_category()
                    spec = selected_config_spec()
                except ValueError:
                    return
                if spec is None:
                    return
                if category:
                    block = site_overrides.get(category)
                    if block is not None:
                        block.pop(spec.key, None)
                        if not block:
                            site_overrides.pop(category, None)
                    site_removals.setdefault(category, set()).add(spec.key)
                else:
                    general_overrides.pop(spec.key, None)
                    general_removals.add(spec.key)
                scope.setCurrentIndex(scope.findData("config"))
                refresh_preview()
                update_studio_preview()
                reload_config_options()

            def toggle_config_secret(visible: bool) -> None:
                spec = selected_config_spec()
                config_option_value.lineEdit().setEchoMode(
                    QLineEdit.Normal if visible or spec is None or not spec.sensitive
                    else QLineEdit.Password
                )

            def load_archive() -> None:
                try:
                    category = normalized_site(archive_site)
                except ValueError as exc:
                    self.show_compact_message(tr("Archive settings", "Pengaturan archive"), str(exc), "warning")
                    return
                extractor = proposed_config().get("extractor", {})
                block = extractor.get(category, {}) if isinstance(extractor, dict) else {}
                if not isinstance(block, dict):
                    block = {}
                site_archive_path.setText(str(block.get("archive") or ""))
                site_archive_format.setText(str(block.get("archive-format") or "{service}{user}{id}_{num}"))
                site_duplicates.setChecked(bool(block.get("duplicates", False)))

            def browse_site_archive() -> None:
                chosen, _ = QFileDialog.getSaveFileName(
                    studio,
                    tr("Choose site archive database", "Pilih database archive situs"),
                    site_archive_path.text(),
                    "SQLite (*.sqlite3 *.sqlite *.db);;All files (*.*)",
                )
                if chosen:
                    site_archive_path.setText(chosen)

            def apply_archive() -> None:
                try:
                    category = normalized_site(archive_site)
                    block = site_archive_options(
                        site_archive_path.text(),
                        duplicates=site_duplicates.isChecked(),
                        archive_format=site_archive_format.text(),
                    )
                except ValueError as exc:
                    self.show_compact_message(tr("Archive settings", "Pengaturan archive"), str(exc), "warning")
                    return
                merge_override(category, block)

            def apply_reddit() -> None:
                block = reddit_site_options(reddit_client_id.text(), reddit_user_agent.text())
                if not block:
                    self.show_compact_message("Reddit", tr("Enter at least one Reddit setting.", "Masukkan minimal satu pengaturan Reddit."), "warning")
                    return
                merge_override("reddit", block)
                reddit_client_id.clear()

            def load_reddit() -> None:
                extractor = proposed_config().get("extractor", {})
                block = extractor.get("reddit", {}) if isinstance(extractor, dict) else {}
                if not isinstance(block, dict):
                    block = {}
                reddit_user_agent.setText(str(block.get("user-agent-oauth") or ""))
                if block.get("client-id"):
                    reddit_client_id.setPlaceholderText(tr(
                        "An existing client ID is stored; blank keeps it",
                        "Client ID lama sudah tersimpan; kosong mempertahankannya",
                    ))

            def toggle_pixiv_secrets(visible: bool) -> None:
                echo = QLineEdit.Normal if visible else QLineEdit.Password
                pixiv_refresh_token.setEchoMode(echo)
                pixiv_phpsessid.setEchoMode(echo)

            def apply_pixiv() -> None:
                block = pixiv_site_options(
                    include=[key for key, checkbox in pixiv_include.items() if checkbox.isChecked()],
                    embeds=pixiv_embeds.isChecked(),
                    covers=pixiv_covers.isChecked(),
                    full_series=pixiv_full_series.isChecked(),
                    metadata=pixiv_metadata.isChecked(),
                    ugoira=pixiv_ugoira.currentData(),
                )
                refresh_value = pixiv_refresh_token.text()
                cookie_value = pixiv_phpsessid.text()
                if (refresh_value or cookie_value) and not pixiv_secret_ack.isChecked():
                    self.show_compact_message(
                        "Pixiv",
                        tr("Confirm the plain-text credential warning first.", "Konfirmasikan peringatan credential teks biasa terlebih dahulu."),
                        "warning",
                    )
                    return
                if refresh_value:
                    block["refresh-token"] = refresh_value
                if cookie_value:
                    block["cookies"] = {"PHPSESSID": cookie_value}
                merge_override("pixiv", block)
                pixiv_refresh_token.clear()
                pixiv_phpsessid.clear()
                pixiv_secret_ack.setChecked(False)

            def load_pixiv() -> None:
                extractor = proposed_config().get("extractor", {})
                block = extractor.get("pixiv", {}) if isinstance(extractor, dict) else {}
                if not isinstance(block, dict):
                    block = {}
                included = block.get("include", [])
                if isinstance(included, str):
                    included = [included]
                included_set = {
                    str(item).strip().lower()
                    for item in included
                } if isinstance(included, list) else set()
                for key, checkbox in pixiv_include.items():
                    checkbox.setChecked(key in included_set)
                pixiv_embeds.setChecked(bool(block.get("embeds", False)))
                pixiv_covers.setChecked(bool(block.get("covers", False)))
                pixiv_full_series.setChecked(bool(block.get("full-series", False)))
                pixiv_metadata.setChecked(bool(block.get("metadata", False)))
                ugoira_value = block.get("ugoira", "original")
                ugoira_index = pixiv_ugoira.findData(ugoira_value)
                pixiv_ugoira.setCurrentIndex(max(0, ugoira_index))
                if block.get("refresh-token"):
                    pixiv_refresh_token.setPlaceholderText(tr(
                        "An existing refresh token is stored; blank keeps it",
                        "Refresh token lama sudah tersimpan; kosong mempertahankannya",
                    ))
                cookies = block.get("cookies", {})
                if isinstance(cookies, dict) and cookies.get("PHPSESSID"):
                    pixiv_phpsessid.setPlaceholderText(tr(
                        "An existing PHPSESSID is stored; blank keeps it",
                        "PHPSESSID lama sudah tersimpan; kosong mempertahankannya",
                    ))

            def update_advanced_visibility(*_args) -> None:
                sensitive = is_sensitive_option_key(advanced_key.text().strip())
                advanced_show.setVisible(sensitive)
                advanced_ack.setVisible(sensitive)
                advanced_value.setEchoMode(
                    QLineEdit.Normal if not sensitive or advanced_show.isChecked() else QLineEdit.Password
                )

            def apply_advanced() -> None:
                try:
                    category = normalized_site(advanced_site)
                    key = advanced_key.text().strip()
                    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", key):
                        raise ValueError(tr("Enter a valid option key.", "Masukkan nama opsi yang valid."))
                    if is_sensitive_option_key(key) and not advanced_ack.isChecked():
                        raise ValueError(tr(
                            "Confirm plain-text storage for this sensitive option.",
                            "Konfirmasikan penyimpanan teks biasa untuk opsi sensitif ini.",
                        ))
                    value = parse_typed_config_value(advanced_value.text(), str(advanced_type.currentData()))
                except ValueError as exc:
                    self.show_compact_message(tr("Advanced option", "Opsi lanjutan"), str(exc), "warning")
                    return
                merge_override(category, {key: value})
                advanced_value.clear()

            def discard_draft() -> None:
                general_overrides.clear()
                site_overrides.clear()
                general_removals.clear()
                site_removals.clear()
                refresh_preview()
                update_studio_preview()
                reload_config_options()

            site_archive_browse.clicked.connect(browse_site_archive)
            archive_load.clicked.connect(load_archive)
            archive_apply.clicked.connect(apply_archive)
            reddit_show.toggled.connect(
                lambda checked: reddit_client_id.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
            )
            reddit_apply.clicked.connect(apply_reddit)
            pixiv_show.toggled.connect(toggle_pixiv_secrets)
            pixiv_apply.clicked.connect(apply_pixiv)
            advanced_key.textChanged.connect(update_advanced_visibility)
            advanced_show.toggled.connect(update_advanced_visibility)
            advanced_apply.clicked.connect(apply_advanced)
            reset_draft.clicked.connect(discard_draft)
            config_scope.currentIndexChanged.connect(reload_config_options)
            config_site.currentIndexChanged.connect(reload_config_options)
            config_option_search.textChanged.connect(reload_config_options)
            config_option_key.currentIndexChanged.connect(configure_config_option)
            config_option_table.cellClicked.connect(choose_table_option)
            config_option_show.toggled.connect(toggle_config_secret)
            config_option_apply.clicked.connect(apply_config_option)
            config_option_remove.clicked.connect(remove_config_option)

            footer = QHBoxLayout()
            footer.addStretch(1)
            done = QPushButton(tr("Done", "Selesai"))
            done.setObjectName("siteConfigDone")
            done.clicked.connect(studio.accept)
            footer.addWidget(done)
            studio_l.addLayout(footer)
            update_advanced_visibility()
            load_archive()
            load_reddit()
            load_pixiv()
            update_studio_preview()
            reload_config_options()
            studio.exec()

        def browse_folder() -> None:
            chosen = QFileDialog.getExistingDirectory(dlg, tr("Choose download folder", "Pilih folder download"), destination.text())
            if chosen:
                destination.setText(chosen)

        def browse_cookie_file() -> None:
            chosen, _ = QFileDialog.getOpenFileName(dlg, tr("Choose cookies.txt", "Pilih cookies.txt"), "", "Cookies (*.txt);;All files (*.*)")
            if chosen:
                cookies_file.setText(chosen)
                valid, message = validate_cookies_txt(chosen)
                self.show_compact_message(
                    "cookies.txt",
                    message,
                    "info" if valid else "warning",
                )

        def browse_archive_file() -> None:
            chosen, _ = QFileDialog.getSaveFileName(dlg, tr("Choose archive database", "Pilih database archive"), archive_path.text(), "SQLite (*.sqlite3 *.sqlite *.db);;All files (*.*)")
            if chosen:
                archive_path.setText(chosen)

        def update_auth_method(*_args) -> None:
            index = max(0, auth_method.currentIndex())
            auth_stack.setCurrentIndex(index)
            method = str(auth_method.currentData())
            help_texts = {
                "none": tr(
                    "Public mode adds no authentication data.",
                    "Mode publik tidak menambahkan data autentikasi.",
                ),
                "browser": tr(
                    "Recommended: log in normally in the selected browser, then let gallery-dl read that profile when the job starts.",
                    "Disarankan: login seperti biasa di browser terpilih, lalu biarkan gallery-dl membaca profil tersebut saat job dimulai.",
                ),
                "file": tr(
                    "Choose an exported Mozilla/Netscape cookies.txt file. JSON cookie exports are not accepted.",
                    "Pilih file cookies.txt hasil ekspor dalam format Mozilla/Netscape. Ekspor cookie JSON tidak dapat digunakan.",
                ),
                "credentials": tr(
                    "Select a specific site. Credentials are never placed in commands; they must be confirmed and saved to config before testing.",
                    "Pilih situs tertentu. Credential tidak pernah dimasukkan ke command; data harus dikonfirmasi dan disimpan ke config sebelum pengujian.",
                ),
                "oauth": tr(
                    "Start the gallery-dl OAuth helper. It opens the official authorization page in your browser.",
                    "Jalankan helper OAuth gallery-dl. Proses ini membuka halaman otorisasi resmi di browser.",
                ),
            }
            auth_method_help.setText(help_texts.get(method, ""))
            preserve = selected_auth_category()
            populate_auth_sites(method, preserve)
            if method == "credentials":
                scope.setCurrentIndex(scope.findData("config"))
            if method == "oauth":
                category = selected_auth_category()
                oauth_index = oauth_site.findData(category)
                if oauth_index >= 0:
                    oauth_site.setCurrentIndex(oauth_index)
            refresh_preview()

        def sync_oauth_site(*_args) -> None:
            if auth_method.currentData() == "oauth":
                category = selected_auth_category()
                oauth_index = oauth_site.findData(category)
                if oauth_index >= 0 and oauth_index != oauth_site.currentIndex():
                    oauth_site.setCurrentIndex(oauth_index)
            refresh_preview()

        def sync_auth_site_from_oauth(*_args) -> None:
            category = str(oauth_site.currentData() or "")
            oauth_instance.setEnabled(category == "mastodon")
            oauth_response.setEnabled(category == "pixiv")
            oauth_send.setEnabled(
                category == "pixiv" and oauth_process.state() != QProcess.NotRunning
            )
            if auth_method.currentData() == "oauth":
                index = auth_site.findData(category)
                if index >= 0 and index != auth_site.currentIndex():
                    auth_site.setCurrentIndex(index)
            refresh_preview()

        def toggle_secret_visibility(visible: bool) -> None:
            echo = QLineEdit.Normal if visible else QLineEdit.Password
            secret_value.setEchoMode(echo)
            extra_auth_value.setEchoMode(echo)

        def prepare_safe_test() -> None:
            mode.setCurrentIndex(mode.findData("simulate"))
            tabs.setCurrentWidget(main_scroll)
            urls.setFocus()
            self.show_compact_message(
                tr("Safe login test", "Uji login aman"),
                tr(
                    "Mode is now Simulate only. Add a URL, add it to the queue, then run DOWNLOAD. gallery-dl will test extraction without saving media files.",
                    "Mode sekarang Hanya simulasi. Tambahkan URL, masukkan ke antrean, lalu jalankan DOWNLOAD. gallery-dl akan menguji ekstraksi tanpa menyimpan file media.",
                ),
                "info",
            )

        oauth_process = QProcess(dlg)
        oauth_process.setProcessChannelMode(QProcess.MergedChannels)
        oauth_output_buffer: list[str] = [""]

        def oauth_parts() -> list[str]:
            oauth_target = composer_oauth_target(
                str(oauth_site.currentData() or ""),
                oauth_instance.text(),
            )
            base = command_string_to_argv(self.gdl_cmd or "gallery-dl")
            return base + [oauth_target] if base else ["gallery-dl", oauth_target]

        def oauth_command_text() -> str:
            return " ".join(quote_arg_for_preview(part) for part in oauth_parts())

        def read_oauth_output() -> None:
            data = bytes(oauth_process.readAllStandardOutput()).decode("utf-8", errors="replace")
            if data:
                # OAuth output can arrive with the bare token in a later chunk
                # than its label. Buffer it until completion so no transient UI
                # update can expose the newly issued credential.
                oauth_output_buffer[0] += data

        def append_oauth_output(text: str) -> None:
            oauth_status.moveCursor(QTextCursor.End)
            oauth_status.insertPlainText(text)
            oauth_status.ensureCursorVisible()

        def oauth_finished(exit_code: int, _status) -> None:
            read_oauth_output()
            if oauth_output_buffer[0]:
                append_oauth_output(redact_oauth_output(oauth_output_buffer[0]))
                oauth_output_buffer[0] = ""
            oauth_start.setEnabled(True)
            oauth_stop.setEnabled(False)
            oauth_send.setEnabled(False)
            oauth_status.appendPlainText(tr(
                f"\nOAuth process finished with exit code {exit_code}. If authorization succeeded, test the site with Simulate only.",
                f"\nProses OAuth selesai dengan kode {exit_code}. Jika otorisasi berhasil, uji situs memakai Hanya simulasi.",
            ))

        def oauth_error(_error) -> None:
            if oauth_output_buffer[0]:
                append_oauth_output(redact_oauth_output(oauth_output_buffer[0]))
                oauth_output_buffer[0] = ""
            oauth_start.setEnabled(True)
            oauth_stop.setEnabled(False)
            oauth_send.setEnabled(False)
            oauth_status.appendPlainText(tr(
                "Could not start OAuth: ",
                "Tidak dapat memulai OAuth: ",
            ) + oauth_process.errorString())

        def start_oauth() -> None:
            try:
                parts = oauth_parts()
            except ValueError as exc:
                self.show_compact_message("OAuth", str(exc), "warning")
                return
            if oauth_process.state() != QProcess.NotRunning:
                return
            oauth_status.clear()
            oauth_output_buffer[0] = ""
            oauth_status.appendPlainText(tr(
                "Starting official gallery-dl OAuth flow...\nYour browser should open. Complete authorization there and return here.\n\n",
                "Memulai alur OAuth resmi gallery-dl...\nBrowser akan terbuka. Selesaikan otorisasi di sana lalu kembali ke sini.\n\n",
            ))
            oauth_start.setEnabled(False)
            oauth_stop.setEnabled(True)
            oauth_send.setEnabled(oauth_site.currentData() == "pixiv")
            oauth_process.setProgram(parts[0])
            oauth_process.setArguments(parts[1:])
            oauth_process.start()

        def stop_oauth() -> None:
            if oauth_process.state() != QProcess.NotRunning:
                oauth_process.terminate()
                if not oauth_process.waitForFinished(1200):
                    oauth_process.kill()

        def send_oauth_response() -> None:
            value = oauth_response.text().strip()
            if not value or oauth_process.state() == QProcess.NotRunning:
                return
            oauth_process.write((value + "\n").encode("utf-8"))
            oauth_response.clear()
            oauth_status.appendPlainText(tr(
                "Pixiv authorization response sent securely.",
                "Respons otorisasi Pixiv dikirim dengan aman.",
            ))

        def copy_oauth_command() -> None:
            try:
                command = oauth_command_text()
            except ValueError as exc:
                self.show_compact_message("OAuth", str(exc), "warning")
                return
            QApplication.clipboard().setText(command)
            self.show_compact_message("OAuth", tr("OAuth command copied.", "Command OAuth disalin."), "info")

        def cleanup_oauth(*_args) -> None:
            if oauth_process.state() != QProcess.NotRunning:
                oauth_process.terminate()
                if not oauth_process.waitForFinished(800):
                    oauth_process.kill()

        def remove_saved_auth_data() -> None:
            nonlocal existing_config, config_error
            category = selected_auth_category()
            extractor = existing_config.get("extractor", {}) if isinstance(existing_config, dict) else {}
            if not isinstance(extractor, dict):
                extractor = {}
            target_block = extractor.get(category, {}) if category else extractor
            if not isinstance(target_block, dict):
                target_block = {}
            managed_keys = set(MANAGED_AUTH_KEYS)
            managed_keys.update({secret_key.currentText().strip(), extra_auth_key.currentText().strip()})
            present = sorted(
                key for key in target_block
                if key in managed_keys or is_sensitive_option_key(key)
            )
            if not present:
                self.show_compact_message(
                    tr("Saved login", "Login tersimpan"),
                    tr("No managed login keys were found for this selection.", "Tidak ditemukan key login yang dikelola untuk pilihan ini."),
                    "info",
                )
                return
            label = category or tr("global extractor settings", "pengaturan extractor global")
            answer = QMessageBox.question(
                dlg,
                tr("Remove saved login", "Hapus login tersimpan"),
                tr(
                    f"Remove these keys from {label}?\n\n" + ", ".join(present) + "\n\nA backup will be created.",
                    f"Hapus key berikut dari {label}?\n\n" + ", ".join(present) + "\n\nBackup akan dibuat.",
                ),
            )
            if answer != QMessageBox.Yes:
                return
            target = Path(self.config_path or detect_config_path() or str(APP_DIR / "config.json")).expanduser()
            try:
                cleaned = copy.deepcopy(existing_config)
                if not isinstance(cleaned.get("extractor"), dict):
                    cleaned["extractor"] = {}
                cleaned_extractor = cleaned["extractor"]
                cleaned_block = cleaned_extractor.get(category, {}) if category else cleaned_extractor
                for key in present:
                    cleaned_block.pop(key, None)
                if category and isinstance(cleaned_block, dict) and not cleaned_block:
                    cleaned_extractor.pop(category, None)
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    backup = unique_path(target.with_name(target.name + f".bak_{timestamp_slug()}"))
                    shutil.copy2(target, backup)
                    try:
                        backup.chmod(0o600)
                    except OSError:
                        pass
                    self.append_log(f"[config] backup created: {backup}")
                atomic_write_text(
                    target,
                    json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n",
                    mode=0o600,
                )
                self.config_path = str(target)
                existing_config = cleaned
                config_error = None
                self._refresh_env()
                auth_method.setCurrentIndex(auth_method.findData("none"))
                auth_username.clear()
                secret_value.clear()
                extra_auth_value.clear()
                refresh_preview()
                self.show_compact_message(
                    tr("Saved login removed", "Login tersimpan dihapus"),
                    tr(f"Updated:\n{target}", f"Diperbarui:\n{target}"),
                    "info",
                )
            except Exception as exc:
                self.show_compact_message(tr("Remove login failed", "Hapus login gagal"), str(exc), "error")

        def apply_recipe(name: str) -> None:
            key = recipe.currentIndex()
            if key == 1:
                sleep_request.setText("2.0-5.0")
                sleep_429.setText("120-600")
                retries.setValue(max(6, retries.value()))
            elif key == 2:
                auth_method.setCurrentIndex(auth_method.findData("browser"))
                preferred = gui_browser if gui_browser != "none" else "firefox"
                if cookies_browser.findText(preferred) >= 0:
                    cookies_browser.setCurrentText(preferred)
            elif key == 3:
                archive_enabled.setChecked(True)
            elif key == 4:
                metadata_json.setChecked(True)
                info_json.setChecked(True)
            elif key == 5:
                index = mode.findData("keywords")
                mode.setCurrentIndex(index)
            refresh_preview()

        def filter_sites(text: str) -> None:
            needle = text.strip().lower()
            for key, checkbox in site_checks.items():
                checkbox.setVisible(not needle or needle in key.lower() or needle in checkbox.text().lower())

        def ensure_urls() -> bool:
            state = current_state()
            if not state.urls:
                self.show_compact_message(tr("Download Composer", "Perancang Download"), tr("Add at least one URL.", "Tambahkan minimal satu URL."), "warning")
                return False
            invalid = [target for target in state.urls if target.startswith("-") or any(char.isspace() for char in target)]
            if invalid:
                self.show_compact_message(
                    tr("Invalid URL", "URL tidak valid"),
                    tr("Each URL must be on one line and cannot begin with an option flag.", "Setiap URL harus berada di satu baris dan tidak boleh diawali flag opsi."),
                    "warning",
                )
                return False
            return True

        def add_to_queue() -> None:
            if not ensure_urls():
                return
            state = current_state()
            if state.cookies_file:
                valid, message = validate_cookies_txt(state.cookies_file)
                if not valid:
                    self.show_compact_message("cookies.txt", message, "warning")
                    tabs.setCurrentWidget(auth_scroll)
                    return
            requires_saved_config = (
                state.apply_to_config
                or bool(state.secret_value or state.extra_auth_value)
                or bool(general_overrides or general_removals or site_removals)
                or bool(site_overrides)
            )
            if requires_saved_config and proposed_config() != existing_config:
                answer = QMessageBox.question(
                    dlg,
                    tr("Save defaults first", "Simpan default terlebih dahulu"),
                    tr(
                        "These shared settings are not saved yet. Save them to the active config before adding the job?",
                        "Pengaturan bersama ini belum disimpan. Simpan ke config aktif sebelum menambahkan job?",
                    ),
                )
                if answer != QMessageBox.Yes or not write_config(self.config_path or detect_config_path() or str(APP_DIR / "config.json")):
                    return
            lines = command_lines()
            current = self.txt_commands.toPlainText().strip()
            combined = "\n".join(lines)
            self.txt_commands.setPlainText(f"{current}\n{combined}".strip() if current else combined)
            self.append_log(f"[composer] appended {len(lines)} job(s) to input")
            self.show_compact_message(tr("Download Composer", "Perancang Download"), tr(f"Added {len(lines)} job(s) to the queue input.", f"{len(lines)} job ditambahkan ke input antrean."), "info")

        def copy_commands() -> None:
            QApplication.clipboard().setText("\n".join(command_lines()))
            self.show_compact_message(tr("Download Composer", "Perancang Download"), tr("Command copied.", "Command disalin."), "info")

        def write_config(path: str) -> bool:
            nonlocal existing_config, config_error
            target = Path(path).expanduser()
            try:
                state = current_state()
                if state.cookies_file:
                    valid, message = validate_cookies_txt(state.cookies_file)
                    if not valid:
                        self.show_compact_message("cookies.txt", message, "warning")
                        tabs.setCurrentWidget(auth_scroll)
                        return False
                if state.secret_value or state.extra_auth_value:
                    if not state.auth_category:
                        self.show_compact_message(
                            tr("Select a site", "Pilih situs"),
                            tr("Username/password and API credentials require a specific site/extractor.", "Username/password dan credential API memerlukan situs/extractor tertentu."),
                            "warning",
                        )
                        tabs.setCurrentWidget(auth_scroll)
                        return False
                    if not state.secret_key or not state.secret_value:
                        self.show_compact_message(
                            tr("Credential incomplete", "Credential belum lengkap"),
                            tr("Enter a secret option and value.", "Masukkan opsi dan nilai secret."),
                            "warning",
                        )
                        tabs.setCurrentWidget(auth_scroll)
                        return False
                    if not credential_ack.isChecked():
                        self.show_compact_message(
                            tr("Confirmation required", "Konfirmasi diperlukan"),
                            tr("Confirm the plain-text credential storage warning before saving.", "Konfirmasikan peringatan penyimpanan credential teks biasa sebelum menyimpan."),
                            "warning",
                        )
                        tabs.setCurrentWidget(auth_scroll)
                        return False
                data = proposed_config()
                json.dumps(data)
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    answer = QMessageBox.question(
                        dlg,
                        tr("Update config", "Perbarui config"),
                        tr(
                            f"Merge Composer settings into:\n{target}\n\nUnknown options are preserved and a backup is created. Config and backup may contain login secrets in plain text. Continue?",
                            f"Gabungkan pengaturan Composer ke:\n{target}\n\nOpsi yang tidak dikelola tetap dipertahankan dan backup dibuat. Config dan backup dapat berisi secret login dalam bentuk teks biasa. Lanjutkan?",
                        ),
                    )
                    if answer != QMessageBox.Yes:
                        return False
                    backup = unique_path(target.with_name(target.name + f".bak_{timestamp_slug()}"))
                    shutil.copy2(target, backup)
                    try:
                        backup.chmod(0o600)
                    except OSError:
                        pass
                    self.append_log(f"[config] backup created: {backup}")
                atomic_write_text(
                    target,
                    json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    mode=0o600,
                )
                self.config_path = str(target)
                config_path_label.setText(str(target))
                existing_config = data
                config_error = None
                general_overrides.clear()
                site_overrides.clear()
                general_removals.clear()
                site_removals.clear()
                self._refresh_env()
                scope.setCurrentIndex(scope.findData("config"))
                refresh_preview()
                self.show_compact_message("Config", tr(f"Saved:\n{target}", f"Tersimpan:\n{target}"), "info")
                return True
            except Exception as exc:
                self.show_compact_message(tr("Save config failed", "Simpan config gagal"), str(exc), "error")
                return False

        def save_active() -> None:
            path = self.config_path or detect_config_path() or str(APP_DIR / "config.json")
            write_config(path)

        def save_as() -> None:
            path, _ = QFileDialog.getSaveFileName(
                dlg,
                tr("Save gallery-dl config", "Simpan config gallery-dl"),
                self.config_path or str(APP_DIR / "config.json"),
                "JSON (*.json *.conf);;All files (*.*)",
            )
            if path:
                write_config(path)

        browse_destination.clicked.connect(browse_folder)
        browse_cookies.clicked.connect(browse_cookie_file)
        browse_archive.clicked.connect(browse_archive_file)
        recipe.currentTextChanged.connect(apply_recipe)
        auth_method.currentIndexChanged.connect(update_auth_method)
        auth_site.currentIndexChanged.connect(sync_oauth_site)
        oauth_site.currentIndexChanged.connect(sync_auth_site_from_oauth)
        oauth_instance.textChanged.connect(refresh_preview)
        show_secrets.toggled.connect(toggle_secret_visibility)
        prepare_test.clicked.connect(prepare_safe_test)
        remove_auth.clicked.connect(remove_saved_auth_data)
        oauth_start.clicked.connect(start_oauth)
        oauth_stop.clicked.connect(stop_oauth)
        oauth_send.clicked.connect(send_oauth_response)
        oauth_response.returnPressed.connect(send_oauth_response)
        oauth_copy.clicked.connect(copy_oauth_command)
        oauth_process.readyReadStandardOutput.connect(read_oauth_output)
        oauth_process.finished.connect(oauth_finished)
        oauth_process.errorOccurred.connect(oauth_error)
        dlg.finished.connect(cleanup_oauth)
        site_search.textChanged.connect(filter_sites)
        site_config_button.clicked.connect(open_site_config_studio)
        select_common.clicked.connect(lambda: _set_checks(site_checks, {"pixiv", "twitter", "instagram", "reddit", "deviantart", "danbooru", "gelbooru"}, refresh_preview))
        select_none.clicked.connect(lambda: _set_checks(site_checks, set(), refresh_preview))

        text_widgets: Iterable[QLineEdit] = (
            destination, directory, filename, cookies_file, archive_path,
            sleep_request, sleep_429, file_range, date_after, date_before,
            filter_expr, proxy, user_agent, extra_args, custom_sites,
            cookies_profile, cookies_domain, auth_username, secret_value,
            extra_auth_value,
        )
        for widget in text_widgets:
            widget.textChanged.connect(refresh_preview)
        urls.textChanged.connect(refresh_preview)
        for widget in (scope, cookies_browser, mode, archive_format, secret_key, extra_auth_key):
            widget.currentIndexChanged.connect(refresh_preview)
        for widget in (secret_key, extra_auth_key):
            if widget.lineEdit() is not None:
                widget.lineEdit().textEdited.connect(refresh_preview)
        for widget in (retries, timeout):
            widget.valueChanged.connect(refresh_preview)
        for widget in (
            exact_destination, archive_enabled, metadata_json, info_json,
            windows_filenames, no_input, cookies_update, credential_ack,
        ):
            widget.toggled.connect(refresh_preview)
        for checkbox in site_checks.values():
            checkbox.toggled.connect(refresh_preview)

        button_row = QHBoxLayout()
        add_button = QPushButton(tr("Add to Queue", "Tambah ke Antrean"))
        add_button.setObjectName("primary")
        copy_button = QPushButton(tr("Copy Command", "Salin Command"))
        save_button = QPushButton(tr("Save Defaults", "Simpan Default"))
        save_as_button = QPushButton(tr("Save As...", "Simpan Sebagai..."))
        close_button = QPushButton(tr("Close", "Tutup"))
        for button in (add_button, copy_button, save_button, save_as_button, close_button):
            button.setMinimumHeight(32)
        button_row.addWidget(add_button)
        button_row.addWidget(copy_button)
        button_row.addStretch(1)
        button_row.addWidget(save_button)
        button_row.addWidget(save_as_button)
        button_row.addWidget(close_button)
        root.addLayout(button_row)
        add_button.clicked.connect(add_to_queue)
        copy_button.clicked.connect(copy_commands)
        save_button.clicked.connect(save_active)
        save_as_button.clicked.connect(save_as)
        close_button.clicked.connect(dlg.accept)

        apply_loaded_defaults()
        update_auth_method()
        sync_auth_site_from_oauth()
        refresh_preview()
        dlg.exec()


def _bounded_int(value: object, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(float(value))))
    except (TypeError, ValueError, OverflowError):
        return minimum


def _set_checks(checks: dict[str, QCheckBox], selected: set[str], callback) -> None:
    for key, checkbox in checks.items():
        checkbox.blockSignals(True)
        checkbox.setChecked(key in selected)
        checkbox.blockSignals(False)
    callback()


__all__ = [
    "ConfigOptionSpec",
    "ComposerFlowchart",
    "ComposerMixin",
    "ComposerState",
    "OAUTH_SITE_CHOICES",
    "apply_config_editor_drafts",
    "build_composer_argv",
    "build_composer_config",
    "composer_oauth_target",
    "config_option_definitions",
    "config_defaults",
    "parse_typed_config_value",
    "pixiv_site_options",
    "reddit_site_options",
    "redact_auth_config",
    "site_archive_options",
    "validate_cookies_txt",
]
