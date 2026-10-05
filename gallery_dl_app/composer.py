"""Job preparation and saved configuration, sharing the parsing helpers."""

from __future__ import annotations

import copy
import dis
import json
import re
import shutil
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

from PySide6.QtCore import QPointF, QProcess, QRectF, Qt, QUrl, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QTextCursor
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
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
    QSplitter,
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
    OAuthOutputRedactor,
    atomic_write_text,
    command_string_to_argv,
    insert_gallery_dl_arguments,
    detect_config_path,
    is_sensitive_option_key,
    parse_text_database,
    quote_arg_for_preview,
    read_text_safely,
    redact_sensitive_argv,
    safe_bool,
    safe_expand_path,
    validate_finite_numbers,
    split_command,
    timestamp_slug,
    unique_path,
    validate_cookies_txt,
)
from .link_builder import LinkBuilder
from .url_builder import URL_MODE_LABELS, validate_supported_url
from .oauth_flow import local_oauth_callback_url, oauth_flow_guidance
from .config_maker import (
    ConfigPath, apply_config_delta, config_changes, config_path_value, contains_config_secrets, edit_config_path, filename_example,
    installed_page_types, installed_site_catalog, resolve_config_path,
)
from .config_value_editor import StructuredConfigEditor
from .postprocessor_editor import PostprocessorEditor, resolved_postprocessor_action
from .config_examples import EXAMPLES
from .content_filter_editor import ContentFilterEditor
from .path_rules_editor import PathRulesEditor


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
    allowed_types: tuple[str, ...] = ()
    string_items: bool = False
    example: str = ""
    choice_help: dict[str, str] = field(default_factory=dict)
    suggestions: tuple[object, ...] = ()


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
        "include": (["artworks"], "Content", "Related profile sections to include.", (
            "artworks", "avatar", "background", "favorite", "novel-user", "novel-bookmark", "sketch",
        )),
        "max-posts": (None, "Limits", "Maximum number of posts.", ()),
        "metadata": (False, "Metadata", "Fetch extended metadata.", (True, False)),
        "metadata-bookmark": (False, "Metadata", "Fetch bookmark metadata.", (True, False)),
        "sanity": (True, "Behavior", "Enable Pixiv sanity checks.", (True, False)),
        "tags": ("japanese", "Metadata", "Tag translation/language mode.", ("japanese", "translated", "original")),
        "ugoira": (True, "Content", "Download Ugoira or original frame archive.", (True, False, "original")),
        "refresh-token": (None, "Authentication", "Pixiv OAuth refresh token.", ()),
    },
    "pixiv-novel": {
        "covers": (False, "Novels", "Download novel cover images.", (True, False)),
        "embeds": (False, "Novels", "Download images embedded in novels.", (True, False)),
        "full-series": (False, "Novels", "Download all novels in a series.", (True, False)),
        "metadata": (False, "Metadata", "Fetch extended novel author metadata.", (True, False)),
        "metadata-bookmark": (False, "Metadata", "Fetch tags on bookmarked novels.", (True, False)),
        "comments": (False, "Metadata", "Fetch novel comments.", (True, False)),
        "tags": ("japanese", "Metadata", "Novel tag language.", ("japanese", "translated", "original")),
        "refresh-token": (None, "Authentication", "Pixiv OAuth refresh token for novels.", ()),
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

# The first screen in Site Config Studio shows a small, useful subset. Search
# and "Show all settings" still expose every discovered option.
_STARTER_SHARED_OPTIONS = frozenset({
    "base-directory", "filename", "archive", "retries", "timeout",
})
_STARTER_SITE_OPTIONS = (
    "include", "original", "quality", "videos", "replies", "comments",
    "metadata", "tags", "ugoira", "pinned", "previews", "embeds",
    "max-posts", "limit",
)
_FRIENDLY_OPTION_NAMES: dict[str, tuple[str, str]] = {
    "base-directory": ("Save files in", "Simpan file di"),
    "filename": ("File name", "Nama file"),
    "archive": ("Download history file", "File riwayat unduhan"),
    "retries": ("Retry failed downloads", "Ulangi unduhan gagal"),
    "timeout": ("Connection timeout (seconds)", "Batas waktu koneksi (detik)"),
    "include": ("Include profile sections", "Sertakan bagian profil"),
    "original": ("Use original files", "Gunakan file asli"),
    "quality": ("Image quality", "Kualitas gambar"),
    "videos": ("Video downloads", "Unduhan video"),
    "replies": ("Include replies", "Sertakan balasan"),
    "comments": ("Include comments", "Sertakan komentar"),
    "metadata": ("Extra information", "Informasi tambahan"),
    "tags": ("Tag language", "Bahasa tag"),
    "ugoira": ("Animated artwork", "Karya animasi"),
    "pinned": ("Include pinned posts", "Sertakan postingan tersemat"),
    "previews": ("Use preview images", "Gunakan gambar pratinjau"),
    "embeds": ("Include embedded media", "Sertakan media tersemat"),
    "max-posts": ("Maximum posts", "Maksimum postingan"),
    "limit": ("Results per request", "Hasil per permintaan"),
    "compression": ("Archive compression", "Kompresi arsip"),
    "extension": ("File extension", "Ekstensi file"),
    "files": ("Files to include", "File yang disertakan"),
    "keep-files": ("Keep original files", "Pertahankan file asli"),
    "mode": ("Processing mode", "Cara pemrosesan"),
    "enabled": ("Enable this feature", "Aktifkan fitur ini"),
    "depth": ("How far to follow linked pages", "Seberapa jauh mengikuti halaman terkait"),
    "filter": ("Choose which files to download", "Pilih file yang diunduh"),
    "image-filter": ("Filter images", "Saring gambar"),
    "filesize-min": ("Minimum file size", "Ukuran file minimum"),
    "filesize-max": ("Maximum file size", "Ukuran file maksimum"),
    "rate": ("Download speed limit", "Batas kecepatan unduhan"),
    "proxy": ("Connect through a proxy", "Hubungkan melalui proxy"),
    "chunk-size": ("Size of each transfer block", "Ukuran setiap blok transfer"),
    "postprocessors": ("Actions after downloading", "Tindakan setelah mengunduh"),
    "headers": ("Request headers", "Header permintaan"),
    "progress": ("Show download progress", "Tampilkan progres unduhan"),
    "log": ("Log file", "File log"),
    "directory": ("Subfolders", "Subfolder"),
    "event": ("When to run this action", "Kapan tindakan dijalankan"),
    "command": ("Command to run", "Perintah yang dijalankan"),
    "commands": ("Commands to run", "Perintah yang dijalankan"),
    "mtime": ("Preserve file modification time", "Pertahankan waktu perubahan file"),
    "part": ("Use temporary files during downloads", "Gunakan file sementara saat mengunduh"),
    "verify": ("Verify server certificates", "Periksa sertifikat server"),
    "skip": ("Handle existing files", "Penanganan file yang sudah ada"),
}
_STARTER_OPTION_HELP: dict[str, tuple[str, str]] = {
    "base-directory": ("Choose the main folder for this website's downloads.", "Pilih folder utama unduhan situs ini."),
    "filename": ("Pattern for saved file names. Keep {extension} so files retain their type.", "Pola nama file tersimpan. Pertahankan {extension} agar jenis file tetap benar."),
    "archive": ("Keeps a list of downloaded items so repeats can be skipped.", "Menyimpan daftar item terunduh agar unduhan berulang dapat dilewati."),
    "retries": ("How many times to try again after a failed download.", "Berapa kali mencoba lagi setelah unduhan gagal."),
    "timeout": ("How long to wait for a response before giving up.", "Lama menunggu respons sebelum berhenti."),
    "archive-pragma": ("Advanced SQLite options. Leave the default unless needed. One command per row, e.g. journal_mode=WAL. This is a list, without names.", "Opsi lanjutan SQLite. Biarkan bawaan jika tidak diperlukan. Satu perintah per baris, mis. journal_mode=WAL. Ini daftar, tanpa kolom nama."),
    "archive-event": ("Choose when to record a download: file = after saving a file; after = after finishing a post; skip = when a file is skipped. file is the usual choice.", "Pilih kapan riwayat dicatat: file = setelah file tersimpan; after = setelah satu postingan selesai; skip = saat file dilewati. Biasanya cukup file."),
    "extension-map": ("Rename a file extension: left = original extension, right = replacement. Example: jpeg → jpg. This changes the name, not the file format.", "Ganti nama ekstensi: kiri = ekstensi asal, kanan = pengganti. Contoh: jpeg → jpg. Ini mengubah nama, bukan format isi file."),
    "previews": ("Save preview images too. For Instagram: Enabled = all previews; Disabled = none; List = choose audio covers and/or video thumbnails.", "Simpan gambar pratinjau juga. Untuk Instagram: Aktif = semua pratinjau; Nonaktif = tidak ada; Daftar = pilih sampul audio dan/atau thumbnail video."),
}
_INSTAGRAM_VIDEO_HELP = {
    "true": ("Download videos using the default mode (yt-dlp).", "Unduh video dengan mode bawaan (yt-dlp)."),
    '"dash"': ("Download and combine the separate video/audio streams using yt-dlp.", "Unduh dan gabungkan aliran video/audio terpisah dengan yt-dlp."),
    '"ytdl"': ("Use yt-dlp for video downloads.", "Gunakan yt-dlp untuk mengunduh video."),
    '"merged"': ("Download the already combined video file.", "Unduh file video yang sudah digabung."),
    "false": ("Skip videos; keep downloading other selected content.", "Lewati video; konten lain yang dipilih tetap diunduh."),
}
_INSTAGRAM_INCLUDE_NAMES = {
    "posts": ("Posts (photos and videos)", "Posts (foto dan video)"),
    "reels": ("Reels", "Reels"), "tagged": ("Tagged posts", "Tagged (menandai akun)"),
    "stories": ("Stories", "Stories (story)"), "highlights": ("Story highlights", "Highlights (sorotan)"),
    "info": ("Profile information", "Info (informasi profil)"), "avatar": ("Profile picture", "Avatar (foto profil)"),
}


@lru_cache(maxsize=1)
def documented_config_options() -> tuple[dict[str, object], ...]:
    """Offline option index generated from the bundled gallery-dl manual."""
    path = Path(__file__).resolve().parent / "assets" / "config-options.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return tuple(item for item in payload["options"] if isinstance(item, dict))
    except (OSError, ValueError, KeyError, TypeError):
        return ()


@lru_cache(maxsize=1)
def config_catalog_version() -> str:
    try:
        path = Path(__file__).resolve().parent / "assets" / "config-options.json"
        return str(json.loads(path.read_text(encoding="utf-8"))["version"]).removeprefix("v")
    except (OSError, ValueError, KeyError, TypeError):
        return "unknown"


def config_example_value(example: str) -> object:
    """Read the first JSON container in a manual example, without executing it."""
    for index, character in enumerate(example):
        if character in "[{":
            try:
                value, _ = json.JSONDecoder().raw_decode(example[index:])
                if isinstance(value, (dict, list)):
                    return value
            except ValueError:
                continue
    return None


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

        classes = [item for item in extractor.extractors() if (
            item.category == wanted or getattr(item, "basecategory", "") == wanted
            or any(instance[0] == wanted for instance in getattr(item, "instances", ()))
        )]
        for cls in classes:
            for base in cls.__mro__:
                module = str(getattr(base, "__module__", ""))
                if not module.startswith("gallery_dl.extractor") or module == "gallery_dl.extractor.common":
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
    for entry in documented_config_options():
        path = str(entry.get("path") or "")
        parts = path.split(".")
        if len(parts) < 3 or parts[0] != "extractor":
            continue
        family = parts[1].strip("[]").lower().replace("-", "").removesuffix("extractor")
        applies = parts[1] == "*" or bool(site and (
            parts[1] == site or parts[1].startswith("[") and family in installed_site_catalog().get(site, ())
        ))
        if not applies:
            continue
        key = ".".join(parts[2:])
        # A dot indicates an extractor subcategory, which requires a nested
        # JSON object and cannot be edited as a flat category option.
        if not re.fullmatch(r"[a-z][a-z0-9-]*", key) or key in specs and parts[1] == "*":
            continue
        specs[key] = ConfigOptionSpec(
            key=key,
            value_type=str(entry.get("type") or "text"),
            default=entry.get("default"),
            group="Documented",
            description=str(entry.get("description") or path),
            choices=tuple(entry.get("choices") or ()),
            sensitive=is_sensitive_option_key(key),
            scope="general" if parts[1] == "*" else "site",
        )
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
    # Carry the manual's allowed formats and examples into curated and runtime
    # forms too, rather than losing them when a curated default takes priority.
    for entry in documented_config_options():
        parts = str(entry.get("path", "")).split(".")
        if len(parts) != 3 or parts[0] != "extractor" or parts[2] not in specs:
            continue
        family = parts[1].strip("[]").lower().replace("-", "").removesuffix("extractor")
        if not (parts[1] == "*" or parts[1] == site or parts[1].startswith("[") and family in installed_site_catalog().get(site, ())):
            continue
        spec = specs[parts[2]]
        specs[parts[2]] = replace(
            spec, allowed_types=tuple(entry.get("allowed_types") or ()),
            string_items=bool(entry.get("string_items")), example=str(entry.get("example") or ""),
            choices=tuple(entry.get("choices") or spec.choices), choice_help=dict(entry.get("choice_help") or {}),
            suggestions=tuple(entry.get("suggestions") or ()),
            default=entry["default"] if parts[2] == "extension-map" else spec.default,
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


def apply_config_path_drafts(
    data: dict,
    *,
    overrides: dict[ConfigPath, object] | None = None,
    removals: set[ConfigPath] | None = None,
) -> dict:
    """Apply edits to documented concrete JSON paths without losing peers."""
    result = copy.deepcopy(data) if isinstance(data, dict) else {}
    for path in removals or ():
        result = edit_config_path(result, path, remove=True)
    for path, value in (overrides or {}).items():
        result = edit_config_path(result, path, value)
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
    metadata: bool = False,
    metadata_bookmark: bool = False,
    captions: bool = False,
    comments: bool = False,
    tags: str = "japanese",
    ugoira: bool | str = True,
) -> dict[str, object]:
    """Build artwork/profile settings under extractor.pixiv."""
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
    if tags not in {"japanese", "translated", "original"}:
        raise ValueError("Pixiv tags must be japanese, translated, or original")
    result: dict[str, object] = {
        "metadata": bool(metadata),
        "metadata-bookmark": bool(metadata_bookmark),
        "captions": bool(captions),
        "comments": bool(comments),
        "tags": tags,
        "ugoira": normalized_ugoira,
    }
    result["include"] = selected
    return result


def pixiv_novel_options(
    *, embeds: bool = False, covers: bool = False, full_series: bool = False,
    metadata: bool = False, metadata_bookmark: bool = False,
    comments: bool = False, tags: str = "japanese",
) -> dict[str, object]:
    """Build novel settings under extractor.pixiv-novel."""
    if tags not in {"japanese", "translated", "original"}:
        raise ValueError("Pixiv novel tags must be japanese, translated, or original")
    return {
        "embeds": bool(embeds),
        "covers": bool(covers),
        "full-series": bool(full_series),
        "metadata": bool(metadata),
        "metadata-bookmark": bool(metadata_bookmark),
        "comments": bool(comments),
        "tags": tags,
    }


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
            return validate_finite_numbers(float(value.strip()))
        except ValueError as exc:
            raise ValueError("Enter a valid number") from exc
    if kind == "null":
        return None
    if kind == "json":
        try:
            return validate_finite_numbers(json.loads(value))
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
    active_config = safe_expand_path(config_path) if config_path else None
    config_exists = bool(active_config and active_config.is_file())
    if state.use_active_config and config_exists:
        parts.extend(["--config", str(active_config)])
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
        if (state.cookies_file or state.cookies_browser != "none") and not state.cookies_update:
            parts.extend(["-o", "cookies-update=false"])
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
        auth_options["cookies-update"] = state.cookies_update
    elif state.cookies_browser and state.cookies_browser != "none":
        browser_source: list[object | None] = [state.cookies_browser]
        if state.cookies_profile or state.cookies_domain:
            browser_source.append(state.cookies_profile or None)
        if state.cookies_domain:
            browser_source.extend([None, None, state.cookies_domain])
        auth_options["cookies"] = browser_source
        auth_options["cookies-update"] = state.cookies_update
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
    # Keep enabled entries intact: recreating them from checkbox defaults
    # discards custom filenames, events, compression, and other saved options.
    # Only remove entries whose corresponding checkbox has been cleared.
    postprocessors = []
    for item in existing_pp if isinstance(existing_pp, list) else []:
        if isinstance(item, dict):
            if item.get("name") == "metadata":
                enabled = state.info_json if item.get("filename") == "info.json" else state.metadata_json
                if not enabled:
                    continue
            elif item.get("name") == "zip":
                if state.archive_format not in {"zip", "cbz"}:
                    continue
                item = copy.deepcopy(item)
                if state.archive_format == "cbz" or item.get("extension") == "cbz":
                    item["extension"] = state.archive_format
        postprocessors.append(copy.deepcopy(item))
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


def _read_json_config(path: str | None, *, required: bool = False) -> tuple[dict, str | None]:
    if not path:
        return {}, None
    config = safe_expand_path(path)
    if not config.exists():
        return {}, "The selected config file no longer exists." if required else None
    if not config.is_file():
        return {}, "Config path is not a file."
    try:
        from .config_helper import parse_config_json
        data = parse_config_json(read_text_safely(config))
        validate_finite_numbers(data)
        if not isinstance(data, dict):
            return {}, "Config root must be a JSON object."
        return data, None
    except json.JSONDecodeError as exc:
        return {}, f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}. Check commas between items and double quotes around names."
    except Exception as exc:
        return {}, str(exc)


