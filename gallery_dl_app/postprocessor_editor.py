"""Task-oriented postprocessor editing, keeping existing actions intact."""

from __future__ import annotations

import copy
from collections.abc import Callable
from functools import lru_cache
from types import SimpleNamespace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget, QPlainTextEdit, QFileDialog,
)

from .config_maker import config_path_value, filename_example
from .postprocessor_config import postprocessor_options, resolved_postprocessor_action


def postprocessor_actions(config: dict, site: str) -> tuple[list[dict | str], bool]:
    """Read this level only: gallery-dl accumulates shared and site actions."""
    path = ("extractor", *([site] if site else []), "postprocessors")
    value, explicit = config_path_value(config, path)
    if value is None:
        return [], explicit
    if isinstance(value, (dict, str)):
        value = [value]
    if not isinstance(value, list) or any(not isinstance(action, (dict, str)) for action in value):
        raise ValueError("Actions must be objects or named presets. Open the full catalog to inspect this value.")
    return copy.deepcopy(value), explicit


def action_title(action: dict, indonesian: bool = False) -> str:
    names = {
        "zip": ("Pack downloaded files into ZIP", "Kemas file unduhan ke ZIP"),
        "json": ("Save information as JSON", "Simpan informasi ke JSON"),
        "jsonl": ("Collect information in data.jsonl", "Kumpulkan informasi di data.jsonl"),
        "tags": ("Save tags in a text file", "Simpan tag ke file teks"),
        "custom": ("Save the post text / description", "Simpan teks / deskripsi postingan"),
        "cbz": ("Pack a chapter into CBZ", "Kemas bab ke CBZ"),
        "rename": ("Rename files", "Ganti nama file"), "ugoira": ("Convert animated artwork", "Konversi karya animasi"),
        "hash": ("Calculate file hashes", "Hitung hash file"), "mtime": ("Set file dates", "Atur tanggal file"),
        "exec": ("Run a configured command", "Jalankan perintah yang dikonfigurasi"),
        "compare": ("Compare files", "Bandingkan file"), "classify": ("Sort files by rules", "Kelompokkan file sesuai aturan"),
        "directory": ("Change folders", "Atur folder"), "python": ("Run configured Python code", "Jalankan kode Python yang dikonfigurasi"),
    }
    key = str(action.get("name", ""))
    if key == "metadata":
        key = str(action.get("mode") or "json")
    elif key == "zip" and action.get("extension") == "cbz":
        key = "cbz"
    return names.get(key, ("Advanced action: " + key, "Tindakan lanjutan: " + key))[int(indonesian)]


def ugoira_settings(output: str) -> dict:
    """Match the gallery-dl CLI recipes, using concat mode on Windows."""
    recipes = {
        "mp4": ["-c:v", "libx264", "-b:v", "5M", "-pix_fmt", "yuv420p", "-an"],
        "webm": ["-c:v", "libvpx-vp9", "-crf", "12", "-b:v", "0", "-pix_fmt", "yuv420p", "-an"],
        "gif": ["-filter_complex", "[0:v] split [a][b];[a] palettegen [p];[b][p] paletteuse"],
        "zip": [],
    }
    return {"extension": output, "mode": "archive" if output == "zip" else "concat",
            "ffmpeg-args": recipes[output], "ffmpeg-twopass": False,
            "repeat-last-frame": output != "gif", "libx264-prevent-odd": True}


@lru_cache(maxsize=512)
def _site_views(site: str) -> tuple[tuple[str, str], ...]:
    from gallery_dl import extractor
    result = set()
    for cls in extractor.extractors():
        names = {str(getattr(cls, "category", "")), str(getattr(cls, "basecategory", ""))}
        names.update(str(item[0]) for item in getattr(cls, "instances", ()))
        if site.lower() in {name.lower() for name in names}:
            result.add((str(getattr(cls, "basecategory", "")), str(getattr(cls, "subcategory", ""))))
    return tuple(sorted(result))


def postprocessor_site_scope(config: dict, action: dict | str, site: str) -> tuple[str, tuple[str, ...]]:
    """Check saved site/page allowlists with the runtime matcher, without evaluating filters."""
    from gallery_dl import util
    shared = config.get("extractor")
    shared = shared if isinstance(shared, dict) else {}
    website = shared.get(site)
    scopes = (shared, website) if isinstance(website, dict) else (shared,)
    options = resolved_postprocessor_action(config, action, overrides=postprocessor_options(config, scopes))
    whitelist = options.get("whitelist")
    values, negate = (whitelist, False) if whitelist is not None else (options.get("blacklist"), True)
    if not values:
        return "all", ()
    if not isinstance(values, (str, list, tuple)) or (not isinstance(values, str) and any(not isinstance(value, str) for value in values)):
        return "unknown", ()
    candidates = _site_views(site)
    if not candidates:
        return "unknown", ()
    match = util.build_extractor_filter(values, negate)
    selected = {page for base, page in candidates if match(SimpleNamespace(category=site, basecategory=base, subcategory=page))}
    if not selected:
        return "excluded", ()
    if len(selected) == len({page for _, page in candidates}):
        return "all", tuple(sorted(selected))
    return "some", tuple(sorted(selected))


