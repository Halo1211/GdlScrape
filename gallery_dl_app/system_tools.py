from __future__ import annotations

import json
import queue
import re
import shutil
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSignalBlocker, Slot
from PySide6.QtWidgets import (
    QAbstractSpinBox,
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
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .core import (
    APP_DIR,
    AUTOSAVE_FILE,
    IS_WINDOWS,
    REDACTED,
    _strip_wrapping_quotes,
    app_data_dir,
    atomic_write_text,
    command_executable_available,
    detect_config_path,
    open_path,
    parse_text_database,
    quote_arg_for_preview,
    read_text_safely,
    redact_sensitive_database_text,
    redact_sensitive_argv,
    redact_sensitive_text,
    safe_bool,
    safe_expand_path,
    safe_int,
    sanitize_service_policy,
    split_command,
    timestamp_slug,
    unique_path,
)
from .models import DownloadJob
from .themes import DARK_QSS, LIGHT_QSS, application_icon, apply_native_window_theme, theme_palette
from .workers import CommandProbeWorker, DownloadWorker


def is_gallery_dl_version_output(value: str) -> bool:
    """Accept gallery-dl's numeric version output, with an optional label."""
    text = str(value or "").strip()
    return re.fullmatch(
        r"(?i)(?:gallery-dl\s+)?v?\d+\.\d+(?:\.\d+)?(?:[-+._a-z0-9]*)?",
        text,
    ) is not None


class SystemToolsMixin:
    @Slot()
    def set_gallery_dl_path(self) -> None:
        if self.active_workers > 0:
            self.show_compact_message("gallery-dl", "Stop the current download before changing the gallery-dl path.", "info")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Select gallery-dl executable")
        if path:
            self.gdl_cmd = path
            self._refresh_env()

    @Slot()
    def choose_config_path(self) -> None:
        if self.active_workers > 0:
            self.show_compact_message("Config", "Stop the current download before changing the config file.", "info")
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose gallery-dl config file",
            str(Path(self.config_path).parent if self.config_path else Path.home()),
            "Config files (*.json *.conf);;All files (*.*)",
        )
        if path:
            self.config_path = path
            self._refresh_env()

    def choose_output_dir(self) -> None:
        if self.active_workers > 0:
            self.show_compact_message("Output directory", "Stop the current download before changing the output directory.", "info")
            return
        path = QFileDialog.getExistingDirectory(self, "Choose output directory", self.edit_output.text().strip() or str(Path.home()))
        if path:
            self.edit_output.setText(path)

    def final_command_for_job(self, job: DownloadJob) -> list[str]:
        # A non-started worker is reused purely as a pure command builder. Parent
        # it to the window so Qt owns and cleans it up (no "destroyed while
        # running" noise) and explicitly release it after use.
        dummy_q: queue.Queue = queue.Queue()
        worker = DownloadWorker(
            0,
            dummy_q,
            self.gdl_cmd or "gallery-dl",
            self.config_path,
            self.edit_output.text().strip(),
            self.combo_cookies.currentText(),
            self.spin_retries.value(),
            "",
            threading.Event(),
            threading.Event(),
            set(),
            False,
            "zip",
            False,
            self.service_policy,
            {},
            None,
            self,
        )
        try:
            return worker.build_command(job)
        finally:
            worker.deleteLater()

    def command_preview(self) -> None:
        if not self._rebuild_from_text():
            return
        if not self.jobs:
            self.show_compact_message("Command Preview", "No URL or command loaded. Paste or import at least one row first.", "info")
            return
        lines = []
        for i, job in enumerate(self.jobs[:50]):
            try:
                cmd = self.final_command_for_job(job)
                # Display-only copy: never mutate the argv used to run the job.
                preview = " ".join(quote_arg_for_preview(x) for x in redact_sensitive_argv(cmd))
                lines.append(f"#{i+1} [{job.service}] {preview}")
            except Exception as exc:
                lines.append(
                    f"#{i+1}: ERROR: {redact_sensitive_text(str(exc))}"
                )
        if len(self.jobs) > 50:
            lines.append(f"... {len(self.jobs)-50} more")
        self.show_scroll_message("Command Preview", "Final commands that will be executed:\n\n" + "\n".join(lines), "info")

    def health_check(self) -> None:
        ind = self._ui_is_indonesian() if hasattr(self, "_ui_is_indonesian") else False
        checks: list[str] = []
        if command_executable_available(self.gdl_cmd):
            checks.append(("[OK] command gallery-dl: " if ind else "[OK] gallery-dl command: ") + str(self.gdl_cmd))
        elif self.gdl_cmd:
            checks.append(
                ("[ERROR] command gallery-dl tidak dapat dijalankan: " if ind
                 else "[ERROR] gallery-dl command cannot be executed: ")
                + str(self.gdl_cmd)
            )
        else:
            checks.append("[ERROR] command gallery-dl tidak ditemukan" if ind else "[ERROR] gallery-dl command not found")

        # detect_config_path() may return a default *candidate* path even before
        # the file exists. Health Check must not mark a planned path as OK.
        if self.config_path:
            cfg = Path(str(self.config_path)).expanduser()
            if cfg.is_file():
                if cfg.suffix.lower() == ".json":
                    try:
                        parsed_config = json.loads(read_text_safely(cfg))
                        if not isinstance(parsed_config, dict):
                            raise ValueError("config root must be a JSON object")
                        checks.append(("[OK] config JSON valid: " if ind else "[OK] config JSON valid: ") + str(cfg))
                    except Exception as exc:
                        checks.append(("[WARNING] config ditemukan tetapi JSON tidak valid: " if ind else "[WARNING] config exists but JSON is invalid: ") + f"{cfg} | {exc}")
                else:
                    checks.append(("[WARNING] config ditemukan, tetapi bukan JSON: " if ind else "[WARNING] config exists, but is not JSON: ") + str(cfg))
            elif cfg.exists() and not cfg.is_file():
                checks.append(("[ERROR] path config bukan file: " if ind else "[ERROR] config path is not a file: ") + str(cfg))
            else:
                checks.append(("[WARNING] config belum dibuat: " if ind else "[WARNING] config file does not exist yet: ") + str(cfg))
                checks.append("   " + ("Gunakan Perancang Download atau Buka/Buat Config." if ind else "Use Download Composer or Open/Create Config."))
        else:
            checks.append("[WARNING] config belum terdeteksi" if ind else "[WARNING] config not detected")

        out = Path(self.edit_output.text().strip() or ".").expanduser()
        try:
            out.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(out)
            checks.append(("[OK] folder output: " if ind else "[OK] output dir: ") + f"{out} | " + ("sisa " if ind else "free ") + f"{usage.free // (1024**3)} GB")
        except Exception as exc:
            checks.append(("[ERROR] masalah folder output: " if ind else "[ERROR] output dir problem: ") + str(exc))
        checks.append(("[INFO] job dimuat: " if ind else "[INFO] loaded jobs: ") + str(len(self.jobs)))
        self.show_scroll_message("Health Check", "\n".join(checks), "info")

    def check_version(self) -> None:
        if not self.gdl_cmd:
            self.show_compact_message(
                "Check gallery-dl version",
                "gallery-dl was not found. Set the gallery-dl path first, or install gallery-dl outside this GUI.",
                "warning",
            )
            return
        if self.version_worker and self.version_worker.isRunning():
            self.show_compact_message("Check gallery-dl version", "Version check is already running.", "info")
            return
        self.btn_version_side.setEnabled(False)
        self.append_log("[system] checking gallery-dl version...")
        old = self.version_worker
        if old is not None:
            try:
                old.deleteLater()
            except Exception:
                pass
        self.version_worker = CommandProbeWorker(self.gdl_cmd, ["--version"], timeout=12, parent=self)

        def finish(output: str, rc: int, error: str) -> None:
            self.btn_version_side.setEnabled(True)
            if error or rc != 0 or not is_gallery_dl_version_output(output):
                if error:
                    detail = error
                elif rc != 0:
                    detail = output or f"gallery-dl exited with rc={rc}"
                else:
                    detail = f"Unexpected --version output: {output or '(empty)'}"
                self.append_log(f"[system] version check failed (rc={rc}): {detail}")
                self.show_compact_message("Check gallery-dl version failed", detail, "error")
                return
            self.append_log(f"[system] gallery-dl version rc={rc}: {output}")
            self.show_compact_message(
                "Installed gallery-dl version",
                f"Version: {output}\n\nThis only runs gallery-dl --version. It does not update gallery-dl or this GUI.",
                "info",
            )

        self.version_worker.done.connect(finish)
        self.version_worker.start()

    def open_or_create_config(self) -> None:
        ind = self._ui_is_indonesian()
        if not self.config_path:
            self.config_path = detect_config_path()
            self._refresh_env()
        if not self.config_path:
            self.show_compact_message(
                "Config",
                "Tidak ada path config. Pilih file config dulu di System." if ind
                else "No config path. Choose a config file first in System.",
                "warning",
            )
            return
        p = Path(self.config_path).expanduser()
        if p.exists() and not p.is_file():
            self.show_compact_message(
                "Config",
                (f"Path config bukan file:\n{p}" if ind else f"Config path is not a file:\n{p}"),
                "warning",
            )
            return
        # Create the file if it does not exist yet.
        if not p.exists():
            res = QMessageBox.question(
                self, "Create config",
                (f"File config belum ada. Buat sekarang?\n{p}" if ind
                 else f"Config file does not exist. Create it now?\n{p}"),
            )
            if res != QMessageBox.Yes:
                return
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(p, "{\n}\n", encoding="utf-8")
                self.append_log(f"[config] created: {p}")
            except Exception as exc:
                self.show_compact_message("Create config failed", str(exc), "error")
                return
        # Try to open the file with the OS handler. open_path() never raises (it
        # returns False on failure), so we must check its result here. On Windows
        # a .json file often has no associated application, which previously made
        # this button look like it did nothing.
        if open_path(p):
            self.append_log(f"[config] opened: {p}")
            return
        # Fallback 1: open the containing folder so the user can reach the file.
        opened_folder = open_path(p.parent)
        # Fallback 2: copy the full path to the clipboard for manual opening.
        try:
            QApplication.clipboard().setText(str(p))
            copied = True
        except Exception:
            copied = False
        parts = []
        if ind:
            parts.append("Tidak bisa membuka file config langsung (biasanya karena .json belum punya aplikasi default).")
            parts.append(f"\nLokasi config:\n{p}")
            if opened_folder:
                parts.append("\nFolder-nya sudah dibuka.")
            if copied:
                parts.append("Path sudah disalin ke clipboard.")
            parts.append("\nBuka file itu dengan editor teks (mis. Notepad).")
        else:
            parts.append("Could not open the config file directly (usually because .json has no default app).")
            parts.append(f"\nConfig location:\n{p}")
            if opened_folder:
                parts.append("\nThe folder was opened for you.")
            if copied:
                parts.append("The path was copied to the clipboard.")
            parts.append("\nOpen that file with a text editor (e.g. Notepad).")
        self.show_scroll_message("Open/Create Config", " ".join(parts), "info")

    def _gallery_dl_config_guide_text(self) -> str:
        ind = self._ui_is_indonesian()
        path_hint = self.config_path or detect_config_path() or str(Path.home() / ".config" / "gallery-dl" / "config.json")
        if ind:
            return f"""Panduan Config gallery-dl
==========================

Lokasi config aktif di GUI:
{path_hint}

1) Fungsi config
----------------
Config gallery-dl adalah file JSON untuk menyimpan pengaturan default. Dengan config, user tidak perlu menulis opsi yang sama berulang-ulang di setiap command.

Struktur dasar:
{{
  "extractor": {{
    "base-directory": "~/gallery-dl/",
    "directory": ["{{category}}", "{{subcategory}}", "{{user[id]}}"],
    "filename": "{{id}}_{{filename}}.{{extension}}",
    "archive": "~/gallery-dl/archive.sqlite3",
    "skip": true
  }}
}}

2) Output dan nama file
-----------------------
- extractor.base-directory = folder dasar semua hasil download.
- extractor.directory = subfolder. Bisa berupa list, misalnya ["{{category}}", "{{user[id]}}"].
- extractor.filename = pola nama file.

Contoh aman Windows:
{{
  "extractor": {{
    "base-directory": "D:/Rips/gallery-dl/",
    "directory": ["{{category}}", "{{user[id]}}"],
    "filename": "{{id}}_{{num}}.{{extension}}",
    "path-restrict": "windows"
  }}
}}

3) Archive untuk skip download ulang
------------------------------------
Archive menyimpan ID file yang pernah diunduh. Ini membantu mencegah download ulang.

Contoh:
{{
  "extractor": {{
    "archive": "~/gallery-dl/archive.sqlite3",
    "archive-pragma": ["journal_mode=WAL", "synchronous=NORMAL"],
    "skip": true
  }}
}}

Makna skip:
- true = skip file yang sudah ada atau sudah tercatat di archive.
- false = overwrite.
- "abort" = hentikan extractor saat menemukan skip.
- "abort:5" = hentikan setelah 5 skip berturut-turut.

4) Cookies dan login
--------------------
Untuk situs yang butuh login, gunakan cookies. Di config, extractor.cookies dapat berupa:
- path cookies.txt,
- object name-value,
- list profil browser.

Contoh cookies browser Firefox:
{{
  "extractor": {{
    "cookies": ["firefox"]
  }}
}}

Contoh cookies.txt:
{{
  "extractor": {{
    "cookies": "D:/cookies/cookies.txt"
  }}
}}

Catatan: kalau masih gagal login, pakai tombol Cookies browser di GUI atau command --cookies-from-browser untuk tes cepat.

5) Rate-limit dan stabilisasi jaringan
--------------------------------------
Gunakan sleep agar lebih ramah server dan mengurangi error 429.

Contoh:
{{
  "extractor": {{
    "sleep-request": "1.0-2.0",
    "sleep-429": "60-180",
    "retries": 5,
    "timeout": 30
  }}
}}

6) Metadata dan postprocessor
-----------------------------
Untuk menyimpan metadata:
{{
  "extractor": {{
    "postprocessors": [
      {{"name": "metadata", "mode": "json"}}
    ]
  }}
}}

7) Setting khusus situs
-----------------------
Setting global diletakkan di extractor. Setting khusus situs diletakkan di extractor.<nama situs>.

Contoh:
{{
  "extractor": {{
    "base-directory": "D:/Rips/gallery-dl/",
    "archive": "D:/Rips/gallery-dl/archive.sqlite3",
    "pixiv": {{
      "filename": "{{id}}_p{{num}}.{{extension}}",
      "directory": ["Pixiv", "{{user[id]}}"]
    }},
    "twitter": {{
      "text-tweets": true,
      "postprocessors": ["content"]
    }}
  }}
}}

8) Cara pakai di GUI ini
------------------------
- Pilih/cek path config di panel Sistem.
- Klik Buka/Buat Config untuk membuka file.
- Klik Panduan/Buat Config untuk membuat template otomatis.
- Jika command input sudah punya --config, GUI tidak akan menambahkan config lagi.
- Jika command input tidak punya --config, GUI memakai config path aktif bila file config ada.

9) Tips aman
------------
- Backup config sebelum edit besar.
- Validasi JSON setelah edit.
- Gunakan slash / untuk path Windows agar JSON lebih mudah dibaca.
- Jangan simpan token/cookies sensitif di file yang akan dibagikan.
"""
        return f"""gallery-dl Config Guide
=======================

Active config path in this GUI:
{path_hint}

1) What config does
-------------------
A gallery-dl config is a JSON file for default settings. It prevents repeating the same options in every command.

Basic structure:
{{
  "extractor": {{
    "base-directory": "~/gallery-dl/",
    "directory": ["{{category}}", "{{subcategory}}", "{{user[id]}}"],
    "filename": "{{id}}_{{filename}}.{{extension}}",
    "archive": "~/gallery-dl/archive.sqlite3",
    "skip": true
  }}
}}

2) Output and filenames
-----------------------
- extractor.base-directory = base folder for all downloads.
- extractor.directory = subfolder pattern, usually a list.
- extractor.filename = downloaded filename pattern.

Windows-safe example:
{{
  "extractor": {{
    "base-directory": "D:/Rips/gallery-dl/",
    "directory": ["{{category}}", "{{user[id]}}"],
    "filename": "{{id}}_{{num}}.{{extension}}",
    "path-restrict": "windows"
  }}
}}

3) Archive to skip re-downloads
-------------------------------
The archive stores downloaded item IDs and helps prevent duplicates.

Example:
{{
  "extractor": {{
    "archive": "~/gallery-dl/archive.sqlite3",
    "archive-pragma": ["journal_mode=WAL", "synchronous=NORMAL"],
    "skip": true
  }}
}}

skip meanings:
- true = skip existing/archive-known files.
- false = overwrite.
- "abort" = stop extractor after a skip.
- "abort:5" = stop after 5 consecutive skips.

4) Cookies and login
--------------------
For login-required sites, use cookies. extractor.cookies can be:
- cookies.txt path,
- name-value object,
- browser profile list.

Firefox browser cookies example:
{{
  "extractor": {{
    "cookies": ["firefox"]
  }}
}}

cookies.txt example:
{{
  "extractor": {{
    "cookies": "D:/cookies/cookies.txt"
  }}
}}

5) Rate-limit and network stability
-----------------------------------
Use sleep settings to reduce 429/rate-limit errors.

Example:
{{
  "extractor": {{
    "sleep-request": "1.0-2.0",
    "sleep-429": "60-180",
    "retries": 5,
    "timeout": 30
  }}
}}

6) Metadata and postprocessors
------------------------------
Save metadata with:
{{
  "extractor": {{
    "postprocessors": [
      {{"name": "metadata", "mode": "json"}}
    ]
  }}
}}

7) Site-specific settings
-------------------------
Global settings go under extractor. Site-specific settings go under extractor.<site>.

Example:
{{
  "extractor": {{
    "base-directory": "D:/Rips/gallery-dl/",
    "archive": "D:/Rips/gallery-dl/archive.sqlite3",
    "pixiv": {{
      "filename": "{{id}}_p{{num}}.{{extension}}",
      "directory": ["Pixiv", "{{user[id]}}"]
    }},
    "twitter": {{
      "text-tweets": true,
      "postprocessors": ["content"]
    }}
  }}
}}

8) How this GUI uses config
---------------------------
- Select/check config path in System.
- Open/Create Config opens the file.
- Download Composer creates jobs and reusable defaults without duplicate inputs.
- If an input command already has --config, the GUI will not add another config option.
- If an input command has no --config, the active config path is used when the file exists.

9) Safety tips
--------------
- Backup your config before major edits.
- Validate JSON after editing.
- Use / in Windows paths to keep JSON readable.
- Do not share configs containing sensitive tokens/cookies.
"""

    def gallery_dl_site_preset_definitions(self) -> list[dict[str, object]]:
        """Curated extractor-category presets for the config creator.

        gallery-dl supports many extractors. The config tree accepts settings at
        extractor.<category> and extractor.<category>.<subcategory> levels, so a
        curated category list is safer than trying to hard-code every subcategory.
        Users can add any category that is not listed via the Custom Sites field.
        """
        return [
            {"key": "pixiv", "name": "Pixiv", "group": "Art", "directory": ["Pixiv", "{subcategory}", "{user[id]}"], "filename": "{id}_p{num}.{extension}", "note": "Ilustrasi, user, bookmarks, ugoira. Gunakan cookies/login jika konten privat."},
            {"key": "fanbox", "name": "Pixiv Fanbox", "group": "Creator", "directory": ["Fanbox", "{creator[id]}", "{id}"], "filename": "{id}_{num}.{extension}", "cookies": True, "browser": "firefox", "note": "Biasanya butuh cookies akun."},
            {"key": "kemono", "name": "Kemono", "group": "Creator", "directory": ["Kemono", "{service}", "{user}"], "filename": "{id}_{num}.{extension}", "cookies": True, "note": "Creator posts. Jika perlu login, pakai cookies."},
            {"key": "coomer", "name": "Coomer", "group": "Creator", "directory": ["Coomer", "{service}", "{user}"], "filename": "{id}_{num}.{extension}", "cookies": True, "note": "Creator posts. Jika perlu login, pakai cookies."},
            {"key": "patreon", "name": "Patreon", "group": "Creator", "directory": ["Patreon", "{user[name]}", "{id}"], "filename": "{id}_{num}.{extension}", "cookies": True, "browser": "chrome", "note": "Sering butuh cookies dan browser headers."},
            {"key": "gumroad", "name": "Gumroad", "group": "Creator", "directory": ["Gumroad", "{user}", "{id}"], "filename": "{id}_{num}.{extension}", "cookies": True, "note": "Produk/konten kreator, cookies bila perlu."},
            {"key": "fantia", "name": "Fantia", "group": "Creator", "directory": ["Fantia", "{fanclub[id]}", "{id}"], "filename": "{id}_{num}.{extension}", "cookies": True, "note": "Konten kreator, cookies sering diperlukan."},
            {"key": "ci-en", "name": "Ci-en", "group": "Creator", "directory": ["Ci-en", "{creator[id]}", "{id}"], "filename": "{id}_{num}.{extension}", "cookies": True, "note": "Konten kreator Jepang."},

            {"key": "flickr", "name": "Flickr", "group": "Photo", "directory": ["Flickr", "{user[username]}", "{subcategory}"], "filename": "{id}_{title}.{extension}", "sleep-request": "1.0-2.0", "note": "Foto, album, favorites, galleries."},
            {"key": "500px", "name": "500px", "group": "Photo", "directory": ["500px", "{user[username]}", "{subcategory}"], "filename": "{id}_{title}.{extension}", "note": "Foto, galleries, profiles."},
            {"key": "deviantart", "name": "DeviantArt", "group": "Art", "directory": ["DeviantArt", "{author[username]}", "{subcategory}"], "filename": "{index}_{title}.{extension}", "cookies": True, "note": "OAuth/cookies bisa diperlukan untuk private/favorites."},
            {"key": "artstation", "name": "ArtStation", "group": "Art", "directory": ["ArtStation", "{user[username]}", "{title}"], "filename": "{id}_{num}.{extension}", "browser": "firefox", "note": "Artwork, albums, likes, search."},
            {"key": "behance", "name": "Behance", "group": "Art", "directory": ["Behance", "{user[username]}", "{title}"], "filename": "{id}_{num}.{extension}", "sleep-request": "2.0-4.0", "browser": "firefox", "note": "Project/galleries, butuh jeda lebih sopan."},
            {"key": "newgrounds", "name": "Newgrounds", "group": "Art", "directory": ["Newgrounds", "{user[name]}", "{subcategory}"], "filename": "{id}_{title}.{extension}", "sleep-request": "0.5-1.5", "cookies": True, "note": "Art/media, login/cookies untuk konten tertentu."},
            {"key": "nijie", "name": "Nijie", "group": "Art", "directory": ["Nijie", "{user[id]}", "{subcategory}"], "filename": "{id}_{num}.{extension}", "sleep-request": "2.0-4.0", "cookies": True, "note": "Umumnya butuh login/cookies."},
            {"key": "twitter", "name": "Twitter / X", "group": "Social", "directory": ["Twitter", "{user[name]}", "{subcategory}"], "filename": "{tweet_id}_{num}.{extension}", "cookies": True, "browser": "firefox", "note": "Posts/media. Cookies membantu untuk rate-limit/login."},
            {"key": "instagram", "name": "Instagram", "group": "Social", "directory": ["Instagram", "{username}", "{subcategory}"], "filename": "{shortcode}_{num}.{extension}", "sleep-request": "6.0-12.0", "cookies": True, "user-agent": "browser", "note": "Perlu jeda tinggi dan cookies untuk stabil."},
            {"key": "reddit", "name": "Reddit", "group": "Social", "directory": ["Reddit", "{subreddit}", "{author}"], "filename": "{id}_{num}.{extension}", "note": "Subreddit, users, posts. Bisa spawn child extractors."},
            {"key": "tumblr", "name": "Tumblr", "group": "Social", "directory": ["Tumblr", "{blog_name}", "{subcategory}"], "filename": "{id}_{num}.{extension}", "cookies": True, "note": "Blog/posts/media."},
            {"key": "bluesky", "name": "Bluesky", "group": "Social", "directory": ["Bluesky", "{user[handle]}", "{subcategory}"], "filename": "{post_id}_{num}.{extension}", "cookies": True, "note": "Posts, feeds, bookmarks, profiles."},
            {"key": "mastodon", "name": "Mastodon", "group": "Social", "directory": ["Mastodon", "{instance}", "{account[acct]}"], "filename": "{id}_{num}.{extension}", "cookies": True, "note": "Instances seperti mastodon.social, pawoo, baraag."},
            {"key": "discord", "name": "Discord", "group": "Social", "directory": ["Discord", "{guild[name]}", "{channel[name]}"], "filename": "{id}_{num}.{extension}", "cookies": True, "note": "Channel/DM/messages, biasanya perlu auth/cookies/token sesuai docs."},

            {"key": "danbooru", "name": "Danbooru", "group": "Booru", "directory": ["Danbooru", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "cookies": True, "note": "Booru/tag search. API key bisa dipakai."},
            {"key": "gelbooru", "name": "Gelbooru", "group": "Booru", "directory": ["Gelbooru", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "cookies": True, "note": "Tag search/posts. Cookies untuk beberapa filter."},
            {"key": "yandere", "name": "Yande.re", "group": "Booru", "directory": ["Yandere", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "note": "Posts/pools/tag search."},
            {"key": "konachan", "name": "Konachan", "group": "Booru", "directory": ["Konachan", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "note": "Posts/pools/tag search."},
            {"key": "safebooru", "name": "Safebooru", "group": "Booru", "directory": ["Safebooru", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "note": "Posts/tag search."},
            {"key": "rule34", "name": "Rule34", "group": "Booru", "directory": ["Rule34", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "sleep-request": "1.0-2.0", "cookies": True, "note": "Posts/tag search. Cookies untuk beberapa kebutuhan."},
            {"key": "e621", "name": "E621", "group": "Booru", "directory": ["E621", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "cookies": True, "note": "API key/cookies bisa diperlukan."},
            {"key": "sankaku", "name": "Sankaku", "group": "Booru", "directory": ["Sankaku", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "cookies": True, "note": "Login/cookies sering diperlukan."},
            {"key": "zerochan", "name": "Zerochan", "group": "Booru", "directory": ["Zerochan", "{subcategory}"], "filename": "{id}_{title}.{extension}", "cookies": True, "note": "Tag/search/user."},

            {"key": "mangadex", "name": "MangaDex", "group": "Manga", "directory": ["MangaDex", "{manga}", "c{chapter}"], "filename": "{page:>03}.{extension}", "cookies": True, "note": "Manga/chapters, login untuk beberapa fitur."},
            {"key": "nhentai", "name": "nhentai", "group": "Manga", "directory": ["nhentai", "{gallery_id}", "{title}"], "filename": "{num:>03}.{extension}", "note": "Galleries."},
            {"key": "webtoons", "name": "Webtoons", "group": "Manga", "directory": ["Webtoons", "{comic}", "{episode}"], "filename": "{num:>03}.{extension}", "sleep-request": "0.5-1.5", "note": "Episodes/chapters."},
            {"key": "bunkr", "name": "Bunkr", "group": "Albums", "directory": ["Bunkr", "{album[id]}"], "filename": "{id}_{filename}.{extension}", "note": "Albums/media files."},
            {"key": "cyberdrop", "name": "Cyberdrop", "group": "Albums", "directory": ["Cyberdrop", "{album[id]}"], "filename": "{id}_{filename}.{extension}", "note": "Albums/media files."},
            {"key": "imgur", "name": "Imgur", "group": "Albums", "directory": ["Imgur", "{album[id]}"], "filename": "{id}_{num}.{extension}", "note": "Albums/images."},
            {"key": "postimages", "name": "Postimages", "group": "Image Host", "directory": ["Postimages", "{subcategory}"], "filename": "{id}_{filename}.{extension}", "note": "Galleries/images."},
            {"key": "imagefap", "name": "ImageFap", "group": "Albums", "directory": ["ImageFap", "{gallery_id}"], "filename": "{id}_{num}.{extension}", "sleep-request": "2.0-4.0", "note": "Galleries."},

            # Extra booru / adult-imageboard presets. These are still normal
            # gallery-dl extractor categories, not special downloader logic.
            {"key": "hypnohub", "name": "HypnoHub", "group": "Booru", "directory": ["HypnoHub", "{subcategory}", "{search_tags}"], "filename": "{id}_{md5}.{extension}", "sleep-request": "2.0-5.0", "sleep-429": "120-600", "cookies": True, "special": "gelbooru_v02", "note": "Gelbooru 0.2 style site. Pakai jeda lebih pelan, tags blacklist/whitelist, archive, dan cookies bila perlu."},
            {"key": "paheal", "name": "Rule34 Paheal", "group": "Booru", "directory": ["Rule34-Paheal", "{subcategory}", "{search_tags}"], "filename": "{id}_{filename}.{extension}", "sleep-request": "2.0-4.0", "cookies": True, "note": "rule34.paheal.net. Category config: paheal."},
            {"key": "rule34us", "name": "Rule34.us", "group": "Booru", "directory": ["Rule34.us", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "sleep-request": "2.0-4.0", "cookies": True, "note": "Rule34.us posts/tag search."},
            {"key": "rule34xyz", "name": "Rule34.xyz", "group": "Booru", "directory": ["Rule34.xyz", "{subcategory}"], "filename": "{id}_{num}.{extension}", "sleep-request": "2.0-4.0", "cookies": True, "note": "Playlist/posts/tag search."},
            {"key": "rule34vault", "name": "R34 Vault", "group": "Booru", "directory": ["R34-Vault", "{subcategory}"], "filename": "{id}_{num}.{extension}", "sleep-request": "2.0-4.0", "cookies": True, "note": "Playlists, posts, tag search."},
            {"key": "realbooru", "name": "Realbooru", "group": "Booru", "directory": ["Realbooru", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "sleep-request": "1.5-3.5", "cookies": True, "note": "Favorites, pools, posts, tag search."},
            {"key": "scatbooru", "name": "Scatbooru", "group": "Booru", "directory": ["Scatbooru", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "sleep-request": "1.5-3.5", "cookies": True, "note": "Favorites, posts, tag search."},
            {"key": "sizebooru", "name": "SizeBooru", "group": "Booru", "directory": ["SizeBooru", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "sleep-request": "1.5-3.5", "cookies": True, "note": "Favorites, galleries, posts, tag search, user uploads."},
            {"key": "twibooru", "name": "Twibooru", "group": "Booru", "directory": ["Twibooru", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "sleep-request": "1.0-2.5", "note": "Galleries, posts, search results."},
            {"key": "tbib", "name": "The Big ImageBoard", "group": "Booru", "directory": ["TBIB", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "sleep-request": "1.5-3.5", "cookies": True, "note": "Gelbooru style, favorites, pools, posts, tag search."},
            {"key": "xbooru", "name": "XBooru", "group": "Booru", "directory": ["XBooru", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "sleep-request": "2.0-5.0", "sleep-429": "120-600", "cookies": True, "special": "gelbooru_v02", "note": "Gelbooru 0.2 style. Pakai jeda dan archive untuk batch besar."},
            {"key": "atfbooru", "name": "ATFBooru", "group": "Booru", "directory": ["ATFBooru", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "cookies": True, "note": "Danbooru instance. API/cookies bisa membantu."},
            {"key": "aibooru", "name": "AIBooru", "group": "Booru", "directory": ["AIBooru", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "cookies": True, "note": "Danbooru instance."},
            {"key": "booruvar", "name": "Booruvar", "group": "Booru", "directory": ["Booruvar", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "cookies": True, "note": "Danbooru instance."},
            {"key": "e926", "name": "E926", "group": "Booru", "directory": ["E926", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "cookies": True, "note": "e621 family, posts/tag search."},
            {"key": "e6ai", "name": "E6AI", "group": "Booru", "directory": ["E6AI", "{subcategory}"], "filename": "{id}_{md5}.{extension}", "cookies": True, "note": "e621 family, posts/tag search."},
            {"key": "derpibooru", "name": "Derpibooru", "group": "Booru", "directory": ["Derpibooru", "{subcategory}"], "filename": "{id}_{sha512}.{extension}", "cookies": True, "note": "Philomena instance. API key/cookies bisa diperlukan."},
            {"key": "ponybooru", "name": "Ponybooru", "group": "Booru", "directory": ["Ponybooru", "{subcategory}"], "filename": "{id}_{sha512}.{extension}", "cookies": True, "note": "Philomena instance. API key/cookies bisa diperlukan."},
            {"key": "furbooru", "name": "Furbooru", "group": "Booru", "directory": ["Furbooru", "{subcategory}"], "filename": "{id}_{sha512}.{extension}", "cookies": True, "note": "Philomena instance. API key/cookies bisa diperlukan."},
            {"key": "rule34hentai", "name": "Rule34Hentai", "group": "Booru", "directory": ["Rule34Hentai", "{subcategory}"], "filename": "{id}_{filename}.{extension}", "sleep-request": "2.0-4.0", "cookies": True, "note": "Shimmie2 instance, posts/tag search."},
            {"key": "yiffverse", "name": "Yiff Verse", "group": "Booru", "directory": ["YiffVerse", "{subcategory}"], "filename": "{id}_{num}.{extension}", "sleep-request": "2.0-4.0", "cookies": True, "note": "Playlists, posts, tag search."},
        ]

    def _selected_site_presets_to_config(self, selected_keys: list[str], site_cookies: bool, cookies_mode: str, cookies_file: str) -> dict[str, dict[str, object]]:
        """Build extractor.<site> config blocks from selected site preset keys."""
        definitions = {str(item["key"]): item for item in self.gallery_dl_site_preset_definitions()}
        out: dict[str, dict[str, object]] = {}
        for key in selected_keys:
            item = definitions.get(str(key).strip().lower())
            if not item:
                continue
            block: dict[str, object] = {}
            directory = item.get("directory")
            filename = item.get("filename")
            if directory:
                block["directory"] = directory
            if filename:
                block["filename"] = filename
            for opt in ("sleep-request", "sleep-429", "browser", "user-agent", "skip", "abort", "terminate"):
                if item.get(opt):
                    block[opt] = item[opt]
            if site_cookies and bool(item.get("cookies")):
                if str(cookies_file or "").strip():
                    block["cookies"] = str(cookies_file).strip()
                else:
                    browser = str(cookies_mode or "").strip().lower()
                    if browser and browser not in {"none", "tidak ada"}:
                        block["cookies"] = [browser]
            if block:
                out[str(item["key"])] = block
        return out

    def _custom_site_keys_to_config(self, custom_text: str) -> dict[str, dict[str, object]]:
        """Build safe generic config blocks for arbitrary gallery-dl categories."""
        out: dict[str, dict[str, object]] = {}
        for raw in re.split(r"[,;\n]+", custom_text or ""):
            key = re.sub(r"[^a-z0-9_.-]+", "", raw.strip().lower())
            if not key:
                continue
            out[key] = {
                "directory": [key, "{subcategory}"],
                "filename": "{category}_{subcategory}_{id}_{num}.{extension}",
            }
        return out

    def _config_site_preset_help_text(self) -> str:
        ind = self._ui_is_indonesian()
        definitions = self.gallery_dl_site_preset_definitions()
        by_group: dict[str, list[str]] = {}
        for item in definitions:
            by_group.setdefault(str(item.get("group", "Other")), []).append(f"{item['name']} ({item['key']})")
        grouped = "\n".join(f"- {group}: " + ", ".join(names) for group, names in sorted(by_group.items()))
        if ind:
            return f"""Preset Situs / Extractor
=========================

Config gallery-dl bisa punya setting global dan setting khusus situs:

{{
  "extractor": {{
    "base-directory": "D:/Rips/gallery-dl/",
    "filename": "{{id}}_{{num}}.{{extension}}",
    "pixiv": {{
      "directory": ["Pixiv", "{{user[id]}}"],
      "filename": "{{id}}_p{{num}}.{{extension}}"
    }}
  }}
}}

Preset yang tersedia di dialog ini:
{grouped}

Catatan penting:
- Tidak mungkin memaksa satu pola keyword sempurna untuk semua situs karena tiap extractor punya keyword berbeda.
- Preset ini memakai pola aman yang umum. Kalau nama folder/file masih kurang pas, cek keyword situs memakai command:
  gallery-dl -K URL
  gallery-dl -E URL
- Untuk melihat semua extractor yang didukung versi gallery-dl lokal:
  gallery-dl --list-extractors
- Jika situs belum ada di daftar, masukkan category di kolom Custom. Contoh: flickr, pixiv, kemono, coomer, danbooru.
- Category adalah nama lowercase situs. Subcategory bisa diatur manual dengan struktur extractor.<category>.<subcategory> jika perlu.
"""
        return f"""Site / Extractor Presets
========================

gallery-dl config can contain global settings and site-specific settings:

{{
  "extractor": {{
    "base-directory": "D:/Rips/gallery-dl/",
    "filename": "{{id}}_{{num}}.{{extension}}",
    "pixiv": {{
      "directory": ["Pixiv", "{{user[id]}}"],
      "filename": "{{id}}_p{{num}}.{{extension}}"
    }}
  }}
}}

Available presets in this dialog:
{grouped}

Notes:
- One perfect keyword pattern cannot be forced for every site because each extractor exposes different metadata keys.
- These presets use broadly safe patterns. If folders/files need tuning, inspect keywords with:
  gallery-dl -K URL
  gallery-dl -E URL
- To list every extractor supported by your local gallery-dl version:
  gallery-dl --list-extractors
- If a site is missing here, add its category in the Custom field, for example: flickr, pixiv, kemono, coomer, danbooru.
- Category is the lowercase site name. Subcategory overrides can be added manually with extractor.<category>.<subcategory> when needed.
"""

    def _build_gallery_dl_config_template(
        self,
        base_dir: str,
        archive_enabled: bool,
        archive_path: str,
        cookies_mode: str,
        cookies_file: str,
        sleep_request: str,
        sleep_429: str,
        retries: int,
        timeout: int,
        windows_safe: bool,
        metadata_json: bool,
        filename: str,
        directory_text: str,
        skip_value: str,
        site_presets: Optional[list[str]] = None,
        site_cookies: bool = False,
        custom_sites: str = "",
    ) -> dict:
        extractor: dict[str, object] = {}
        if base_dir.strip():
            extractor["base-directory"] = base_dir.strip()
        directory_parts = [part.strip() for part in re.split(r"[;,]", directory_text or "") if part.strip()]
        if directory_parts:
            extractor["directory"] = directory_parts
        if filename.strip():
            extractor["filename"] = filename.strip()
        if archive_enabled and archive_path.strip():
            extractor["archive"] = archive_path.strip()
            extractor["archive-pragma"] = ["journal_mode=WAL", "synchronous=NORMAL"]
        if skip_value == "true":
            extractor["skip"] = True
        elif skip_value == "false":
            extractor["skip"] = False
        elif skip_value:
            extractor["skip"] = skip_value
        if cookies_file.strip():
            extractor["cookies"] = cookies_file.strip()
        else:
            browser = cookies_mode.strip().lower()
            if browser and browser not in {"none", "tidak ada"}:
                extractor["cookies"] = [browser]
        if sleep_request.strip():
            extractor["sleep-request"] = sleep_request.strip()
        if sleep_429.strip():
            extractor["sleep-429"] = sleep_429.strip()
        if retries > 0:
            extractor["retries"] = retries
        if timeout > 0:
            extractor["timeout"] = timeout
        if windows_safe:
            extractor["path-restrict"] = "windows"
        if metadata_json:
            extractor["postprocessors"] = [{"name": "metadata", "mode": "json"}]
        # Make site presets more tolerant of missing metadata keys.
        extractor.setdefault("keywords-default", "unknown")
        site_blocks = self._selected_site_presets_to_config(site_presets or [], site_cookies, cookies_mode, cookies_file)
        site_blocks.update(self._custom_site_keys_to_config(custom_sites))
        for key, block in site_blocks.items():
            existing = extractor.get(key)
            if isinstance(existing, dict):
                merged = dict(existing)
                merged.update(block)
                extractor[key] = merged
            else:
                extractor[key] = block
        return {"extractor": extractor}

    def _legacy_open_config_guide_dialog(self) -> None:
        """Retained for migration reference; the public UI uses ComposerMixin."""
        ind = self._ui_is_indonesian()
        dlg = QDialog(self)
        dlg.setWindowTitle("Panduan & Pembuat Config" if ind else "Config Guide & Creator")
        dlg.resize(920, 680)
        dlg.setMinimumSize(780, 560)
        root = QVBoxLayout(dlg)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        intro = QLabel(
            "Panduan ini khusus untuk config gallery-dl. Builder command tetap terpisah."
            if ind else
            "This guide is only for gallery-dl config. The command builder stays separate."
        )
        intro.setObjectName("subtle")
        intro.setWordWrap(True)
        root.addWidget(intro)

        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        guide_box = QPlainTextEdit()
        guide_box.setReadOnly(True)
        guide_box.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        guide_box.setPlainText(self._gallery_dl_config_guide_text())
        tabs.addTab(guide_box, "Panduan" if ind else "Guide")

        creator_page = QWidget()
        creator_l = QGridLayout(creator_page)
        creator_l.setContentsMargins(8, 8, 8, 8)
        creator_l.setHorizontalSpacing(10)
        creator_l.setVerticalSpacing(7)
        tabs.addTab(creator_page, "Buat Config" if ind else "Create Config")

        site_page = QWidget()
        site_l = QVBoxLayout(site_page)
        site_l.setContentsMargins(8, 8, 8, 8)
        site_l.setSpacing(8)
        site_intro = QLabel(
            "Pilih preset situs yang sering dipakai. Untuk situs lain, masukkan category extractor di kolom Custom."
            if ind else
            "Select common site presets. For other sites, add extractor categories in the Custom field."
        )
        site_intro.setObjectName("subtle")
        site_intro.setWordWrap(True)
        site_l.addWidget(site_intro)

        site_scroll = QScrollArea()
        site_scroll.setWidgetResizable(True)
        site_scroll.setFrameShape(QFrame.NoFrame)
        site_content = QWidget()
        site_grid = QGridLayout(site_content)
        site_grid.setContentsMargins(4, 4, 4, 4)
        site_grid.setHorizontalSpacing(10)
        site_grid.setVerticalSpacing(5)
        site_scroll.setWidget(site_content)
        site_l.addWidget(site_scroll, 1)

        site_checkboxes: dict[str, QCheckBox] = {}
        definitions = self.gallery_dl_site_preset_definitions()
        for i, item in enumerate(definitions):
            label = f"{item['name']}  ({item['key']})"
            cb = QCheckBox(label)
            cb.setToolTip(str(item.get("note", "")))
            site_checkboxes[str(item["key"])] = cb
            site_grid.addWidget(cb, i // 2, i % 2)
        site_grid.setColumnStretch(0, 1)
        site_grid.setColumnStretch(1, 1)

        site_extra = QFrame()
        site_extra.setObjectName("card")
        site_extra_l = QGridLayout(site_extra)
        site_extra_l.setContentsMargins(8, 8, 8, 8)
        site_extra_l.setHorizontalSpacing(8)
        site_extra_l.setVerticalSpacing(6)
        site_cookies = QCheckBox("Pakai cookies untuk preset yang butuh login" if ind else "Use cookies for login-required presets")
        site_cookies.setChecked(True)
        custom_sites = QLineEdit()
        custom_sites.setPlaceholderText("contoh: hypnohub; rule34; paheal; rule34us; xbooru" if ind else "example: hypnohub; rule34; paheal; rule34us; xbooru")
        btn_common = QPushButton("Pilih umum" if ind else "Select common")
        btn_art = QPushButton("Pilih art/creator" if ind else "Select art/creator")
        btn_social = QPushButton("Pilih sosial" if ind else "Select social")
        btn_booru = QPushButton("Pilih booru" if ind else "Select booru")
        btn_adult_booru = QPushButton("Pilih booru dewasa" if ind else "Select adult booru")
        btn_clear_sites = QPushButton("Bersihkan" if ind else "Clear")
        site_extra_l.addWidget(site_cookies, 0, 0, 1, 3)
        site_extra_l.addWidget(QLabel("Custom category" if not ind else "Category custom"), 1, 0)
        site_extra_l.addWidget(custom_sites, 1, 1, 1, 4)
        for col, btn in enumerate([btn_common, btn_art, btn_social, btn_booru, btn_adult_booru, btn_clear_sites]):
            site_extra_l.addWidget(btn, 2, col)
        site_extra_l.setColumnStretch(1, 1)
        site_l.addWidget(site_extra)

        site_help = QPlainTextEdit()
        site_help.setReadOnly(True)
        site_help.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        site_help.setPlainText(self._config_site_preset_help_text())
        site_help.setMinimumHeight(130)
        site_l.addWidget(site_help)
        tabs.addTab(site_page, "Preset Situs" if ind else "Site Presets")

        def lab(text: str, row: int, col: int = 0) -> None:
            label = QLabel(text)
            label.setObjectName("fieldLabel")
            creator_l.addWidget(label, row, col)

        base_dir = QLineEdit(self.edit_output.text().strip() or "~/gallery-dl/")
        archive_enabled = QCheckBox("Aktifkan archive SQLite" if ind else "Enable SQLite archive")
        archive_enabled.setChecked(True)
        archive_path = QLineEdit(str((APP_DIR / "archive.sqlite3").expanduser()))
        cookies_mode = QComboBox()
        cookies_mode.addItems(["none", "chrome", "firefox", "edge", "brave", "chromium", "opera"])
        gui_cookie = self.combo_cookies.currentText() if hasattr(self, "combo_cookies") else "none"
        if cookies_mode.findText(gui_cookie) >= 0:
            cookies_mode.setCurrentText(gui_cookie)
        cookies_file = QLineEdit()
        cookies_file.setPlaceholderText("D:/cookies/cookies.txt")
        directory = QLineEdit("{category}; {subcategory}; {user[id]}")
        filename = QLineEdit("{id}_{num}.{extension}")
        sleep_request = QLineEdit("1.0-2.0")
        sleep_429 = QLineEdit("60-180")
        retries = QSpinBox()
        retries.setButtonSymbols(QAbstractSpinBox.UpDownArrows)
        retries.setRange(0, 99)
        retries.setValue(max(0, self.spin_retries.value() if hasattr(self, "spin_retries") else 5))
        timeout = QSpinBox()
        timeout.setButtonSymbols(QAbstractSpinBox.UpDownArrows)
        timeout.setRange(0, 999)
        timeout.setValue(30)
        timeout.setSuffix(" s")
        windows_safe = QCheckBox("Nama file aman Windows" if ind else "Windows-safe filenames")
        windows_safe.setChecked(IS_WINDOWS)
        metadata_json = QCheckBox("Tulis metadata JSON" if ind else "Write metadata JSON")
        skip_combo = QComboBox()
        skip_combo.addItems(["true", "false", "abort", "abort:5", "terminate:3"])

        lab("Base directory" if not ind else "Folder dasar", 0)
        creator_l.addWidget(base_dir, 0, 1, 1, 3)
        lab("Directory pattern" if not ind else "Pola folder", 1)
        creator_l.addWidget(directory, 1, 1, 1, 3)
        lab("Filename pattern" if not ind else "Pola nama file", 2)
        creator_l.addWidget(filename, 2, 1, 1, 3)
        creator_l.addWidget(archive_enabled, 3, 1, 1, 3)
        lab("Archive path" if not ind else "Path archive", 4)
        creator_l.addWidget(archive_path, 4, 1, 1, 3)
        lab("Cookies browser" if not ind else "Cookies browser", 5)
        creator_l.addWidget(cookies_mode, 5, 1)
        lab("cookies.txt" if not ind else "cookies.txt", 5, 2)
        creator_l.addWidget(cookies_file, 5, 3)
        lab("Sleep request" if not ind else "Jeda request", 6)
        creator_l.addWidget(sleep_request, 6, 1)
        lab("Sleep 429" if not ind else "Jeda 429", 6, 2)
        creator_l.addWidget(sleep_429, 6, 3)
        lab("Retries" if not ind else "Coba ulang", 7)
        creator_l.addWidget(retries, 7, 1)
        lab("Timeout" if not ind else "Timeout", 7, 2)
        creator_l.addWidget(timeout, 7, 3)
        lab("Skip behavior" if not ind else "Perilaku skip", 8)
        creator_l.addWidget(skip_combo, 8, 1)
        creator_l.addWidget(windows_safe, 8, 2)
        creator_l.addWidget(metadata_json, 8, 3)
        creator_l.setColumnStretch(1, 1)
        creator_l.setColumnStretch(3, 1)

        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        tabs.addTab(preview, "Preview JSON" if not ind else "Pratinjau JSON")

        def selected_site_keys() -> list[str]:
            return [key for key, cb in site_checkboxes.items() if cb.isChecked()]

        def current_config() -> dict:
            return self._build_gallery_dl_config_template(
                base_dir.text(), archive_enabled.isChecked(), archive_path.text(), cookies_mode.currentText(), cookies_file.text(),
                sleep_request.text(), sleep_429.text(), retries.value(), timeout.value(), windows_safe.isChecked(),
                metadata_json.isChecked(), filename.text(), directory.text(), skip_combo.currentText(),
                selected_site_keys(), site_cookies.isChecked(), custom_sites.text()
            )

        def refresh_preview() -> None:
            preview.setPlainText(json.dumps(current_config(), indent=2, ensure_ascii=False))

        for w in [base_dir, archive_path, cookies_file, directory, filename, sleep_request, sleep_429]:
            w.textChanged.connect(refresh_preview)
        for w in [archive_enabled, windows_safe, metadata_json]:
            w.toggled.connect(refresh_preview)
        for w in [cookies_mode, skip_combo]:
            w.currentTextChanged.connect(lambda _=None: refresh_preview())
        for cb in site_checkboxes.values():
            cb.toggled.connect(lambda _=None: refresh_preview())
        site_cookies.toggled.connect(lambda _=None: refresh_preview())
        custom_sites.textChanged.connect(lambda _=None: refresh_preview())
        retries.valueChanged.connect(lambda _=None: refresh_preview())
        timeout.valueChanged.connect(lambda _=None: refresh_preview())

        def set_site_selection(keys: set[str]) -> None:
            for key, cb in site_checkboxes.items():
                cb.blockSignals(True)
                cb.setChecked(key in keys)
                cb.blockSignals(False)
            refresh_preview()

        common_keys = {"pixiv", "fanbox", "kemono", "coomer", "flickr", "twitter", "instagram", "reddit", "deviantart", "danbooru", "gelbooru", "rule34", "hypnohub"}
        art_keys = {str(item["key"]) for item in definitions if str(item.get("group")) in {"Art", "Creator", "Photo"}}
        social_keys = {str(item["key"]) for item in definitions if str(item.get("group")) == "Social"}
        booru_keys = {str(item["key"]) for item in definitions if str(item.get("group")) == "Booru"}
        adult_booru_keys = {"hypnohub", "rule34", "paheal", "rule34us", "rule34xyz", "rule34vault", "realbooru", "scatbooru", "sizebooru", "tbib", "xbooru", "rule34hentai", "yiffverse"}
        btn_common.clicked.connect(lambda: set_site_selection(common_keys))
        btn_art.clicked.connect(lambda: set_site_selection(art_keys))
        btn_social.clicked.connect(lambda: set_site_selection(social_keys))
        btn_booru.clicked.connect(lambda: set_site_selection(booru_keys))
        btn_adult_booru.clicked.connect(lambda: set_site_selection(adult_booru_keys))
        btn_clear_sites.clicked.connect(lambda: set_site_selection(set()))
        refresh_preview()

        btn_row = QHBoxLayout()
        copy_guide = QPushButton("Salin Panduan" if ind else "Copy Guide")
        copy_json = QPushButton("Salin JSON" if ind else "Copy JSON")
        save_current = QPushButton("Simpan ke Config Aktif" if ind else "Save to Active Config")
        save_as = QPushButton("Simpan Sebagai..." if ind else "Save As...")
        close_btn = QPushButton("Tutup" if ind else "Close")
        btn_row.addWidget(copy_guide)
        btn_row.addWidget(copy_json)
        btn_row.addStretch(1)
        btn_row.addWidget(save_current)
        btn_row.addWidget(save_as)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

        def write_config(path: str) -> None:
            try:
                data = current_config()
                json.loads(json.dumps(data))
                pp = Path(path).expanduser()
                pp.parent.mkdir(parents=True, exist_ok=True)
                if pp.exists():
                    res = QMessageBox.question(
                        dlg,
                        "Overwrite existing config" if not ind else "Timpa config yang ada",
                        (
                            f"This config already exists:\n{pp}\n\nA backup will be created before saving. Continue?"
                            if not ind else
                            f"Config ini sudah ada:\n{pp}\n\nBackup akan dibuat sebelum menyimpan. Lanjutkan?"
                        ),
                    )
                    if res != QMessageBox.Yes:
                        return
                    backup = unique_path(pp.with_name(pp.name + f".bak_{timestamp_slug()}"))
                    shutil.copy2(pp, backup)
                    try:
                        backup.chmod(0o600)
                    except OSError:
                        pass
                    self.append_log(f"[config] backup created: {backup}")
                atomic_write_text(
                    pp,
                    json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                    mode=0o600,
                )
                self.config_path = str(pp)
                self._refresh_env()
                self.show_compact_message("Config" if not ind else "Config", f"Saved:\n{pp}" if not ind else f"Tersimpan:\n{pp}", "info")
            except Exception as exc:
                self.show_compact_message("Save config failed" if not ind else "Simpan config gagal", str(exc), "error")

        copy_guide.clicked.connect(lambda: QApplication.clipboard().setText(guide_box.toPlainText()))
        copy_json.clicked.connect(lambda: QApplication.clipboard().setText(preview.toPlainText()))
        save_current.clicked.connect(lambda: write_config(self.config_path or detect_config_path() or str(APP_DIR / "config.json")))
        def save_as_clicked() -> None:
            path, _ = QFileDialog.getSaveFileName(dlg, "Save config" if not ind else "Simpan config", self.config_path or str(APP_DIR / "config.json"), "JSON (*.json);;All files (*.*)")
            if path:
                write_config(path)
        save_as.clicked.connect(save_as_clicked)
        close_btn.clicked.connect(dlg.accept)
        dlg.exec()

    def validate_config(self) -> None:
        if not self.config_path:
            self.show_compact_message("Config", "Config path is not detected.", "warning")
            return
        p = Path(self.config_path).expanduser()
        if p.exists() and not p.is_file():
            self.show_compact_message("Config", f"Config path is not a file:\n{p}", "warning")
            return
        if not p.exists():
            res = QMessageBox.question(self, "Config missing", f"Create config file?\n{p}")
            if res == QMessageBox.Yes:
                try:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_text(p, "{\n}\n", encoding="utf-8")
                except Exception as exc:
                    self.show_compact_message("Create config failed", str(exc), "error")
            return
        if p.suffix.lower() == ".json":
            try:
                text = read_text_safely(p)
                # Detect duplicate keys. Standard json.loads silently keeps only
                # the LAST value for a repeated key, so a config that parses
                # "valid" can still be silently dropping an entire section
                # (e.g. a second "reddit" block overriding the first). Surface
                # this as a warning instead of a false all-clear.
                dup_keys: list[str] = []

                def _dup_hook(pairs: list[tuple[str, object]]) -> dict:
                    seen: set[str] = set()
                    for key, _value in pairs:
                        if key in seen:
                            dup_keys.append(key)
                        seen.add(key)
                    return dict(pairs)

                parsed_config = json.loads(text, object_pairs_hook=_dup_hook)
                if not isinstance(parsed_config, dict):
                    raise ValueError("Config root must be a JSON object")
                if dup_keys:
                    unique_dups = ", ".join(sorted(set(dup_keys)))
                    self.show_compact_message(
                        "Config duplicate keys",
                        "Config JSON parses, but has DUPLICATE keys: "
                        f"{unique_dups}.\n\nJSON keeps only the last value for each "
                        "repeated key, so earlier settings under these keys are "
                        "silently ignored. Merge or remove the duplicates.",
                        "warning",
                    )
                else:
                    self.show_compact_message("Config", "Config JSON is valid.", "info")
            except Exception as exc:
                self.show_compact_message("Invalid config", str(exc), "error")
        else:
            self.show_compact_message("Config", "Config exists. Non-JSON config was not parsed.", "info")

    def open_output_folder(self) -> None:
        path = safe_expand_path(self.edit_output.text() or ".")
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.show_compact_message("Open output folder", f"Could not create the folder:\n{path}\n\n{exc}", "warning")
            return
        if not open_path(path):
            self.show_compact_message("Open output folder", f"Could not open the folder:\n{path}", "warning")

    def set_theme(self, theme: str, *, persist: bool = True) -> None:
        """Apply a named theme and keep the header selector in sync."""
        normalized = str(theme or "").strip().lower()
        if normalized not in {"dark", "light"}:
            return
        app = QApplication.instance()
        if app is None:
            return
        self.current_theme = normalized
        dark = normalized == "dark"
        app.setPalette(theme_palette(dark))
        app.setStyleSheet(DARK_QSS if dark else LIGHT_QSS)
        icon = application_icon(dark=dark)
        app.setWindowIcon(icon)
        self.setWindowIcon(icon)
        brand_mark = getattr(self, "lbl_brand_mark", None)
        if brand_mark is not None:
            brand_mark.setPixmap(icon.pixmap(30, 30))
        if self.tray_icon is not None:
            self.tray_icon.setIcon(icon)
        theme_filter = getattr(app, "_native_window_theme_filter", None)
        if theme_filter is not None:
            theme_filter.set_dark(dark)
        else:
            # Fallback for a window constructed outside create_application().
            apply_native_window_theme(self, dark)

        selector = getattr(self, "combo_theme", None)
        if selector is not None:
            index = selector.findData(normalized)
            if index >= 0 and selector.currentIndex() != index:
                blocker = QSignalBlocker(selector)
                selector.setCurrentIndex(index)
                del blocker
        if persist:
            self.autosave_session()

    def toggle_theme(self) -> None:
        self.set_theme("light" if self.current_theme == "dark" else "dark")

    def save_session(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save session", str(APP_DIR / "session.json"), "JSON (*.json)")
        if not path:
            return
        try:
            data = self.session_data()
            atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            self.append_log(f"[session] saved: {path}")
        except Exception as exc:
            self.show_compact_message("Save session failed", str(exc), "error")

    def load_session(self) -> None:
        if self.active_workers > 0:
            self.show_compact_message("Load session", "Stop the current download before loading a session.", "info")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Load session", str(APP_DIR), "JSON (*.json);;All files (*.*)")
        if not path:
            return
        try:
            data = json.loads(read_text_safely(path))
            if not isinstance(data, dict):
                raise ValueError("Session file must contain a JSON object.")
            self.apply_session_data(data)
            self.append_log(f"[session] loaded: {path}")
        except Exception as exc:
            self.show_compact_message("Load session failed", f"Session file cannot be loaded:\n{exc}", "error")

    def session_data(self) -> dict:
        persisted_gdl_cmd = self.gdl_cmd or ""
        if redact_sensitive_text(persisted_gdl_cmd) != persisted_gdl_cmd:
            # The executable field is not an authentication store. Omitting a
            # secret-bearing compound command keeps autosave safe and lets the
            # next launch retain its freshly detected local gallery-dl path.
            persisted_gdl_cmd = ""
        return {
            "schema": 4,
            "commands": redact_sensitive_database_text(self.txt_commands.toPlainText()),
            "gdl_cmd": persisted_gdl_cmd,
            "config_path": self.config_path,
            "output_dir": self.edit_output.text(),
            "workers": self.spin_workers.value(),
            "cookies": self.combo_cookies.currentText(),
            "retries": self.spin_retries.value(),
            "compress": self.chk_compress.isChecked(),
            "archive": self.combo_archive.currentText(),
            "convert_webp": self.chk_convert_webp.isChecked(),
            "theme": self.current_theme,
            "help_language": getattr(self, "help_language", "English"),
            "audit_mode": self.audit_mode,
            "compact_mode": self.compact_mode,
            "notifications_enabled": self.notifications_enabled,
            "service_policy": self.service_policy,
            "retry_strategy": self.retry_strategy,
            "active_account_profile_id": self.active_account_profile_id,
            "clipboard_inbox_enabled": self.clipboard_inbox_enabled,
            "clipboard_allowed_hosts": self.clipboard_allowed_hosts,
            "close_to_tray": self.close_to_tray,
        }

    def apply_session_data(self, data: dict) -> None:
        # Schema note: fields are read defensively (missing keys -> defaults,
        # combo values via findText, policy via sanitize), so schema 1-3
        # session/profile files all apply safely without a migration table.
        if not isinstance(data, dict):
            raise ValueError("Session data must be a JSON object")
        # Validate fields that become commands or filesystem paths before any
        # widget/state mutation.  Stringifying containers produces plausible-
        # looking but broken commands/paths and can leave a partially applied
        # session when a later Qt setText() rejects the value.
        for field in ("commands", "gdl_cmd", "config_path", "output_dir"):
            value = data.get(field)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"Session field {field} must be text")
        commands_text = data.get("commands") or ""
        # Validate before changing any settings so an oversized/malformed
        # session cannot partially apply and then leave stale queue state.
        parse_text_database(commands_text)
        saved_cmd = (data.get("gdl_cmd") or "").strip()
        if REDACTED in saved_cmd:
            self.append_log(
                "[session] ignored a gallery-dl command whose credentials "
                "were removed; keeping the detected local command"
            )
        elif saved_cmd:
            # A session can carry a gallery-dl path from another machine, a
            # removed install, or even a directory selected by malformed data.
            # Only restore concrete paths that still name a file. Bare PATH
            # commands and compound commands such as ``python -m gallery_dl``
            # remain valid and are resolved when the worker starts.
            probe = Path(_strip_wrapping_quotes(saved_cmd)).expanduser()
            looks_like_path = len(split_command(saved_cmd)) == 1 and (
                "/" in saved_cmd or "\\" in saved_cmd or probe.suffix.lower() == ".exe"
            )
            if looks_like_path and not probe.is_file():
                kept = self.gdl_cmd or "not found"
                self.append_log(
                    f"[session] saved gallery-dl executable is unavailable ({saved_cmd}); "
                    f"keeping detected: {kept}"
                )
            else:
                self.gdl_cmd = saved_cmd
        self.config_path = data.get("config_path") or self.config_path
        self.edit_output.setText(str(data.get("output_dir") or "./downloads"))
        self.spin_workers.setValue(safe_int(data.get("workers"), 3, 1, 32))
        cookies = str(data.get("cookies") or "none")
        if self.combo_cookies.findText(cookies) >= 0:
            self.combo_cookies.setCurrentText(cookies)
        self.spin_retries.setValue(safe_int(data.get("retries"), 0, 0, 20))
        self.chk_compress.setChecked(safe_bool(data.get("compress"), False))
        archive = str(data.get("archive") or "zip")
        if self.combo_archive.findText(archive) >= 0:
            self.combo_archive.setCurrentText(archive)
        self.chk_convert_webp.setChecked(safe_bool(data.get("convert_webp"), False))
        theme = str(data.get("theme") or self.current_theme)
        self.set_help_language(str(data.get("help_language") or getattr(self, "help_language", "English")))
        self.audit_mode = safe_bool(data.get("audit_mode"), self.audit_mode)
        self.notifications_enabled = safe_bool(data.get("notifications_enabled"), self.notifications_enabled)
        if isinstance(data.get("service_policy"), dict):
            self.service_policy = sanitize_service_policy(data.get("service_policy"))
        if isinstance(data.get("retry_strategy"), dict):
            allowed = {"network", "rate-limit", "unknown", "path", "config", "auth/cookies", "not-found"}
            self.retry_strategy.update({str(k): safe_bool(v, self.retry_strategy.get(str(k), False)) for k, v in data.get("retry_strategy", {}).items() if str(k) in allowed})
        account_id = safe_int(data.get("active_account_profile_id"), 0, 0)
        self.active_account_profile_id = account_id or None
        self.clipboard_inbox_enabled = safe_bool(data.get("clipboard_inbox_enabled"), False)
        self.clipboard_allowed_hosts = str(data.get("clipboard_allowed_hosts") or "")
        self.close_to_tray = safe_bool(data.get("close_to_tray"), False)
        compact_requested = safe_bool(data.get("compact_mode"), self.compact_mode)
        self.txt_commands.setPlainText(commands_text)
        if theme in {"dark", "light"}:
            self.set_theme(theme, persist=False)
        if compact_requested != self.compact_mode:
            self.set_compact_mode(compact_requested, notify=False)
        self._refresh_env()

    def autosave_session(self) -> None:
        try:
            app_data_dir()
            atomic_write_text(AUTOSAVE_FILE, json.dumps(self.session_data(), ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


__all__ = ['SystemToolsMixin']
