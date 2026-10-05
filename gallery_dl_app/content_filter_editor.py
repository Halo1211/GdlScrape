"""Choose file extensions without asking beginners to write filter expressions."""

import ast
import re
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget


FILE_TYPES = {
    "images": ("jpg", "jpeg", "png", "gif", "webp", "avif", "bmp", "tif", "tiff", "heic", "heif"),
    "videos": ("mp4", "webm", "mkv", "mov", "m4v", "avi", "flv", "ts"),
    "audio": ("mp3", "m4a", "ogg", "opus", "wav", "flac", "aac"),
}


def extension_filter(text: str) -> str:
    extensions = tuple(dict.fromkeys(part.lower().lstrip(".") for part in re.split(r"[,;\s]+", text.strip()) if part))
    if not extensions or any(not re.fullmatch(r"[a-z0-9]{1,12}", part) for part in extensions):
        raise ValueError("Enter extensions such as jpg, png, mp4 / Masukkan ekstensi seperti jpg, png, mp4")
    return "extension in " + repr(extensions)


def filter_extensions(value: object) -> tuple[str, ...] | None:
    """Recognize only our simple extension membership rule; never evaluate it."""
    if not isinstance(value, str):
        return None
    try:
        node = ast.parse(value, mode="eval").body
        if not (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "extension"
                and len(node.ops) == 1 and isinstance(node.ops[0], ast.In) and len(node.comparators) == 1):
            return None
        items = ast.literal_eval(node.comparators[0])
        if not isinstance(items, (list, tuple, set)) or not items or any(not isinstance(item, str) for item in items):
            return None
        if any(not re.fullmatch(r"[a-z0-9]{1,12}", item) for item in items):
            return None
        return tuple(items)
    except (ValueError, TypeError, SyntaxError, RecursionError):
        return None


class ContentFilterEditor(QWidget):
    def __init__(self, value: object, explicit: bool, change: Callable[[object, bool], None], *, indonesian: bool = False):
        super().__init__()
        self.change, self.indonesian = change, indonesian
        self.setObjectName("contentFilterEditor")
        tr = self.tr_text
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.addWidget(QLabel(tr("Which file types should be saved?", "Jenis file apa yang disimpan?")))
        source = QLabel(tr("Filter in the draft: customized for this website.", "Filter di draft: khusus situs ini.") if explicit else tr("Filter in the draft: from shared settings or gallery-dl defaults.", "Filter di draft: dari pengaturan bersama atau bawaan gallery-dl."))
        source.setWordWrap(True)
        layout.addWidget(source)
        self.choice = QComboBox()
        self.choice.setObjectName("contentFilterChoice")
        for key, en, id_text in (
            ("all", "All file types (disable the file filter for this website)", "Semua jenis file (matikan filter file untuk situs ini)"),
            ("images", "Images only (including GIF)", "Hanya gambar (termasuk GIF)"),
            ("videos", "Videos only", "Hanya video"), ("audio", "Audio only", "Hanya audio"),
            ("custom", "Choose extensions", "Pilih ekstensi"),
        ):
            self.choice.addItem(tr(en, id_text), key)
        extensions = filter_extensions(value)
        key = "all" if value is None else next((key for key, items in FILE_TYPES.items() if extensions and set(items) == set(extensions)), "custom")
        if value is not None and extensions is None:
            self.choice.addItem(tr("Existing advanced filter (preserved)", "Filter lanjutan yang sudah ada (dipertahankan)"), "advanced")
            key = "advanced"
        self.choice.setCurrentIndex(self.choice.findData(key))
        layout.addWidget(self.choice)
        self.extensions = QLineEdit(", ".join(extensions or ()))
        self.extensions.setObjectName("contentFilterExtensions")
        self.extensions.setPlaceholderText("jpg, png, mp4")
        layout.addWidget(self.extensions)
        self.help = QLabel()
        self.help.setObjectName("contentFilterHelp")
        self.help.setWordWrap(True)
        self.help.setTextFormat(Qt.PlainText)
        layout.addWidget(self.help)
        buttons = QHBoxLayout()
        self.apply = QPushButton(tr("Use these file types", "Pakai jenis file ini"))
        self.apply.setObjectName("contentFilterApply")
        self.apply.clicked.connect(self.apply_filter)
        self.reset = QPushButton(tr("Use shared/default filter", "Pakai filter bersama/bawaan"))
        self.reset.setObjectName("contentFilterReset")
        self.reset.setEnabled(explicit)
        self.reset.clicked.connect(lambda: self.change(None, True))
        buttons.addWidget(self.apply)
        buttons.addWidget(self.reset)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.choice.currentIndexChanged.connect(self.explain)
        self.extensions.textChanged.connect(self.explain)
        self.explain()

    def tr_text(self, english: str, indonesian: str) -> str:
        return indonesian if self.indonesian else english

    def explain(self, *_args) -> None:
        key = self.choice.currentData()
        self.extensions.setVisible(key == "custom")
        self.apply.setEnabled(key != "advanced")
        if key == "advanced":
            self.help.setText(self.tr_text("Your existing rule stays intact. Choose another file type to replace it, or edit file-filter in All settings.", "Aturan yang sudah ada tetap utuh. Pilih jenis lain untuk menggantinya, atau edit file-filter di Semua pengaturan."))
            return
        self.help.setText(self.tr_text("Filters files by extension, after the website lists them. Does not reduce page requests or enable disabled videos. Existing post filters and website settings still apply. Use these file types updates the draft; save config to use it on future downloads.", "Menyaring ekstensi file setelah situs menampilkan daftar file. Tidak mengurangi permintaan halaman atau mengaktifkan video yang dimatikan. Filter postingan dan pengaturan situs tetap berlaku. Pakai jenis file ini mengubah draft; simpan config agar dipakai saat unduhan berikutnya.") + ("\n" + ", ".join(FILE_TYPES[key]) if key in FILE_TYPES else ""))

    def apply_filter(self) -> None:
        key = self.choice.currentData()
        if key == "advanced":
            return
        try:
            value = None if key == "all" else extension_filter(self.extensions.text() if key == "custom" else ",".join(FILE_TYPES[key]))
        except ValueError as exc:
            self.help.setText(str(exc))
            return
        self.change(value, False)
