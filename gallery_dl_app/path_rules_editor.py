"""Edit ordered folder and filename rules without typing expressions or JSON."""

import ast
import copy
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from .config_maker import filename_example, installed_page_types
from .content_filter_editor import FILE_TYPES, extension_filter, filter_extensions


def rule_condition(kind: str, text: str) -> str:
    if kind == "otherwise":
        return ""
    if kind in FILE_TYPES:
        return extension_filter(",".join(FILE_TYPES[kind]))
    if kind == "extension":
        return extension_filter(text)
    if not text.strip():
        raise ValueError("Fill the condition value / Isi nilai kondisi")
    if kind == "subcategory":
        return "subcategory == " + repr(text.strip())
    if kind in {"title", "content", "description"}:
        # Missing website fields simply do not match. Never run this here.
        return repr(text) + f" in str(locals().get('{kind}') or '')"
    raise ValueError("Keep the advanced condition or choose a supported condition")


def read_rule_condition(expression: str) -> tuple[str, str]:
    if not expression:
        return "otherwise", ""
    extensions = filter_extensions(expression)
    if extensions:
        return next((key for key, items in FILE_TYPES.items() if set(items) == set(extensions)), "extension"), ", ".join(extensions)
    try:
        node = ast.parse(expression, mode="eval").body
        if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
            return "advanced", ""
        left, right = node.left, node.comparators[0]
        if isinstance(node.ops[0], ast.Eq) and isinstance(left, ast.Name) and left.id == "subcategory" and isinstance(right, ast.Constant) and isinstance(right.value, str):
            return "subcategory", right.value
        if isinstance(node.ops[0], ast.Eq) and isinstance(left, ast.Name) and left.id == "extension" and isinstance(right, ast.Constant) and isinstance(right.value, str):
            if filter_extensions("extension in " + repr((right.value,))):
                return "extension", right.value
        if isinstance(node.ops[0], ast.In) and isinstance(left, ast.Constant) and isinstance(left.value, str):
            for field in ("title", "content", "description"):
                if (isinstance(right, ast.Name) and right.id == field) or ast.dump(right) == ast.dump(ast.parse(f"str(locals().get('{field}') or '')", mode="eval").body):
                    return field, left.value
    except (ValueError, SyntaxError, RecursionError):
        pass
    return "advanced", ""


