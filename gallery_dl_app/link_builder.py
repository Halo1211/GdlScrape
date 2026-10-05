"""Build and inspect supported links without downloading their contents."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QCompleter, QGridLayout, QHBoxLayout,
    QFileDialog, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from .config_maker import installed_site_catalog
from .core import atomic_write_text, read_text_safely, redact_sensitive_text
from .url_builder import URL_MODE_LABELS, URL_RECIPE_MODES, build_site_url, url_recipe_fields, validate_supported_url


class LinkBuilder(QWidget):
    configureRequested = Signal(str)

    def __init__(self, add_urls: Callable[[list[str]], int], *, indonesian: bool = False) -> None:
        super().__init__()
        self.indonesian = indonesian
        self.add_urls = add_urls
        self.loading = False
        self.drafts: dict[tuple[str, str], tuple[list[str], bool, str]] = {}
        self.active_recipe: tuple[str, str] | None = None
        self.setObjectName("linkBuilder")
        tr = self.tr_text
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignTop)
        layout.addWidget(QLabel(tr("Link Builder — choose a website and what to download", "Pembuat Tautan — pilih situs dan konten yang diunduh")))
        instructions = QLabel(tr("Already have a link? Use Paste complete links. To build one, choose a website and content type, then fill in the username or ID. Your entries are kept when switching choices.", "Sudah punya tautan? Pakai Tempel tautan lengkap. Untuk membuatnya, pilih situs dan jenis konten, lalu isi pengguna atau ID. Isian tetap tersimpan saat berganti pilihan."))
        instructions.setObjectName("guidedUrlInstructions")
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        row = QHBoxLayout()
        self.site = QComboBox()
        self.site.setObjectName("guidedUrlSite")
        self.site.setEditable(True)
        self.site.setInsertPolicy(QComboBox.NoInsert)
        self.site.addItem(tr("Detect website from URL", "Kenali situs dari tautan"), "")
        for name in sorted(set(installed_site_catalog()) | set(URL_RECIPE_MODES)):
            self.site.addItem(name, name)
        self.site.completer().setFilterMode(Qt.MatchContains)
        self.site.completer().setCaseSensitivity(Qt.CaseInsensitive)
        self.site.completer().setCompletionMode(QCompleter.PopupCompletion)
        self.mode = QComboBox()
        self.mode.setObjectName("guidedUrlMode")
        row.addWidget(self.site, 1)
        row.addWidget(self.mode, 1)
        layout.addLayout(row)
        fields = QGridLayout()
        self.fields = fields
        self.labels, self.inputs = [], []
        for index, name in enumerate(("guidedUrlTarget", "guidedUrlDetail", "guidedUrlPostID")):
            label = QLabel()
            field = QLineEdit()
            field.setObjectName(name)
            self.labels.append(label)
            self.inputs.append(field)
            fields.addWidget(label, 0, index)
            fields.addWidget(field, 1, index)
            fields.setColumnStretch(index, 1)
            field.textChanged.connect(self.update_preview)
        layout.addLayout(fields)
        actions = QHBoxLayout()
        self.example = QPushButton(tr("Fill an example", "Isi contoh"))
        self.example.setObjectName("guidedUrlExample")
        self.example.clicked.connect(self.fill_example)
        self.paste = QPushButton(tr("Paste complete links", "Tempel tautan lengkap"))
        self.paste.setObjectName("guidedUrlPaste")
        self.paste.clicked.connect(self.paste_links)
        self.import_file = QPushButton(tr("Import links TXT…", "Impor tautan TXT…"))
        self.import_file.setObjectName("guidedUrlImport")
        self.import_file.setToolTip(tr("One complete URL per line. All rows are checked before replacing this form.",
                                      "Satu URL lengkap per baris. Semua baris diperiksa sebelum formulir ini diganti."))
        self.import_file.clicked.connect(self.import_links)
        self.clear = QPushButton(tr("Clear this form", "Kosongkan formulir ini"))
        self.clear.setObjectName("guidedUrlClear")
        self.clear.clicked.connect(self.clear_form)
        self.batch = QCheckBox(tr("Several targets (one per line)", "Beberapa target (satu per baris)"))
        self.batch.setObjectName("guidedUrlBatch")
        self.batch.toggled.connect(self.update_preview)
        self.copy = QPushButton(tr("Copy links", "Salin tautan"))
        self.copy.setObjectName("guidedUrlCopy")
        self.copy.clicked.connect(self.copy_links)
        self.export = QPushButton(tr("Export links TXT…", "Ekspor tautan TXT…"))
        self.export.setObjectName("guidedUrlExport")
        self.export.clicked.connect(self.export_links)
        self.add = QPushButton(tr("Keep these links to prepare more", "Simpan tautan ini untuk menyiapkan lainnya"))
        self.add.setObjectName("guidedUrlAdd")
        self.add.clicked.connect(self.add_links)
        for widget in (self.example, self.paste, self.import_file, self.clear):
            actions.addWidget(widget)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addWidget(self.batch)
        self.batch_input = QPlainTextEdit()
        self.batch_input.setObjectName("guidedUrlBatchTargets")
        self.batch_input.setMaximumHeight(90)
        self.batch_input.setPlaceholderText(tr("One username, ID, search query, or complete URL per line", "Satu pengguna, ID, pencarian, atau tautan lengkap per baris"))
        self.batch_input.textChanged.connect(self.update_preview)
        layout.addWidget(self.batch_input)
        self.help = QLabel()
        self.help.setObjectName("guidedUrlHelper")
        self.help.setTextFormat(Qt.PlainText)
        self.help.setWordWrap(True)
        layout.addWidget(self.help)
        self.note = QLabel()
        self.note.setObjectName("guidedUrlNote")
        self.note.setTextFormat(Qt.PlainText)
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.preview = QPlainTextEdit()
        self.preview.setObjectName("guidedUrlPreview")
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(70)
        layout.addWidget(self.preview)
        result_actions = QHBoxLayout()
        result_actions.addWidget(self.copy)
        result_actions.addWidget(self.export)
        result_actions.addWidget(self.add)
        result_actions.addStretch(1)
        layout.addLayout(result_actions)
        next_step = QLabel(tr("Ready? Click Add to Queue below to include the current link. To collect several different links first, use Keep these links and change the form. Start Download in the main window when the queue is ready.", "Sudah siap? Klik Tambah ke Antrean di bawah untuk menyertakan tautan saat ini. Untuk mengumpulkan beberapa tautan berbeda, pakai Simpan tautan ini lalu ubah formulir. Mulai Download di halaman utama ketika antrean siap."))
        next_step.setObjectName("guidedUrlNextStep")
        next_step.setWordWrap(True)
        layout.addWidget(next_step)
        configure_row = QHBoxLayout()
        self.detected_site = QComboBox()
        self.detected_site.setObjectName("guidedUrlDetectedSite")
        self.detected_site.setToolTip(tr("Website recognized from the resulting links", "Situs yang dikenali dari hasil tautan"))
        configure_row.addWidget(self.detected_site)
        self.configure = QPushButton(tr("Open saved config for this website…", "Buka config tersimpan untuk situs ini…"))
        self.configure.setObjectName("guidedUrlConfigure")
        self.configure.clicked.connect(self.configure_website)
        configure_row.addWidget(self.configure)
        configure_row.addStretch(1)
        layout.addLayout(configure_row)
        self.site.currentIndexChanged.connect(self.update_modes)
        self.site.editTextChanged.connect(self.update_modes)
        self.mode.currentIndexChanged.connect(self.update_fields)
        self.update_modes()

    def tr_text(self, english: str, indonesian: str) -> str:
        return indonesian if self.indonesian else english

    def site_key(self) -> str:
        if self.site.currentText() == self.site.itemText(0):
            return ""
        return self.site.currentText().strip().lower()

    def update_modes(self, *_args) -> None:
        if self.loading:
            return
        self.loading = True
        previous = self.mode.currentData()
        self.mode.clear()
        for key in (*URL_RECIPE_MODES.get(self.site_key(), ()), "url"):
            self.mode.addItem(self.tr_text(*URL_MODE_LABELS[key]), key)
        index = self.mode.findData(previous)
        self.mode.setCurrentIndex(max(0, index))
        self.loading = False
        self.update_fields()

    def update_fields(self, *_args) -> None:
        if self.loading:
            return
        self.loading = True
        if self.active_recipe is not None:
            self.drafts[self.active_recipe] = ([field.text() for field in self.inputs], self.batch.isChecked(), self.batch_input.toPlainText())
        self.active_recipe = (self.site_key(), str(self.mode.currentData()))
        values, batch, batch_text = self.drafts.get(self.active_recipe, (["", "", ""], False, ""))
        fields = url_recipe_fields(self.site_key(), str(self.mode.currentData()))
        for index, (label, field) in enumerate(zip(self.labels, self.inputs, strict=True)):
            label.setVisible(index < len(fields))
            field.setVisible(index < len(fields))
            self.fields.setColumnStretch(index, 1 if index < len(fields) else 0)
            field.setText(values[index])
            if index < len(fields):
                en, id_text, example = fields[index]
                label.setText(self.tr_text(en, id_text))
                field.setPlaceholderText(self.tr_text("Example: ", "Contoh: ") + example)
        self.batch.setVisible(True)
        self.batch.setChecked(batch)
        batch_example = " | ".join(item[2] for item in fields)
        self.batch_input.setPlaceholderText(self.tr_text("One target per line. Example: ", "Satu target per baris. Contoh: ") + batch_example)
        self.help.setText(self.tr_text(
            "For several targets, use one row per link. Separate multiple fields with | in the same order as the form. A wrong row blocks the whole batch. Use Paste complete links for URLs you already have.",
            "Untuk beberapa target, pakai satu baris per tautan. Pisahkan beberapa kolom dengan | sesuai urutan formulir. Baris salah menahan seluruh batch. Pakai Tempel tautan lengkap untuk URL yang sudah ada.",
        ))
        site = self.site_key()
        if site in {"instagram", "pixiv", "patreon", "fanbox"}:
            self.help.setText(self.help.text() + self.tr_text(
                " Login may be needed: prepare browser cookies or an account before running the queue.",
                " Login mungkin diperlukan: siapkan cookies browser atau akun sebelum menjalankan antrean.",
            ))
        if site == "bluesky":
            self.help.setText(self.help.text() + self.tr_text(
                " Bluesky accepts a complete handle (artist.bsky.social) or a DID (did:plc:…). The post ID is the last part after /post/.",
                " Bluesky menerima handle lengkap (artist.bsky.social) atau DID (did:plc:…). ID postingan adalah bagian terakhir setelah /post/.",
            ))
        self.batch_input.setPlainText(batch_text)
        self.example.setVisible(self.mode.currentData() != "url")
        self.loading = False
        self.update_preview()

    def clear_form(self) -> None:
        self.loading = True
        for field in self.inputs:
            field.clear()
        self.batch_input.clear()
        self.batch.setChecked(False)
        self.loading = False
        self.update_preview()

    def fill_example(self) -> None:
        self.loading = True
        fields = url_recipe_fields(self.site_key(), str(self.mode.currentData()))
        for field, (_en, _id, example) in zip(self.inputs, fields):
            field.setText(example)
        if self.batch.isChecked():
            self.batch_input.setPlainText(" | ".join(item[2] for item in fields))
        self.loading = False
        self.update_preview()

    def generated_links(self) -> list[str]:
        site, mode = self.site_key(), str(self.mode.currentData())
        if site and self.site.findData(site) < 0:
            raise ValueError(self.tr_text("Choose a website from the list", "Pilih situs dari daftar"))
        fields = url_recipe_fields(site, mode)
        targets = [(index, line.strip()) for index, line in enumerate(self.batch_input.toPlainText().splitlines(), 1) if line.strip()] if self.batch.isChecked() else [(1, ":".join(field.text().strip() for field in self.inputs[:len(fields)]))]
        if not targets or any(not target or target.startswith(":") or target.endswith(":") for _, target in targets):
            raise ValueError(self.tr_text("Fill the fields above to see the resulting link", "Isi kolom di atas untuk melihat hasil tautan"))
        if len(targets) > 200:
            raise ValueError(self.tr_text("Use at most 200 targets per batch", "Gunakan paling banyak 200 target per batch"))
        links = []
        for index, target in targets:
            try:
                if self.batch.isChecked() and len(fields) > 1:
                    parts = [part.strip() for part in target.split("|")]
                    if len(parts) != len(fields) or any(not part for part in parts):
                        raise ValueError(self.tr_text(f"Use {len(fields)} fields separated by |", f"Gunakan {len(fields)} kolom dipisahkan dengan |"))
                    target = ":".join(parts)
                url, _subcategory = build_site_url(site, mode, target)
            except ValueError as exc:
                raise ValueError((f"Baris {index}: " if self.indonesian else f"Line {index}: ") + str(exc)) from exc
            if url not in links:
                links.append(url)
        return links

    def paste_links(self) -> None:
        text = QApplication.clipboard().text().strip()
        if not text:
            self.note.setText(self.tr_text("Copy a complete website URL first, then paste here.", "Salin tautan situs lengkap terlebih dahulu, lalu tempel di sini."))
            return
        self.site.setCurrentIndex(0)
        self.mode.setCurrentIndex(self.mode.findData("url"))
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) > 1:
            self.batch.setChecked(True)
            self.batch_input.setPlainText(text)
        else:
            self.batch.setChecked(False)
            self.inputs[0].setText(text)
        self.update_preview()

    def import_links(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr_text("Import complete links", "Impor tautan lengkap"),
                                            "", "Text files (*.txt);;All files (*)")
        if not path:
            return
        try:
            text = read_text_safely(path)
            rows = [(index, line.strip()) for index, line in enumerate(text.splitlines(), 1) if line.strip()]
            if not rows:
                raise ValueError(self.tr_text("The file contains no links.", "File tidak berisi tautan."))
            if len(rows) > 200:
                raise ValueError(self.tr_text("Use at most 200 targets per batch", "Gunakan paling banyak 200 target per batch"))
            links = []
            for index, value in rows:
                try:
                    url, _ = validate_supported_url(value)
                except ValueError as exc:
                    raise ValueError((f"Baris {index}: " if self.indonesian else f"Line {index}: ") + str(exc)) from exc
                if url not in links:
                    links.append(url)
        except (OSError, ValueError) as exc:
            self.note.setText(self.tr_text("Could not import links: ", "Tidak dapat mengimpor tautan: ") + redact_sensitive_text(str(exc)))
            return
        # Update only after every source row is valid. Recipe drafts survive switching.
        self.site.setCurrentIndex(0)
        self.mode.setCurrentIndex(self.mode.findData("url"))
        self.loading = True
        self.batch.setChecked(len(links) > 1)
        self.batch_input.setPlainText("\n".join(links))
        self.inputs[0].setText(links[0] if len(links) == 1 else "")
        self.loading = False
        self.update_preview()

    def export_links(self) -> None:
        try:
            links = self.generated_links()
        except ValueError as exc:
            self.note.setText(str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(self, self.tr_text("Export complete links", "Ekspor tautan lengkap"),
                                            "links.txt", "Text files (*.txt)")
        if not path:
            return
        try:
            atomic_write_text(path, "\n".join(links) + "\n", encoding="utf-8")
        except (OSError, ValueError) as exc:
            self.note.setText(self.tr_text("Could not export links: ", "Tidak dapat mengekspor tautan: ") + redact_sensitive_text(str(exc)))
            return
        self.note.setText(self.tr_text(f"Exported {len(links)} unique link(s).", f"{len(links)} tautan unik berhasil diekspor."))

    def update_preview(self, *_args) -> None:
        if self.loading:
            return
        self.batch_input.setVisible(self.batch.isChecked())
        for field in self.inputs:
            field.setEnabled(not self.batch.isChecked())
        try:
            links = self.generated_links()
        except ValueError as exc:
            self.preview.clear()
            self.note.setText(str(exc))
            self.add.setEnabled(False)
            self.copy.setEnabled(False)
            self.export.setEnabled(False)
            self.configure.setEnabled(False)
            self.detected_site.clear()
            self.detected_site.setEnabled(False)
            return
        from gallery_dl import extractor
        recognized: dict[str, set[str]] = {}
        for link in links:
            found = extractor.find(link)
            if found is not None:
                recognized.setdefault(found.category, set()).add(found.subcategory)
        previous = self.detected_site.currentData()
        self.detected_site.clear()
        for category in sorted(recognized):
            self.detected_site.addItem(category, category)
        self.detected_site.setCurrentIndex(max(0, self.detected_site.findData(previous)))
        self.detected_site.setEnabled(bool(recognized))
        self.preview.setPlainText("\n".join(links))
        summary = "; ".join(category + " (" + ", ".join(self.tr_text(*URL_MODE_LABELS.get(kind, (kind.replace("-", " "), kind.replace("-", " ")))) for kind in sorted(types)) + ")" for category, types in recognized.items())
        self.note.setText(self.tr_text(f"{len(links)} unique link(s) recognized: ", f"{len(links)} tautan unik dikenali: ") + summary + self.tr_text(". This checks the URL format; it does not check access or download anything.", ". Pemeriksaan hanya mencocokkan bentuk URL; akses dan unduhan belum dijalankan."))
        self.add.setEnabled(True)
        self.copy.setEnabled(True)
        self.export.setEnabled(True)
        self.configure.setEnabled(bool(recognized))

    def configure_website(self) -> None:
        try:
            links = self.generated_links()
        except ValueError as exc:
            self.note.setText(str(exc))
            return
        if links and self.detected_site.currentData():
            self.configureRequested.emit(str(self.detected_site.currentData()))

    def copy_links(self) -> None:
        try:
            QApplication.clipboard().setText("\n".join(self.generated_links()))
        except ValueError as exc:
            self.note.setText(str(exc))

    def add_links(self) -> None:
        try:
            links = self.generated_links()
        except ValueError as exc:
            self.note.setText(str(exc))
            return
        count = self.add_urls(links)
        self.note.setText(self.tr_text(f"Added {count} new link(s) to the download list. Existing links were skipped. Choose your download settings, then add the jobs to the queue.", f"{count} tautan baru ditambahkan ke daftar unduhan. Tautan yang sudah ada dilewati. Atur unduhan, lalu tambahkan job ke antrean."))