class ComposerFlowchart(QWidget):
    """Compact, theme-aware overview of the Composer workflow."""

    def __init__(self, *, indonesian: bool, config_mode: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.indonesian = indonesian
        self.config_mode = config_mode
        self.setMinimumHeight(150 if config_mode else 310)
        self.setAccessibleName(
            ("Alur pembuatan config" if indonesian else "Config maker usage flow") if config_mode else
            ("Alur penggunaan Download Composer" if indonesian else "Download Composer usage flow")
        )
        self.setToolTip(
            ("Pilih folder, atur situs, periksa perubahan, lalu simpan." if indonesian else
             "Choose a folder, set up websites, review changes, then save.") if config_mode else
            (
            "Ikuti alur dari kiri ke kanan. Pilihan scope bercabang lalu bergabung kembali di Preview."
            if indonesian else
            "Follow the flow from left to right. The scope choice branches and joins again at Preview."
            )
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
        if title_font.pointSizeF() <= 0:
            title_font.setPointSizeF(10.0)
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
        if self.config_mode:
            labels = (
                [("Folder unduhan", "Tempat menyimpan file."), ("Pengaturan situs", "Pilih situs; ubah yang perlu."),
                 ("Periksa perubahan", "Lihat hasil sebelum simpan."), ("Simpan config", "Siap dipakai untuk unduhan.")]
                if self.indonesian else
                [("Download folder", "Where files will be saved."), ("Website settings", "Choose a site; change what you need."),
                 ("Review changes", "Check the result before saving."), ("Save config", "Ready for your next downloads.")]
            )
            for index, (title, detail) in enumerate(labels):
                rect = QRectF(xs[index], top_y, node_w, 90)
                if index < 3:
                    self._arrow(painter, rect.topRight() + QPointF(0, 45), QPointF(xs[index + 1], top_y + 45), accent)
                self._node(painter, rect, str(index + 1), title, detail,
                           fill=neutral, border=border, text=text, muted=muted)
            return
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

    def open_config_builder(self, preferred_site: str = "") -> None:
        self.open_download_composer(config_only=True, preferred_site=preferred_site if isinstance(preferred_site, str) else "")

    def open_download_composer(self, *, config_only: bool = False, preferred_site: str = "") -> None:  # noqa: C901 - UI composition is intentionally local
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
        dlg.setObjectName("configBuilderDialog" if config_only else "downloadComposerDialog")
        dlg.setWindowTitle(tr("Config Builder", "Pembuat Config") if config_only else tr("Add downloads", "Tambah Unduhan"))
        if config_only:
            dlg.resize(900, 620)
            dlg.setMinimumSize(760, 520)
        else:
            dlg.resize(980, 740)
            dlg.setMinimumSize(820, 620)
        root = QVBoxLayout(dlg)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(9)

        title_row = QHBoxLayout()
        title = QLabel(tr("Config Builder", "Pembuat Config") if config_only else tr("Add downloads", "Tambah Unduhan"))
        title.setObjectName("title")
        title_row.addWidget(title)
        title_row.addStretch(1)
        scope_badge = QLabel()
        scope_badge.setObjectName("statusPill")
        title_row.addWidget(scope_badge)
        root.addLayout(title_row)
        intro = QLabel(tr(
            "Choose a download folder and website settings, then save your config file. The app creates it for you; you do not need to write JSON.",
            "Pilih folder unduhan dan pengaturan situs, lalu simpan file config. Aplikasi membuatnya untuk Anda; tidak perlu menulis JSON.",
        ) if config_only else tr(
            "1. Paste or build links  2. Choose options for these jobs  3. Add to queue. Saved website settings are edited through Config.",
            "1. Tempel atau buat tautan  2. Pilih opsi untuk job ini  3. Tambah ke antrean. Pengaturan situs tersimpan diedit melalui Config.",
        ))
        intro.setObjectName("subtle")
        intro.setWordWrap(True)
        root.addWidget(intro)
        active_path = None
        config_start_button = None
        if config_only:
            active_path = QLabel(tr("Config file: ", "File config: ") + str(self.config_path or detect_config_path() or APP_DIR / "config.json"))
            active_path.setObjectName("subtle")
            active_path.setWordWrap(True)
            active_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
            root.addWidget(active_path)
            config_start_button = QPushButton(tr("Choose a website…", "Pilih situs…"))
            config_start_button.setObjectName("configWebsiteSettingsButton")
            root.addWidget(config_start_button)

        tabs = QTabWidget()
        tabs.setObjectName("composerTabs")
        root.addWidget(tabs, 1)
        definitions = self.gallery_dl_site_preset_definitions()
        definition_names = {
            str(item.get("key") or "").strip().lower(): str(item.get("name") or "").strip()
            for item in definitions
            if str(item.get("key") or "").strip()
        }
        runtime_categories: set[str] = set()
        try:
            runtime_categories.update(installed_site_catalog())
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
        if config_only:
            scope.addItem(tr("Saved defaults", "Default tersimpan"), "config")
        scope.setToolTip(tr(
            "Job override writes shared values into this command. Saved defaults keeps them in the config instead.",
            "Override job menulis nilai bersama ke command ini. Default tersimpan menyimpannya di config.",
        ))
        job_l.addWidget(scope, 0, 2)
        urls = QPlainTextEdit()
        urls.setObjectName("composerPreparedLinks")
        urls.setPlaceholderText(tr("One URL per line", "Satu URL per baris"))
        urls.setMinimumHeight(88)
        urls.setMaximumHeight(105)
        urls.setToolTip(tr(
            "Paste one gallery-dl-supported URL per line. Every line becomes a separate queue job.",
            "Tempel satu URL yang didukung gallery-dl per baris. Setiap baris menjadi satu job antrean.",
        ))
        job_l.addWidget(QLabel(tr("Prepared links", "Tautan yang disiapkan")), 3, 0)
        job_l.addWidget(urls, 3, 1, 1, 2)
        def append_built_links(links: list[str]) -> int:
            existing = [line.strip() for line in urls.toPlainText().splitlines() if line.strip()]
            additions = [url for url in links if url not in existing]
            if additions:
                urls.setPlainText("\n".join(existing + additions))
            return len(additions)

        guided_builder = LinkBuilder(append_built_links, indonesian=ind)
        job_l.addWidget(guided_builder, 1, 0, 2, 3)
        config_path_label = QLabel(str(self.config_path or detect_config_path() or ""))
        config_path_label.setObjectName("subtle")
        config_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        job_l.addWidget(QLabel(tr("Active config", "Config aktif")), 4, 0)
        job_l.addWidget(config_path_label, 4, 1, 1, 2)
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
        directory = QLineEdit()
        directory.setObjectName("composerSubfolders")
        directory.setPlaceholderText(tr("Leave blank to use website/config folders; separate overrides with ;", "Kosongkan untuk folder situs/config; pisahkan override dengan ;"))
        directory.setToolTip(tr(
            "Folder levels below Download folder. Separate each level with a semicolon. Available keywords vary by extractor; inspect them with Mode: List keywords.",
            "Tingkat folder di bawah Folder download. Pisahkan setiap tingkat dengan titik koma. Keyword berbeda per extractor; periksa melalui Mode: Lihat keyword.",
        ))
        filename = QLineEdit()
        filename.setObjectName("composerFilename")
        filename.setPlaceholderText(tr("Leave blank to use website/config filenames", "Kosongkan untuk nama file situs/config"))
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
        main_l.addStretch(1)

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
        oauth_response.setObjectName("composerOAuthResponse")
        oauth_response.setEchoMode(QLineEdit.Password)
        oauth_response.setPlaceholderText(tr("Pixiv code or full callback URL", "Code Pixiv atau URL callback lengkap"))
        oauth_send = QPushButton(tr("Send response", "Kirim respons"))
        oauth_send.setObjectName("composerOAuthSend")
        oauth_send.setEnabled(False)
        oauth_status = QPlainTextEdit()
        oauth_status.setObjectName("composerOAuthStatus")
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
        site_config_button = QPushButton(tr("Website Settings…", "Pengaturan Situs…"))
        site_config_button.setObjectName("siteConfigStudioButton")
        site_config_button.setToolTip(tr(
            "Choose a website and change its download settings with simple controls.",
            "Pilih situs dan ubah pengaturan unduhannya dengan kontrol yang mudah.",
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
        config_summary = QPlainTextEdit(preview_page)
        config_summary.setObjectName("configReviewSummary")
        config_summary.setReadOnly(True)
        if config_only:
            preview_tabs.insertTab(1, config_summary, tr("Changes explained", "Penjelasan perubahan"))
        else:
            config_summary.hide()
        helper_page = QWidget()
        helper_layout = QVBoxLayout(helper_page)
        helper_note = QLabel(tr(
            "Check this draft for conflicting defaults, undefined actions, invalid filters and missing animation tools. Checks do not change your settings. Test website access through the Download Composer after saving.",
            "Periksa draft untuk default yang bertabrakan, tindakan belum terdefinisi, filter salah dan tool animasi yang belum ada. Pemeriksaan tidak mengubah pengaturan. Uji akses situs melalui Download Composer setelah menyimpan.",
        ))
        helper_note.setWordWrap(True)
        helper_layout.addWidget(helper_note)
        helper_result = QPlainTextEdit()
        helper_result.setObjectName("configHelperResult")
        helper_result.setReadOnly(True)
        helper_layout.addWidget(helper_result, 1)
        helper_check = QPushButton(tr("Check current config draft", "Periksa draft config saat ini"))
        helper_check.setObjectName("configHelperCheck")
        helper_layout.addWidget(helper_check)
        preview_tabs.addTab(helper_page, tr("Config helper", "Bantuan config"))
        preview_l.addWidget(preview_tabs, 1)
        warnings = QLabel()
        warnings.setObjectName("subtle")
        warnings.setWordWrap(True)
        preview_l.addWidget(warnings)
        tabs.addTab(preview_page, tr("Preview", "Pratinjau"))

        import_config_button = None
        load_example_button = None
        if config_only:
            start_scroll = QScrollArea()
            start_scroll.setWidgetResizable(True)
            start_scroll.setFrameShape(QFrame.NoFrame)
            start_page = QWidget()
            start_l = QVBoxLayout(start_page)
            start_l.addWidget(ComposerFlowchart(indonesian=ind, config_mode=True))
            start_note = QLabel(tr(
                "Start here. Change only what you need; gallery-dl supplies defaults for the rest. Settings apply to every website unless you choose a specific website.",
                "Mulai di sini. Ubah yang diperlukan; pengaturan lainnya memakai bawaan gallery-dl. Pengaturan berlaku untuk semua situs kecuali Anda memilih situs tertentu.",
            ))
            start_note.setWordWrap(True)
            start_l.addWidget(start_note)
            import_config_button = QPushButton(tr("Use an existing config as a starting point…", "Gunakan config yang sudah ada sebagai contoh…"))
            import_config_button.setObjectName("configStarterImport")
            import_config_button.setToolTip(tr("Replaces the current draft. The selected source file is only read; choose Save As to write a separate copy.", "Mengganti draft saat ini. File sumber hanya dibaca; gunakan Simpan Sebagai untuk menulis salinan terpisah."))
            start_l.addWidget(import_config_button)
            example_row = QHBoxLayout()
            example_search = QLineEdit()
            example_search.setObjectName("configStarterExampleSearch")
            example_search.setPlaceholderText(tr("Find examples by website or purpose…", "Cari contoh berdasarkan situs atau tujuan…"))
            start_l.addWidget(example_search)
            config_example_picker = QComboBox()
            config_example_picker.setObjectName("configStarterExample")
            for example in EXAMPLES:
                config_example_picker.addItem(tr(*example.title), example.key)
            load_example_button = QPushButton(tr("Use this example", "Pakai contoh ini"))
            load_example_button.setObjectName("configStarterLoadExample")
            load_example_button.setToolTip(tr("Replaces the draft with this example. Choose your folder and login, review changes, then Save As.", "Mengganti draft dengan contoh ini. Pilih folder dan login, periksa perubahan, lalu Simpan Sebagai."))
            example_row.addWidget(config_example_picker, 1)
            example_row.addWidget(load_example_button)
            start_l.addLayout(example_row)
            example_note = QLabel()
            example_note.setObjectName("configStarterExampleDescription")
            example_note.setTextFormat(Qt.PlainText)
            example_note.setWordWrap(True)
            start_l.addWidget(example_note)
            example_source = QLabel()
            example_source.setObjectName("configStarterExampleSource")
            example_source.setOpenExternalLinks(True)
            example_source.setWordWrap(True)
            start_l.addWidget(example_source)

            def describe_example(*_args) -> None:
                example = next((item for item in EXAMPLES if item.key == config_example_picker.currentData()), None)
                load_example_button.setEnabled(example is not None)
                if example is None:
                    example_note.setText(tr("No matching examples. Try a website name such as Instagram or a purpose such as JSON.", "Tidak ada contoh yang cocok. Coba nama situs seperti Instagram atau tujuan seperti JSON."))
                    example_source.clear()
                    return
                example_note.setText(tr(*example.description) + tr("\nLoading replaces the draft. Choose your folder, review, then Save As.", "\nMemuat mengganti draft. Pilih folder, periksa, lalu Simpan Sebagai."))
                example_source.setText(f'<a href="{example.source}">{tr("Read the source example", "Baca sumber contoh")}</a>')

            def filter_examples(query: str) -> None:
                previous = config_example_picker.currentData()
                config_example_picker.blockSignals(True)
                config_example_picker.clear()
                words = query.lower().split()
                for example in EXAMPLES:
                    haystack = " ".join((example.key, *example.title, *example.description)).lower()
                    if all(word in haystack for word in words):
                        config_example_picker.addItem(tr(*example.title), example.key)
                config_example_picker.setCurrentIndex(max(0, config_example_picker.findData(previous)))
                config_example_picker.blockSignals(False)
                describe_example()

            config_example_picker.currentIndexChanged.connect(describe_example)
            example_search.textChanged.connect(filter_examples)
            describe_example()
            folder_label = QLabel()
            folder_label.setWordWrap(True)
            folder_label.setObjectName("configStarterFolder")

            def show_starter_folder(value: str) -> None:
                folder_label.setText(tr("Download folder: ", "Folder unduhan: ") + (value or "./gallery-dl/"))

            destination.textChanged.connect(show_starter_folder)
            show_starter_folder(destination.text())
            start_l.addWidget(folder_label)
            choose_folder = QPushButton(tr("1. Choose download folder…", "1. Pilih folder unduhan…"))
            choose_folder.setObjectName("configStarterChooseFolder")
            choose_folder.clicked.connect(lambda: browse_folder())
            start_l.addWidget(choose_folder)
            root.removeWidget(config_start_button)
            config_start_button.setText(tr("2. Choose a website…", "2. Pilih situs…"))
            start_l.addWidget(config_start_button)
            login_shortcut = QPushButton(tr("Login needed? Set up cookies or an account…", "Perlu login? Atur cookies atau akun…"))
            login_shortcut.clicked.connect(lambda: tabs.setCurrentWidget(auth_scroll))
            start_l.addWidget(login_shortcut)
            remember_downloads = QCheckBox(tr("Skip files already downloaded", "Lewati file yang sudah diunduh"))
            remember_downloads.setObjectName("configStarterHistory")

            def set_starter_history(checked: bool) -> None:
                if checked and not archive_path.text().strip():
                    config_target = safe_expand_path(self.config_path or str(APP_DIR / "config.json"))
                    archive_path.setText(str(config_target.parent / "download-history.sqlite3"))
                archive_enabled.setChecked(checked)

            remember_downloads.toggled.connect(set_starter_history)
            archive_enabled.toggled.connect(remember_downloads.setChecked)
            start_l.addWidget(remember_downloads)
            history_help = QLabel(tr(
                "Uses a download history file. Uncheck this to stop using the shared history; per-website history settings still apply.",
                "Memakai file riwayat unduhan. Hapus centang untuk menghentikan riwayat bersama; pengaturan riwayat per situs tetap berlaku.",
            ))
            history_help.setWordWrap(True)
            start_l.addWidget(history_help)
            review_shortcut = QPushButton(tr("3. Review config before saving", "3. Periksa config sebelum menyimpan"))
            review_shortcut.setObjectName("configStarterReview")
            review_shortcut.clicked.connect(lambda: tabs.setCurrentWidget(preview_page))
            start_l.addWidget(review_shortcut)
            start_l.addStretch(1)
            start_scroll.setWidget(start_page)
            tabs.insertTab(0, start_scroll, tr("Start here", "Mulai di sini"))

        existing_path = self.config_path or detect_config_path()
        existing_config, config_error = _read_json_config(existing_path)
        preserve_source = bool(existing_path) and config_error is None
        # Only draft values created in Site Config Studio live here. They are
        # merged recursively into the existing config and are not persisted
        # until the user explicitly saves from the main Composer dialog.
        general_overrides: dict[str, object] = {}
        site_overrides: dict[str, dict[str, object]] = {}
        general_removals: set[str] = set()
        site_removals: dict[str, set[str]] = {}
        path_overrides: dict[ConfigPath, object] = {}
        path_removals: set[ConfigPath] = set()
        loaded_state: ComposerState | None = None

        def apply_loaded_defaults() -> None:
            values = config_defaults(existing_config)
            if config_only and preserve_source:
                destination.clear()
                directory.clear()
                filename.clear()
            if values.get("destination"):
                destination.setText(str(values["destination"]))
            if config_only and values.get("directory"):
                directory.setText("; ".join(values["directory"]))
            exact_destination.setChecked(config_only and bool(values.get("exact_destination", False)))
            if config_only and values.get("filename"):
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
            if preserve_source and loaded_state is not None:
                baseline = build_composer_config(loaded_state, existing=existing_config)
                data = apply_config_delta(existing_config, baseline, data)
            data = apply_config_editor_drafts(
                data,
                general_overrides=general_overrides,
                site_overrides=site_overrides,
                general_removals=general_removals,
                site_removals=site_removals,
            )
            return apply_config_path_drafts(
                data, overrides=path_overrides, removals=path_removals,
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

        def describe_changes(data: dict) -> str:
            safe = redact_auth_config(data)
            changes = config_changes(existing_config, data)
            if not changes:
                return tr("No unsaved changes. Your saved config is ready to use.", "Tidak ada perubahan yang belum tersimpan. Config siap dipakai.")
            lines = [tr(f"{len(changes)} setting(s) will change when you save:", f"{len(changes)} pengaturan akan berubah saat disimpan:"), ""]
            for path, removed in changes:
                names = _FRIENDLY_OPTION_NAMES.get(str(path[-1]))
                name = tr(*names) if names else str(path[-1]).replace("-", " ").capitalize()
                section = ".".join(str(part) for part in path[:-1])
                if section == "extractor":
                    section = tr("All websites", "Semua situs")
                elif section.startswith("extractor."):
                    section = section.removeprefix("extractor.")
                if removed:
                    value = tr("Use default", "Pakai bawaan")
                else:
                    value, _ = config_path_value(safe, path)
                    if isinstance(value, bool):
                        value = tr("Enabled", "Aktif") if value else tr("Disabled", "Nonaktif")
                    elif isinstance(value, (dict, list)):
                        value = json.dumps(value, ensure_ascii=False)
                    value = str(value)
                    if len(value) > 180:
                        value = value[:177] + "…"
                lines.append(f"• {section} → {name}: {value}")
            return "\n".join(lines)

        def refresh_preview(*_args) -> None:
            state = current_state()
            command_preview.setPlainText("\n".join(command_lines(redact=True)))
            safe_config = redact_auth_config(
                proposed_config(),
                extra_keys=(state.secret_key, state.extra_auth_key),
            )
            config_preview.setPlainText(json.dumps(safe_config, indent=2, ensure_ascii=False))
            config_summary.setPlainText(describe_changes(proposed_config()))
            is_config = state.apply_to_config
            scope_badge.setText(tr("SAVED DEFAULTS", "DEFAULT TERSIMPAN") if is_config else tr("JOB OVERRIDE", "OVERRIDE JOB"))
            issues: list[str] = []
            if not state.urls and not config_only:
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
            if config_error:
                issues.append(tr("The active config could not be merged: ", "Config aktif tidak dapat digabung: ") + config_error)
            warnings.setText("\n".join("! " + issue for issue in issues))

        def check_config_draft(*_args) -> None:
            from .config_helper import config_helper_text
            from .core import dependency_status
            helper_result.setPlainText(config_helper_text(proposed_config(), indonesian=ind, dependencies=dependency_status()))

        helper_check.clicked.connect(check_config_draft)
        preview_tabs.currentChanged.connect(lambda _index: check_config_draft() if preview_tabs.currentWidget() == helper_page else None)

        def open_site_config_studio(preferred_site: str = "") -> None:  # noqa: C901 - guided tabbed editor
            studio = QDialog(dlg)
            studio.setObjectName("siteConfigStudio")
            studio.setWindowTitle(tr("Website Settings", "Pengaturan Situs"))
            studio.resize(1040, 760)
            studio.setMinimumSize(860, 640)
            studio_l = QVBoxLayout(studio)
            studio_intro = QLabel(tr(
                "Choose a website and decide how its downloads should work. Save config creates or updates your config file with settings for every website you changed.",
                "Pilih situs dan atur cara unduhannya. Simpan config membuat atau memperbarui file config dengan pengaturan semua situs yang Anda ubah.",
            ))
            studio_intro.setWordWrap(True)
            studio_intro.setObjectName("subtle")
            studio_l.addWidget(studio_intro)
            studio_guide = QLabel(tr(
                "1. Choose a website   2. Change only what you need   3. Save config",
                "1. Pilih situs   2. Ubah yang diperlukan   3. Simpan config",
            ))
            studio_guide.setObjectName("siteConfigGuide")
            studio_guide.setWordWrap(True)
            studio_l.addWidget(studio_guide)
            studio_path = QLabel(tr("Config file: ", "File config: ") + str(
                self.config_path or detect_config_path() or APP_DIR / "config.json"
            ))
            studio_path.setObjectName("siteConfigFilePath")
            studio_path.setWordWrap(True)
            studio_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
            studio_l.addWidget(studio_path)

            studio_tabs = QTabWidget()
            studio_tabs.setObjectName("siteConfigTabs")
            studio_l.addWidget(studio_tabs, 1)
            advanced_tabs = QTabWidget()
            advanced_tabs.setObjectName("siteConfigAdvancedTabs")
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
            selected_studio_site = preferred_site if preferred_site in site_names else next(
                (key for key, checkbox in site_checks.items() if checkbox.isChecked() and key in site_names),
                "instagram" if "instagram" in site_names else site_names[0],
            )

            def site_picker(object_name: str, default: str) -> QComboBox:
                picker = QComboBox()
                picker.setObjectName(object_name)
                picker.setEditable(True)
                picker.setInsertPolicy(QComboBox.NoInsert)
                picker.setMaxVisibleItems(24)
                picker.addItems(site_names)
                picker.setCurrentText(default if default in site_names else site_names[0])
                picker.lineEdit().setPlaceholderText(tr("Type to find a site", "Ketik untuk mencari situs"))
                if picker.completer() is not None:
                    picker.completer().setCaseSensitivity(Qt.CaseInsensitive)
                    picker.completer().setFilterMode(Qt.MatchContains)
                    picker.completer().setCompletionMode(QCompleter.PopupCompletion)
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
                for key in block:
                    prefix = ("extractor", *([category] if category else []), key)
                    for path in list(path_overrides):
                        if path[:len(prefix)] == prefix:
                            path_overrides.pop(path)
                    path_removals.difference_update(path for path in list(path_removals) if path[:len(prefix)] == prefix)
                site_overrides[category] = _merge_dict(site_overrides.get(category, {}), block)
                # Structured editors show the whole mapping: removed rows must
                # disappear, rather than return through the recursive merge.
                for key, value in block.items():
                    if isinstance(value, dict):
                        path_overrides[("extractor", *([category] if category else []), key)] = copy.deepcopy(value)
                removed = site_removals.get(category)
                if removed:
                    removed.difference_update(block)
                    if not removed:
                        site_removals.pop(category, None)
                scope.setCurrentIndex(scope.findData("config"))
                refresh_preview()
                update_studio_preview()

            # One generated options page covers every installed extractor.
            site_options_page = QWidget()
            site_options_l = QVBoxLayout(site_options_page)
            site_options_l.setContentsMargins(10, 10, 10, 10)
            site_options_intro = QLabel(tr(
                "These settings affect only the website you choose. Start with the common controls below; search or show all settings when you need more.",
                "Pengaturan ini hanya memengaruhi situs yang Anda pilih. Mulai dari kontrol umum di bawah; cari atau tampilkan semua pengaturan bila perlu.",
            ))
            site_options_intro.setWordWrap(True)
            site_options_l.addWidget(site_options_intro)
            site_options_site = site_picker("siteOptionsSiteCombo", selected_studio_site)
            site_options_search = QLineEdit()
            site_options_search.setObjectName("siteOptionsSearch")
            site_options_search.setPlaceholderText(tr(
                "Find a setting, e.g. comments or cookies", "Cari pengaturan, mis. komentar atau cookies",
            ))
            site_options_picker_row = QHBoxLayout()
            site_options_picker_row.addWidget(QLabel(tr("Website", "Situs")))
            site_options_picker_row.addWidget(site_options_site, 1)
            site_options_picker_row.addWidget(site_options_search, 1)
            site_options_l.addLayout(site_options_picker_row)
            site_options_goal = QComboBox()
            site_options_goal.setObjectName("siteOptionsGoal")
            for label, key in (
                (tr("Start with the essentials", "Mulai dari pengaturan penting"), "starter"),
                (tr("Choose what to download", "Pilih konten yang diunduh"), "content"),
                (tr("Choose folders and file names", "Atur folder dan nama file"), "paths"),
                (tr("Avoid downloading the same items again", "Hindari unduhan berulang"), "history"),
                (tr("Adjust connection and waiting times", "Atur koneksi dan waktu tunggu"), "network"),
                (tr("Do something after downloading", "Atur tindakan setelah unduhan"), "after"),
            ):
                site_options_goal.addItem(label, key)
            site_options_l.addWidget(site_options_goal)
            site_options_show_all = QCheckBox(tr(
                "Show all settings (advanced)", "Tampilkan semua pengaturan (lanjutan)",
            ))
            site_options_show_all.setObjectName("siteOptionsShowAll")
            site_options_l.addWidget(site_options_show_all)
            site_options_status = QLabel()
            site_options_status.setObjectName("siteOptionsStatus")
            site_options_status.setWordWrap(True)
            site_options_l.addWidget(site_options_status)
            site_options_apply = QPushButton(tr("Apply site settings", "Terapkan pengaturan situs"))
            site_options_apply.setObjectName("applyGeneratedSiteSettings")
            site_options_l.addWidget(site_options_apply, 0, Qt.AlignLeft)
            site_options_scroll = QScrollArea()
            site_options_scroll.setWidgetResizable(True)
            site_options_scroll.setFrameShape(QFrame.NoFrame)
            site_options_content = QWidget()
            site_options_cards = QVBoxLayout(site_options_content)
            site_options_cards.setContentsMargins(2, 2, 2, 2)
            site_options_cards.setSpacing(8)
            site_options_scroll.setWidget(site_options_content)
            site_options_l.addWidget(site_options_scroll, 1)
            studio_tabs.addTab(site_options_page, tr("Website Settings", "Pengaturan Situs"))

            # Complete general/per-site option editor -------------------------
            all_options_page = QWidget()
            all_options_l = QVBoxLayout(all_options_page)
            all_options_l.setContentsMargins(10, 10, 10, 10)
            all_options_help = QLabel(tr(
                f"Choose from {len(site_names)} available extractor categories. Type a site name to search, then pick a result. The table shows common and site-specific options; select a row, enter a value, and click Set value. Use General defaults only when the value should apply to every site.",
                f"Pilih dari {len(site_names)} kategori extractor yang tersedia. Ketik nama situs untuk mencari, lalu pilih hasilnya. Tabel menampilkan opsi umum dan khusus situs; pilih baris, masukkan nilai, lalu klik Atur nilai. Gunakan Default umum hanya jika nilai harus berlaku untuk semua situs.",
            ))
            all_options_help.setObjectName("siteConfigAllSitesHelp")
            all_options_help.setWordWrap(True)
            all_options_l.addWidget(all_options_help)
            option_scope_row = QGridLayout()
            config_scope = QComboBox()
            config_scope.setObjectName("configScopeCombo")
            config_scope.addItem(tr("Per-site override", "Override per situs"), "site")
            config_scope.addItem(tr("General defaults — all sites", "Default umum — semua situs"), "general")
            config_site = site_picker("configEditorSiteCombo", selected_studio_site)
            config_option_search = QLineEdit()
            config_option_search.setObjectName("configOptionSearch")
            config_option_search.setPlaceholderText(tr(
                "Search option name, group, or description",
                "Cari nama opsi, grup, atau deskripsi",
            ))
            option_scope_row.addWidget(QLabel(tr("Level", "Tingkat")), 0, 0)
            option_scope_row.addWidget(config_scope, 0, 1)
            option_scope_row.addWidget(QLabel(tr("Site / extractor", "Situs / extractor")), 0, 2)
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
            advanced_tabs.addTab(all_options_page, tr("All Settings", "Semua Pengaturan"))
            # The full reference above owns the option catalog. Keep the legacy
            # editor available internally without another visible catalog page.
            advanced_tabs.setTabVisible(advanced_tabs.indexOf(all_options_page), False)

            reference_page = QWidget()
            reference_l = QVBoxLayout(reference_page)
            reference_l.setContentsMargins(10, 10, 10, 10)
            reference_intro = QLabel(tr(
                f"All {len(documented_config_options())} documented settings from gallery-dl {config_catalog_version()}. Search by name or purpose, select a setting, then choose a value. Leave settings unchanged to use gallery-dl defaults.",
                f"Seluruh {len(documented_config_options())} pengaturan terdokumentasi gallery-dl {config_catalog_version()}. Cari nama atau kegunaannya, pilih pengaturan, lalu pilih nilainya. Biarkan pengaturan lainnya memakai bawaan gallery-dl.",
            ))
            reference_intro.setWordWrap(True)
            reference_l.addWidget(reference_intro)
            reference_group = QComboBox()
            reference_group.setObjectName("configReferenceGroup")
            for label, prefix in (
                (tr("All settings", "Semua pengaturan"), ""),
                (tr("Downloads and website settings", "Unduhan dan pengaturan situs"), "extractor."),
                (tr("Network and file transfers", "Jaringan dan transfer file"), "downloader."),
                (tr("Messages and logs", "Pesan dan log"), "output."),
                (tr("Actions after downloading", "Tindakan setelah mengunduh"), "postprocessor."),
                (tr("Cache", "Cache"), "cache."),
                (tr("Templates", "Template"), "jinja."),
            ):
                reference_group.addItem(label, prefix)
            reference_l.addWidget(reference_group)
            reference_search = QLineEdit()
            reference_search.setObjectName("configReferenceSearch")
            reference_search.setPlaceholderText(tr("Search all documented paths", "Cari semua path terdokumentasi"))
            reference_l.addWidget(reference_search)
            reference_table = QTableWidget(0, 2)
            reference_table.setObjectName("configReferenceTable")
            reference_table.setHorizontalHeaderLabels([tr("Setting", "Pengaturan"), tr("Section", "Bagian")])
            reference_table.setEditTriggers(QTableWidget.NoEditTriggers)
            reference_table.verticalHeader().setVisible(False)
            reference_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            reference_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
            reference_l.addWidget(reference_table, 1)
            reference_help = QPlainTextEdit()
            reference_help.setObjectName("configReferenceHelp")
            reference_help.setReadOnly(True)
            reference_help.setMaximumHeight(120)
            reference_l.addWidget(reference_help)
            reference_status = QLabel()
            reference_status.setObjectName("configReferenceStatus")
            reference_status.setWordWrap(True)
            reference_l.addWidget(reference_status)
            reference_target_row = QHBoxLayout()
            reference_site = site_picker("configReferenceSite", selected_studio_site)
            reference_site.insertItem(0, tr("All websites (shared setting)", "Semua situs (pengaturan bersama)"), "")
            reference_site.setCurrentIndex(0)
            reference_site.setEditable(False)
            reference_subcategory = QLineEdit()
            reference_subcategory.setObjectName("configReferenceSubcategory")
            reference_page_type = QComboBox()
            reference_page_type.setObjectName("configReferencePageType")
            reference_page_type.setToolTip(tr("Apply this common setting to the whole website or only one page type, such as bookmarks or a gallery.", "Terapkan pengaturan umum ini ke seluruh situs atau satu jenis halaman, seperti bookmark atau galeri."))
            reference_subcategory.setPlaceholderText(tr("Optional page type, e.g. user", "Jenis halaman opsional, mis. user"))
            reference_downloader = QComboBox()
            reference_downloader.setObjectName("configReferenceDownloader")
            for label, name in ((tr("All downloaders", "Semua downloader"), ""), ("HTTP", "http"), ("yt-dlp", "ytdl")):
                reference_downloader.addItem(label, name)
            reference_postprocessor = QComboBox()
            reference_postprocessor.setObjectName("configReferencePostprocessor")
            reference_target_row.addWidget(reference_site, 2)
            reference_target_row.addWidget(reference_page_type, 1)
            reference_target_row.addWidget(reference_subcategory, 1)
            reference_target_row.addWidget(reference_downloader, 1)
            reference_target_row.addWidget(reference_postprocessor, 1)
            reference_l.addLayout(reference_target_row)
            reference_editor = QGridLayout()
            reference_type = QComboBox()
            reference_type.setObjectName("configReferenceType")
            for label, kind in ((tr("Text", "Teks"), "text"), (tr("Yes / No", "Ya / Tidak"), "boolean"),
                                (tr("Whole number", "Bilangan bulat"), "integer"), (tr("Number", "Angka"), "number"),
                                (tr("List / named values", "Daftar / pasangan nama-nilai"), "json"), ("Null", "null")):
                reference_type.addItem(label, kind)
            reference_value = QLineEdit()
            reference_value.setObjectName("configReferenceValue")
            reference_value.setPlaceholderText(tr("Typed value for selected path", "Nilai sesuai tipe untuk path terpilih"))
            reference_choice = QComboBox()
            reference_choice.setObjectName("configReferenceChoice")
            reference_editor.addWidget(reference_choice, 0, 0, 1, 3)
            reference_ack = QCheckBox(tr("Allow plain-text storage of this secret", "Izinkan penyimpanan teks biasa untuk rahasia ini"))
            reference_ack.setObjectName("configReferenceSecretAck")
            reference_set = QPushButton(tr("Apply this setting", "Terapkan pengaturan ini"))
            reference_set.setObjectName("setReferenceOption")
            reference_remove = QPushButton(tr("Use default", "Pakai bawaan"))
            reference_remove.setObjectName("removeReferenceOption")
            reference_editor.addWidget(reference_type, 1, 0)
            reference_editor.addWidget(reference_value, 1, 1, 1, 2)
            reference_editor.addWidget(reference_ack, 2, 1, 1, 2)
            reference_editor.addWidget(reference_set, 3, 1)
            reference_editor.addWidget(reference_remove, 3, 2)
            reference_editor.setColumnStretch(1, 1)
            reference_l.addLayout(reference_editor)
            reference_structured = StructuredConfigEditor(parse_typed_config_value, indonesian=ind)
            reference_structured.setObjectName("configReferenceStructuredValue")
            reference_l.addWidget(reference_structured)
            reference_apply_note = QLabel(tr(
                "Your edits stay in the draft when you choose another setting. Save config checks and saves all edits. For a list, add one item per row; named values need a name and a replacement/value.",
                "Edit tetap ada di draft saat memilih pengaturan lain. Simpan config memeriksa dan menyimpan semua edit. Untuk daftar, tambahkan satu item per baris; pasangan membutuhkan nama dan pengganti/nilai.",
            ))
            reference_apply_note.setWordWrap(True)
            reference_l.addWidget(reference_apply_note)
            reference_link = QLabel(
                '<a href="https://gdl-org.github.io/docs/configuration.html">'
                + tr("Open current official documentation", "Buka dokumentasi resmi terkini") + "</a>"
            )
            reference_link.setOpenExternalLinks(True)
            reference_l.addWidget(reference_link)
            reference_splitter = QSplitter(Qt.Horizontal)
            reference_browser = QWidget()
            reference_browser_l = QVBoxLayout(reference_browser)
            reference_browser_l.setContentsMargins(0, 0, 0, 0)
            for widget in (reference_group, reference_search, reference_table):
                reference_l.removeWidget(widget)
                reference_browser_l.addWidget(widget)
            reference_details = QWidget()
            reference_details_l = QVBoxLayout(reference_details)
            for widget in (reference_help, reference_status):
                reference_l.removeWidget(widget)
                reference_details_l.addWidget(widget)
            for layout in (reference_target_row, reference_editor):
                reference_l.removeItem(layout)
                reference_details_l.addLayout(layout)
            for widget in (reference_structured, reference_apply_note, reference_link):
                reference_l.removeWidget(widget)
                reference_details_l.addWidget(widget)
            reference_details_l.addStretch(1)
            reference_scroll = QScrollArea()
            reference_scroll.setWidgetResizable(True)
            reference_scroll.setFrameShape(QFrame.NoFrame)
            reference_scroll.setWidget(reference_details)
            reference_splitter.addWidget(reference_browser)
            reference_splitter.addWidget(reference_scroll)
            reference_splitter.setSizes([380, 620])
            reference_l.addWidget(reference_splitter, 1)
            studio_tabs.addTab(reference_page, tr("All gallery-dl settings", "Semua pengaturan gallery-dl"))

            def reference_site_name() -> str:
                return reference_site.currentText() if reference_site.currentIndex() > 0 else ""

            def reference_raw_path() -> str:
                row = reference_table.currentRow()
                item = reference_table.item(row, 0) if row >= 0 else None
                return str(item.data(Qt.UserRole) or item.text()) if item is not None else ""

            def reference_path() -> ConfigPath | None:
                try:
                    return resolve_config_path(
                        reference_raw_path(), site=reference_site_name(),
                        subcategory=reference_subcategory.text().strip(),
                        downloader=str(reference_downloader.currentData() or ""),
                        postprocessor_index=int(reference_postprocessor.currentData() or 0),
                    )
                except ValueError:
                    return None

            reference_pending: dict[ConfigPath, dict] = {}

            def load_reference_editor() -> None:
                raw_path = reference_raw_path()
                entry = next((item for item in documented_config_options() if item.get("path") == raw_path), {})
                postprocessor = raw_path.startswith("postprocessor.")
                choose_site = postprocessor or raw_path.startswith("extractor.*.") or raw_path.startswith("extractor.[")
                reference_site.setVisible(choose_site)
                reference_subcategory.setVisible(raw_path.startswith("extractor.*.") and bool(reference_site_name()))
                reference_page_type.setVisible(raw_path.startswith("extractor.*.") and bool(reference_site_name()))
                reference_downloader.setVisible(raw_path.startswith("downloader.*."))
                reference_postprocessor.setVisible(postprocessor)
                if postprocessor:
                    base = ("extractor", *([reference_site_name()] if reference_site_name() else []), "postprocessors")
                    items, _ = config_path_value(proposed_config(), base, [])
                    items = items if isinstance(items, list) else [items] if isinstance(items, (dict, str)) else []
                    kind = raw_path.split(".")[1]
                    selected_index = reference_postprocessor.currentData()
                    reference_postprocessor.blockSignals(True)
                    reference_postprocessor.clear()
                    for index, item in enumerate(items):
                        if isinstance(item, (dict, str)) and resolved_postprocessor_action(proposed_config(), item).get("name") == kind:
                            reference_postprocessor.addItem(f"{kind} #{index + 1}", index)
                    reference_postprocessor.addItem(tr(f"New {kind} action", f"Tindakan {kind} baru"), len(items))
                    match_index = reference_postprocessor.findData(selected_index)
                    reference_postprocessor.setCurrentIndex(max(0, match_index))
                    reference_postprocessor.blockSignals(False)
                reference_help.setPlainText(
                    str(entry.get("description") or "")
                    + "\n\n" + tr("Default: ", "Bawaan: ") + str(entry.get("default_text") or json.dumps(entry.get("default")))
                    + ("\n\n" + tr("Example: ", "Contoh: ") + str(entry["example"]) if entry.get("example") else "")
                    + ("\n\n" + str(entry["notes"]) if entry.get("notes") else "")
                )
                path = reference_path()
                reference_page.setProperty("editorPath", path)
                can_edit = path is not None
                reference_set.setEnabled(can_edit)
                reference_remove.setEnabled(can_edit)
                if not can_edit:
                    reference_status.setText(tr(
                        "Choose a compatible website above for a setting shared by a family of websites.",
                        "Pilih situs yang sesuai di atas untuk pengaturan keluarga situs.",
                    ))
                    reference_value.clear()
                    reference_ack.hide()
                    reference_choice.hide()
                    reference_structured.hide()
                    reference_value.setProperty("dirty", False)
                    return
                type_index = reference_type.findData(str(entry.get("type") or "text"))
                reference_type.setCurrentIndex(max(0, type_index))
                node, explicitly_set = config_path_value(proposed_config(), path, entry.get("default"))
                if postprocessor and not explicitly_set:
                    action, found = config_path_value(proposed_config(), path[:-1])
                    if not found:
                        items, _ = config_path_value(proposed_config(), path[:-2])
                        if path[-2] == 0 and isinstance(items, (dict, str)):
                            action, found = items, True
                    if found and isinstance(action, (dict, str)):
                        node = resolved_postprocessor_action(proposed_config(), action).get(path[-1], node)
                actual_type = "boolean" if isinstance(node, bool) else "integer" if isinstance(node, int) else "number" if isinstance(node, float) else "json" if isinstance(node, (dict, list)) else "text" if isinstance(node, str) else None
                if actual_type:
                    reference_type.setCurrentIndex(reference_type.findData(actual_type))
                reference_structured.configure(
                    allowed_types=tuple(entry.get("allowed_types") or ("list", "object")),
                    string_items=bool(entry.get("string_items")) or path[-1] == "extension-map", item_choices=tuple(entry.get("choices") or entry.get("suggestions") or ()),
                    hint=tr(*_STARTER_OPTION_HELP[path[-1]]) if path[-1] in _STARTER_OPTION_HELP else str(entry.get("description") or ""),
                    example=config_example_value(str(entry.get("example") or "")) or ({"jpeg": "jpg"} if path[-1] == "extension-map" else None),
                    name_hint="jpeg" if path[-1] == "extension-map" else "",
                    value_hint="jpg" if path[-1] == "extension-map" else "journal_mode=WAL" if path[-1] == "archive-pragma" else "",
                    allow_default_key=path[-1] in {"directory", "filename"},
                )
                structured_value = node if node is not None else {} if "object" in str(entry.get("declared_type")) else []
                reference_structured.set_value(structured_value)
                if path[-1] == "extension-map":
                    reference_structured.table.setHorizontalHeaderLabels([tr("Original extension", "Ekstensi asal"), tr("Value type", "Jenis nilai"), tr("Replacement", "Pengganti")])
                else:
                    reference_structured.table.setHorizontalHeaderLabels([tr("Name", "Nama"), tr("Value type", "Jenis nilai"), tr("One item per row", "Satu item per baris")])
                if path[-1] in {"directory", "filename"}:
                    reference_structured.table.setHorizontalHeaderLabels([tr("Condition (blank = otherwise)", "Kondisi (kosong = jika lainnya tidak cocok)"), tr("Value type", "Jenis nilai"), tr("Folder / filename pattern", "Pola folder / nama file")])
                    reference_structured.help.setText(tr("Conditional patterns: each row has a condition and its result. A blank condition is the fallback when no other row matches. Conditions are saved without being evaluated here.", "Pola bersyarat: setiap baris berisi kondisi dan hasilnya. Kondisi kosong dipakai saat baris lain tidak cocok. Kondisi disimpan tanpa dievaluasi di sini."))
                sensitive = is_sensitive_option_key(path[-1])
                reference_ack.setVisible(sensitive or reference_type.currentData() == "json")
                reference_ack.setChecked(False)
                reference_value.setEchoMode(QLineEdit.Password if sensitive else QLineEdit.Normal)
                if sensitive:
                    reference_value.clear()
                    reference_value.setPlaceholderText(tr(
                        "Existing secret hidden; enter a replacement", "Rahasia lama tersembunyi; masukkan pengganti",
                    ))
                elif node is None:
                    reference_value.clear()
                elif isinstance(node, (dict, list)):
                    reference_value.setText(json.dumps(node, ensure_ascii=False))
                elif isinstance(node, bool):
                    reference_value.setText(str(node).lower())
                else:
                    reference_value.setText(str(node))
                choices = list(entry.get("choices") or ())
                if entry.get("type") == "boolean" and not choices:
                    choices = [True, False]
                reference_choice.blockSignals(True)
                reference_choice.clear()
                reference_choice.addItem(tr("Enter a custom value below", "Masukkan nilai khusus di bawah"))
                for choice in choices:
                    label = (tr("Enabled", "Aktif") if choice else tr("Disabled", "Nonaktif")) if isinstance(choice, bool) else str(choice)
                    reference_choice.addItem(label, choice)
                matching = next((index for index in range(1, reference_choice.count()) if
                                 type(reference_choice.itemData(index)) is type(node) and reference_choice.itemData(index) == node), 0)
                reference_choice.setCurrentIndex(matching)
                reference_choice.setVisible(bool(choices) and not sensitive)
                reference_choice.blockSignals(False)
                reference_status.setText(
                    ".".join(str(part) for part in path) + " · "
                    + (tr("Set in config", "Diatur dalam config") if explicitly_set else tr("Manual default", "Default manual"))
                )
                reference_status.setProperty("appliedStatus", reference_status.text())
                reference_remove.setEnabled(explicitly_set)
                reference_value.setProperty("dirty", False)
                pending = reference_pending.get(path)
                if pending:
                    reference_remove.setEnabled(True)
                    reference_type.setCurrentIndex(reference_type.findData(pending["type"]))
                    reference_value.setText(pending["raw"])
                    reference_structured.set_snapshot(pending["snapshot"])
                    reference_ack.setChecked(pending["ack"])
                    reference_choice.blockSignals(True)
                    reference_choice.setCurrentIndex(0)
                    reference_choice.blockSignals(False)
                    reference_value.setProperty("dirty", True)
                    reference_status.setText(reference_status.text() + tr(" · Unsaved draft", " · Draft belum disimpan"))
                show_reference_value_editor()

            def update_reference_editor(*_args) -> None:
                reference_page.setProperty("loading", True)
                try:
                    load_reference_editor()
                finally:
                    reference_page.setProperty("loading", False)

            def reload_reference(*_args) -> None:
                needle = reference_search.text().strip().lower()
                entries = [
                    item for item in documented_config_options()
                    if str(item.get("path") or "").startswith(str(reference_group.currentData() or ""))
                    and (not needle or needle in " ".join((
                        str(item.get("path") or ""),
                        str(item.get("description") or ""),
                        " ".join(_FRIENDLY_OPTION_NAMES.get(str(item.get("path") or "").split(".")[-1], ())),
                    )).lower())
                ]
                reference_table.blockSignals(True)
                reference_table.setRowCount(len(entries))
                for row, entry in enumerate(entries):
                    raw = str(entry.get("path") or "")
                    parts = raw.split(".")
                    names = _FRIENDLY_OPTION_NAMES.get(parts[-1])
                    label = tr(*names) if names else parts[-1].replace("-", " ").capitalize()
                    for column, value in enumerate((
                        label, ".".join(parts[:2]),
                    )):
                        item = QTableWidgetItem(str(value))
                        item.setData(Qt.UserRole, raw)
                        item.setToolTip(raw)
                        reference_table.setItem(row, column, item)
                if entries:
                    reference_table.setCurrentCell(0, 0)
                reference_table.blockSignals(False)
                update_reference_editor()

            def set_reference_option() -> bool:
                path = reference_path()
                if path is None:
                    return False
                try:
                    value = reference_structured.value() if reference_type.currentData() == "json" else parse_typed_config_value(reference_value.text(), str(reference_type.currentData()))
                    if (is_sensitive_option_key(path[-1]) or contains_config_secrets(value)) and not reference_ack.isChecked():
                        raise ValueError(tr("Confirm plain-text storage first.", "Konfirmasikan penyimpanan teks biasa."))
                    additions = {path: value}
                    if reference_raw_path().startswith("postprocessor."):
                        additions.update(reference_action_name(path, reference_raw_path().split(".")[1]))
                    apply_config_path_drafts(proposed_config(), overrides=additions)
                except ValueError as exc:
                    self.show_compact_message("Config path", str(exc), "warning")
                    return False
                path_overrides.update(additions)
                reference_pending.pop(path, None)
                path_removals.discard(path)
                scope.setCurrentIndex(scope.findData("config"))
                refresh_preview()
                update_studio_preview()
                update_reference_editor()
                return True

            def remove_reference_option() -> None:
                path = reference_path()
                if path is None:
                    return
                path_overrides.pop(path, None)
                reference_pending.pop(path, None)
                path_removals.add(path)
                scope.setCurrentIndex(scope.findData("config"))
                refresh_preview()
                update_studio_preview()
                update_reference_editor()

            reference_search.textChanged.connect(reload_reference)
            reference_group.currentIndexChanged.connect(reload_reference)
            reference_table.cellClicked.connect(update_reference_editor)
            reference_table.currentCellChanged.connect(update_reference_editor)
            reference_site.currentIndexChanged.connect(update_reference_editor)
            def refresh_page_types(*_args) -> None:
                category = reference_site_name()
                choices = set(installed_page_types(category))
                saved, _ = config_path_value(proposed_config(), ("extractor", category), {})
                option_keys = {spec.key for spec in config_option_definitions(category)}
                if isinstance(saved, dict):
                    choices.update(key for key, value in saved.items() if isinstance(value, dict) and key not in option_keys and re.fullmatch(r"[a-z][a-z0-9_-]*", key))
                reference_page_type.blockSignals(True)
                reference_page_type.clear()
                reference_page_type.addItem(tr("Whole website", "Seluruh situs"), "")
                for key in sorted(choices):
                    label = tr(*URL_MODE_LABELS[key]) if key in URL_MODE_LABELS else key.replace("-", " ").capitalize()
                    reference_page_type.addItem(f"{label} ({key})", key)
                reference_page_type.blockSignals(False)
                reference_subcategory.clear()
            reference_site.currentIndexChanged.connect(refresh_page_types)
            reference_page_type.currentIndexChanged.connect(lambda: reference_subcategory.setText(str(reference_page_type.currentData() or "")))
            refresh_page_types()
            reference_subcategory.textChanged.connect(update_reference_editor)
            reference_downloader.currentIndexChanged.connect(update_reference_editor)
            reference_postprocessor.currentIndexChanged.connect(update_reference_editor)

            def show_reference_value_editor(*_args) -> None:
                structured = reference_type.currentData() == "json" and reference_path() is not None
                reference_structured.setVisible(structured)
                reference_value.setVisible(not structured)
                path = reference_path()
                sensitive_value = False
                if structured:
                    try:
                        sensitive_value = contains_config_secrets(reference_structured.value())
                    except ValueError:
                        sensitive_value = reference_structured.kind.currentData() == "object" and any(
                            is_sensitive_option_key(row[0]) for row in reference_structured.snapshot()["rows"]
                        )
                reference_ack.setVisible(bool(path) and (sensitive_value or is_sensitive_option_key(path[-1])))

            def reference_action_name(path: ConfigPath, kind: str) -> dict:
                action, found = config_path_value(proposed_config(), path[:-1])
                if not found and path[-2] == 0:
                    action, found = config_path_value(proposed_config(), path[:-2])
                if found and isinstance(action, (dict, str)) and resolved_postprocessor_action(proposed_config(), action).get("name") == kind:
                    return {}
                return {path[:-1] + ("name",): kind}

            def mark_reference_dirty(*_args) -> None:
                if reference_page.property("loading"):
                    return
                path = reference_page.property("editorPath")
                if not path:
                    return
                path = tuple(path)
                raw_path = reference_raw_path()
                reference_pending[path] = {
                    "document": raw_path, "site": reference_site_name(),
                    "subcategory": reference_subcategory.text(), "downloader": reference_downloader.currentData(),
                    "postprocessor": reference_postprocessor.currentData(), "type": reference_type.currentData(),
                    "raw": reference_value.text(), "snapshot": reference_structured.snapshot(), "ack": reference_ack.isChecked(),
                }
                if raw_path.startswith("postprocessor."):
                    # Reserve an action immediately so another action type
                    # cannot reuse this draft's new list index.
                    path_overrides.update(reference_action_name(path, raw_path.split(".")[1]))
                reference_value.setProperty("dirty", True)
                reference_status.setText(str(reference_status.property("appliedStatus") or "") + tr(
                    f" · {len(reference_pending)} unsaved edit(s); Save config saves all",
                    f" · {len(reference_pending)} edit belum disimpan; Simpan config menyimpan semua",
                ))

            reference_value.textChanged.connect(mark_reference_dirty)
            reference_ack.toggled.connect(mark_reference_dirty)
            reference_type.currentIndexChanged.connect(show_reference_value_editor)
            reference_type.activated.connect(mark_reference_dirty)
            reference_structured.valueChanged.connect(mark_reference_dirty)
            reference_structured.valueChanged.connect(show_reference_value_editor)

            def choose_reference_value(index: int) -> None:
                if index <= 0:
                    return
                value = reference_choice.currentData()
                kind = "boolean" if isinstance(value, bool) else "integer" if isinstance(value, int) else "number" if isinstance(value, float) else "null" if value is None else "text"
                reference_type.setCurrentIndex(reference_type.findData(kind))
                reference_value.setText(json.dumps(value) if not isinstance(value, str) else value)
                mark_reference_dirty()

            reference_choice.currentIndexChanged.connect(choose_reference_value)
            reference_set.clicked.connect(set_reference_option)
            reference_remove.clicked.connect(remove_reference_option)
            reload_reference()

            def apply_pending_reference_options() -> bool:
                additions: dict[ConfigPath, object] = {}
                for path, draft in list(reference_pending.items()):
                    try:
                        value = StructuredConfigEditor.parse_snapshot(draft["snapshot"], parse_typed_config_value) if draft["type"] == "json" else parse_typed_config_value(draft["raw"], draft["type"])
                        if (is_sensitive_option_key(path[-1]) or contains_config_secrets(value)) and not draft["ack"]:
                            raise ValueError(tr("Confirm plain-text storage first.", "Konfirmasikan penyimpanan teks biasa."))
                        additions[path] = value
                        apply_config_path_drafts(proposed_config(), overrides=additions)
                    except ValueError as exc:
                        reference_group.setCurrentIndex(0)
                        reference_search.setText(draft["document"])
                        reference_site.setCurrentIndex(max(0, reference_site.findText(draft["site"])))
                        reference_subcategory.setText(draft["subcategory"])
                        reference_downloader.setCurrentIndex(max(0, reference_downloader.findData(draft["downloader"])))
                        reference_postprocessor.setCurrentIndex(max(0, reference_postprocessor.findData(draft["postprocessor"])))
                        studio_tabs.setCurrentWidget(reference_page)
                        self.show_compact_message(tr("Check this setting", "Periksa pengaturan ini"),
                                                  ".".join(map(str, path)) + ": " + str(exc), "warning")
                        return False
                path_overrides.update(additions)
                for path in additions:
                    path_removals.discard(path)
                reference_pending.clear()
                update_reference_editor()
                if additions:
                    scope.setCurrentIndex(scope.findData("config"))
                    refresh_preview()
                    update_studio_preview()
                return True

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
            studio_tabs.addTab(archive_page, tr("Download History", "Riwayat Unduhan"))

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
            advanced_tabs.addTab(advanced_page, tr("Custom Option", "Opsi Khusus"))

            # Safe draft preview ----------------------------------------------
            draft_page = QWidget()
            draft_l = QVBoxLayout(draft_page)
            draft_preview = QPlainTextEdit()
            draft_preview.setObjectName("siteConfigDraftPreview")
            draft_preview.setReadOnly(True)
            draft_preview.setLineWrapMode(QPlainTextEdit.NoWrap)
            studio_summary = QPlainTextEdit()
            studio_summary.setObjectName("siteConfigChangeSummary")
            studio_summary.setReadOnly(True)
            studio_summary.setMaximumHeight(170)
            draft_l.addWidget(QLabel(tr(
                "Effective config (credentials hidden)",
                "Config efektif (credential disembunyikan)",
            )))
            draft_l.addWidget(studio_summary)
            draft_l.addWidget(QLabel(tr("Generated config (advanced preview)", "Config yang dibuat (pratinjau lanjutan)")))
            draft_l.addWidget(draft_preview, 1)
            reset_draft = QPushButton(tr("Discard all Site Studio draft changes", "Buang semua perubahan draft Studio Situs"))
            draft_l.addWidget(reset_draft, 0, Qt.AlignLeft)
            studio_tabs.addTab(draft_page, tr("Review Changes", "Periksa Perubahan"))
            studio_tabs.addTab(advanced_tabs, tr("More Tools", "Alat Lanjutan"))

            def stage_postprocessors(category: str, actions: list[dict | str] | None) -> None:
                path = ("extractor", *([category] if category else []), "postprocessors")
                for key in list(path_overrides):
                    if key[:len(path)] == path:
                        path_overrides.pop(key)
                path_removals.difference_update(key for key in list(path_removals) if key[:len(path)] == path)
                if actions is None:
                    path_removals.add(path)
                else:
                    path_overrides[path] = actions
                scope.setCurrentIndex(scope.findData("config"))
                refresh_preview()
                update_studio_preview()

            postprocessor_editor = PostprocessorEditor(
                site_names, proposed_config, stage_postprocessors, site=selected_studio_site, indonesian=ind,
            )
            postprocessor_editor.setProperty("sourceSite", selected_studio_site)
            studio_tabs.insertTab(1, postprocessor_editor, tr("After downloading", "Setelah Mengunduh"))

            def open_postprocessor_catalog(category: str, name: str, index: int) -> None:
                reference_group.setCurrentIndex(max(0, reference_group.findData("postprocessor.")))
                reference_search.setText("postprocessor." + (name + "." if name else ""))
                reference_site.setCurrentIndex(max(0, reference_site.findText(category)))
                reference_postprocessor.setCurrentIndex(max(0, reference_postprocessor.findData(index)))
                studio_tabs.setCurrentWidget(reference_page)

            postprocessor_editor.advancedRequested.connect(open_postprocessor_catalog)

            def stage_postprocessor_setting(category: str, key: str, value: object) -> None:
                path = ("extractor", *([category] if category else []), key)
                path_removals.discard(path)
                path_overrides[path] = value
                scope.setCurrentIndex(scope.findData("config"))
                refresh_preview()
                update_studio_preview()

            postprocessor_editor.settingRequested.connect(stage_postprocessor_setting)

            def update_studio_preview() -> None:
                effective = proposed_config()
                safe = redact_auth_config(effective)
                draft_preview.setPlainText(json.dumps(safe, indent=2, ensure_ascii=False))
                studio_summary.setPlainText(describe_changes(proposed_config()))

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

            site_options_pending: dict[str, dict[str, tuple[object, bool, bool]]] = {}

            def site_form_specs(category: str) -> tuple[ConfigOptionSpec, ...]:
                specs = {spec.key: spec for spec in config_option_definitions(category)}
                block, _ = config_path_value(proposed_config(), ("extractor", category), {})
                if isinstance(block, dict):
                    for key, value in block.items():
                        if key not in specs and re.fullmatch(r"[a-z][a-z0-9-]*", key):
                            specs[key] = ConfigOptionSpec(
                                key=key, value_type=_option_value_type(key, value), group="From your config",
                                description=tr("Existing option from your config. Its value is preserved until you edit it.", "Opsi lama dari config Anda. Nilainya dipertahankan sampai Anda mengeditnya."),
                                sensitive=is_sensitive_option_key(key),
                            )
                return tuple(sorted(specs.values(), key=lambda spec: (spec.group, spec.key)))

            def friendly_option_name(key: str) -> str:
                names = _FRIENDLY_OPTION_NAMES.get(key)
                return tr(*names) if names else key.replace("-", " ").replace("_", " ").capitalize()

            def update_site_options_status(category: str, shown_count: int, hidden_count: int) -> None:
                pending_count = len(site_options_pending.get(category, {}))
                site_options_status.setText(tr(
                    f"{category}: {shown_count} settings shown" +
                    (f" · {hidden_count} more under Show all settings" if hidden_count else "") +
                    f" · {pending_count} change(s) ready to apply.",
                    f"{category}: {shown_count} pengaturan tampil" +
                    (f" · {hidden_count} lainnya di Tampilkan semua pengaturan" if hidden_count else "") +
                    f" · {pending_count} perubahan siap diterapkan.",
                ))
                site_options_apply.setEnabled(pending_count > 0)
                site_options_apply.setText(tr(
                    f"Apply changes for {category} ({pending_count})",
                    f"Terapkan perubahan untuk {category} ({pending_count})",
                ))

            def reload_site_options(*_args) -> None:
                while site_options_cards.count():
                    item = site_options_cards.takeAt(0)
                    widget = item.widget()
                    if widget is not None:
                        widget.setParent(None)
                        widget.deleteLater()
                category = site_options_site.currentText().strip().lower()
                if category not in site_names:
                    site_options_status.setText(tr(
                        "Choose a site from the suggestions to see its form.",
                        "Pilih situs dari saran untuk melihat formulirnya.",
                    ))
                    site_options_apply.setEnabled(False)
                    return
                if site_options_page.property("lastSite") != category:
                    site_options_page.setProperty("lastSite", category)
                    site_options_show_all.blockSignals(True)
                    site_options_show_all.setChecked(False)
                    site_options_show_all.blockSignals(False)
                    if site_options_search.text():
                        site_options_search.clear()
                        return

                specs = site_form_specs(category)
                needle = site_options_search.text().strip().lower()
                all_matches = [
                    spec for spec in specs
                    if not needle or needle in " ".join((
                        spec.key, friendly_option_name(spec.key), spec.group, spec.description,
                    )).lower()
                ]
                beginner_mode = not needle and not site_options_show_all.isChecked()
                goal = str(site_options_goal.currentData())
                goal_keys = {
                    "paths": {"base-directory", "directory", "filename"},
                    "history": {"archive"},
                    "network": {"retries", "timeout", "sleep", "sleep-request", "sleep-429"},
                }
                if beginner_mode and goal in goal_keys:
                    matching = [spec for spec in all_matches if spec.key in goal_keys[goal]]
                elif beginner_mode and goal == "content":
                    matching = [spec for spec in all_matches if spec.scope == "site" and spec.key in _STARTER_SITE_OPTIONS]
                elif beginner_mode:
                    beginner_specific = [
                        spec for spec in all_matches
                        if spec.scope == "site" and (
                            spec.key in _STARTER_SITE_OPTIONS or
                            (spec.value_type == "boolean" and spec.group in {"Content", "Metadata"})
                        )
                    ]
                    priority = {key: index for index, key in enumerate(_STARTER_SITE_OPTIONS)}
                    beginner_specific.sort(key=lambda spec: priority.get(spec.key, len(priority)))
                    if not beginner_specific:
                        beginner_specific = [spec for spec in all_matches if spec.scope == "site"][:4]
                    matching = beginner_specific[:8] + [
                        spec for spec in all_matches
                        if spec.scope != "site" and spec.key in _STARTER_SHARED_OPTIONS
                    ]
                else:
                    matching = all_matches
                guided_paths = beginner_mode and goal == "paths"
                specific = [spec for spec in matching if spec.scope == "site" and not (guided_paths and spec.key in {"directory", "filename"})]
                shared = [spec for spec in matching if spec.scope != "site" and not (guided_paths and spec.key in {"directory", "filename"})]
                hidden_count = len(all_matches) - len(matching)
                update_site_options_status(category, len(matching), hidden_count)
                pending = site_options_pending.setdefault(category, {})

                def mark_change(key: str, value: object, parsed: bool, acknowledged: bool = False) -> None:
                    pending[key] = (value, parsed, acknowledged)
                    reset = studio.findChild(QPushButton, f"removeSiteOption_{key}")
                    if reset is not None:
                        reset.setEnabled(True)
                    update_site_options_status(category, len(matching), hidden_count)

                def stage_site_value(key: str, value: object, remove: bool) -> None:
                    pending.pop(key, None)
                    path = ("extractor", category, key)
                    block = site_overrides.get(category)
                    if block is not None:
                        block.pop(key, None)
                    path_overrides.pop(path, None)
                    path_removals.discard(path)
                    if remove:
                        path_removals.add(path)
                    else:
                        path_overrides[path] = value
                    scope.setCurrentIndex(scope.findData("config"))
                    refresh_preview()
                    update_studio_preview()
                    reload_site_options()
                    reload_config_options()

                guided_path_widgets = []
                if guided_paths:
                    for key in ("directory", "filename"):
                        spec = next(spec for spec in specs if spec.key == key)
                        value, source, explicit = current_config_value(category, spec)
                        if source == tr("Built-in default", "Default bawaan"):
                            value = None  # Real filename/folder defaults differ by extractor.
                        form_snapshot = None
                        if key in pending:
                            raw, parsed, _ = pending[key]
                            try:
                                if isinstance(raw, dict) and raw.get("path_rule_form") is True:
                                    value = dict(raw["rules"])
                                    form_snapshot = raw
                                else:
                                    value = raw if parsed else StructuredConfigEditor.parse_snapshot(raw, parse_typed_config_value) if isinstance(raw, dict) and "rows" in raw else parse_typed_config_value(str(raw), spec.value_type)
                            except ValueError:
                                note = QLabel(tr("An unsaved advanced edit needs correction. Updating a guided rule replaces that edit.", "Edit lanjutan yang belum diterapkan perlu diperbaiki. Memperbarui aturan terpandu mengganti edit tersebut."))
                                note.setWordWrap(True)
                                site_options_cards.addWidget(note)
                            explicit = True

                        def stage_rule(value: object, remove: bool, field: str = key) -> None:
                            if remove:
                                stage_site_value(field, value, True)
                            else:
                                mark_change(field, value, not (isinstance(value, dict) and value.get("path_rule_form") is True))

                        editor = PathRulesEditor(key, value, explicit, stage_rule, site=category, indonesian=ind)
                        if form_snapshot is not None:
                            editor.restore_snapshot(form_snapshot)
                        guided_path_widgets.append(editor)

                if beginner_mode and goal in {"content", "starter"}:
                    filter_spec = next(spec for spec in specs if spec.key == "file-filter")
                    filter_value, _, filter_explicit = current_config_value(category, filter_spec)
                    if "file-filter" in pending:
                        filter_value = pending["file-filter"][0]
                        filter_explicit = True

                    site_options_cards.addWidget(ContentFilterEditor(
                        filter_value, filter_explicit, lambda value, remove: stage_site_value("file-filter", value, remove), indonesian=ind,
                    ))

                if beginner_mode and goal == "history":
                    history_spec = next(spec for spec in specs if spec.key == "archive")
                    archive_value, _, _ = current_config_value(category, history_spec)
                    if "archive" in pending:
                        archive_value = pending["archive"][0]
                    history_toggle = QCheckBox(tr("Skip items already recorded in download history", "Lewati item yang sudah tercatat di riwayat unduhan"))
                    history_toggle.setObjectName("siteHistoryEnabled")
                    history_toggle.setChecked(bool(archive_value))
                    history_toggle.toggled.connect(lambda enabled: (
                        mark_change("archive", str(archive_value or APP_DIR / "archives" / (category + ".sqlite3")) if enabled else None, True),
                        reload_site_options(),
                    ))
                    site_options_cards.addWidget(history_toggle)
                    history_note = QLabel(tr("Enable this to create a history file for this website. Its database is created when downloads run; SQLite options can stay at their defaults.", "Aktifkan untuk memakai file riwayat situs ini. Database dibuat saat unduhan berjalan; pengaturan SQLite dapat dibiarkan bawaan."))
                    history_note.setWordWrap(True)
                    site_options_cards.addWidget(history_note)

                def add_group(group_name: str, group_specs: list[ConfigOptionSpec]) -> None:
                    card = QFrame()
                    card.setObjectName("card")
                    card_l = QVBoxLayout(card)
                    card_l.setContentsMargins(14, 12, 14, 12)
                    heading = QLabel(group_name)
                    heading.setObjectName("sectionTitle")
                    card_l.addWidget(heading)
                    fields = QGridLayout()
                    fields.setHorizontalSpacing(18)
                    fields.setVerticalSpacing(12)
                    fields.setColumnStretch(0, 1)
                    fields.setColumnStretch(1, 1)
                    field_row = field_column = 0

                    for index, spec in enumerate(group_specs):
                        current, source, explicit = current_config_value(category, spec)
                        saved = pending.get(spec.key)
                        field = QWidget()
                        field_l = QVBoxLayout(field)
                        field_l.setContentsMargins(0, 0, 0, 0)
                        field_l.setAlignment(Qt.AlignTop)
                        display_name = friendly_option_name(spec.key)
                        state = (
                            tr("custom for this website", "khusus situs ini") if explicit else
                            tr("from shared settings", "dari pengaturan bersama")
                            if source == tr("Inherited General", "Warisan Umum") else
                            tr("default", "bawaan")
                        )
                        label = QLabel(f"{display_name}  ·  {state}")
                        label.setToolTip(f"{spec.key}: {spec.description}")
                        field_l.addWidget(label)
                        hint = None
                        if beginner_mode or spec.key in _STARTER_OPTION_HELP or spec.example:
                            help_text = _STARTER_OPTION_HELP.get(spec.key) if spec.key != "previews" or category == "instagram" else None
                            hint = QLabel(tr(*help_text) if help_text else spec.description)
                            hint.setObjectName("subtle")
                            hint.setWordWrap(True)
                            field_l.addWidget(hint)
                        editor_row = QHBoxLayout()
                        editor_row.setContentsMargins(0, 0, 0, 0)

                        if spec.value_type == "json" and spec.choices and all(isinstance(choice, str) for choice in spec.choices) and (
                            isinstance(spec.default, list) or "list" in spec.allowed_types
                        ):
                            control = QWidget()
                            control.setObjectName(f"siteOptionChecklist_{spec.key}")
                            checklist = QGridLayout(control)
                            checklist.setContentsMargins(0, 0, 0, 0)
                            checklist.setColumnStretch(0, 1)
                            checklist.setColumnStretch(1, 1)
                            selected_values = saved[0] if saved else current
                            if isinstance(selected_values, str):
                                selected_values = selected_values.split(",")
                                if selected_values == ["all"]:
                                    selected_values = list(spec.choices)
                            selected_values = selected_values if isinstance(selected_values, list) else []
                            checks: list[QCheckBox] = []
                            choices = list(spec.choices) + [value for value in selected_values if value not in spec.choices]
                            for choice_index, choice in enumerate(choices):
                                choice_name = tr(*_INSTAGRAM_INCLUDE_NAMES[choice]) if category == "instagram" and spec.key == "include" and choice in _INSTAGRAM_INCLUDE_NAMES else str(choice).replace("-", " ").capitalize()
                                checkbox = QCheckBox(choice_name)
                                checkbox.setObjectName(f"siteOptionList_{spec.key}_{choice}")
                                checkbox.setProperty("configValue", choice)
                                checkbox.setChecked(choice in selected_values)
                                checks.append(checkbox)
                                checklist.addWidget(checkbox, choice_index // 2, choice_index % 2)

                            def update_list(_checked: bool = False, key: str = spec.key,
                                            boxes: list[QCheckBox] = checks) -> None:
                                mark_change(key, [box.property("configValue") for box in boxes if box.isChecked()], True)

                            for checkbox in checks:
                                checkbox.toggled.connect(update_list)
                        elif spec.value_type == "boolean" and "list" not in spec.allowed_types and (
                            not spec.choices or set(spec.choices) == {True, False}
                        ):
                            control = QCheckBox(tr("Enabled", "Aktif"))
                            control.setObjectName(f"siteOptionCheck_{spec.key}")
                            control.setChecked(bool(saved[0] if saved else current))
                            control.toggled.connect(
                                lambda checked, key=spec.key: mark_change(key, checked, True)
                            )
                        elif spec.choices and "list" not in spec.allowed_types and "object" not in spec.allowed_types:
                            control = QComboBox()
                            control.setObjectName(f"siteOptionChoice_{spec.key}")
                            choices = list(spec.choices)
                            choice_descriptions = spec.choice_help
                            if category == "instagram" and spec.key == "videos":
                                choice_descriptions = {key: tr(*description) for key, description in _INSTAGRAM_VIDEO_HELP.items()}
                                if hint:
                                    hint.setText(tr("Choose how videos are downloaded. The selected mode is explained below.", "Pilih cara mengunduh video. Mode terpilih dijelaskan di bawah."))
                            selected = saved[0] if saved else current
                            if selected not in choices:
                                choices.insert(0, selected)
                            for choice in choices:
                                if beginner_mode and isinstance(choice, bool):
                                    choice_label = tr("Enabled", "Aktif") if choice else tr("Disabled", "Nonaktif")
                                elif beginner_mode and isinstance(choice, str):
                                    choice_label = choice.replace("-", " ").capitalize()
                                else:
                                    choice_label = config_value_text(choice) or "null"
                                control.addItem(choice_label, choice)
                                control.setItemData(control.count() - 1, choice_descriptions.get(json.dumps(choice, ensure_ascii=False), ""), Qt.ToolTipRole)
                            control.setCurrentIndex(max(0, control.findData(selected)))
                            control.currentIndexChanged.connect(
                                lambda _index, key=spec.key, picker=control: mark_change(
                                    key, picker.currentData(), True
                                )
                            )
                            choice_hint = QLabel()
                            choice_hint.setWordWrap(True)
                            def describe_choice(_index: int = 0, picker: QComboBox = control, label: QLabel = choice_hint,
                                                descriptions: dict = choice_descriptions) -> None:
                                selected = picker.currentData()
                                help_text = descriptions.get(json.dumps(selected, ensure_ascii=False), "")
                                label.setText(str(picker.currentText()) + (" — " + help_text if help_text else ""))
                            control.currentIndexChanged.connect(describe_choice)
                            describe_choice()
                            field_l.addWidget(choice_hint)
                        elif not spec.sensitive and (spec.value_type == "json" or "list" in spec.allowed_types or isinstance(current, (list, dict))):
                            types = spec.allowed_types or (("object",) if isinstance(spec.default, dict) else ("list",))
                            control = StructuredConfigEditor(
                                parse_typed_config_value, indonesian=ind, allowed_types=types,
                                string_items=spec.string_items or spec.key in {"archive-pragma", "extension-map"},
                                item_choices=spec.choices or spec.suggestions, example=config_example_value(spec.example) or ({"jpeg": "jpg"} if spec.key == "extension-map" else None),
                                hint=tr(*_STARTER_OPTION_HELP[spec.key]) if spec.key in _STARTER_OPTION_HELP and (spec.key != "previews" or category == "instagram") else spec.description,
                                name_hint="jpeg" if spec.key == "extension-map" else "",
                                value_hint="jpg" if spec.key == "extension-map" else "journal_mode=WAL" if spec.key == "archive-pragma" else "",
                                allow_default_key=spec.key in {"directory", "filename"},
                            )
                            if hint:
                                hint.hide()
                            control.setObjectName(f"siteOptionStructured_{spec.key}")
                            if spec.key == "extension-map":
                                control.table.setHorizontalHeaderLabels([tr("Original extension", "Ekstensi asal"), tr("Value type", "Jenis nilai"), tr("Replacement", "Pengganti")])
                            elif spec.key in {"directory", "filename"}:
                                control.table.setHorizontalHeaderLabels([tr("Condition (blank = otherwise)", "Kondisi (kosong = jika lainnya tidak cocok)"), tr("Value type", "Jenis nilai"), tr("Folder / filename pattern", "Pola folder / nama file")])
                                control.help.setText(tr("Conditional patterns: keep one blank condition for the fallback. Each value is the folder list or filename to use when its condition matches. Conditions are saved without being evaluated here.", "Pola bersyarat: satu kondisi kosong menjadi pilihan jika lainnya tidak cocok. Nilai berisi daftar folder atau nama file saat kondisi cocok. Kondisi disimpan tanpa dievaluasi di sini."))
                            value = saved[0] if saved else current
                            if saved and not saved[1] and isinstance(value, dict) and "rows" in value:
                                control.set_snapshot(value)
                            else:
                                control.set_value(value if value is not None else {} if types == ("object",) else [])
                        else:
                            control = QLineEdit()
                            control.setObjectName(f"siteOptionValue_{spec.key}")
                            control.setText(str(saved[0]) if saved else (
                                "" if spec.sensitive else config_value_text(current)
                            ))
                            if spec.sensitive:
                                control.setEchoMode(QLineEdit.Password)
                                control.setPlaceholderText(tr(
                                    "Enter replacement (stored value hidden)",
                                    "Masukkan pengganti (nilai lama tersembunyi)",
                                ))
                            else:
                                control.setPlaceholderText(tr("Enter a value", "Masukkan nilai"))
                        control.setToolTip(spec.description)
                        editor_row.addWidget(control, 1)
                        if isinstance(control, QLineEdit) and spec.key in {"base-directory", "archive"}:
                            browse_button = QPushButton(tr("Browse…", "Pilih…"))
                            browse_button.setObjectName(f"siteOptionBrowse_{spec.key}")

                            def browse_path(_checked: bool = False, key: str = spec.key,
                                            target: QLineEdit = control) -> None:
                                if key == "archive":
                                    chosen, _ = QFileDialog.getSaveFileName(
                                        studio, tr("Choose download history file", "Pilih file riwayat unduhan"),
                                        target.text(), "SQLite (*.sqlite3 *.sqlite *.db);;All files (*.*)",
                                    )
                                else:
                                    chosen = QFileDialog.getExistingDirectory(
                                        studio, tr("Choose download folder", "Pilih folder unduhan"),
                                        target.text(),
                                    )
                                if chosen:
                                    target.setText(chosen)

                            browse_button.clicked.connect(browse_path)
                            editor_row.addWidget(browse_button)
                        remove_button = QPushButton(tr("Use default", "Pakai bawaan"))
                        remove_button.setObjectName(f"removeSiteOption_{spec.key}")
                        remove_button.setToolTip(tr(
                            "Undo this website's custom value.",
                            "Batalkan nilai khusus untuk situs ini.",
                        ))
                        remove_button.setEnabled(explicit or saved is not None)
                        editor_row.addWidget(remove_button)
                        field_l.addLayout(editor_row)
                        acknowledge = None
                        if spec.sensitive or isinstance(control, StructuredConfigEditor):
                            acknowledge = QCheckBox(tr(
                                "Allow plain-text storage", "Izinkan penyimpanan teks biasa",
                            ))
                            acknowledge.setObjectName(f"siteOptionAcknowledge_{spec.key}")
                            acknowledge.setChecked(bool(saved and saved[2]))
                            acknowledge.toggled.connect(
                                lambda checked, key=spec.key: pending.__setitem__(
                                    key, (pending[key][0], pending[key][1], checked)
                                ) if key in pending else None
                            )
                            field_l.addWidget(acknowledge)
                            if isinstance(control, StructuredConfigEditor):
                                def show_secret_ack(editor: StructuredConfigEditor = control, box: QCheckBox = acknowledge) -> None:
                                    try:
                                        sensitive_value = contains_config_secrets(editor.value())
                                    except ValueError:
                                        sensitive_value = editor.kind.currentData() == "object" and any(
                                            is_sensitive_option_key(row[0]) for row in editor.snapshot()["rows"]
                                        )
                                    box.setVisible(sensitive_value)
                                control.valueChanged.connect(show_secret_ack)
                                show_secret_ack()
                        if isinstance(control, QLineEdit):
                            control.textChanged.connect(
                                lambda value, key=spec.key, ack_box=acknowledge: mark_change(
                                    key, value, False, bool(ack_box and ack_box.isChecked())
                                )
                            )
                            if spec.key == "filename":
                                name_preview = QLabel()
                                name_preview.setTextFormat(Qt.PlainText)
                                name_preview.setWordWrap(True)
                                name_preview.setObjectName("siteFilenameExample")
                                def preview_name(_text: str = "", target: QLineEdit = control, label: QLabel = name_preview) -> None:
                                    try:
                                        example = filename_example(target.text(), category)
                                        label.setText(tr("Example with sample data: ", "Contoh dengan data ilustrasi: ") + example)
                                    except ValueError:
                                        label.setText(tr("Advanced pattern preserved. Actual file names depend on this website's metadata.", "Pola lanjutan dipertahankan. Nama file sebenarnya mengikuti metadata situs ini."))
                                control.textChanged.connect(preview_name)
                                preview_name()
                                field_l.addWidget(name_preview)
                        elif isinstance(control, StructuredConfigEditor):
                            def update_structured(key: str = spec.key, editor: StructuredConfigEditor = control,
                                                  ack_box: QCheckBox | None = acknowledge) -> None:
                                mark_change(key, editor.snapshot(), False, bool(ack_box and ack_box.isChecked()))

                            control.valueChanged.connect(update_structured)

                        def remove_value(_checked: bool = False, key: str = spec.key) -> None:
                            pending.pop(key, None)
                            block = site_overrides.get(category)
                            if block is not None:
                                block.pop(key, None)
                                if not block:
                                    site_overrides.pop(category, None)
                            site_removals.setdefault(category, set()).add(key)
                            scope.setCurrentIndex(scope.findData("config"))
                            refresh_preview()
                            update_studio_preview()
                            reload_site_options()
                            reload_config_options()

                        remove_button.clicked.connect(remove_value)
                        wide = isinstance(control, StructuredConfigEditor) or len(group_specs) == 1
                        if wide and field_column:
                            field_row += 1
                            field_column = 0
                        fields.addWidget(field, field_row, field_column, 1, 2 if wide else 1, Qt.AlignTop)
                        if wide or field_column:
                            field_row += 1
                            field_column = 0
                        else:
                            field_column = 1
                    card_l.addLayout(fields)
                    site_options_cards.addWidget(card)

                if not specific and not needle:
                    note = QLabel(tr(
                        "This website uses the common download settings below.",
                        "Situs ini memakai pengaturan unduhan umum di bawah.",
                    ))
                    note.setWordWrap(True)
                    site_options_cards.addWidget(note)
                if beginner_mode:
                    if specific:
                        add_group(tr("What to download", "Apa yang diunduh"), specific)
                    if shared:
                        add_group(tr("Saving and reliability", "Penyimpanan dan keandalan"), shared)
                else:
                    for section, entries in (
                        (tr("Website options", "Opsi situs"), specific),
                        (tr("Other settings for this website", "Pengaturan lain untuk situs ini"), shared),
                    ):
                        for group in dict.fromkeys(spec.group for spec in entries):
                            add_group(f"{section} · {group}", [spec for spec in entries if spec.group == group])
                if not matching:
                    site_options_cards.addWidget(QLabel(tr(
                        "No matching options.", "Tidak ada opsi yang cocok.",
                    )))
                for editor in guided_path_widgets:
                    site_options_cards.addWidget(editor)
                site_options_cards.addStretch(1)

            def apply_generated_site_options(category: str | None = None) -> bool:
                failed_key = ""
                try:
                    category = category or normalized_site(site_options_site)
                    pending = site_options_pending.get(category, {})
                    if not pending:
                        return True
                    specs = {spec.key: spec for spec in site_form_specs(category)}
                    block: dict[str, object] = {}
                    for key, (raw, parsed, acknowledged) in pending.items():
                        failed_key = key
                        spec = specs[key]
                        if spec.sensitive and (not raw or not acknowledged):
                            raise ValueError(tr(
                                f"Enter {friendly_option_name(key)} and confirm plain-text storage.",
                                f"Masukkan {friendly_option_name(key)} dan konfirmasikan penyimpanan teks biasa.",
                            ))
                        try:
                            block[key] = raw if parsed else PathRulesEditor.parse_snapshot(raw) if isinstance(raw, dict) and raw.get("path_rule_form") is True else StructuredConfigEditor.parse_snapshot(
                                raw, parse_typed_config_value
                            ) if isinstance(raw, dict) and "rows" in raw else parse_typed_config_value(str(raw), spec.value_type)
                            if contains_config_secrets(block[key]) and not acknowledged:
                                raise ValueError(tr("Confirm plain-text storage first.", "Konfirmasikan penyimpanannya dalam teks biasa."))
                        except ValueError as exc:
                            raise ValueError(f"{friendly_option_name(key)}: {exc}") from exc
                except (KeyError, ValueError) as exc:
                    if category in site_names:
                        site_options_site.setCurrentIndex(site_options_site.findText(category))
                    studio_tabs.setCurrentWidget(site_options_page)
                    if failed_key:
                        failed_edit = site_options_pending.get(category, {}).get(failed_key)
                        if failed_edit and isinstance(failed_edit[0], dict) and failed_edit[0].get("path_rule_form") is True:
                            site_options_search.clear()
                            site_options_goal.setCurrentIndex(site_options_goal.findData("paths"))
                            reload_site_options()
                        else:
                            site_options_search.setText(failed_key)
                    self.show_compact_message(tr("Check this setting", "Periksa pengaturan ini"), str(exc), "warning")
                    return False
                merge_override(category, block)
                pending.clear()
                reload_site_options()
                reload_config_options()
                return True

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
                    config_option_key.blockSignals(True)
                    config_option_key.clear()
                    config_option_key.blockSignals(False)
                    config_option_table.setRowCount(0)
                    option_description.setText(tr(
                        "Choose a site from the suggestions to see its options.",
                        "Pilih situs dari saran untuk melihat opsinya.",
                    ))
                    config_option_apply.setEnabled(False)
                    config_option_remove.setEnabled(False)
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
                site_options_pending.clear()
                reference_pending.clear()
                general_removals.clear()
                site_removals.clear()
                path_overrides.clear()
                path_removals.clear()
                refresh_preview()
                update_studio_preview()
                reload_config_options()
                reload_site_options()
                reload_reference()

            def sync_site_option_picker(text: str, target: QComboBox) -> None:
                category = text.strip().lower()
                if category in site_names and target.currentText() != category:
                    target.setCurrentIndex(target.findText(category))

            site_archive_browse.clicked.connect(browse_site_archive)
            archive_load.clicked.connect(load_archive)
            archive_apply.clicked.connect(apply_archive)
            advanced_key.textChanged.connect(update_advanced_visibility)
            advanced_show.toggled.connect(update_advanced_visibility)
            advanced_apply.clicked.connect(apply_advanced)
            reset_draft.clicked.connect(discard_draft)
            config_scope.currentIndexChanged.connect(reload_config_options)
            config_site.currentTextChanged.connect(reload_config_options)
            config_site.currentTextChanged.connect(
                lambda value: sync_site_option_picker(value, site_options_site)
            )
            site_options_site.currentTextChanged.connect(reload_site_options)
            site_options_site.currentTextChanged.connect(
                lambda value: sync_site_option_picker(value, config_site)
            )
            site_options_search.textChanged.connect(reload_site_options)
            site_options_show_all.toggled.connect(reload_site_options)
            def choose_site_goal() -> None:
                if site_options_goal.currentData() == "after":
                    studio_tabs.setCurrentWidget(postprocessor_editor)
                    return
                site_options_search.clear()
                site_options_show_all.setChecked(False)
                reload_site_options()
            site_options_goal.currentIndexChanged.connect(choose_site_goal)
            site_options_apply.clicked.connect(lambda _checked=False: apply_generated_site_options())
            config_option_search.textChanged.connect(reload_config_options)
            config_option_key.currentIndexChanged.connect(configure_config_option)
            config_option_table.cellClicked.connect(choose_table_option)
            config_option_show.toggled.connect(toggle_config_secret)
            config_option_apply.clicked.connect(apply_config_option)
            config_option_remove.clicked.connect(remove_config_option)
            def visit_studio_tab(index: int) -> None:
                if studio_tabs.widget(index) is site_options_page:
                    reload_site_options()
                elif studio_tabs.widget(index) is draft_page:
                    apply_pending_site_options()
                    update_studio_preview()
                elif studio_tabs.widget(index) is postprocessor_editor:
                    if apply_pending_site_options():
                        category = site_options_site.currentText().strip().lower()
                        if category in site_names and postprocessor_editor.property("sourceSite") != category:
                            postprocessor_editor.site.setCurrentIndex(postprocessor_editor.site.findData(category))
                            postprocessor_editor.setProperty("sourceSite", category)
                        postprocessor_editor.reload()

            studio_tabs.currentChanged.connect(visit_studio_tab)

            footer = QHBoxLayout()
            footer.addStretch(1)
            done = QPushButton(tr("Keep draft & return", "Simpan draf & kembali"))
            done.setObjectName("siteConfigDone")
            done.setToolTip(tr(
                "Keep changes in this Config Maker draft. Save config writes them to your file.",
                "Pertahankan perubahan dalam draf Pembuat Config ini. Simpan config menulisnya ke file.",
            ))

            def apply_pending_site_options() -> bool:
                if not apply_pending_reference_options():
                    return False
                for category, pending in list(site_options_pending.items()):
                    if pending and not apply_generated_site_options(category):
                        return False
                return True

            def finish_site_studio() -> None:
                if apply_pending_site_options():
                    studio.accept()

            def save_site_config() -> None:
                if apply_pending_site_options() and write_config(
                    self.config_path or detect_config_path() or str(APP_DIR / "config.json")
                ):
                    studio.accept()

            done.clicked.connect(finish_site_studio)
            footer.addWidget(done)
            save_config = QPushButton(tr("Save config", "Simpan config"))
            save_config.setObjectName("siteConfigSave")
            save_config.setToolTip(tr(
                "Save this Config Maker draft and changes for every website to the file shown above.",
                "Simpan draf Pembuat Config dan perubahan seluruh situs ke file yang tertera di atas.",
            ))
            save_config.clicked.connect(save_site_config)
            footer.addWidget(save_config)
            studio_l.addLayout(footer)
            update_advanced_visibility()
            load_archive()
            update_studio_preview()
            reload_config_options()
            reload_site_options()
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
            oauth_response.setEnabled(True)
            oauth_send.setEnabled(oauth_process.state() != QProcess.NotRunning)
            oauth_send.setText(tr("Send Pixiv code", "Kirim code Pixiv") if category == "pixiv" else tr(
                "Send callback URL", "Kirim URL callback",
            ))
            oauth_response.setPlaceholderText(
                tr("Pixiv code or callback URL", "Code Pixiv atau URL callback") if category == "pixiv" else
                tr("Full HTTPS redirect or localhost:6414 URL", "URL redirect HTTPS atau localhost:6414 lengkap")
            )
            oauth_note.setText(oauth_flow_guidance(category))
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
        oauth_stream = [OAuthOutputRedactor()]
        oauth_running_site = [""]
        oauth_network = QNetworkAccessManager(dlg)
        oauth_network.setProxy(QNetworkProxy(QNetworkProxy.NoProxy))

        def oauth_parts() -> list[str]:
            oauth_target = composer_oauth_target(
                str(oauth_site.currentData() or ""),
                oauth_instance.text(),
            )
            base = command_string_to_argv(self.gdl_cmd or "gallery-dl")
            parts: list[str] = []
            if self.config_path:
                config = safe_expand_path(self.config_path)
                if config.is_file():
                    parts.extend(["--config", str(config)])
            return insert_gallery_dl_arguments(
                base or ["gallery-dl"], parts + ["-o", "extractor.input=true", oauth_target]
            )

        def oauth_command_text() -> str:
            return " ".join(quote_arg_for_preview(part) for part in oauth_parts())

        def read_oauth_output() -> None:
            data = bytes(oauth_process.readAllStandardOutput()).decode("utf-8", errors="replace")
            if data:
                append_oauth_output(oauth_stream[0].feed(data))

        def append_oauth_output(text: str) -> None:
            oauth_status.moveCursor(QTextCursor.End)
            oauth_status.insertPlainText(text)
            oauth_status.ensureCursorVisible()

        def oauth_finished(exit_code: int, _status) -> None:
            read_oauth_output()
            append_oauth_output(oauth_stream[0].finish())
            oauth_running_site[0] = ""
            oauth_site.setEnabled(True)
            oauth_instance.setEnabled(oauth_site.currentData() == "mastodon")
            oauth_start.setEnabled(True)
            oauth_stop.setEnabled(False)
            oauth_send.setEnabled(False)
            oauth_status.appendPlainText(tr(
                f"\nOAuth process finished with exit code {exit_code}. If authorization succeeded, test the site with Simulate only.",
                f"\nProses OAuth selesai dengan kode {exit_code}. Jika otorisasi berhasil, uji situs memakai Hanya simulasi.",
            ))

        def oauth_error(_error) -> None:
            append_oauth_output(oauth_stream[0].finish())
            oauth_running_site[0] = ""
            oauth_site.setEnabled(True)
            oauth_instance.setEnabled(oauth_site.currentData() == "mastodon")
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
            oauth_running_site[0] = str(oauth_site.currentData() or "")
            oauth_status.clear()
            oauth_stream[0] = OAuthOutputRedactor()
            oauth_status.appendPlainText(tr(
                "Starting official gallery-dl OAuth flow...\nYour browser should open. Complete authorization there and return here.\n\n",
                "Memulai alur OAuth resmi gallery-dl...\nBrowser akan terbuka. Selesaikan otorisasi di sana lalu kembali ke sini.\n\n",
            ))
            oauth_status.appendPlainText(oauth_flow_guidance(oauth_running_site[0]) + "\n")
            oauth_site.setEnabled(False)
            oauth_instance.setEnabled(False)
            oauth_start.setEnabled(False)
            oauth_stop.setEnabled(True)
            oauth_send.setEnabled(True)
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
            category = oauth_running_site[0]
            if category == "pixiv":
                oauth_process.write((value + "\n").encode("utf-8"))
                oauth_response.clear()
                oauth_status.appendPlainText(tr("Pixiv code sent to gallery-dl.", "Code Pixiv dikirim ke gallery-dl."))
                return
            try:
                callback = local_oauth_callback_url(category, value)
            except ValueError as exc:
                oauth_status.appendPlainText(str(exc))
                return
            oauth_response.clear()
            reply = oauth_network.get(QNetworkRequest(QUrl(callback)))

            def callback_finished() -> None:
                oauth_status.appendPlainText(tr(
                    "Local callback delivered to gallery-dl." if reply.error() == QNetworkReply.NoError else
                    "Local callback failed; check that OAuth is still waiting.",
                    "Callback lokal dikirim ke gallery-dl." if reply.error() == QNetworkReply.NoError else
                    "Callback lokal gagal; periksa apakah OAuth masih menunggu.",
                ))
                reply.deleteLater()

            reply.finished.connect(callback_finished)

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
            target = safe_expand_path(self.config_path or detect_config_path() or str(APP_DIR / "config.json"))
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
            validated = []
            for index, line in enumerate(urls.toPlainText().splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    target, _ = validate_supported_url(line)
                except ValueError as exc:
                    self.show_compact_message(tr("Check your links", "Periksa tautan Anda"),
                                              (f"Baris {index}: " if ind else f"Line {index}: ") + str(exc), "warning")
                    return False
                if target not in validated:
                    validated.append(target)
            if not config_only:
                active_inputs = guided_builder.batch_input.toPlainText().strip() if guided_builder.batch.isChecked() else any(field.text().strip() for field in guided_builder.inputs if not field.isHidden())
                if active_inputs:
                    try:
                        for target in guided_builder.generated_links():
                            if target not in validated:
                                validated.append(target)
                    except ValueError as exc:
                        self.show_compact_message(tr("Check your links", "Periksa tautan Anda"), str(exc), "warning")
                        return False
            if not validated:
                self.show_compact_message(tr("Download Composer", "Perancang Download"), tr("Add at least one URL.", "Tambahkan minimal satu URL."), "warning")
                return False
            urls.setPlainText("\n".join(validated))
            return True

        def add_to_queue() -> None:
            if self.active_workers > 0:
                self.show_compact_message("Download Composer", "Wait for the current download to finish before changing the queue.", "info")
                return
            if not ensure_urls():
                return

            def queue_candidate() -> tuple[list[str], str] | None:
                lines = command_lines()
                current = self.txt_commands.toPlainText().strip()
                combined = "\n".join(lines)
                candidate = f"{current}\n{combined}".strip() if current else combined
                try:
                    parse_text_database(candidate)
                except ValueError as exc:
                    self.show_compact_message("Download Composer", str(exc), "error")
                    return None
                return lines, candidate

            proposed_queue = queue_candidate()
            if proposed_queue is None:
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
                or bool(path_overrides or path_removals)
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
                proposed_queue = queue_candidate()
                if proposed_queue is None:
                    return
            lines, candidate = proposed_queue
            self.txt_commands.setPlainText(candidate)
            self._rebuild_from_text()
            self.append_log(f"[composer] appended {len(lines)} job(s) to input")
            self.show_compact_message(tr("Download Composer", "Perancang Download"), tr(f"Added {len(lines)} job(s) to the queue input.", f"{len(lines)} job ditambahkan ke input antrean."), "info")
            dlg.accept()

        def copy_commands() -> None:
            if not ensure_urls():
                return
            QApplication.clipboard().setText("\n".join(command_lines()))
            self.show_compact_message(tr("Download Composer", "Perancang Download"), tr("Command copied.", "Command disalin."), "info")

        def write_config(path: str) -> bool:
            nonlocal existing_config, config_error, loaded_state, preserve_source
            if self.active_workers > 0:
                self.show_compact_message("Download Composer", "Wait for the current download to finish before changing the config.", "info")
                return False
            target = safe_expand_path(path)
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
                if active_path is not None:
                    active_path.setText(tr("Config file: ", "File config: ") + str(target))
                existing_config = data
                preserve_source = True
                loaded_state = copy.deepcopy(current_state())
                config_error = None
                general_overrides.clear()
                site_overrides.clear()
                general_removals.clear()
                site_removals.clear()
                path_overrides.clear()
                path_removals.clear()
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

        def load_config_example(selected: str) -> None:
            nonlocal existing_config, config_error, loaded_state, preserve_source
            if not selected:
                return
            imported, error = _read_json_config(selected, required=True)
            if error:
                self.show_compact_message(tr("Cannot read config", "Config tidak bisa dibaca"), str(error) + tr("\nThe current draft is unchanged.", "\nPeriksa koma dan tanda petik pada baris tersebut. Draft saat ini tetap utuh."), "warning")
                return
            existing_config, config_error, loaded_state = imported, None, None
            preserve_source = True
            general_overrides.clear()
            site_overrides.clear()
            general_removals.clear()
            site_removals.clear()
            path_overrides.clear()
            path_removals.clear()
            _set_checks(site_checks, set(), lambda: None)
            destination.clear()
            directory.clear()
            filename.clear()
            archive_path.clear()
            auth_username.clear()
            secret_value.clear()
            extra_auth_value.clear()
            apply_loaded_defaults()
            update_auth_method()
            loaded_state = copy.deepcopy(current_state())
            refresh_preview()
            self.show_compact_message(tr("Config loaded as a draft", "Config dimuat sebagai draft"), tr(
                "The original values are preserved until you edit them. Choose a website to edit its settings; use Save As for a separate copy.",
                "Nilai asli dipertahankan sampai Anda mengeditnya. Pilih situs untuk mengatur nilainya; gunakan Simpan Sebagai untuk salinan terpisah.",
            ), "info")

        if import_config_button is not None:
            def import_config() -> None:
                selected, _ = QFileDialog.getOpenFileName(
                    dlg, tr("Choose a config to use as a starting point", "Pilih config untuk dijadikan contoh"),
                    "", "JSON config (*.json *.conf);;All files (*.*)",
                )
                load_config_example(selected)
            import_config_button.clicked.connect(import_config)
        if load_example_button is not None:
            load_example_button.clicked.connect(lambda: load_config_example(str(next(item.path for item in EXAMPLES if item.key == config_example_picker.currentData()))))

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
        def open_saved_website_settings(site: str) -> None:
            nonlocal existing_config, config_error, preserve_source
            # Open the saved-config workspace with its own draft. Job fields
            # must never leak into the config through this shortcut.
            self.open_config_builder(site)
            existing_path = self.config_path or detect_config_path()
            existing_config, config_error = _read_json_config(existing_path)
            preserve_source = bool(existing_path) and config_error is None
            config_path_label.setText(str(existing_path or ""))
            refresh_preview()

        guided_builder.configureRequested.connect(open_saved_website_settings)
        if config_start_button is not None:
            config_start_button.clicked.connect(open_site_config_studio)
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
        save_button = QPushButton(tr("Save config file", "Simpan file config") if config_only else tr("Save Defaults", "Simpan Default"))
        save_as_button = QPushButton(tr("Save As...", "Simpan Sebagai..."))
        close_button = QPushButton(tr("Close", "Tutup"))
        for button in (add_button, copy_button, save_button, save_as_button, close_button):
            button.setMinimumHeight(32)
        button_row.addWidget(add_button)
        preview_l.addWidget(copy_button, 0, Qt.AlignRight)
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
        if config_only:
            scope.setCurrentIndex(scope.findData("config"))
            scope.setEnabled(False)
            job_card.hide()
            add_button.hide()
            copy_button.hide()
            tabs.setTabVisible(tabs.indexOf(guide_scroll), False)
            tabs.setTabText(tabs.indexOf(main_scroll), tr("Defaults", "Default"))
            preview_tabs.setTabVisible(0, False)
            preview_tabs.setCurrentWidget(config_summary)
            tabs.setCurrentWidget(start_scroll)
        else:
            save_button.hide()
            save_as_button.hide()
            scope.hide()
            tabs.setTabVisible(tabs.indexOf(guide_scroll), False)
            preview_tabs.setTabVisible(preview_tabs.indexOf(config_preview), False)
            tabs.setTabText(tabs.indexOf(main_scroll), tr("1. Links and destination", "1. Tautan dan tujuan"))
            tabs.setTabText(tabs.indexOf(advanced_scroll), tr("2. Options for these jobs", "2. Opsi untuk job ini"))
            tabs.setTabText(tabs.indexOf(auth_scroll), tr("Login for these jobs", "Login untuk job ini"))
            tabs.setCurrentWidget(main_scroll)
        # Preset site toggles duplicated the Config Maker website forms.
        tabs.setTabVisible(tabs.indexOf(sites_page), False)
        update_auth_method()
        sync_auth_site_from_oauth()
        loaded_state = copy.deepcopy(current_state())
        refresh_preview()
        if config_only and preferred_site:
            QTimer.singleShot(0, lambda: open_site_config_studio(preferred_site))
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
