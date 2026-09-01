"""Unified gallery-dl download and configuration composer.

The official gallery-dl model is simple: configuration provides reusable
defaults and command-line options override those defaults for a single run.
This module mirrors that model in one UI instead of exposing separate command
and config builders with overlapping fields.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

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
    redact_sensitive_argv,
    redact_sensitive_text,
    safe_bool,
    split_command,
    timestamp_slug,
    unique_path,
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


def _add_value(parts: list[str], flag: str, value: str) -> None:
    value = str(value or "").strip()
    if value:
        parts.extend([flag, value])


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
        # gallery-dl 1.29 exposes this as a long option only; ``-a`` is not a
        # supported alias and makes argparse reject an otherwise valid job.
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


def validate_cookies_txt(path: str) -> tuple[bool, str]:
    """Perform a lightweight Netscape cookies.txt format check."""

    if not str(path or "").strip():
        return False, "No cookies.txt file selected."
    target = Path(path).expanduser()
    if not target.exists() or not target.is_file():
        return False, "cookies.txt file does not exist."
    try:
        # Cookie exports can grow very large. Read only the validation prefix
        # instead of materializing the whole file on the GUI thread.
        with target.open("rb") as handle:
            text = handle.read(512 * 1024).decode("utf-8-sig", errors="replace")
    except Exception as exc:
        return False, f"Could not read cookies.txt: {exc}"
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#"):
            continue
        if len(line.split("\t")) >= 7:
            return True, "Netscape cookies.txt format detected."
    return False, "No Netscape-format cookie rows were found (expected 7 tab-separated columns)."


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
        auth_site.setEditable(True)
        auth_site.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        auth_site.setMinimumContentsLength(18)
        auth_site.addItem(tr("All sites (global)", "Semua situs (global)"), "")
        for item in definitions:
            auth_site.addItem(f"{item['name']} ({item['key']})", str(item["key"]))
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
        oauth_site.setEditable(True)
        oauth_site.addItems(["pixiv", "deviantart", "flickr", "reddit", "smugmug", "tumblr", "mastodon"])
        oauth_site.setToolTip(tr(
            "OAuth target used as gallery-dl oauth:<site>. Mastodon can include an instance, for example mastodon:https://mastodon.social/.",
            "Target OAuth yang dipakai sebagai gallery-dl oauth:<situs>. Mastodon dapat menyertakan instance, misalnya mastodon:https://mastodon.social/.",
        ))
        oauth_start = QPushButton(tr("Start OAuth", "Mulai OAuth"))
        oauth_start.setObjectName("primary")
        oauth_stop = QPushButton(tr("Stop", "Hentikan"))
        oauth_stop.setEnabled(False)
        oauth_copy = QPushButton(tr("Copy command", "Salin command"))
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
        oauth_l.addWidget(oauth_status, 1, 0, 1, 4)
        oauth_note = QLabel(tr(
            "OAuth does not use the username/password fields above. gallery-dl opens the site's official authorization page and normally stores received tokens in its cache. Never share token output.",
            "OAuth tidak memakai field username/password di atas. gallery-dl membuka halaman otorisasi resmi situs dan biasanya menyimpan token yang diterima ke cache. Jangan pernah membagikan output token.",
        ))
        oauth_note.setObjectName("subtle")
        oauth_note.setWordWrap(True)
        oauth_l.addWidget(oauth_note, 2, 0, 1, 4)
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
        site_tools.addWidget(site_search, 1)
        site_tools.addWidget(select_common)
        site_tools.addWidget(select_none)
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
            blocks.update(self._custom_site_keys_to_config(custom_sites.text()))
            return blocks

        def proposed_config() -> dict:
            state = current_state()
            return build_composer_config(
                state,
                site_blocks=selected_site_blocks(state),
                existing=existing_config,
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
            if method == "credentials":
                scope.setCurrentIndex(scope.findData("config"))
            if method == "oauth":
                category = selected_auth_category()
                if category:
                    oauth_site.setEditText(category)
            refresh_preview()

        def sync_oauth_site(*_args) -> None:
            if auth_method.currentData() == "oauth":
                category = selected_auth_category()
                if category:
                    oauth_site.setEditText(category)
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

        def oauth_parts() -> list[str]:
            target = oauth_site.currentText().strip()
            if target.lower().startswith("oauth:"):
                oauth_target = target
            else:
                oauth_target = "oauth:" + target
            base = command_string_to_argv(self.gdl_cmd or "gallery-dl")
            return base + [oauth_target] if base else ["gallery-dl", oauth_target]

        def oauth_command_text() -> str:
            return " ".join(quote_arg_for_preview(part) for part in oauth_parts())

        def read_oauth_output() -> None:
            data = bytes(oauth_process.readAllStandardOutput()).decode("utf-8", errors="replace")
            if data:
                oauth_status.moveCursor(QTextCursor.End)
                oauth_status.insertPlainText(redact_sensitive_text(data))
                oauth_status.ensureCursorVisible()

        def oauth_finished(exit_code: int, _status) -> None:
            read_oauth_output()
            oauth_start.setEnabled(True)
            oauth_stop.setEnabled(False)
            oauth_status.appendPlainText(tr(
                f"\nOAuth process finished with exit code {exit_code}. If authorization succeeded, test the site with Simulate only.",
                f"\nProses OAuth selesai dengan kode {exit_code}. Jika otorisasi berhasil, uji situs memakai Hanya simulasi.",
            ))

        def oauth_error(_error) -> None:
            oauth_start.setEnabled(True)
            oauth_stop.setEnabled(False)
            oauth_status.appendPlainText(tr(
                "Could not start OAuth: ",
                "Tidak dapat memulai OAuth: ",
            ) + oauth_process.errorString())

        def start_oauth() -> None:
            target = oauth_site.currentText().strip()
            if not target:
                self.show_compact_message("OAuth", tr("Choose an OAuth site.", "Pilih situs OAuth."), "warning")
                return
            if oauth_process.state() != QProcess.NotRunning:
                return
            parts = oauth_parts()
            oauth_status.clear()
            oauth_status.appendPlainText(tr(
                "Starting official gallery-dl OAuth flow...\nYour browser should open. Complete authorization there and return here.\n\n",
                "Memulai alur OAuth resmi gallery-dl...\nBrowser akan terbuka. Selesaikan otorisasi di sana lalu kembali ke sini.\n\n",
            ))
            oauth_start.setEnabled(False)
            oauth_stop.setEnabled(True)
            oauth_process.setProgram(parts[0])
            oauth_process.setArguments(parts[1:])
            oauth_process.start()

        def stop_oauth() -> None:
            if oauth_process.state() != QProcess.NotRunning:
                oauth_process.terminate()
                if not oauth_process.waitForFinished(1200):
                    oauth_process.kill()

        def copy_oauth_command() -> None:
            QApplication.clipboard().setText(oauth_command_text())
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
            requires_saved_config = state.apply_to_config or bool(state.secret_value or state.extra_auth_value)
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
        if auth_site.lineEdit() is not None:
            auth_site.lineEdit().textEdited.connect(sync_oauth_site)
        show_secrets.toggled.connect(toggle_secret_visibility)
        prepare_test.clicked.connect(prepare_safe_test)
        remove_auth.clicked.connect(remove_saved_auth_data)
        oauth_start.clicked.connect(start_oauth)
        oauth_stop.clicked.connect(stop_oauth)
        oauth_copy.clicked.connect(copy_oauth_command)
        oauth_process.readyReadStandardOutput.connect(read_oauth_output)
        oauth_process.finished.connect(oauth_finished)
        oauth_process.errorOccurred.connect(oauth_error)
        dlg.finished.connect(cleanup_oauth)
        site_search.textChanged.connect(filter_sites)
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
        refresh_preview()
        dlg.exec()


def _bounded_int(value: object, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(float(value))))
    except (TypeError, ValueError):
        return minimum


def _set_checks(checks: dict[str, QCheckBox], selected: set[str], callback) -> None:
    for key, checkbox in checks.items():
        checkbox.blockSignals(True)
        checkbox.setChecked(key in selected)
        checkbox.blockSignals(False)
    callback()


__all__ = [
    "ComposerFlowchart",
    "ComposerMixin",
    "ComposerState",
    "build_composer_argv",
    "build_composer_config",
    "config_defaults",
    "redact_auth_config",
    "validate_cookies_txt",
]