class PostprocessorEditor(QWidget):
    advancedRequested = Signal(str, str, int)
    settingRequested = Signal(str, str, object)

    def __init__(self, sites: list[str], config: Callable[[], dict],
                 change: Callable[[str, list[dict | str] | None], None], *,
                 site: str = "", indonesian: bool = False) -> None:
        super().__init__()
        self.config, self.change = config, change
        self.indonesian = indonesian
        self.loading = False
        self.valid = True
        self.actions: list[dict | str] = []
        self.setObjectName("postprocessorEditor")
        tr = self.tr_text
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        introduction = QLabel(tr(
            "Choose what should happen to downloaded files. Adding an action changes your draft; actions run on future downloads after you save config.",
            "Pilih apa yang dilakukan pada file unduhan. Menambah tindakan mengubah draft; tindakan dijalankan pada unduhan berikutnya setelah config disimpan.",
        ))
        introduction.setWordWrap(True)
        layout.addWidget(introduction)
        self.site = QComboBox()
        self.site.setObjectName("postprocessorSite")
        self.site.addItem(tr("All websites", "Semua situs"), "")
        for name in sites:
            self.site.addItem(name, name)
        self.site.setCurrentIndex(max(0, self.site.findData(site)))
        layout.addWidget(QLabel(tr("Apply these actions to", "Terapkan tindakan untuk")))
        layout.addWidget(self.site)
        self.source = QLabel()
        self.source.setWordWrap(True)
        self.source.setTextFormat(Qt.PlainText)
        layout.addWidget(self.source)
        self.shared = QLabel()
        self.shared.setObjectName("postprocessorSharedActions")
        self.shared.setWordWrap(True)
        self.shared.setTextFormat(Qt.PlainText)
        layout.addWidget(self.shared)
        layout.addWidget(QLabel(tr("Add a new action", "Tambahkan tindakan baru")))
        add_row = QHBoxLayout()
        self.task = QComboBox()
        self.task.setObjectName("postprocessorTask")
        for key in ("zip", "json", "tags", "jsonl", "custom", "cbz", "ugoira", "mtime"):
            action = {"name": key} if key in {"ugoira", "mtime"} else {"name": "zip", "extension": key} if key in {"zip", "cbz"} else {"name": "metadata", "mode": key}
            self.task.addItem(action_title(action, indonesian), key)
        self.task.addItem(tr("CBZ + information inside (keep originals)", "CBZ + informasi di dalam (file asli tetap ada)"), "cbz-info")
        self.add_named_presets()
        add = QPushButton(tr("Add this action", "Tambahkan tindakan ini"))
        add.setObjectName("postprocessorAdd")
        add.clicked.connect(self.add_action)
        add_row.addWidget(self.task, 1)
        add_row.addWidget(add)
        layout.addLayout(add_row)
        self.task_help = QLabel()
        self.task_help.setWordWrap(True)
        layout.addWidget(self.task_help)
        layout.addWidget(QLabel(tr("Actions added at this target (ordered within each event)", "Tindakan tambahan pada target ini (berurutan pada kejadian yang sama)")))
        self.list = QListWidget()
        self.list.setObjectName("postprocessorActions")
        self.list.setMaximumHeight(115)
        self.list.setMinimumHeight(85)
        layout.addWidget(self.list)
        controls = QHBoxLayout()
        self.up = QPushButton(tr("Move up", "Naikkan"))
        self.down = QPushButton(tr("Move down", "Turunkan"))
        self.remove = QPushButton(tr("Remove selected action", "Hapus tindakan terpilih"))
        self.remove.setObjectName("postprocessorRemove")
        self.up.setObjectName("postprocessorUp")
        self.down.setObjectName("postprocessorDown")
        self.up.clicked.connect(lambda: self.move(-1))
        self.down.clicked.connect(lambda: self.move(1))
        self.remove.clicked.connect(self.remove_action)
        for button in (self.up, self.down, self.remove):
            controls.addWidget(button)
        controls.addStretch(1)
        layout.addLayout(controls)
        layout.addWidget(QLabel(tr("Settings for the selected action", "Pengaturan tindakan terpilih")))
        self.zip_options = QWidget()
        options = QVBoxLayout(self.zip_options)
        options.setContentsMargins(0, 0, 0, 0)
        self.keep = QCheckBox(tr("Keep the original downloaded files too", "Pertahankan juga file unduhan asli"))
        self.keep.setObjectName("postprocessorKeepFiles")
        options.addWidget(self.keep)
        self.compression = QComboBox()
        self.compression.setObjectName("postprocessorCompression")
        for label, key in (
            (tr("Standard ZIP compression", "Kompresi ZIP biasa"), "zip"),
            (tr("Store without compression (fast)", "Simpan tanpa kompresi (cepat)"), "store"),
            ("Bzip2", "bzip2"), ("LZMA", "lzma"),
        ):
            self.compression.addItem(label, key)
        options.addWidget(self.compression)
        options.addWidget(QLabel(tr("Extra files to include in ZIP / CBZ (optional)", "File tambahan di dalam ZIP / CBZ (opsional)")))
        self.zip_files = QPlainTextEdit()
        self.zip_files.setObjectName("postprocessorZipFiles")
        self.zip_files.setMaximumHeight(75)
        self.zip_files.setPlaceholderText("info.json\ntags.txt")
        options.addWidget(self.zip_files)
        zip_note = QLabel(tr("One existing file per line, relative to the media download folder. For info.json, add an information action before ZIP and write it when the gallery starts. The CBZ + information shortcut sets up both actions.", "Satu file yang sudah ada per baris, dihitung dari folder unduhan media. Untuk info.json, tambahkan tindakan informasi sebelum ZIP dan tulis saat galeri dimulai. Pintasan CBZ + informasi menyiapkan kedua tindakan."))
        zip_note.setWordWrap(True)
        options.addWidget(zip_note)
        layout.addWidget(self.zip_options)
        self.metadata_options = QWidget()
        metadata_layout = QVBoxLayout(self.metadata_options)
        metadata_layout.setContentsMargins(0, 0, 0, 0)
        self.event = QComboBox()
        self.event.setObjectName("postprocessorEvent")
        for label, key in (
            (tr("One information file per downloaded file", "Satu file informasi per file unduhan"), "file"),
            (tr("One information file per post (when the post starts)", "Satu file informasi per postingan (saat mulai)"), "post"),
            (tr("One information file after processing the post", "Satu file informasi setelah postingan diproses"), "post-after"),
            (tr("Write information when the download finishes", "Tulis informasi saat unduhan selesai"), "finalize"),
        ):
            self.event.addItem(label, key)
        metadata_layout.addWidget(self.event)
        self.filename = QLineEdit()
        self.filename.setObjectName("postprocessorFilename")
        self.filename.setPlaceholderText(tr("Blank: use the downloaded file name + .json/.txt", "Kosong: pakai nama file unduhan + .json/.txt"))
        metadata_layout.addWidget(QLabel(tr("Information file name (optional)", "Nama file informasi (opsional)")))
        metadata_layout.addWidget(self.filename)
        example_name = QPushButton(tr("Use post title + ID", "Pakai judul postingan + ID"))
        example_name.setObjectName("postprocessorTitleFilename")
        example_name.clicked.connect(self.use_title_filename)
        metadata_layout.addWidget(example_name, 0, Qt.AlignLeft)
        self.name_preview = QLabel()
        self.name_preview.setWordWrap(True)
        self.name_preview.setTextFormat(Qt.PlainText)
        self.name_preview.setObjectName("postprocessorFilenamePreview")
        metadata_layout.addWidget(self.name_preview)
        metadata_layout.addWidget(QLabel(tr("Where to save information files", "Lokasi file informasi")))
        location_row = QHBoxLayout()
        self.location = QComboBox()
        self.location.setObjectName("postprocessorLocation")
        for key, en, id_text in (
            ("download", "Relative to the media download folder", "Di dalam folder unduhan media"),
            ("base", "Relative to the download root (separate from media)", "Di folder utama unduhan (terpisah dari media)"),
            ("custom", "Choose another root folder…", "Pilih folder utama lain…"),
            ("advanced", "Custom path pattern (preserved)", "Pola path khusus (dipertahankan)"),
        ):
            self.location.addItem(tr(en, id_text), key)
        location_row.addWidget(self.location, 1)
        browse = QPushButton(tr("Choose another folder…", "Pilih folder lain…"))
        browse.setObjectName("postprocessorLocationBrowse")
        browse.clicked.connect(self.choose_metadata_root)
        location_row.addWidget(browse)
        metadata_layout.addLayout(location_row)
        self.location_note = QLabel()
        self.location_note.setObjectName("postprocessorLocationNote")
        self.location_note.setTextFormat(Qt.PlainText)
        self.location_note.setWordWrap(True)
        metadata_layout.addWidget(self.location_note)
        metadata_layout.addWidget(QLabel(tr("Subfolder levels (one per line; blank = chosen root)", "Tingkat subfolder (satu per baris; kosong = lokasi terpilih)")))
        self.metadata_directory = QPlainTextEdit()
        self.metadata_directory.setObjectName("postprocessorDirectory")
        self.metadata_directory.setMaximumHeight(70)
        self.metadata_directory.setPlaceholderText("metadata\n{category}")
        metadata_layout.addWidget(self.metadata_directory)
        self.custom_options = QWidget()
        custom_layout = QVBoxLayout(self.custom_options)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.addWidget(QLabel(tr("Text to write (metadata placeholders are allowed)", "Teks yang ditulis (boleh memakai placeholder metadata)")))
        self.content = QPlainTextEdit()
        self.content.setObjectName("postprocessorContent")
        self.content.setMaximumHeight(90)
        custom_layout.addWidget(self.content)
        self.content_help = QLabel(tr("Example: {content|description} uses the post text, or its description. Available fields depend on the website; the pattern is saved without being executed here.", "Contoh: {content|description} memakai teks postingan, atau deskripsinya. Field mengikuti situs; pola disimpan tanpa dijalankan di sini."))
        self.content_help.setWordWrap(True)
        custom_layout.addWidget(self.content_help)
        metadata_layout.addWidget(self.custom_options)
        self.json_options = QWidget()
        json_row = QHBoxLayout(self.json_options)
        json_row.setContentsMargins(0, 0, 0, 0)
        json_row.addWidget(QLabel(tr("JSON indentation", "Indentasi JSON")))
        self.indent_style = QComboBox()
        self.indent_style.setObjectName("postprocessorIndentStyle")
        for key, en, id_text in (("spaces", "Spaces", "Spasi"), ("tabs", "Tabs", "Tab"), ("custom", "Custom (preserved)", "Khusus (dipertahankan)")):
            self.indent_style.addItem(tr(en, id_text), key)
        json_row.addWidget(self.indent_style)
        self.indent = QSpinBox()
        self.indent.setObjectName("postprocessorIndent")
        self.indent.setRange(0, 16)
        self.ascii = QCheckBox(tr("Escape non-ASCII characters", "Ubah karakter Unicode menjadi kode escape"))
        self.ascii.setObjectName("postprocessorAscii")
        self.ascii.setToolTip(tr("Leave unchecked to keep Japanese and other Unicode text readable.", "Biarkan tidak dicentang agar teks Jepang dan Unicode lainnya tetap mudah dibaca."))
        json_row.addWidget(self.indent)
        json_row.addWidget(self.ascii)
        json_row.addStretch(1)
        metadata_layout.addWidget(self.json_options)
        layout.addWidget(self.metadata_options)
        self.animation_options = QWidget()
        animation_layout = QVBoxLayout(self.animation_options)
        animation_layout.setContentsMargins(0, 0, 0, 0)
        animation_layout.addWidget(QLabel(tr("Animation output", "Hasil animasi")))
        self.animation_format = QComboBox()
        self.animation_format.setObjectName("postprocessorAnimationFormat")
        for en, id_text, key in (
            ("MP4 video (FFmpeg required)", "Video MP4 (memerlukan FFmpeg)", "mp4"),
            ("WebM video (FFmpeg required)", "Video WebM (memerlukan FFmpeg)", "webm"),
            ("GIF animation (FFmpeg required)", "Animasi GIF (memerlukan FFmpeg)", "gif"),
            ("ZIP of frames (no FFmpeg conversion)", "ZIP frame (tanpa konversi FFmpeg)", "zip"),
            ("Custom settings (preserved)", "Pengaturan khusus (dipertahankan)", "custom"),
        ):
            self.animation_format.addItem(tr(en, id_text), key)
        animation_layout.addWidget(self.animation_format)
        self.animation_keep = QCheckBox(tr("Keep the original animation files too", "Pertahankan juga file animasi asli"))
        self.animation_keep.setObjectName("postprocessorAnimationKeep")
        animation_layout.addWidget(self.animation_keep)
        animation_note = QLabel(tr("For Pixiv/Danbooru Ugoira only. Ordinary videos and still images are unchanged. MP4/WebM/GIF require FFmpeg on PATH or ffmpeg-location in More options. Original mode is selected when adding this action to avoid a second built-in conversion.", "Hanya untuk Ugoira Pixiv/Danbooru. Video biasa dan gambar statis tetap seperti semula. MP4/WebM/GIF memerlukan FFmpeg di PATH atau ffmpeg-location di Opsi lain. Mode original dipilih saat menambah tindakan agar tidak dikonversi dua kali."))
        animation_note.setWordWrap(True)
        animation_layout.addWidget(animation_note)
        layout.addWidget(self.animation_options)
        self.date_options = QWidget()
        date_layout = QVBoxLayout(self.date_options)
        date_layout.setContentsMargins(0, 0, 0, 0)
        date_layout.addWidget(QLabel(tr("Use a date from website metadata", "Gunakan tanggal dari metadata situs")))
        self.date_field = QLineEdit()
        self.date_field.setObjectName("postprocessorDateField")
        self.date_field.setPlaceholderText("{date}")
        date_layout.addWidget(self.date_field)
        date_note = QLabel(tr("Sets the file's modification date. {date} is a common field; some sites use another field, such as {status[date]}. No date is evaluated here.", "Mengatur tanggal modifikasi file. {date} umum dipakai; sebagian situs memakai field lain seperti {status[date]}. Tanggal tidak dievaluasi di sini."))
        date_note.setWordWrap(True)
        date_layout.addWidget(date_note)
        layout.addWidget(self.date_options)
        self.result = QLabel()
        self.result.setObjectName("postprocessorResult")
        self.result.setWordWrap(True)
        self.result.setTextFormat(Qt.PlainText)
        layout.addWidget(self.result)
        buttons = QHBoxLayout()
        advanced = QPushButton(tr("More options for this action…", "Opsi lain untuk tindakan ini…"))
        advanced.setObjectName("postprocessorAdvanced")
        advanced.clicked.connect(self.open_advanced)
        reset = QPushButton(tr("Remove this target's additional actions", "Hapus tindakan tambahan target ini"))
        reset.setObjectName("postprocessorUseDefault")
        reset.clicked.connect(self.use_default)
        buttons.addWidget(advanced)
        buttons.addWidget(reset)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)
        self.site.currentIndexChanged.connect(self.reload)
        self.list.currentRowChanged.connect(self.select_action)
        self.task.currentIndexChanged.connect(self.explain_task)
        self.keep.toggled.connect(lambda: self.edit_zip("keep-files"))
        self.compression.currentIndexChanged.connect(lambda: self.edit_zip("compression"))
        self.zip_files.textChanged.connect(lambda: self.edit_zip("files"))
        self.event.currentIndexChanged.connect(lambda: self.edit_metadata("event"))
        self.filename.textChanged.connect(lambda: self.edit_metadata("filename"))
        self.indent.valueChanged.connect(lambda: self.edit_metadata("indent"))
        self.indent_style.currentIndexChanged.connect(lambda: self.edit_metadata("indent_style"))
        self.location.currentIndexChanged.connect(self.change_metadata_location)
        self.metadata_directory.textChanged.connect(lambda: self.edit_metadata("directory"))
        self.ascii.toggled.connect(lambda: self.edit_metadata("ascii"))
        self.content.textChanged.connect(lambda: self.edit_metadata("content"))
        self.animation_format.currentIndexChanged.connect(lambda: self.edit_animation("format"))
        self.animation_keep.toggled.connect(lambda: self.edit_animation("keep-files"))
        self.date_field.textEdited.connect(self.edit_date)
        self.reload()
        self.explain_task()

    def tr_text(self, english: str, indonesian: str) -> str:
        return indonesian if self.indonesian else english

    def add_named_presets(self) -> None:
        presets = self.config().get("postprocessor") or {}
        if not isinstance(presets, dict):
            return
        for name, value in presets.items():
            if isinstance(value, dict) and not name.startswith("#") and self.task.findData("preset:" + name) < 0:
                self.task.addItem(self.tr_text("Use saved action: ", "Pakai tindakan tersimpan: ") + name, "preset:" + name)

    def explain_task(self, *_args) -> None:
        texts = {
            "cbz-info": ("Creates info.json when the gallery starts, then packs it with the downloaded images into CBZ. Sets up two ordered actions and keeps original files.", "Membuat info.json saat galeri dimulai, lalu mengemasnya bersama gambar unduhan ke CBZ. Menyiapkan dua tindakan berurutan dan mempertahankan file asli."),
            "zip": ("Example: a folder of photos becomes a ZIP beside that folder. Original files are kept by default. JSON/tag sidecar files are separate from the ZIP.", "Contoh: folder foto dikemas ke ZIP di samping folder itu. File asli dipertahankan secara bawaan. File informasi JSON/tag terpisah dari ZIP."),
            "json": ("Each downloaded file gets an information file. Example: photo.jpg → photo.jpg.json, with the metadata returned by the website.", "Setiap file unduhan mendapat file informasi. Contoh: photo.jpg → photo.jpg.json, berisi metadata yang diberikan situs."),
            "tags": ("Save available tags as one tag per line. Example: photo.jpg → photo.jpg.txt. The website must supply tag metadata.", "Simpan tag yang tersedia, satu tag per baris. Contoh: photo.jpg → photo.jpg.txt. Situs perlu menyediakan metadata tag."),
            "jsonl": ("Append information for downloaded files into data.jsonl. Each line is one JSON record; existing records are retained.", "Tambahkan informasi file unduhan ke data.jsonl. Satu baris berisi satu catatan JSON; catatan lama tetap ada."),
            "custom": ("Write the post text or description to {id}.txt once per post. Change the text pattern below for website-specific content.", "Tulis teks atau deskripsi ke {id}.txt satu kali per postingan. Ubah pola teks di bawah untuk konten khusus situs."),
            "cbz": ("Create a comic-reader archive beside the chapter folder. Original files are kept by default.", "Buat arsip pembaca komik di samping folder bab. File asli dipertahankan secara bawaan."),
            "ugoira": ("Convert Pixiv/Danbooru Ugoira to MP4, WebM, GIF or a frame ZIP. MP4 is initially selected and source files are kept. FFmpeg is needed for video/GIF output.", "Konversi Ugoira Pixiv/Danbooru ke MP4, WebM, GIF atau ZIP frame. Awalnya memakai MP4 dan file sumber dipertahankan. FFmpeg diperlukan untuk video/GIF."),
            "mtime": ("Set the modification date from {date} metadata. Choose a website first if its date field differs.", "Atur tanggal modifikasi memakai metadata {date}. Pilih situs terlebih dahulu jika field tanggalnya berbeda."),
        }
        key = str(self.task.currentData())
        self.task_help.setText(self.tr_text("References a named action from your config. Editing the added action here changes only this target, keeping the saved definition intact.", "Memakai tindakan bernama dari config Anda. Mengedit tindakan yang ditambahkan hanya mengubah target ini; definisi tersimpannya tetap utuh.") if key.startswith("preset:") else self.tr_text("When added with default settings: ", "Saat ditambahkan dengan pengaturan awal: ") + self.tr_text(*texts[key]))

    def reload(self, *_args, selected: int = 0) -> None:
        self.loading = True
        try:
            self.actions, explicit = postprocessor_actions(self.config(), str(self.site.currentData()))
        except ValueError as exc:
            self.actions = []
            self.valid = False
            self.list.clear()
            self.source.setText(str(exc))
            self.loading = False
            self.select_action()
            return
        self.valid = True
        self.add_named_presets()
        self.list.clear()
        for action in self.actions:
            title = action_title(resolved_postprocessor_action(self.config(), action), self.indonesian)
            if isinstance(action, str):
                title += self.tr_text(" · saved: ", " · tersimpan: ") + action
            item = QListWidgetItem(title)
            self.list.addItem(item)
        target = self.site.currentText()
        self.source.setText(self.tr_text(
            f"{target}: {len(self.actions)} action(s) saved at this level." + (" No local list yet." if not explicit else ""),
            f"{target}: {len(self.actions)} tindakan tersimpan pada tingkat ini." + (" Belum ada daftar khusus." if not explicit else ""),
        ))
        try:
            shared, _ = postprocessor_actions(self.config(), "") if self.site.currentData() else ([], False)
        except ValueError:
            shared = []
        self.shared.setVisible(bool(shared))
        shared_lines = [self.tr_text("Shared actions for this website:", "Tindakan bersama untuk situs ini:")]
        for action in shared:
            title = action_title(resolved_postprocessor_action(self.config(), action), self.indonesian)
            status, pages = postprocessor_site_scope(self.config(), action, str(self.site.currentData()))
            label = self.tr_text("Allowed on this website", "Diizinkan pada situs ini") if status == "all" else self.tr_text("Excluded by saved website restrictions", "Dikecualikan oleh batasan situs tersimpan") if status == "excluded" else self.tr_text("Only on page types: ", "Hanya pada jenis halaman: ") + ", ".join(pages) if status == "some" else self.tr_text("Advanced restrictions preserved", "Batasan lanjutan dipertahankan")
            if status != "excluded" and "filter" in resolved_postprocessor_action(self.config(), action):
                label += self.tr_text("; also depends on its saved condition", "; juga mengikuti kondisi tersimpan")
            shared_lines.append("• " + title + " — " + label)
        shared_lines.append(self.tr_text("Site actions add to shared actions. Saved conditions, page-specific options and postprocess switches still apply. Edit shared actions by choosing All websites.", "Tindakan situs menambah tindakan bersama. Kondisi, opsi khusus halaman dan sakelar postprocess tetap berlaku. Edit tindakan bersama dengan memilih Semua situs."))
        self.shared.setText("\n".join(shared_lines))
        self.list.setCurrentRow(min(selected, len(self.actions) - 1))
        self.loading = False
        self.select_action()

    def select_action(self, *_args) -> None:
        if self.loading:
            return
        row = self.list.currentRow()
        self.up.setEnabled(row > 0)
        self.down.setEnabled(0 <= row < len(self.actions) - 1)
        self.remove.setEnabled(row >= 0)
        action = resolved_postprocessor_action(self.config(), self.actions[row]) if row >= 0 else {}
        self.zip_options.setVisible(action.get("name") == "zip")
        self.animation_options.setVisible(action.get("name") == "ugoira")
        self.date_options.setVisible(action.get("name") == "mtime")
        mode = action.get("mode", "json")
        self.metadata_options.setVisible(action.get("name") == "metadata" and mode in {"json", "jsonl", "tags", "custom"})
        self.loading = True
        self.keep.setChecked(bool(action.get("keep-files", False)))
        files = action.get("files", [])
        simple_files = isinstance(files, list) and all(isinstance(path, str) for path in files)
        self.zip_files.setEnabled(simple_files)
        self.zip_files.setPlainText("\n".join(files) if simple_files else self.tr_text("Advanced value preserved; edit through More options.", "Nilai lanjutan dipertahankan; edit melalui Opsi lain."))
        animation_type = "custom"
        if action.get("mode") == "archive":
            animation_type = "zip" if action.get("extension", "zip") == "zip" else "custom"
        elif action.get("mode", "concat") in {"concat", "auto"}:
            for key in ("mp4", "webm", "gif"):
                if action.get("extension") == key and list(action.get("ffmpeg-args") or []) == ugoira_settings(key)["ffmpeg-args"]:
                    animation_type = key
                    break
        self.animation_format.setCurrentIndex(self.animation_format.findData(animation_type))
        self.animation_keep.setChecked(bool(action.get("keep-files", False)))
        self.date_field.setText(str(action.get("value") or ""))
        index = self.compression.findData(action.get("compression", "store"))
        if index < 0:
            self.compression.addItem(str(action["compression"]), action["compression"])
            index = self.compression.count() - 1
        self.compression.setCurrentIndex(index)
        event = action.get("event", "file")
        if self.event.findData(event) < 0:
            self.event.addItem(self.tr_text("Custom event (preserved)", "Kejadian khusus (dipertahankan)"), event)
        self.event.setCurrentIndex(self.event.findData(event))
        self.filename.setText(str(action.get("filename") or ""))
        indent = action.get("indent", 4)
        self.indent_style.setCurrentIndex(self.indent_style.findData("tabs" if indent == "\t" else "spaces" if isinstance(indent, int) and not isinstance(indent, bool) else "custom"))
        self.indent.setValue(indent if isinstance(indent, int) and not isinstance(indent, bool) else 4)
        self.indent.setEnabled(self.indent_style.currentData() == "spaces")
        base = action.get("base-directory", False)
        location = "download" if base is False or base is None else "base" if base is True else "custom" if isinstance(base, str) else "advanced"
        self.location.setCurrentIndex(self.location.findData(location))
        directory = action.get("directory", ".")
        simple_directory = isinstance(directory, str) or (isinstance(directory, list) and all(isinstance(part, str) for part in directory))
        self.metadata_directory.setReadOnly(not simple_directory)
        self.metadata_directory.setPlainText("\n".join(directory) if isinstance(directory, list) and simple_directory else str(directory))
        anchor = self.tr_text("Media download folder", "Folder unduhan media") if location == "download" else self.tr_text("Download root", "Folder utama unduhan") if location == "base" else str(base)
        suffix = " / ".join(directory) if isinstance(directory, list) and simple_directory else str(directory)
        self.location_note.setText(self.tr_text("Location: ", "Lokasi: ") + anchor + (" / " + suffix if suffix and suffix != "." else "") + self.tr_text(". Metadata patterns use this website's fields. A custom root/pattern from config is preserved until changed.", ". Pola metadata memakai field situs ini. Lokasi/pola khusus dari config tetap utuh sampai diubah."))
        self.ascii.setChecked(bool(action.get("ascii", False)))
        self.json_options.setVisible(mode in {"json", "jsonl"})
        self.custom_options.setVisible(mode == "custom")
        self.content.setPlainText(str(action.get("format", action.get("content-format", "{content|description}\n"))))
        self.filename.setPlaceholderText(self.tr_text("Blank: use data.jsonl", "Kosong: pakai data.jsonl") if mode == "jsonl" else self.tr_text("Blank: use the downloaded file name + .json/.txt", "Kosong: pakai nama file unduhan + .json/.txt"))
        self.loading = False
        if self.filename.text():
            try:
                example = filename_example(self.filename.text(), str(self.site.currentData() or "website"))
                self.name_preview.setText(self.tr_text("Example with sample data: ", "Contoh dengan data ilustrasi: ") + example)
            except ValueError:
                self.name_preview.setText(self.tr_text("Advanced pattern preserved; actual names depend on website metadata.", "Pola lanjutan dipertahankan; nama sebenarnya mengikuti metadata situs."))
        else:
            self.name_preview.setText(self.tr_text("Uses data.jsonl in the selected information folder.", "Memakai data.jsonl di folder informasi terpilih.") if mode == "jsonl" else self.tr_text("Uses the downloaded file name. Patterns use the metadata supplied by each website.", "Memakai nama file unduhan. Pola menggunakan metadata yang diberikan masing-masing situs."))
        if action.get("name") == "zip":
            text = self.tr_text("Result: ZIP and original files.", "Hasil: ZIP dan file asli.") if self.keep.isChecked() else self.tr_text("Result: ZIP. Original downloaded files are removed after being added to it.", "Hasil: ZIP. File unduhan asli dihapus setelah dimasukkan ke ZIP.")
            if action.get("extension") == "cbz":
                text = text.replace("ZIP", "CBZ")
            if action.get("files"):
                text += self.tr_text(" Includes the extra files listed above if they exist.", " Menyertakan file tambahan di atas jika tersedia.")
        elif action.get("name") == "metadata":
            text = self.tr_text("Result: an extra information/tag file. Downloaded files remain unchanged.", "Hasil: file informasi/tag tambahan. File unduhan tetap seperti semula.")
            if action.get("filename") or action.get("directory"):
                text += self.tr_text(" Uses a custom filename/folder from your config.", " Memakai nama file/folder khusus dari config Anda.")
        elif action.get("name") == "ugoira":
            output = str(action.get("extension") or "webm").upper()
            text = self.tr_text(f"Result: {output} animation. ", f"Hasil: animasi {output}. ")
            text += self.tr_text("Original animation files are kept.", "File animasi asli dipertahankan.") if self.animation_keep.isChecked() else self.tr_text("Source animation files are removed after successful conversion.", "File animasi sumber dihapus setelah konversi berhasil.")
            if action.get("mode") != "archive":
                text += self.tr_text(" Requires the configured conversion tool (usually FFmpeg).", " Memerlukan alat konversi yang dikonfigurasi (biasanya FFmpeg).")
        elif action.get("name") == "mtime":
            text = self.tr_text("Result: the file modification date follows website metadata.", "Hasil: tanggal modifikasi file mengikuti metadata situs.")
        else:
            text = self.tr_text("Select or add an action. Existing advanced actions can be edited through More options.", "Pilih atau tambahkan tindakan. Tindakan lanjutan yang sudah ada bisa diedit melalui Opsi lain.")
        if any(key in action for key in ("filter", "whitelist", "blacklist")):
            text += self.tr_text(" Saved conditions/site restrictions apply to this action.", " Kondisi/batasan situs yang tersimpan berlaku untuk tindakan ini.")
        self.result.setText(text + self.tr_text(" Save config to use these actions on future downloads.", " Simpan config agar tindakan dipakai pada unduhan berikutnya."))

    def commit(self, selected: int) -> None:
        self.change(str(self.site.currentData()), copy.deepcopy(self.actions))
        self.reload(selected=selected)

    def add_action(self) -> None:
        if not self.valid:
            return
        key = self.task.currentData()
        if key == "cbz-info":
            self.actions.extend([
                {"name": "metadata", "mode": "json", "event": "init", "filename": "info.json", "indent": 2, "ascii": False},
                {"name": "zip", "extension": "cbz", "compression": "store", "keep-files": True, "files": ["info.json"]},
            ])
            self.commit(len(self.actions) - 1)
            return
        if key.startswith("preset:"):
            action = key.removeprefix("preset:")
        elif key in {"zip", "cbz"}:
            action = {"name": "zip", "compression": "zip", "keep-files": True}
            if key == "cbz":
                action["extension"] = "cbz"
        elif key == "custom":
            action = {"name": "metadata", "mode": "custom", "event": "post", "filename": "{id}.txt", "format": "{content|description}\n"}
        elif key == "ugoira":
            site = str(self.site.currentData())
            if site not in {"", "pixiv", "danbooru"}:
                self.task_help.setText(self.tr_text("Choose Pixiv, Danbooru or All websites for this action.", "Pilih Pixiv, Danbooru atau Semua situs untuk tindakan ini."))
                return
            action = {"name": "ugoira", **ugoira_settings("mp4"), "keep-files": True, "whitelist": ["pixiv", "danbooru"]}
            self.settingRequested.emit(site, "ugoira", "original")
            if not site:
                # A site override takes precedence over the shared source mode.
                # Existing conversion modes must also request the original frames.
                for target in ("pixiv", "danbooru"):
                    mode, explicit = config_path_value(self.config(), ("extractor", target, "ugoira"))
                    if explicit and mode != "original":
                        self.settingRequested.emit(target, "ugoira", "original")
        elif key == "mtime":
            action = {"name": "mtime", "value": "{date}"}
        else:
            action = {"name": "metadata", "mode": key, "event": "file"}
        if key in {"json", "jsonl"}:
            action.update({"indent": 2, "ascii": False})
        self.actions.append(action)
        self.commit(len(self.actions) - 1)

    def editable_action(self, row: int) -> dict:
        if isinstance(self.actions[row], str):
            self.actions[row] = {"type": self.actions[row]}
        return self.actions[row]

    def edit_zip(self, key: str) -> None:
        row = self.list.currentRow()
        if self.loading or row < 0 or resolved_postprocessor_action(self.config(), self.actions[row]).get("name") != "zip":
            return
        position = self.zip_files.textCursor().position()
        raw_files = self.zip_files.toPlainText()
        self.editable_action(row)[key] = list(dict.fromkeys(line.strip() for line in raw_files.splitlines() if line.strip())) if key == "files" else self.keep.isChecked() if key == "keep-files" else self.compression.currentData()
        self.commit(row)
        if key == "files":
            # Preserve an unfinished blank line while the user types the next
            # filename. The config value can still ignore blank/duplicate rows.
            self.loading = True
            self.zip_files.setPlainText(raw_files)
            self.loading = False
        cursor = self.zip_files.textCursor()
        cursor.setPosition(min(position, len(self.zip_files.toPlainText())))
        self.zip_files.setTextCursor(cursor)

    def edit_metadata(self, key: str) -> None:
        row = self.list.currentRow()
        if self.loading or row < 0 or resolved_postprocessor_action(self.config(), self.actions[row]).get("name") != "metadata":
            return
        values = {"event": self.event.currentData(), "filename": self.filename.text(),
                  "indent": self.indent.value(), "ascii": self.ascii.isChecked()}
        if key == "indent_style" and self.indent_style.currentData() == "custom":
            self.select_action()
            return
        action = self.editable_action(row)
        if key == "directory":
            action[key] = self.metadata_directory.toPlainText().splitlines()
        elif key == "indent_style":
            style = self.indent_style.currentData()
            action["indent"] = "\t" if style == "tabs" else self.indent.value()
        elif key == "content":
            resolved = resolved_postprocessor_action(self.config(), action)
            action["format" if "format" in resolved or "content-format" not in resolved else "content-format"] = self.content.toPlainText()
        elif key == "filename" and not values[key]:
            if "type" in action:
                action[key] = None
            else:
                action.pop(key, None)
        else:
            action[key] = values[key]
        # Keep the typing cursor in place: rebuilding this page on each letter
        # would send the cursor to the end of the filename.
        position = self.filename.cursorPosition()
        content_position = self.content.textCursor().position()
        directory_position = self.metadata_directory.textCursor().position()
        raw_directory = self.metadata_directory.toPlainText()
        self.commit(row)
        if key == "directory":
            self.loading = True
            self.metadata_directory.setPlainText(raw_directory)
            self.loading = False
        cursor = self.metadata_directory.textCursor()
        cursor.setPosition(min(directory_position, len(self.metadata_directory.toPlainText())))
        self.metadata_directory.setTextCursor(cursor)
        self.filename.setCursorPosition(position)
        cursor = self.content.textCursor()
        cursor.setPosition(min(content_position, len(self.content.toPlainText())))
        self.content.setTextCursor(cursor)

    def change_metadata_location(self, *_args) -> None:
        if self.loading or self.list.currentRow() < 0:
            return
        selected = self.location.currentData()
        if selected == "custom":
            self.choose_metadata_root()
        elif selected in {"download", "base"}:
            self.editable_action(self.list.currentRow())["base-directory"] = selected == "base"
            self.commit(self.list.currentRow())
        else:
            self.select_action()

    def choose_metadata_root(self) -> None:
        row = self.list.currentRow()
        if self.loading or row < 0:
            return
        action = resolved_postprocessor_action(self.config(), self.actions[row])
        if action.get("name") != "metadata":
            return
        original = action.get("base-directory")
        chosen = QFileDialog.getExistingDirectory(self, self.tr_text("Choose a folder for information files", "Pilih folder untuk file informasi"), original if isinstance(original, str) else "")
        if chosen:
            self.editable_action(row)["base-directory"] = chosen
            self.commit(row)
        else:
            self.select_action()

    def edit_animation(self, key: str) -> None:
        row = self.list.currentRow()
        if self.loading or row < 0 or resolved_postprocessor_action(self.config(), self.actions[row]).get("name") != "ugoira":
            return
        if key == "format" and self.animation_format.currentData() == "custom":
            return
        action = self.editable_action(row)
        if key == "keep-files":
            action[key] = self.animation_keep.isChecked()
        else:
            action.update(ugoira_settings(str(self.animation_format.currentData())))
        self.commit(row)

    def edit_date(self, value: str) -> None:
        row = self.list.currentRow()
        if self.loading or row < 0 or resolved_postprocessor_action(self.config(), self.actions[row]).get("name") != "mtime":
            return
        position = self.date_field.cursorPosition()
        self.editable_action(row)["value"] = value
        self.commit(row)
        self.date_field.setCursorPosition(position)

    def use_title_filename(self) -> None:
        row = self.list.currentRow()
        mode = resolved_postprocessor_action(self.config(), self.actions[row]).get("mode", "json") if row >= 0 else "json"
        extension = "txt" if mode in {"tags", "custom"} else "jsonl" if mode == "jsonl" else "json"
        self.filename.setText("{title} [{id}]." + extension)

    def move(self, offset: int) -> None:
        row = self.list.currentRow()
        other = row + offset
        if row >= 0 and 0 <= other < len(self.actions):
            self.actions[row], self.actions[other] = self.actions[other], self.actions[row]
            self.commit(other)

    def remove_action(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.actions.pop(row)
            self.commit(max(0, row - 1))

    def use_default(self) -> None:
        self.change(str(self.site.currentData()), None)
        self.reload()

    def open_advanced(self) -> None:
        row = self.list.currentRow()
        if row >= 0 and isinstance(self.actions[row], str):
            self.editable_action(row)
        if row >= 0:
            self.commit(row)
        name = str(resolved_postprocessor_action(self.config(), self.actions[row]).get("name", "")) if row >= 0 else ""
        self.advancedRequested.emit(str(self.site.currentData()), name, max(0, row))
