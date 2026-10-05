"""Useful application maintenance for the portable EXE and source checkout."""

import shutil
import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QGridLayout, QGroupBox, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .core import APP_DIR, APP_VERSION, command_executable_available, open_path, redact_sensitive_text, safe_expand_path


def build_application_tools(window) -> QWidget:
    ind = window._ui_is_indonesian()
    tr = lambda en, id_text: id_text if ind else en
    page = QWidget()
    page.setObjectName("applicationTools")
    outer = QVBoxLayout(page)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    body = QWidget()
    layout = QVBoxLayout(body)
    scroll.setWidget(body)
    outer.addWidget(scroll)

    def label(text: str, name: str = "") -> QLabel:
        item = QLabel(text)
        item.setTextFormat(Qt.PlainText)
        item.setTextInteractionFlags(Qt.TextSelectableByMouse)
        item.setWordWrap(True)
        if name:
            item.setObjectName(name)
        layout.addWidget(item)
        return item

    frozen = bool(getattr(sys, "frozen", False))
    label(tr("Application and files", "Aplikasi dan file"))
    label(tr(
        "gallery-dl is included with this application. No separate Python runtime or installation is needed. Keep the whole extracted application folder together. Update by using a newer GdlScrape release.",
        "gallery-dl sudah disertakan. Tidak perlu memasang Python atau runtime. Simpan seluruh folder hasil ekstraksi bersama. Untuk memperbarui aplikasi, gunakan rilis GdlScrape yang lebih baru.",
    ) if frozen else tr("Running from source. The summary below shows the download command and file locations used by this installation.", "Berjalan dari kode sumber. Ringkasan di bawah menunjukkan program unduhan dan lokasi file yang dipakai instalasi ini."))
    status = label("", "applicationStatus")
    locations = label("", "applicationLocations")

    def refresh() -> None:
        try:
            import gallery_dl.version
            version = gallery_dl.version.__version__
        except ImportError:
            version = tr("unavailable", "tidak tersedia")
        command = window.gdl_cmd or ""
        command_ok = command_executable_available(command)
        ffmpeg = shutil.which("ffmpeg")
        status.setText(
            f"GdlScrape {APP_VERSION}\n" + tr("Gallery-dl module used by the GUI", "Modul gallery-dl yang dipakai GUI") + f": {version}\n"
            + tr("Download command", "Program unduhan") + ": " + (tr("available", "tersedia") if command_ok else tr("missing — check the extracted application files", "tidak ditemukan — periksa file hasil ekstraksi")) + "\n"
            + tr("FFmpeg on PATH", "FFmpeg di PATH") + ": " + (tr("found", "ditemukan") if ffmpeg else tr("not found; needed only for media conversion", "tidak ditemukan; diperlukan untuk konversi media"))
        )
        config = str(safe_expand_path(window.config_path).resolve()) if window.config_path else tr("Not selected; create/import through Config Maker", "Belum dipilih; buat/impor lewat Config Maker")
        config_state = ""
        if window.config_path:
            config_state = tr(" (file exists)", " (file tersedia)") if safe_expand_path(window.config_path).is_file() else tr(" (not created yet)", " (belum dibuat)")
        output = str(safe_expand_path(window.edit_output.text().strip() or ".").resolve())
        locations.setText(redact_sensitive_text(
            tr("Download program: ", "Program unduhan: ") + (command or tr("not found", "tidak ditemukan")) + "\n"
            + "Config: " + config + config_state + "\n"
            + tr("Shared download folder: ", "Folder unduhan bersama: ") + output + "\n"
            + tr("Application data: ", "Data aplikasi: ") + str(APP_DIR) + "\n"
            + tr("FFmpeg: ", "FFmpeg: ") + (ffmpeg or "—")
        ))

    label(tr("Website/job settings can choose a different download folder. FFmpeg may also use a custom location from config. The included module version can differ from a custom download command; Check downloader version verifies the active command.", "Pengaturan situs/job dapat memilih folder unduhan berbeda. FFmpeg juga bisa memakai lokasi khusus dari config. Versi modul dalam aplikasi dapat berbeda dari program unduhan khusus; Periksa versi downloader memeriksa program yang aktif."))

    def open_config_folder() -> None:
        if not window.config_path:
            window.show_compact_message(tr("Config folder", "Folder config"), tr("Create or import a config through Config Maker first.", "Buat atau impor config lewat Config Maker dahulu."), "info")
            return
        parent = safe_expand_path(window.config_path).resolve().parent
        if not parent.is_dir() or not open_path(parent):
            window.show_compact_message(tr("Config folder", "Folder config"), tr("Could not open this folder: ", "Tidak dapat membuka folder ini: ") + str(parent), "warning")

    actions = (
        ("config", "Open Config Maker", "Buka Config Maker", window.open_config_builder),
        ("output", "Open downloads", "Buka folder unduhan", window.open_output_folder),
        ("configFolder", "Open config folder", "Buka folder config", open_config_folder),
        ("data", "Open application data", "Buka data aplikasi", window.open_app_data_dir),
        ("backupConfig", "Back up active config", "Cadangkan config aktif", window.backup_config_file),
        ("backupData", "Back up application data", "Cadangkan data aplikasi", window.backup_app_data),
        ("health", "Check config and folders", "Periksa config dan folder", window.health_check),
        ("version", "Check downloader version", "Periksa versi downloader", window.check_version),
        ("report", "Export diagnostic report", "Ekspor laporan diagnosis", window.export_audit_report),
        ("releases", "Open release page", "Buka halaman rilis", lambda: QDesktopServices.openUrl(QUrl("https://github.com/Halo1211/GdlScrape/releases"))),
        ("refresh", "Refresh this summary", "Segarkan ringkasan", refresh),
    )
    for en_title, id_title, keys in (
        ("Open your files", "Buka file Anda", ("output", "configFolder", "data")),
        ("Backups and saved config", "Backup dan config tersimpan", ("backupConfig", "backupData", "config")),
        ("Troubleshooting and updates", "Pemeriksaan dan pembaruan", ("health", "version", "report", "releases", "refresh")),
    ):
        group = QGroupBox(tr(en_title, id_title))
        buttons = QGridLayout(group)
        selected = [action for key in keys for action in actions if action[0] == key]
        for index, (key, en, id_text, callback) in enumerate(selected):
            button = QPushButton(tr(en, id_text))
            button.setObjectName("applicationTools_" + key)
            if key == "version":
                worker = getattr(window, "version_worker", None)
                button.setEnabled(worker is None or not worker.isRunning())
            button.clicked.connect(callback)
            buttons.addWidget(button, index // 2, index % 2)
        layout.addWidget(group)
    label(tr("Application data contains the library, schedules, settings and account sessions. A backup may contain login data: keep it private. Updating the EXE does not move this data folder.", "Data aplikasi berisi library, jadwal, pengaturan dan sesi akun. Backup dapat berisi data login: simpan secara pribadi. Memperbarui EXE tidak memindahkan folder data ini."))
    layout.addStretch(1)
    refresh()
    return page