class PathRulesEditor(QWidget):
    def __init__(self, kind: str, value: object, explicit: bool, change: Callable[[object, bool], None], *, site: str, indonesian: bool = False):
        super().__init__()
        self.kind, self.site, self.change, self.indonesian = kind, site, change, indonesian
        self.loading = False
        self.condition_changed = False
        self.form_dirty = False
        self.active_row = -1
        self.base_value = copy.deepcopy(value)
        self.base_remove = not explicit
        self.setObjectName("pathRules_" + kind)
        self.rules = list(copy.deepcopy(value).items()) if isinstance(value, dict) else [("", copy.deepcopy(value))] if value is not None else []
        self.valid = all(isinstance(condition, str) for condition, _ in self.rules)
        tr = self.tr_text
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.addWidget(QLabel(tr("Folder rules", "Aturan folder") if kind == "directory" else tr("File name rules", "Aturan nama file")))
        note = QLabel(tr("Rules are checked from top to bottom. The first match wins; Otherwise is the fallback. Edit a rule, then Update rule in draft. Save config to use it on future downloads.", "Aturan diperiksa dari atas ke bawah. Yang pertama cocok dipakai; Selain itu adalah pilihan cadangan. Edit aturan lalu Perbarui aturan di draft. Simpan config untuk unduhan berikutnya."))
        note.setWordWrap(True)
        layout.addWidget(note)
        source = QLabel(tr("Editing rules for this website's draft.", "Mengedit aturan pada draft situs ini.") if explicit else tr("Uses shared rules if saved there; otherwise uses this website's own defaults. Updating a rule creates a website override.", "Memakai aturan bersama bila ada; jika tidak, memakai bawaan situs ini. Memperbarui aturan membuat pengaturan khusus situs."))
        source.setWordWrap(True)
        layout.addWidget(source)
        self.list = QListWidget()
        self.list.setObjectName("pathRulesList_" + kind)
        self.list.setMinimumHeight(75)
        self.list.setMaximumHeight(110)
        layout.addWidget(self.list)
        controls = QHBoxLayout()
        for key, en, id_text, callback in (
            ("add", "New rule", "Aturan baru", self.new_rule),
            ("up", "Move up", "Naikkan", lambda: self.move(-1)),
            ("down", "Move down", "Turunkan", lambda: self.move(1)),
            ("remove", "Remove rule", "Hapus aturan", self.remove_rule),
        ):
            button = QPushButton(tr(en, id_text))
            button.setObjectName(f"pathRules{key.capitalize()}_{kind}")
            button.clicked.connect(callback)
            controls.addWidget(button)
            setattr(self, key + "_button", button)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.condition = QComboBox()
        self.condition.setObjectName("pathRuleCondition_" + kind)
        choices = [("otherwise", "Otherwise", "Selain itu"), ("title", "Title contains", "Judul mengandung"),
                   ("content", "Post text contains", "Teks postingan mengandung"), ("description", "Description contains", "Deskripsi mengandung"),
                   ("subcategory", "Page type is", "Jenis halaman adalah")]
        if kind == "filename":
            choices += [("images", "Image file", "File gambar"), ("videos", "Video file", "File video"), ("audio", "Audio file", "File audio"), ("extension", "File extension is one of", "Ekstensi file termasuk")]
        choices += [("advanced", "Existing advanced condition (preserved)", "Kondisi lanjutan yang sudah ada (dipertahankan)")]
        for key, en, id_text in choices:
            self.condition.addItem(tr(en, id_text), key)
        layout.addWidget(self.condition)
        self.argument = QComboBox()
        self.argument.setEditable(True)
        self.argument.setInsertPolicy(QComboBox.NoInsert)
        self.argument.setObjectName("pathRuleArgument_" + kind)
        layout.addWidget(self.argument)
        self.destination = QPlainTextEdit() if kind == "directory" else QLineEdit()
        self.destination.setObjectName("pathRuleDestination_" + kind)
        if kind == "directory":
            self.destination.setMaximumHeight(85)
            self.destination.setPlaceholderText(site + "\nSelected")
        else:
            self.destination.setPlaceholderText("{id|filename}.{extension}")
        layout.addWidget(QLabel(tr("Subfolders: one folder level per line (blank = base folder)", "Subfolder: satu tingkat folder per baris (kosong = folder utama)") if kind == "directory" else tr("File name pattern", "Pola nama file")))
        layout.addWidget(self.destination)
        self.preview = QLabel()
        self.preview.setObjectName("pathRulePreview_" + kind)
        self.preview.setTextFormat(Qt.PlainText)
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        self.update = QPushButton(tr("Update rule in draft", "Perbarui aturan di draft"))
        self.update.setObjectName("pathRuleUpdate_" + kind)
        self.update.clicked.connect(self.update_rule)
        update_row = QHBoxLayout()
        update_row.addWidget(self.update)
        cancel = QPushButton(tr("Discard this rule edit", "Batalkan edit aturan ini"))
        cancel.setObjectName("pathRuleDiscard_" + kind)
        cancel.clicked.connect(self.discard_edit)
        update_row.addWidget(cancel)
        update_row.addStretch(1)
        layout.addLayout(update_row)
        options = QHBoxLayout()
        example = QPushButton(tr("Fill an example (replace rules)", "Isi contoh (ganti aturan)"))
        example.setObjectName("pathRulesExample_" + kind)
        example.clicked.connect(self.fill_example)
        options.addWidget(example)
        self.reset = QPushButton(tr("Use shared/default setting", "Pakai pengaturan bersama/bawaan"))
        self.reset.setObjectName("pathRulesReset_" + kind)
        self.reset.setEnabled(explicit)
        self.reset.clicked.connect(lambda: self.change(None, True))
        options.addWidget(self.reset)
        options.addStretch(1)
        layout.addLayout(options)
        help_text = QLabel(tr("Text matching is case-sensitive. Metadata fields vary between websites; missing fields do not match new text rules. Advanced imported conditions remain intact and are never executed in this editor.", "Pencocokan teks membedakan huruf besar/kecil. Field metadata berbeda tiap situs; field yang tidak tersedia tidak cocok dengan aturan teks baru. Kondisi lanjutan dari config tetap utuh dan tidak dijalankan di editor."))
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.list.currentRowChanged.connect(self.select_rule)
        self.condition.currentIndexChanged.connect(self.change_condition)
        self.argument.editTextChanged.connect(self.change_argument)
        self.destination.textChanged.connect(self.edit_destination)
        self.reload()

    def tr_text(self, english: str, indonesian: str) -> str:
        return indonesian if self.indonesian else english

    def title(self, expression: str) -> str:
        if not isinstance(expression, str):
            return self.tr_text("Invalid condition; use All settings", "Kondisi tidak valid; gunakan Semua pengaturan")
        kind, argument = read_rule_condition(expression)
        index = self.condition.findData(kind)
        if index < 0 or kind == "advanced":
            return self.tr_text("Advanced condition (preserved)", "Kondisi lanjutan (dipertahankan)")
        return self.condition.itemText(index) + (": " + argument if argument and kind not in FILE_TYPES else "")

    def reload(self, selected: int = 0) -> None:
        self.loading = True
        self.list.clear()
        for condition, value in self.rules:
            text = " / ".join(value) if isinstance(value, list) and all(isinstance(part, str) for part in value) else str(value)
            self.list.addItem(self.title(condition) + " → " + (text or self.tr_text("Base folder", "Folder utama")))
        self.loading = False
        self.list.setCurrentRow(min(selected, len(self.rules) - 1))
        self.select_rule()

    def select_rule(self, *_args) -> None:
        if self.loading:
            return
        row = self.list.currentRow()
        if self.form_dirty:
            try:
                self.rules = list(self.parse_snapshot(self.snapshot()).items())
                self.form_dirty = False
                self.base_value = dict(copy.deepcopy(self.rules))
                self.base_remove = False
                self.change(copy.deepcopy(self.base_value), False)
                self.reload(row)
                return
            except ValueError as exc:
                self.list.blockSignals(True)
                self.list.setCurrentRow(self.active_row)
                self.list.blockSignals(False)
                self.preview.setText(str(exc))
                return
        self.active_row = row
        self.up_button.setEnabled(row > 0)
        self.down_button.setEnabled(0 <= row < len(self.rules) - 1)
        self.remove_button.setEnabled(row >= 0)
        self.loading = True
        expression, value = self.rules[row] if row >= 0 else ("", [] if self.kind == "directory" else "")
        kind, argument = read_rule_condition(expression) if isinstance(expression, str) else ("advanced", "")
        self.condition.setCurrentIndex(self.condition.findData(kind) if self.condition.findData(kind) >= 0 else self.condition.findData("advanced"))
        self.argument.clear()
        if kind == "subcategory":
            self.argument.addItems(installed_page_types(self.site))
        self.argument.setEditText(argument)
        text = "\n".join(value) if self.kind == "directory" and isinstance(value, list) and all(isinstance(part, str) for part in value) else str(value)
        self.destination.setPlainText(text) if self.kind == "directory" else self.destination.setText(text)
        self.loading = False
        self.condition_changed = False
        self.show_preview()

    def change_condition(self, *_args) -> None:
        if self.loading:
            return
        self.condition_changed = True
        if self.condition.currentData() == "subcategory":
            text = self.argument.currentText()
            self.argument.blockSignals(True)
            self.argument.clear()
            self.argument.addItems(installed_page_types(self.site))
            self.argument.setEditText(text)
            self.argument.blockSignals(False)
        self.show_preview()
        self.publish_form()

    def change_argument(self, *_args) -> None:
        if not self.loading:
            self.condition_changed = True
            self.show_preview()
            self.publish_form()

    def edit_destination(self, *_args) -> None:
        if not self.loading:
            self.show_preview()
            self.publish_form()

    def publish_form(self) -> None:
        self.form_dirty = True
        self.change(self.snapshot(), False)

    def snapshot(self) -> dict:
        return {"path_rule_form": True, "rules": copy.deepcopy(self.rules), "row": self.active_row,
                "base_value": copy.deepcopy(self.base_value), "base_remove": self.base_remove,
                "condition": self.condition.currentData(), "argument": self.argument.currentText(),
                "condition_changed": self.condition_changed,
                "destination": self.destination.toPlainText().splitlines() if self.kind == "directory" else self.destination.text(), "kind": self.kind}

    @staticmethod
    def parse_snapshot(snapshot: dict) -> dict:
        rules = copy.deepcopy(snapshot["rules"])
        row = snapshot["row"]
        expression = rules[row][0] if row >= 0 and not snapshot["condition_changed"] else rule_condition(snapshot["condition"], snapshot["argument"])
        if any(condition == expression and index != row for index, (condition, _) in enumerate(rules)):
            raise ValueError("This condition already has a rule; only one Otherwise rule is allowed / Kondisi sudah punya aturan; hanya satu Selain itu yang diizinkan")
        value = snapshot["destination"]
        if snapshot["kind"] == "filename" and not value.strip():
            raise ValueError("Enter a file name pattern / Masukkan pola nama file")
        if row >= 0:
            rules[row] = (expression, value)
        else:
            rules.append((expression, value))
        PathRulesEditor.validate_rules(rules, snapshot["kind"])
        return dict(rules)

    def restore_snapshot(self, snapshot: dict) -> None:
        self.loading = True
        self.base_value = copy.deepcopy(snapshot["base_value"])
        self.base_remove = snapshot["base_remove"]
        self.active_row = snapshot["row"]
        self.list.setCurrentRow(self.active_row)
        self.condition.setCurrentIndex(self.condition.findData(snapshot["condition"]))
        self.argument.setEditText(snapshot["argument"])
        self.destination.setPlainText("\n".join(snapshot["destination"])) if self.kind == "directory" else self.destination.setText(snapshot["destination"])
        self.condition_changed = snapshot["condition_changed"]
        self.loading = False
        self.form_dirty = True
        self.show_preview()

    def show_preview(self, *_args) -> None:
        if self.loading:
            return
        kind = self.condition.currentData()
        self.argument.setVisible(kind in {"title", "content", "description", "subcategory", "extension"})
        self.argument.lineEdit().setPlaceholderText("jpg, png, mp4" if kind == "extension" else "favorite" if kind == "subcategory" else self.tr_text("Word or phrase, e.g. nature", "Kata atau frasa, misalnya nature"))
        self.update.setEnabled(self.valid and (kind != "advanced" or (self.list.currentRow() >= 0 and not self.condition_changed)))
        try:
            patterns = self.destination.toPlainText().splitlines() if self.kind == "directory" else [self.destination.text()]
            if self.kind == "filename" and not self.destination.text():
                self.preview.setText(self.tr_text("Enter a pattern, or leave this form to keep the website's existing defaults.", "Masukkan pola, atau biarkan formulir ini untuk memakai bawaan situs yang sudah ada."))
                return
            text = " / ".join(filename_example(pattern, self.site) for pattern in patterns) or self.tr_text("Base folder", "Folder utama")
            self.preview.setText(self.tr_text("Example result when this rule matches (sample data): ", "Contoh hasil ketika aturan cocok (data ilustrasi): ") + text)
        except (ValueError, TypeError):
            self.preview.setText(self.tr_text("Advanced pattern preserved; actual results need this website's metadata.", "Pola lanjutan dipertahankan; hasil nyata mengikuti metadata situs ini."))

    def new_rule(self) -> None:
        self.list.setCurrentRow(-1)
        if self.list.currentRow() != -1:
            return
        self.loading = True
        self.active_row = -1
        self.condition.setCurrentIndex(self.condition.findData("title"))
        self.argument.setEditText("")
        self.destination.setPlainText("") if self.kind == "directory" else self.destination.clear()
        self.loading = False
        self.condition_changed = True
        self.show_preview()
        self.publish_form()
        self.preview.setText(self.tr_text("Fill the condition and destination, then Update rule in draft.", "Isi kondisi dan tujuan, lalu Perbarui aturan di draft."))

    def update_rule(self) -> None:
        row = self.active_row
        try:
            self.rules = list(self.parse_snapshot(self.snapshot()).items())
            if row < 0:
                row = len(self.rules) - 1
            self.form_dirty = False
            self.commit(row)
        except ValueError as exc:
            self.preview.setText(str(exc))

    def discard_edit(self) -> None:
        self.form_dirty = False
        self.rules = list(copy.deepcopy(self.base_value).items()) if isinstance(self.base_value, dict) else [("", copy.deepcopy(self.base_value))] if self.base_value is not None else []
        self.reload(max(0, self.active_row))
        self.change(copy.deepcopy(self.base_value), self.base_remove)

    def commit(self, selected: int = 0) -> None:
        self.validate_rules(self.rules, self.kind)
        self.form_dirty = False
        self.base_value = dict(copy.deepcopy(self.rules))
        self.base_remove = False
        self.change(copy.deepcopy(self.base_value), False)
        self.reset.setEnabled(True)
        self.reload(selected)

    @staticmethod
    def validate_rules(rules: list, kind: str) -> None:
        if any(not isinstance(condition, str) or (not isinstance(value, list) or any(not isinstance(part, str) for part in value) if kind == "directory" else not isinstance(value, str)) for condition, value in rules):
            raise ValueError("A saved rule has an invalid value. Use All settings or Fill an example / Nilai aturan tidak valid. Gunakan Semua pengaturan atau Isi contoh")

    def move(self, direction: int) -> None:
        if self.form_dirty:
            self.update_rule()
            if self.form_dirty:
                return
        row = self.list.currentRow()
        target = row + direction
        if row >= 0 and 0 <= target < len(self.rules):
            self.rules[row], self.rules[target] = self.rules[target], self.rules[row]
            self.commit(target)

    def remove_rule(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.rules.pop(row)
            self.form_dirty = False
            self.commit(max(0, row - 1))

    def fill_example(self) -> None:
        if self.kind == "directory":
            self.rules = [(rule_condition("title", "nature"), [self.site, "Nature"]), ("", [self.site, "Other"])]
        else:
            self.rules = [(rule_condition("images", ""), "{id|filename}_image.{extension}"), (rule_condition("videos", ""), "{id|filename}_video.{extension}"), ("", "{id|filename}.{extension}")]
        self.valid = True
        self.commit()
