"""Edit JSON lists and objects through rows rather than JSON punctuation."""

from __future__ import annotations

import json
from collections.abc import Callable

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .core import is_sensitive_option_key
from .config_maker import contains_config_secrets


class StructuredConfigEditor(QWidget):
    valueChanged = Signal()

    def __init__(self, parse_value: Callable[[str, str], object], *, indonesian: bool = False,
                 allowed_types: tuple[str, ...] = ("list", "object"), string_items: bool = False,
                 item_choices: tuple[object, ...] = (), example: object = None,
                 hint: str = "", name_hint: str = "", value_hint: str = "",
                 allow_default_key: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.parse_value = parse_value
        self.indonesian = indonesian
        self.loading = False
        self.string_items = string_items
        self.item_choices = item_choices
        self.name_hint = name_hint
        self.value_hint = value_hint
        self.allow_default_key = allow_default_key
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.kind = QComboBox()
        self.labels = {
            "list": ("List of items", "Daftar item"), "object": ("Name → replacement / value", "Nama → pengganti / nilai"),
            "text": ("Text value", "Nilai teks"), "boolean": ("Enable / disable", "Aktifkan / nonaktifkan"),
            "integer": ("Whole number", "Bilangan bulat"), "number": ("Number", "Angka"), "null": ("No value (null)", "Tanpa nilai (null)"),
        }
        for kind in allowed_types:
            self.kind.addItem(self.labels[kind][int(indonesian)], kind)
        self.kind.setVisible(len(allowed_types) > 1)
        layout.addWidget(self.kind)
        self.help = QLabel(hint or (
            "Daftar: satu item per baris. Pasangan: isi nama dan nilainya. Pakai bawaan jika tidak perlu diubah."
            if indonesian else "List: one item per row. Named values: enter a name and its value. Use default if no change is needed."
        ))
        self.help.setWordWrap(True)
        layout.addWidget(self.help)
        self.scalar = QLineEdit()
        self.scalar.setPlaceholderText(value_hint or ("Masukkan nilai" if indonesian else "Enter a value"))
        self.boolean = QComboBox()
        self.boolean.addItem("Aktif" if indonesian else "Enabled", True)
        self.boolean.addItem("Nonaktif" if indonesian else "Disabled", False)
        layout.addWidget(self.scalar)
        layout.addWidget(self.boolean)
        self.table = QTableWidget(0, 3)
        # Use our own name fields. Qt's default item delegate can copy a
        # pixel-sized stylesheet font as pointSize=-1 while starting an edit.
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setHorizontalHeaderLabels(
            ["Nama", "Jenis nilai", "Nilai"] if indonesian else ["Name", "Value type", "Value"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setMinimumSectionSize(70)
        # The application theme gives editors padding. Fixed default table
        # rows were shorter than these widgets and clipped neighbouring rows.
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.verticalHeader().setMinimumSectionSize(42)
        self.table.setMaximumHeight(210)
        self.table.setMinimumHeight(100)
        self.table.setStyleSheet("QTableWidget::item { padding: 2px; } QLineEdit, QComboBox { min-width: 0; min-height: 24px; padding: 3px; }")
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        add = QPushButton("Tambah item" if indonesian else "Add item")
        remove = QPushButton("Hapus item terpilih" if indonesian else "Remove selected item")
        add.clicked.connect(lambda: self.add_row())
        remove.clicked.connect(self.remove_row)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        self.example = example
        self.use_example = QPushButton("Isi contoh" if indonesian else "Fill example")
        self.use_example.setObjectName("structuredFillExample")
        self.use_example.clicked.connect(self.fill_example)
        buttons.addWidget(self.use_example)
        layout.addLayout(buttons)
        self.buttons = buttons
        self.result = QLabel()
        self.result.setObjectName("structuredValueExplanation")
        self.result.setWordWrap(True)
        self.result.setTextFormat(Qt.PlainText)
        layout.addWidget(self.result)
        self.kind.currentIndexChanged.connect(self.change_kind)
        self.table.itemChanged.connect(self.update_names)
        self.scalar.textChanged.connect(self.notify)
        self.boolean.currentIndexChanged.connect(self.notify)
        self.change_kind()

    def configure(self, *, allowed_types: tuple[str, ...], string_items: bool = False,
                  item_choices: tuple[object, ...] = (), hint: str = "", example: object = None,
                  name_hint: str = "", value_hint: str = "", allow_default_key: bool = False) -> None:
        self.loading = True
        self.string_items, self.item_choices = string_items, item_choices
        self.example, self.name_hint, self.value_hint = example, name_hint, value_hint
        self.allow_default_key = allow_default_key
        self.kind.clear()
        for kind in allowed_types or ("list", "object"):
            self.kind.addItem(self.labels[kind][int(self.indonesian)], kind)
        self.kind.setVisible(self.kind.count() > 1)
        if hint:
            self.help.setText(hint)
        self.loading = False

    def fill_example(self) -> None:
        self.set_value(self.example)
        self.notify()

    def notify(self, *_args) -> None:
        if not self.loading:
            self.explain_value()
            self.valueChanged.emit()

    def explain_value(self) -> None:
        if self.kind.currentData() not in {"list", "object"}:
            self.result.hide()
            return
        self.result.show()
        try:
            value = self.value()
        except ValueError as exc:
            self.result.setStyleSheet("color: #e99874;")
            self.result.setText(("Periksa isi baris: " if self.indonesian else "Check the row contents: ") + str(exc))
            return
        self.result.setStyleSheet("")
        if isinstance(value, dict):
            caption = f"{len(value)} pasangan akan disimpan" if self.indonesian else f"{len(value)} name/value pair(s) will be saved"
            samples = [f"{name or ('Jika kondisi lain tidak cocok' if self.indonesian else 'Otherwise')} → " + ("••••" if is_sensitive_option_key(name) or contains_config_secrets(item) else str(item)[:60]) for name, item in list(value.items())[:3]]
        else:
            caption = f"{len(value)} item akan disimpan" if self.indonesian else f"{len(value)} item(s) will be saved"
            samples = ["••••" if contains_config_secrets(item) else str(item)[:60] for item in value[:3]]
        self.result.setText(caption + (": " + "; ".join(samples) if samples else ". " + (
            "Klik Tambah item jika memerlukan nilai khusus." if self.indonesian else "Click Add item if you need a custom value."
        )))

    def change_kind(self, *_args) -> None:
        kind = self.kind.currentData()
        self.table.setColumnHidden(0, kind == "list")
        self.table.setColumnHidden(1, kind in {"list", "object"} and self.string_items)
        self.table.setVisible(kind in {"list", "object"})
        self.scalar.setVisible(kind in {"text", "integer", "number"})
        self.boolean.setVisible(kind == "boolean")
        for index in range(self.buttons.count()):
            widget = self.buttons.itemAt(index).widget()
            if widget:
                widget.setVisible(kind in {"list", "object"})
        self.use_example.setVisible(kind in {"list", "object"} and isinstance(self.example, (dict, list)))
        self.notify()

    def update_names(self, *_args) -> None:
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 0)
            editor = self.table.cellWidget(row, 2)
            if isinstance(editor, QLineEdit):
                editor.setEchoMode(QLineEdit.Password if name and is_sensitive_option_key(name.text()) else QLineEdit.Normal)
            name_editor = self.table.cellWidget(row, 0)
            if name and name_editor and name_editor.text() != name.text():
                name_editor.setText(name.text())
        self.notify()

    def add_row(self, name: str = "", value: object = "") -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        value_type = "boolean" if isinstance(value, bool) else "integer" if isinstance(value, int) else "number" if isinstance(value, float) else "json" if isinstance(value, (list, dict)) else "null" if value is None else "text"
        kind = QComboBox()
        for label, key in (
            ("Teks" if self.indonesian else "Text", "text"),
            ("Ya / Tidak" if self.indonesian else "Yes / No", "boolean"),
            ("Bilangan bulat" if self.indonesian else "Whole number", "integer"),
            ("Angka" if self.indonesian else "Number", "number"),
            ("JSON", "json"), ("Null", "null"),
        ):
            kind.addItem(label, key)
        kind.setCurrentIndex(kind.findData(value_type))
        raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if self.item_choices and self.kind.currentData() == "list":
            editor = QComboBox()
            editor.setEditable(True)
            editor.lineEdit().setPlaceholderText(self.value_hint or ("Pilih item atau ketik nilai" if self.indonesian else "Choose an item or type a value"))
            for choice in self.item_choices:
                editor.addItem(str(choice))
            editor.setCurrentText(raw)
            editor.currentTextChanged.connect(self.notify)
        else:
            editor = QLineEdit(raw)
            editor.setPlaceholderText(self.value_hint or ("Isi satu item" if self.indonesian else "Enter one item"))
            editor.setEchoMode(QLineEdit.Password if is_sensitive_option_key(name) else QLineEdit.Normal)
            editor.textChanged.connect(self.notify)
        self.table.setCellWidget(row, 1, kind)
        self.table.setCellWidget(row, 2, editor)
        name_editor = QLineEdit(name)
        name_editor.setPlaceholderText(self.name_hint or ("Nama, mis. jpeg" if self.indonesian else "Name, e.g. jpeg"))
        self.table.setCellWidget(row, 0, name_editor)
        item = QTableWidgetItem(name)
        item.setToolTip(self.name_hint or ("Nama yang unik, misalnya jpeg" if self.indonesian else "Unique name, e.g. jpeg"))
        self.table.setItem(row, 0, item)
        name_editor.textChanged.connect(item.setText)
        def select_editor(widget: QWidget, column: int) -> None:
            for index in range(self.table.rowCount()):
                if self.table.cellWidget(index, column) is widget:
                    self.table.setCurrentCell(index, column)
                    break

        name_editor.textEdited.connect(lambda _text: select_editor(name_editor, 0))
        if isinstance(editor, QLineEdit):
            editor.textEdited.connect(lambda _text: select_editor(editor, 2))
        else:
            editor.activated.connect(lambda _index: select_editor(editor, 2))
        kind.currentIndexChanged.connect(self.notify)
        self.table.setCurrentCell(row, 2)
        self.notify()

    def remove_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self.notify()

    def set_value(self, value: object) -> None:
        self.loading = True
        try:
            self.table.setRowCount(0)
            kind = "object" if isinstance(value, dict) else "list" if isinstance(value, list) else "boolean" if isinstance(value, bool) else "integer" if isinstance(value, int) else "number" if isinstance(value, float) else "null" if value is None else "text"
            index = self.kind.findData(kind)
            if index < 0:
                self.kind.addItem(kind, kind)
                index = self.kind.count() - 1
                self.kind.show()
            self.kind.setCurrentIndex(index)
            self.change_kind()
            self.scalar.setText(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
            if isinstance(value, bool):
                self.boolean.setCurrentIndex(self.boolean.findData(value))
            entries = value.items() if isinstance(value, dict) else (("", item) for item in value) if isinstance(value, list) else ()
            for name, item in entries:
                self.add_row(str(name), item)
            self.table.resizeRowsToContents()
        finally:
            self.loading = False
        self.explain_value()

    def value(self) -> object:
        return self.parse_snapshot(self.snapshot(), self.parse_value)

    def snapshot(self) -> dict:
        return {"mode": self.kind.currentData(), "allow_default_key": self.allow_default_key, "raw": json.dumps(self.boolean.currentData()) if self.kind.currentData() == "boolean" else self.scalar.text(), "rows": [
            [self.table.item(row, 0).text(), self.table.cellWidget(row, 1).currentData(),
             self.table.cellWidget(row, 2).currentText() if isinstance(self.table.cellWidget(row, 2), QComboBox) else self.table.cellWidget(row, 2).text()]
            for row in range(self.table.rowCount())
        ]}

    def set_snapshot(self, snapshot: dict) -> None:
        self.loading = True
        try:
            self.table.setRowCount(0)
            index = self.kind.findData(snapshot["mode"])
            if index < 0:
                self.kind.addItem(self.labels.get(snapshot["mode"], (snapshot["mode"],) * 2)[int(self.indonesian)], snapshot["mode"])
                index = self.kind.count() - 1
            self.kind.setCurrentIndex(index)
            self.scalar.setText(snapshot.get("raw", ""))
            if snapshot["mode"] == "boolean":
                self.boolean.setCurrentIndex(self.boolean.findData(snapshot.get("raw") == "true"))
            for name, kind, raw in snapshot["rows"]:
                self.add_row(name)
                row = self.table.rowCount() - 1
                picker = self.table.cellWidget(row, 1)
                picker.setCurrentIndex(picker.findData(kind))
                editor = self.table.cellWidget(row, 2)
                if isinstance(editor, QComboBox):
                    editor.setCurrentText(raw)
                else:
                    editor.setText(raw)
            self.table.resizeRowsToContents()
        finally:
            self.loading = False
        self.explain_value()

    @staticmethod
    def parse_snapshot(snapshot: dict, parse_value: Callable[[str, str], object]) -> object:
        if snapshot["mode"] not in {"list", "object"}:
            return parse_value(snapshot.get("raw", ""), snapshot["mode"])
        named = snapshot["mode"] == "object"
        result = {} if named else []
        for index, (name, kind, raw) in enumerate(snapshot["rows"], 1):
            try:
                value = parse_value(raw, str(kind))
            except ValueError as exc:
                raise ValueError(f"Row {index}: {exc}") from exc
            if named:
                name = name.strip()
                if name in result:
                    raise ValueError(f"Row {index}: names must be unique (only one default row is allowed)")
                if not name and not snapshot.get("allow_default_key", False):
                    raise ValueError(f"Row {index}: enter a unique, nonempty name")
                result[name] = value
            else:
                result.append(value)
        return result
