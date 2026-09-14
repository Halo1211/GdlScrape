from __future__ import annotations

import json
import queue
import threading
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QMainWindow,
    QSystemTrayIcon,
)

from .advanced_tools import AdvancedToolsMixin
from .composer import ComposerMixin
from .dashboard_ui import DashboardUiMixin
from .core import (
    APP_DIR,
    APP_NAME,
    APP_VERSION,
    IS_WINDOWS,
    OFFICIAL_GALLERY_DL_LINKS,
    app_data_dir,
    dependency_status,
    detect_config_path,
    find_gallery_dl,
    safe_expand_path,
)
from .models import DownloadJob, JobResult
from .management import ManagementMixin
from .queue_controller import QueueControllerMixin
from .reports import ReportsMixin
from .system_tools import SystemToolsMixin
from .ui_shell import UiShellMixin
from .workers import CommandProbeWorker, DownloadWorker


class MainWindow(
    QMainWindow,
    DashboardUiMixin,
    ComposerMixin,
    UiShellMixin,
    QueueControllerMixin,
    SystemToolsMixin,
    ReportsMixin,
    AdvancedToolsMixin,
    ManagementMixin,
):
    COL_NUM, COL_SERVICE, COL_ID, COL_DEST, COL_TAG, COL_STATUS, COL_STATS = range(7)

    # QMainWindow intentionally remains first in the inheritance list for the
    # existing initialization/layout contract. Explicitly forward Qt virtual
    # handlers implemented by AdvancedToolsMixin; otherwise QMainWindow's
    # descriptors win MRO lookup and silently make shutdown safety and file
    # drag/drop unreachable.
    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override
        AdvancedToolsMixin.dragEnterEvent(self, event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        AdvancedToolsMixin.dropEvent(self, event)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        AdvancedToolsMixin.closeEvent(self, event)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setAcceptDrops(True)
        self.resize(1220, 760)
        self.setMinimumSize(1000, 640)

        self.gdl_cmd = find_gallery_dl()
        self.config_path = detect_config_path()
        self.current_file: Optional[str] = None
        self.jobs: list[DownloadJob] = []
        self.results: dict[int, JobResult] = {}
        self.failed_indices: set[int] = set()
        self.stopped_indices: set[int] = set()
        self.cancelled_indices: set[int] = set()
        self.done_indices: set[int] = set()
        # Thread-safe cancel set shared with workers. The GUI-side
        # ``cancelled_indices`` is for display/result bookkeeping only and must
        # NOT be read from worker threads. ``_worker_cancel`` is the single set
        # workers consult; it is guarded by ``_worker_cancel_lock`` and is only
        # ever added to during a run (never discarded mid-run), which removes the
        # cross-thread mutation race that could raise RuntimeError or lose state.
        self._worker_cancel_lock = threading.Lock()
        self._worker_cancel: set[int] = set()
        self.workers: list[DownloadWorker] = []
        self.task_queue: Optional[queue.Queue] = None
        self.pause_event = threading.Event()
        self.stop_event = threading.Event()
        self.running_indices: set[int] = set()
        self.active_job_indices: set[int] = set()
        self.all_log_lines: list[str] = []
        self.active_workers = 0
        self.total_run = 0
        self.processed_run = 0
        self._processed_in_run: set[int] = set()
        self.started_at: Optional[float] = None
        self.current_theme = "dark"
        self.help_language = "English"
        self.delay_timer: Optional[QTimer] = None
        self.tray_icon: Optional[QSystemTrayIcon] = None
        self.version_worker: Optional[CommandProbeWorker] = None
        self.audit_mode = False
        self.compact_mode = False
        # v11 advanced queue policies. These are GUI-side helpers only.
        # They do not replace gallery-dl archive/config behavior.
        self.service_policy: dict[str, dict[str, int]] = {}
        self.retry_strategy: dict[str, bool] = {
            "network": True,
            "rate-limit": True,
            "unknown": True,
            "path": False,
            "config": False,
            "auth/cookies": False,
            "not-found": False,
        }
        self.notifications_enabled = True

        self._initialize_management_state()

        app_data_dir()
        self._build_ui()
        self._install_shortcuts()
        self.statusBar().setSizeGripEnabled(False)
        self._refresh_env()
        self._update_counts()
        self._load_autosave_silently()
        self._start_management_services()
        self.eta_timer = QTimer(self)
        self.eta_timer.timeout.connect(self._refresh_eta)
        self.eta_timer.start(1000)

def _official_gallery_dl_reference_text(ind: bool = False) -> str:
    if ind:
        return f"""
Referensi resmi yang dimasukkan ke helper ini:
- Dependency resmi: {OFFICIAL_GALLERY_DL_LINKS['readme']}
- Dokumentasi konfigurasi: {OFFICIAL_GALLERY_DL_LINKS['configuration']}
- Contoh default gallery-dl.conf: {OFFICIAL_GALLERY_DL_LINKS['default_config']}

Ringkasan isi resmi:
- gallery-dl memakai config berbasis JSON.
- extractor.base-directory adalah folder dasar download. Default resmi: ./gallery-dl/.
- extractor.cookies bisa berupa path cookies.txt, object cookie name-value, atau list profil browser seperti ["firefox"].
- extractor.archive menyimpan ID file yang sudah diunduh. File archive adalah database SQLite dan membantu skip download ulang.
- gallery-dl mencari config di lokasi standar Windows: %APPDATA%\\gallery-dl\\config.json, %USERPROFILE%\\gallery-dl\\config.json, dan %USERPROFILE%\\gallery-dl.conf.
- Linux/macOS: /etc/gallery-dl.conf, $XDG_CONFIG_HOME/gallery-dl/config.json, ~/.config/gallery-dl/config.json, dan ~/.gallery-dl.conf.
"""
    return f"""
Official references included in this helper:
- Official dependencies: {OFFICIAL_GALLERY_DL_LINKS['readme']}
- Configuration documentation: {OFFICIAL_GALLERY_DL_LINKS['configuration']}
- Default gallery-dl.conf example: {OFFICIAL_GALLERY_DL_LINKS['default_config']}

Official-content summary:
- gallery-dl uses a JSON-based configuration file.
- extractor.base-directory is the base folder for downloads. Official default: ./gallery-dl/.
- extractor.cookies may be a cookies.txt path, a cookie name-value object, or a browser-profile list such as ["firefox"].
- extractor.archive stores downloaded file IDs. The archive is an SQLite database and helps skip already-downloaded items.
- Windows config lookup includes %APPDATA%\\gallery-dl\\config.json, %USERPROFILE%\\gallery-dl\\config.json, and %USERPROFILE%\\gallery-dl.conf.
- Linux/macOS lookup includes /etc/gallery-dl.conf, $XDG_CONFIG_HOME/gallery-dl/config.json, ~/.config/gallery-dl/config.json, and ~/.gallery-dl.conf.
"""

def _patched_show_dependency_helper(self: MainWindow) -> None:
    ind = self._ui_is_indonesian() if hasattr(self, "_ui_is_indonesian") else False
    deps = dependency_status()
    ok = "OK"
    missing = "MISSING" if not ind else "BELUM ADA"
    lines = []
    lines.append("Dependency Helper - gallery-dl + GUI" if not ind else "Bantuan Dependensi - gallery-dl + GUI")
    lines.append("=" * 62)
    lines.append(_official_gallery_dl_reference_text(ind).strip())
    lines.append("")
    lines.append("Detected status:" if not ind else "Status terdeteksi:")
    for name in sorted(deps):
        lines.append(f"- {name}: {ok if deps[name] else missing}")
    lines.append("")
    lines.append("Install / upgrade commands:" if not ind else "Command install / upgrade:")
    if IS_WINDOWS:
        lines.extend([
            "py -m pip install --upgrade pip setuptools wheel",
            "py -m pip install -U gallery-dl PySide6 openpyxl pillow keyring",
            "py -m pip install -U yt-dlp PySocks brotli zstandard PyYAML toml truststore jinja2",
        ])
    else:
        lines.extend([
            "python3 -m pip install --upgrade pip setuptools wheel",
            "python3 -m pip install -U gallery-dl PySide6 openpyxl pillow keyring",
            "python3 -m pip install -U yt-dlp PySocks brotli zstandard PyYAML toml truststore jinja2",
        ])
    lines.append("")
    lines.append("External tools:" if not ind else "Tool eksternal:")
    lines.append("- Install FFmpeg if you need Pixiv Ugoira/media conversion." if not ind else "- Install FFmpeg jika butuh konversi Pixiv Ugoira/media.")
    lines.append("- Install mkvmerge for accurate Ugoira timecodes." if not ind else "- Install mkvmerge untuk timecode Ugoira yang akurat.")
    lines.append("- Install 7-Zip if you use 7z/xz archive output in the Feature Hub." if not ind else "- Install 7-Zip jika memakai output arsip 7z/xz di Feature Hub.")
    lines.append("")
    lines.append("Notes:" if not ind else "Catatan:")
    lines.append("- This GUI still runs gallery-dl through subprocess; dependencies only affect gallery-dl capabilities." if not ind else "- GUI ini tetap menjalankan gallery-dl lewat subprocess; dependency hanya memengaruhi kemampuan gallery-dl.")
    lines.append("- openpyxl is only needed for XLSX import/export. Pillow is only needed for PNG→WebP post-processing." if not ind else "- openpyxl hanya untuk import/export XLSX. Pillow hanya untuk post-process PNG→WebP.")
    self.show_scroll_message("Dependency Helper", "\n".join(lines), "info")

def _patched_quick_guide_text(self: MainWindow, language: str | None = None) -> str:
    language = language or getattr(self, "help_language", "English")
    ind = language == "Indonesia"
    base = MainWindow.__dict__.get("_original_quick_guide_text", lambda s, lang=None: "")(self, language)
    return base.rstrip() + "\n\n" + _official_gallery_dl_reference_text(ind).strip() + "\n"

def _patched_database_help_text(self: MainWindow, language: str | None = None) -> str:
    language = language or getattr(self, "help_language", "English")
    ind = language == "Indonesia"
    base = MainWindow.__dict__.get("_original_database_help_text", lambda s, lang=None: "")(self, language)
    return base.rstrip() + "\n\n" + _official_gallery_dl_reference_text(ind).strip() + "\n"

def _patched_config_guide_text(self: MainWindow) -> str:
    ind = self._ui_is_indonesian() if hasattr(self, "_ui_is_indonesian") else False
    base = MainWindow.__dict__.get("_original_gallery_dl_config_guide_text", lambda s: "")(self)
    official = _official_gallery_dl_reference_text(ind)
    extra = """
Practical Config Builder improvements in this version
-----------------------------------------------------
- Save to existing config now asks for confirmation and creates a timestamped .bak backup.
- Generated JSON avoids comments/placeholders so Validate Config can parse it directly.
- Browser cookies are generated as a browser list, for example ["firefox"].
- Archive uses SQLite path and WAL-friendly pragma lines.
- Output path is stored in extractor.base-directory
GUI Output dir remains only a fallback when a command lacks -d/--destination.
"""
    if ind:
        extra = """
Penyempurnaan Config Builder versi ini
--------------------------------------
- Simpan ke config yang sudah ada sekarang meminta konfirmasi dan membuat backup .bak bertimestamp.
- JSON hasil builder tidak memakai komentar/placeholder sehingga langsung bisa divalidasi.
- Cookies browser dibuat sebagai list profil browser, contoh ["firefox"].
- Archive memakai path SQLite dan pragma yang ramah WAL.
- Output path disimpan di extractor.base-directory
Output dir GUI tetap hanya fallback jika command tidak punya -d/--destination.
"""
    return base.rstrip() + "\n\n" + official.strip() + "\n\n" + extra.strip() + "\n"

def _patched_gallery_dl_config_template_text(self: MainWindow, preset: str = "Safe default") -> str:
    archive = str(APP_DIR / "gallery-dl-archive.sqlite3").replace("\\", "/")
    out_dir = str(safe_expand_path(self.edit_output.text() or "./downloads")).replace("\\", "/")
    browser = self.combo_cookies.currentText().strip().lower() if hasattr(self, "combo_cookies") else "none"
    extractor: dict[str, object] = {
        "base-directory": out_dir,
        "directory": ["{category}", "{subcategory}", "{user[id]|user[name]|username|author|id}"],
        "filename": "{id}_{num}_{filename}.{extension}",
        "archive": archive,
        "archive-pragma": ["journal_mode=WAL", "synchronous=NORMAL"],
        "archive-event": ["file"],
        "archive-mode": "file",
        "skip": True,
        "retries": max(4, int(self.spin_retries.value() or 4)) if hasattr(self, "spin_retries") else 4,
        "timeout": 30.0,
        "sleep-request": "1.0-2.0",
        "sleep-429": "60-180",
        "path-restrict": "windows" if IS_WINDOWS else "auto",
        "path-replace": "_",
        "path-strip": "auto",
        "keywords-default": "unknown",
    }
    key = str(preset or "Safe default").lower()
    if "cookies" in key and browser != "none":
        extractor["cookies"] = [browser]
        extractor["cookies-update"] = True
    if "rate" in key:
        # Plain duration ranges work on the minimum supported gallery-dl and
        # later releases. Keep generated defaults conservative because exotic
        # duration expressions vary between gallery-dl versions.
        extractor.update({"sleep": "2.0-5.0", "sleep-request": "1.5-3.0", "sleep-retries": "60-300", "sleep-429": "120-900", "retries": 8})
    if "windows" in key:
        extractor.update({"path-restrict": "windows", "path-strip": "windows", "path-extended": True})
    if "metadata" in key:
        extractor["postprocessors"] = [{"name": "metadata", "mode": "json"}]
    if "site override" in key:
        extractor.update({
            "pixiv": {"directory": ["Pixiv", "{user[id]}", "{id}"], "filename": "{id}_p{num}.{extension}", "sleep-request": "1.0-2.5"},
            "twitter": {"directory": ["Twitter", "{user[name]}", "{tweet_id}"], "filename": "{tweet_id}_{num}.{extension}", "sleep-request": "2.0-4.0"},
        })
    data = {
        "extractor": extractor,
        "downloader": {"part": True, "mtime": True, "retries": max(4, int(self.spin_retries.value() or 4)) if hasattr(self, "spin_retries") else 4, "timeout": 30.0},
        "output": {
            "progress": True,
            "log": "[{name}][{levelname}] {message}",
            "errorfile": str(APP_DIR / "gallery-dl-errors.txt").replace("\\", "/"),
            "unsupportedfile": str(APP_DIR / "gallery-dl-unsupported.txt").replace("\\", "/"),
        },
    }
    return json.dumps(data, indent=4, ensure_ascii=False)

def _patched_gallery_dl_command_guide_text(self: MainWindow, language: str | None = None) -> str:
    language = language or getattr(self, "help_language", "English")
    ind = language == "Indonesia"
    base = MainWindow.__dict__.get("_original_gallery_dl_command_guide_text", lambda s, lang=None: "")(self, language)
    add = """

Official gallery-dl notes added to this builder
-----------------------------------------------
- Basic syntax remains: gallery-dl [OPTIONS] URLS.
- The builder always quotes paths/URLs in the preview so spaces in output folders are safe.
- URL is required before appending/exporting a command. Unusual extractor-prefixed URLs are allowed after confirmation.
- Use --cookies-from-browser for quick login testing, or set extractor.cookies in Config Builder for a persistent config.
- Use --download-archive for command-level duplicate skipping, or extractor.archive in config for persistent archive behavior.
"""
    if ind:
        add = """

Catatan resmi gallery-dl yang ditambahkan ke builder
----------------------------------------------------
- Sintaks dasar tetap: gallery-dl [OPTIONS] URLS.
- Builder selalu mengutip path/URL pada preview sehingga folder output berspasi aman.
- URL wajib diisi sebelum command ditambahkan/diekspor. URL dengan prefix extractor yang tidak biasa tetap bisa dipakai setelah konfirmasi.
- Pakai --cookies-from-browser untuk tes login cepat, atau set extractor.cookies di Config Builder untuk config permanen.
- Pakai --download-archive untuk skip duplikat per command, atau extractor.archive di config untuk archive permanen.
"""
    return base.rstrip() + add + "\n"

MainWindow._original_quick_guide_text = MainWindow.quick_guide_text

MainWindow._original_database_help_text = MainWindow.database_help_text

MainWindow._original_gallery_dl_config_guide_text = MainWindow._gallery_dl_config_guide_text

MainWindow._original_gallery_dl_command_guide_text = MainWindow.gallery_dl_command_guide_text

MainWindow.show_dependency_helper = _patched_show_dependency_helper

MainWindow.quick_guide_text = _patched_quick_guide_text

MainWindow.database_help_text = _patched_database_help_text

MainWindow._gallery_dl_config_guide_text = _patched_config_guide_text

MainWindow.gallery_dl_config_template_text = _patched_gallery_dl_config_template_text

MainWindow.gallery_dl_command_guide_text = _patched_gallery_dl_command_guide_text

__all__ = ['MainWindow']
