from __future__ import annotations

import csv
import io
import queue
import threading
import time
import zipfile
from pathlib import Path

from PySide6.QtCore import QTimer, Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMenu,
    QMessageBox,
    QTableWidgetItem,
)

from .core import (
    IS_WINDOWS,
    MAX_IMPORT_BYTES,
    MAX_COMMAND_LINE_CHARS,
    MAX_LOG_LINE_CHARS,
    MAX_LOG_LINES,
    MAX_QUEUE_SOURCE_ROWS,
    REDACTED,
    _row_value,
    build_raw_command_from_columns,
    classify_error,
    command_executable_available,
    csv_rows_first_column_fallback,
    find_exact_duplicate_groups,
    looks_like_database_entry,
    open_path,
    parse_text_database,
    read_text_safely,
    redact_sensitive_text,
    safe_int,
)
from .models import JobResult
from .themes import SERVICE_COLORS, STATUS_COLORS
from .workers import DownloadWorker


MAX_XLSX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_XLSX_ARCHIVE_MEMBERS = 4096


def _validate_import_record(row_number: int, *values: object) -> None:
    """Reject oversized import sources while they are still being streamed."""
    if row_number > MAX_QUEUE_SOURCE_ROWS:
        raise ValueError(
            f"Import exceeds the {MAX_QUEUE_SOURCE_ROWS:,} source-row safety limit. "
            "Split it into smaller files."
        )
    for value in values:
        if len(str(value or "")) > MAX_COMMAND_LINE_CHARS:
            raise ValueError(
                f"One imported cell exceeds the {MAX_COMMAND_LINE_CHARS // 1024} KB safety limit."
            )


def validate_xlsx_archive(path: str | Path) -> None:
    """Reject malformed or expansion-heavy XLSX archives before openpyxl."""
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"Invalid XLSX/ZIP file: {exc}") from exc
    if len(members) > MAX_XLSX_ARCHIVE_MEMBERS:
        raise ValueError(
            f"XLSX contains too many archive members "
            f"({len(members)} > {MAX_XLSX_ARCHIVE_MEMBERS})."
        )
    expanded_size = sum(max(0, int(member.file_size)) for member in members)
    if expanded_size > MAX_XLSX_UNCOMPRESSED_BYTES:
        raise ValueError(
            f"XLSX expands beyond the safety limit "
            f"({expanded_size // (1024 * 1024)} MB > "
            f"{MAX_XLSX_UNCOMPRESSED_BYTES // (1024 * 1024)} MB)."
        )


class QueueControllerMixin:
    def _refresh_env(self) -> None:
        ok = command_executable_available(self.gdl_cmd)
        ind = getattr(self, "help_language", "English") == "Indonesia"
        self.lbl_ready.setText(("SIAP" if ind else "READY") if ok else ("GALLERY-DL BELUM ADA" if ind else "GALLERY-DL MISSING"))
        self.lbl_ready.setObjectName("good" if ok else "bad")
        self.lbl_ready.style().unpolish(self.lbl_ready)
        self.lbl_ready.style().polish(self.lbl_ready)
        if hasattr(self, "edit_gdl_cmd"):
            self.edit_gdl_cmd.setText(self.gdl_cmd or "")
        if hasattr(self, "edit_config_path"):
            self.edit_config_path.setText(self.config_path or "")

    def _set_running_ui(self, running: bool) -> None:
        # Lock the editor while a run is active. Edits during a run were never
        # reparsed (on_text_changed bails when workers are active and the
        # pending debounce is swallowed), silently desyncing the text from
        # self.jobs/queue table until the next manual rebuild. Programmatic
        # setPlainText still works on a read-only widget, so internal callers
        # are unaffected.
        if hasattr(self, "txt_commands"):
            self.txt_commands.setReadOnly(running)
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_pause.setEnabled(running)
        self.btn_cancel.setEnabled(running)
        self.btn_import.setEnabled(not running)
        self.btn_paste.setEnabled(not running)
        self.btn_clear_input.setEnabled(not running)
        self.spin_workers.setEnabled(not running)
        self.spin_retries.setEnabled(not running)
        self.spin_delay.setEnabled(not running)
        self.edit_output.setEnabled(not running)
        self.btn_output.setEnabled(not running)
        self.combo_cookies.setEnabled(not running)
        if hasattr(self, "btn_set_gdl"):
            self.btn_set_gdl.setEnabled(not running)
        if hasattr(self, "btn_choose_config"):
            self.btn_choose_config.setEnabled(not running)
        if hasattr(self, "btn_action_builder"):
            self.btn_action_builder.setEnabled(not running)
        if hasattr(self, "btn_config_guide_side"):
            self.btn_config_guide_side.setEnabled(not running)
        self.btn_retry.setEnabled((not running) and bool(self.failed_indices or self.stopped_indices or self.cancelled_indices))

    def _update_counts(self) -> None:
        self.lbl_entry_count.setText(f"{len(self.jobs)} {'entri' if self._ui_is_indonesian() else 'entry'}")
        ind = self._ui_is_indonesian()
        done_label, failed_label, stopped_label = (
            ("Selesai", "Gagal", "Berhenti") if ind else ("Done", "Failed", "Stopped")
        )
        self.lbl_log_counts.setText(
            f"{done_label} {len(self.done_indices)}   {failed_label} {len(self.failed_indices)}"
        )
        self.lbl_summary.setText(
            f"{self.processed_run}/{self.total_run} · {done_label} {len(self.done_indices)} · "
            f"{failed_label} {len(self.failed_indices)} · {stopped_label} {len(self.stopped_indices)}"
        )
        self.progress.setMaximum(max(1, self.total_run))
        self.progress.setValue(self.processed_run)
        self.btn_retry.setEnabled(bool(self.failed_indices or self.stopped_indices or self.cancelled_indices) and self.active_workers <= 0)
        duplicate_count = sum(len(group) - 1 for group in find_exact_duplicate_groups(self.jobs))
        state = "Running" if self.active_workers > 0 else "Ready"
        if self._ui_is_indonesian():
            state = "Berjalan" if self.active_workers > 0 else "Siap"
            self.statusBar().showMessage(
                f"{state}  |  {len(self.jobs)} job  |  {duplicate_count} duplikat persis  |  Ctrl+Enter: mulai  |  F5: periksa"
            )
        else:
            self.statusBar().showMessage(
                f"{state}  |  {len(self.jobs)} jobs  |  {duplicate_count} exact duplicates  |  Ctrl+Enter: start  |  F5: check"
            )

    def _refresh_eta(self) -> None:
        if self.active_workers <= 0 or not self.started_at or self.processed_run <= 0:
            self.lbl_eta.setText(("Sisa waktu: -" if self._ui_is_indonesian() else "ETA: -"))
            return
        elapsed = time.time() - self.started_at
        rate = self.processed_run / max(elapsed, 1)
        remaining = max(0, self.total_run - self.processed_run)
        sec = int(remaining / max(rate, 0.0001))
        self.lbl_eta.setText((f"Sisa waktu: {sec//60}m {sec%60}s" if self._ui_is_indonesian() else f"ETA: {sec//60}m {sec%60}s"))

    def _log_line_visible(self, line: str) -> bool:
        mode = self.combo_log_filter.currentText() if hasattr(self, "combo_log_filter") else "All"
        mode_key = {
            "Semua": "All",
            "Galat": "Error",
            "Peringatan": "Warning",
            "Selesai": "Done",
            "Baris worker aktif": "Current worker lines",
        }.get(mode, mode)
        low = line.lower()
        if mode_key == "Error":
            return any(x in low for x in ("error", "failed", "traceback", "exception", "❌"))
        if mode_key == "Warning":
            return any(x in low for x in ("warning", "warn", "⚠"))
        if mode_key == "Done":
            return any(x in low for x in ("done", "✅"))
        if mode_key == "Current worker lines":
            return line.startswith("[W")
        return True

    def append_log(self, text: str) -> None:
        # Batch GUI appends so verbose gallery-dl output cannot flood the Qt
        # event loop. The complete bounded log remains available for filters
        # and exports.
        text = redact_sensitive_text(text)
        if len(text) > MAX_LOG_LINE_CHARS:
            text = text[:MAX_LOG_LINE_CHARS] + " … [truncated]"
        self.all_log_lines.append(text)
        if len(self.all_log_lines) > MAX_LOG_LINES:
            del self.all_log_lines[: len(self.all_log_lines) - MAX_LOG_LINES]
        if not self._log_line_visible(text):
            return
        if not hasattr(self, "_pending_log_lines"):
            self._pending_log_lines: list[str] = []
        self._pending_log_lines.append(text)
        if len(self._pending_log_lines) >= 80:
            self._flush_pending_log_lines()
            return
        if not hasattr(self, "_log_flush_timer"):
            self._log_flush_timer = QTimer(self)
            self._log_flush_timer.setSingleShot(True)
            self._log_flush_timer.timeout.connect(self._flush_pending_log_lines)
        if not self._log_flush_timer.isActive():
            self._log_flush_timer.start(120)

    def _flush_pending_log_lines(self) -> None:
        pending = getattr(self, "_pending_log_lines", [])
        if not pending or not hasattr(self, "log_all"):
            return
        scrollbar = self.log_all.verticalScrollBar()
        cursor_at_end = scrollbar.value() == scrollbar.maximum()
        self.log_all.appendPlainText("\n".join(pending))
        pending.clear()
        if cursor_at_end:
            scrollbar.setValue(scrollbar.maximum())

    def refresh_log_view(self) -> None:
        if not hasattr(self, "log_all"):
            return
        if hasattr(self, "_pending_log_lines"):
            self._pending_log_lines.clear()
        cursor_at_end = self.log_all.verticalScrollBar().value() == self.log_all.verticalScrollBar().maximum()
        self.log_all.clear()
        visible_lines = [
            line
            for line in self.all_log_lines[-MAX_LOG_LINES:]
            if self._log_line_visible(line)
        ]
        if visible_lines:
            self.log_all.setPlainText("\n".join(visible_lines))
        if cursor_at_end:
            self.log_all.verticalScrollBar().setValue(self.log_all.verticalScrollBar().maximum())

    @Slot()
    def on_text_changed(self) -> None:
        # Debounce: reparsing the whole database, rebuilding the queue table and
        # writing autosave on every keystroke makes typing lag badly on large
        # databases. Coalesce rapid edits and run the heavy rebuild ~350 ms after
        # the user stops typing. Direct callers use _rebuild_from_text() for a
        # synchronous rebuild (e.g. right before starting a run).
        if self.active_workers > 0:
            return
        if not hasattr(self, "_text_debounce"):
            self._text_debounce = QTimer(self)
            self._text_debounce.setSingleShot(True)
            self._text_debounce.timeout.connect(self._rebuild_from_text)
        self._text_debounce.start(350)

    def _rebuild_from_text(self) -> bool:
        if self.active_workers > 0:
            return False
        if hasattr(self, "_text_debounce"):
            self._text_debounce.stop()
        text = self.txt_commands.toPlainText()
        try:
            parsed_jobs = parse_text_database(text)
        except ValueError as exc:
            self.show_compact_message("Queue input rejected", str(exc), "error")
            return False
        self.jobs = parsed_jobs
        self._clear_result_state()
        self.populate_queue_table()
        self._update_counts()
        self.autosave_session()
        return True

    @Slot()
    def import_file(self) -> None:
        if self.active_workers > 0:
            self.show_compact_message("Import", "Stop the current download before importing a new database.", "info")
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import link database",
            "",
            "Supported (*.txt *.csv *.xlsx);;Text (*.txt);;CSV (*.csv);;Excel (*.xlsx);;All files (*.*)",
        )
        if not path:
            return
        try:
            suffix = Path(path).suffix.lower()
            if suffix == ".xlsx":
                text = self.read_xlsx_as_commands(path)
            elif suffix == ".csv":
                text = self.read_csv_as_commands(path)
            else:
                text = read_text_safely(path)
            # Validate limits before handing the text to QPlainTextEdit; loading
            # an invalid million-row file into the widget already causes the
            # freeze that the queue cap is meant to prevent.
            parse_text_database(text)
            self.current_file = path
            self.txt_commands.setPlainText(text)
            self.lbl_loaded_file.setText(Path(path).name)
            self.lbl_loaded_file.setToolTip(path)
            self.append_log(f"[input] imported: {path}")
        except Exception as exc:
            self.show_compact_message("Import failed", str(exc), "error")

    def read_csv_as_commands(self, path: str) -> str:
        text = read_text_safely(path)
        if not text.strip():
            return ""
        sample = text[:4096]
        try:
            # Restrict candidates to real CSV delimiters. Without this, cells
            # containing URLs or full commands (which include spaces) make the
            # Sniffer pick " " as the delimiter and mangle every row.
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel

        known_headers = {
            "url", "link", "links", "source", "target", "destination", "folder", "output",
            "output_dir", "output dir", "command", "gallery-dl command", "gallery_dl_command",
            "raw", "cmd", "extra args", "extra_args", "args", "options", "enabled",
            "enable", "active", "run", "tag", "group", "label", "notes", "note",
            "comment", "comments",
        }
        try:
            has_header = csv.Sniffer().has_header(sample)
        except csv.Error:
            has_header = True
        # Sniffer commonly returns False for perfectly valid small files such
        # as ``url,enabled`` followed by same-shaped text rows.  Explicit known
        # column names are stronger evidence than its type/length heuristic.
        try:
            first_row = next(csv.reader(io.StringIO(text), dialect=dialect), [])
        except csv.Error:
            first_row = []
        explicit_header = {str(value or "").strip().lower() for value in first_row}
        has_header = has_header or bool(explicit_header & known_headers)

        output: list[str] = []
        recognized_schema = False
        current_tag = ""
        if has_header:
            # Feed the raw text, not splitlines(): the csv module can only
            # reconstruct quoted cells that CONTAIN newlines when the newline
            # characters are still in the stream. With splitlines() a cell
            # like "line1\nline2" was silently glued into "line1line2" — and a
            # quoted command/url cell spanning lines became one garbled row.
            reader = csv.DictReader(io.StringIO(text), dialect=dialect)
            fieldnames = [str(x or "").strip().lower() for x in (reader.fieldnames or [])]
            if set(fieldnames) & known_headers:
                recognized_schema = True
                for row_number, row in enumerate(reader, start=2):
                    _validate_import_record(row_number, *(row.values()))
                    normalized_row = {k or "": v or "" for k, v in row.items()}
                    cmd = build_raw_command_from_columns(normalized_row)
                    if cmd:
                        tag = _row_value(normalized_row, "tag", "group", "label")
                        notes = _row_value(normalized_row, "notes", "note", "comment", "comments")
                        if tag != current_tag:
                            output.append(f"# {tag}" if tag else "#")
                            current_tag = tag
                        if notes:
                            output.append(f"#@notes {notes}")
                        output.append(cmd)
            else:
                # Unknown headers: treat as a first-column database but skip the header row
                # when it does not look like a real URL/command.
                output = csv_rows_first_column_fallback(text, dialect, skip_header=True)
        else:
            output = csv_rows_first_column_fallback(text, dialect, skip_header=False)

        if not output and not recognized_schema:
            # Last safe fallback: keep non-empty, non-comment raw lines so the user can
            # still inspect them in Import Preview instead of crashing.
            fallback: list[str] = []
            for row_number, line in enumerate(io.StringIO(text), start=1):
                _validate_import_record(row_number, line)
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    fallback.append(stripped)
            return "\n".join(fallback)
        return "\n".join(output)

    def read_xlsx_as_commands(self, path: str) -> str:
        try:
            import openpyxl  # type: ignore
        except Exception as exc:
            raise RuntimeError("Install openpyxl first: pip install openpyxl") from exc
        try:
            xlsx_size = Path(path).stat().st_size
        except OSError:
            xlsx_size = 0
        if xlsx_size > MAX_IMPORT_BYTES:
            raise ValueError(
                f"XLSX is too large to import "
                f"({xlsx_size // (1024 * 1024)} MB > {MAX_IMPORT_BYTES // (1024 * 1024)} MB limit)."
            )
        validate_xlsx_archive(path)
        wb = openpyxl.load_workbook(
            path,
            read_only=True,
            data_only=True,
            keep_links=False,
        )
        try:
            ws = wb.active
            iterator = ws.iter_rows(values_only=True)
            try:
                header_values = next(iterator)
            except StopIteration:
                return ""
            headers = [str(x or "").strip() for x in header_values]
            header_set = {h.lower() for h in headers}
            known = {
                "url", "link", "links", "source", "target", "command", "cmd", "raw",
                "gallery-dl command", "gallery_dl_command", "destination", "folder",
                "output", "output_dir", "output dir", "extra args", "extra_args",
                "args", "options", "enabled", "enable", "active", "run", "tag",
                "group", "label", "notes", "note", "comment", "comments",
            }
            output: list[str] = []
            current_tag = ""
            if header_set & known:
                for row_number, values in enumerate(iterator, start=2):
                    _validate_import_record(row_number, *values)
                    if not values:
                        continue
                    # Preserve falsey spreadsheet values.  In particular,
                    # ``FALSE`` in an enabled column must remain "False" so
                    # build_raw_command_from_columns() can skip that row;
                    # ``value or ''`` incorrectly turned it into an enabled
                    # blank value.
                    row = {
                        headers[i]: ("" if values[i] is None else str(values[i])).strip()
                        for i in range(min(len(headers), len(values)))
                    }
                    cmd = build_raw_command_from_columns(row)
                    if cmd:
                        tag = _row_value(row, "tag", "group", "label")
                        notes = _row_value(row, "notes", "note", "comment", "comments")
                        if tag != current_tag:
                            output.append(f"# {tag}" if tag else "#")
                            current_tag = tag
                        if notes:
                            output.append(f"#@notes {notes}")
                        output.append(cmd)
            else:
                first_header = str(header_values[0] or "").strip() if header_values else ""
                if looks_like_database_entry(first_header):
                    output.append(first_header)
                for row_number, values in enumerate(iterator, start=2):
                    _validate_import_record(row_number, *values)
                    if values:
                        first = str(values[0] or "").strip()
                        if first and (looks_like_database_entry(first) or not first.startswith("#")):
                            output.append(first)
            return "\n".join(output)
        finally:
            try:
                wb.close()
            except Exception:
                pass

    @Slot()
    def paste_clipboard(self) -> None:
        if self.active_workers > 0:
            self.show_compact_message("Paste", "Stop the current download before changing the input.", "info")
            return
        text = QApplication.clipboard().text()
        if not text.strip():
            self.show_compact_message("Clipboard", "Clipboard is empty.", "info")
            return
        # Same guard as file import (stab3.3): a huge accidental copy would
        # otherwise freeze setPlainText + the reparse/autosave pipeline.
        if len(text) > MAX_IMPORT_BYTES:
            self.show_compact_message(
                "Paste",
                f"Clipboard text is too large ({len(text) // (1024 * 1024)} MB > "
                f"{MAX_IMPORT_BYTES // (1024 * 1024)} MB limit).",
                "error",
            )
            return
        current = self.txt_commands.toPlainText().strip()
        combined = (current + "\n" + text.strip()).strip() if current else text.strip()
        try:
            parse_text_database(combined)
        except ValueError as exc:
            self.show_compact_message("Paste", str(exc), "error")
            return
        self.txt_commands.setPlainText(combined)

    @Slot()
    def clear_input(self) -> None:
        if self.active_workers > 0:
            self.show_compact_message("Clear", "Stop the current download before clearing the input.", "info")
            return
        if self.txt_commands.toPlainText().strip():
            res = QMessageBox.question(self, "Clear", "Clear all URL/command input?")
            if res != QMessageBox.Yes:
                return
        self.txt_commands.clear()
        self.current_file = None
        self.lbl_loaded_file.setText("Belum ada file" if self._ui_is_indonesian() else "No file loaded")

    def _stable_table_font(self, bold: bool = False) -> QFont:
        """Return a table font with a valid positive point size.

        Some Qt/platform combinations can emit
        ``QFont::setPointSize: Point size <= 0 (-1)`` when a default
        constructed QFont is pushed into table items. Deriving from the
        QApplication font and normalizing the point size avoids that warning.
        """
        app = QApplication.instance()
        font = QFont(app.font() if app else QFont("Segoe UI" if IS_WINDOWS else "Arial"))
        if font.pointSize() <= 0:
            font.setPointSize(10)
        font.setBold(bold)
        return font

    def status_item(self, status: str) -> QTableWidgetItem:
        item = QTableWidgetItem(status)
        item.setForeground(QColor(STATUS_COLORS.get(status, "#eef3ff")))
        item.setFont(self._stable_table_font(True))
        return item

    def service_item(self, service: str) -> QTableWidgetItem:
        item = QTableWidgetItem(service)
        item.setForeground(QColor(SERVICE_COLORS.get(service.lower(), "#cbd5e1")))
        item.setFont(self._stable_table_font(True))
        return item

    def populate_queue_table(self) -> None:
        self.tbl_queue.setRowCount(len(self.jobs))
        for idx, job in enumerate(self.jobs):
            self.tbl_queue.setItem(idx, self.COL_NUM, QTableWidgetItem(str(idx + 1)))
            self.tbl_queue.setItem(idx, self.COL_SERVICE, self.service_item(job.service))
            self.tbl_queue.setItem(idx, self.COL_ID, QTableWidgetItem(job.ident))
            dest_item = QTableWidgetItem(job.dest)
            dest_item.setToolTip(redact_sensitive_text(job.raw))
            self.tbl_queue.setItem(idx, self.COL_DEST, dest_item)
            tag_item = QTableWidgetItem(job.tag)
            if job.notes:
                tag_item.setToolTip(job.notes)
            self.tbl_queue.setItem(idx, self.COL_TAG, tag_item)
            self.tbl_queue.setItem(idx, self.COL_STATUS, self.status_item("queued"))
            self.tbl_queue.setItem(idx, self.COL_STATS, QTableWidgetItem("-"))
        self.apply_queue_filter()

    def set_queue_status(self, idx: int, status: str) -> None:
        if 0 <= idx < self.tbl_queue.rowCount():
            self.tbl_queue.setItem(idx, self.COL_STATUS, self.status_item(status))

    def set_queue_stats(self, idx: int, dl: int, skip: int, err: int, warn: int) -> None:
        if 0 <= idx < self.tbl_queue.rowCount():
            self.tbl_queue.setItem(idx, self.COL_STATS, QTableWidgetItem(f"{dl}/{skip}/{err}/{warn}"))

    @Slot()
    def apply_queue_filter(self) -> None:
        status_filter = self.combo_filter.currentText().lower() if hasattr(self, "combo_filter") else "all"
        status_filter = {
            "semua": "all",
            "antre": "queued",
            "berjalan": "running",
            "selesai": "done",
            "gagal": "failed",
            "dihentikan": "stopped",
            "dibatalkan": "cancelled",
        }.get(status_filter, status_filter)
        query = self.search_queue.text().strip().lower() if hasattr(self, "search_queue") else ""
        for row, job in enumerate(self.jobs):
            status_item = self.tbl_queue.item(row, self.COL_STATUS)
            status = status_item.text().lower() if status_item else "queued"
            visible = True
            if status_filter != "all" and status != status_filter:
                visible = False
            hay = " ".join([job.raw, job.url, job.service, job.ident, job.dest, job.tag, job.notes]).lower()
            if query and query not in hay:
                visible = False
            self.tbl_queue.setRowHidden(row, not visible)

    def selected_indices(self) -> list[int]:
        rows = {idx.row() for idx in self.tbl_queue.selectedIndexes()}
        return sorted(r for r in rows if 0 <= r < len(self.jobs))

    def show_queue_context_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.addAction("Copy URL", self.copy_selected_url)
        menu.addAction("Copy Command", self.copy_selected_command)
        menu.addAction("Open Destination", self.open_selected_destination)
        menu.addSeparator()
        menu.addAction("Retry Selected", self.retry_selected)
        menu.addAction("Cancel Selected", self.cancel_selected)
        menu.addSeparator()
        menu.addAction("Move Up", lambda: self.move_selected(-1))
        menu.addAction("Move Down", lambda: self.move_selected(1))
        menu.addAction("Move Top", self.move_selected_top)
        menu.addAction("Move Bottom", self.move_selected_bottom)
        menu.addSeparator()
        menu.addAction("Delete Selected", self.delete_selected)
        menu.exec(self.tbl_queue.mapToGlobal(pos))

    def _clear_result_state(self) -> None:
        self.results.clear()
        self.failed_indices.clear()
        self.stopped_indices.clear()
        self.cancelled_indices.clear()
        self.done_indices.clear()
        self.running_indices.clear()
        self.active_job_indices.clear()
        self.total_run = 0
        self.processed_run = 0
        self.started_at = None

    def sync_editor_from_jobs(self) -> None:
        self.txt_commands.blockSignals(True)
        lines: list[str] = []
        last_tag = ""
        for job in self.jobs:
            if job.tag != last_tag:
                # Tags are STICKY in parse_text_database: every row after a
                # "# tag" header inherits it until the next header. When a
                # reorder/delete puts an untagged job after a tagged group, a
                # bare "#" reset line must be emitted, otherwise the untagged
                # job silently inherits the previous tag on reparse.
                lines.append(f"# {job.tag}" if job.tag else "#")
                last_tag = job.tag
            if job.notes:
                lines.append(f"#@notes {' '.join(job.notes.split())}")
            lines.append(job.raw)
        self.txt_commands.setPlainText("\n".join(lines))
        self.txt_commands.blockSignals(False)
        self._clear_result_state()
        self.populate_queue_table()
        self.autosave_session()
        self._update_counts()

    def copy_selected_url(self) -> None:
        idxs = self.selected_indices()
        if not idxs:
            # Do not silently WIPE the clipboard when nothing is selected.
            self.show_compact_message("Copy URL", "Select at least one queue row first.", "info")
            return
        QApplication.clipboard().setText(
            "\n".join(redact_sensitive_text(self.jobs[i].url) for i in idxs)
        )

    def copy_selected_command(self) -> None:
        idxs = self.selected_indices()
        if not idxs:
            self.show_compact_message("Copy Command", "Select at least one queue row first.", "info")
            return
        QApplication.clipboard().setText(
            "\n".join(redact_sensitive_text(self.jobs[i].raw) for i in idxs)
        )

    def open_selected_destination(self) -> None:
        idxs = self.selected_indices()
        if not idxs:
            return
        dest = self.jobs[idxs[0]].dest if self.jobs[idxs[0]].dest != "-" else self.edit_output.text().strip()
        if not dest:
            self.show_compact_message("Open destination", "Destination is unknown.", "info")
            return
        path = Path(dest)
        if not path.is_dir():
            self.show_compact_message("Open destination", f"Folder does not exist:\n{path}", "warning")
            return
        if not open_path(path):
            self.show_compact_message("Open destination", f"Could not open the folder:\n{path}", "warning")

    def move_selected(self, delta: int) -> None:
        if self.active_workers > 0:
            return
        idxs = self.selected_indices()
        if len(idxs) != 1:
            self.show_compact_message("Move", "Select exactly one row.", "info")
            return
        i = idxs[0]
        j = i + delta
        if 0 <= j < len(self.jobs):
            self.jobs[i], self.jobs[j] = self.jobs[j], self.jobs[i]
            self.sync_editor_from_jobs()
            self.tbl_queue.selectRow(j)

    def move_selected_top(self) -> None:
        if self.active_workers > 0:
            return
        idxs = self.selected_indices()
        if len(idxs) != 1:
            return
        job = self.jobs.pop(idxs[0])
        self.jobs.insert(0, job)
        self.sync_editor_from_jobs()
        self.tbl_queue.selectRow(0)

    def move_selected_bottom(self) -> None:
        if self.active_workers > 0:
            return
        idxs = self.selected_indices()
        if len(idxs) != 1:
            return
        job = self.jobs.pop(idxs[0])
        self.jobs.append(job)
        self.sync_editor_from_jobs()
        self.tbl_queue.selectRow(len(self.jobs) - 1)

    def delete_selected(self) -> None:
        if self.active_workers > 0:
            return
        idxs = self.selected_indices()
        if not idxs:
            return
        for i in reversed(idxs):
            self.jobs.pop(i)
        self.sync_editor_from_jobs()
        self._update_counts()

    def remove_exact_duplicates(self) -> None:
        """Preview and remove only jobs with identical effective arguments."""
        if self.active_workers > 0:
            self.show_compact_message(
                "Dedupe",
                "Stop the current download before changing the queue.",
                "info",
            )
            return
        if not self._rebuild_from_text():
            return
        groups = find_exact_duplicate_groups(self.jobs)
        if not groups:
            message = "Tidak ada job duplikat persis." if self._ui_is_indonesian() else "No exact duplicate jobs were found."
            self.show_compact_message("Dedupe", message, "info")
            return

        remove_indices = {index for group in groups for index in group[1:]}
        preview_lines = []
        for group in groups[:10]:
            first = self.jobs[group[0]]
            rows = ", ".join(str(index + 1) for index in group)
            preview_lines.append(
                f"Rows {rows}: {redact_sensitive_text(first.url)[:110]}"
            )
        if len(groups) > 10:
            preview_lines.append(f"... {len(groups) - 10} more group(s)")

        ind = self._ui_is_indonesian()
        prompt = (
            f"Ditemukan {len(remove_indices)} duplikat persis dalam {len(groups)} grup. "
            "Kemunculan pertama akan dipertahankan.\n\n"
            if ind
            else f"Found {len(remove_indices)} exact duplicate(s) in {len(groups)} group(s). "
            "The first occurrence of each job will be kept.\n\n"
        )
        prompt += "\n".join(preview_lines)
        answer = QMessageBox.question(self, "Dedupe", prompt)
        if answer != QMessageBox.Yes:
            return

        self.jobs = [job for index, job in enumerate(self.jobs) if index not in remove_indices]
        self.sync_editor_from_jobs()
        message = (
            f"Menghapus {len(remove_indices)} duplikat persis."
            if ind
            else f"Removed {len(remove_indices)} exact duplicate(s)."
        )
        self.append_log(f"[queue] {message}")
        self.show_compact_message("Dedupe", message, "info")

    def current_run_indices(self) -> list[int]:
        return [i for i, job in enumerate(self.jobs) if job.enabled]

    def validate_targets_before_start(self, indices: list[int]) -> bool:
        suspicious = []
        for i in indices:
            job = self.jobs[i]
            if not job.url.startswith(("http://", "https://")):
                suspicious.append(f"#{i+1}: URL not detected")
            if job.dest not in ("", "-"):
                drive = Path(job.dest).drive
                if IS_WINDOWS and drive and not Path(drive + "\\").exists():
                    suspicious.append(f"#{i+1}: drive may not exist: {drive}")
        duplicate_groups = find_exact_duplicate_groups([self.jobs[i] for i in indices])
        duplicate_count = sum(len(group) - 1 for group in duplicate_groups)
        if duplicate_count:
            suspicious.append(
                f"{duplicate_count} exact duplicate job(s) detected; use Dedupe to remove them"
            )
        if (self.chk_compress.isChecked() or self.chk_convert_webp.isChecked()) and any(
            self.jobs[i].dest in ("", "-") for i in indices
        ):
            suspicious.append(
                "post-processing will be skipped for rows without an explicit -d/--destination (shared-output safety)"
            )
        if suspicious:
            preview = "\n".join(suspicious[:12])
            res = QMessageBox.question(self, "Target warning", f"Some entries look risky:\n\n{preview}\n\nStart anyway?")
            return res == QMessageBox.Yes
        return True

    @Slot()
    def start_download(self) -> None:
        if self.active_workers > 0:
            self.show_compact_message("Start", "A download is already running.", "info")
            return
        if self.delay_timer and self.delay_timer.isActive():
            # Previously a dead end: the info popup gave no way to cancel a
            # scheduled start (Stop is disabled while idle), so a mistaken
            # 30-minute delay could only be aborted by closing the app.
            res = QMessageBox.question(
                self,
                "Delayed start",
                "A delayed start is already scheduled. Cancel it?"
                if not self._ui_is_indonesian()
                else "Start terjadwal sudah aktif. Batalkan?",
            )
            if res == QMessageBox.Yes:
                self.delay_timer.stop()
                self.append_log("[scheduler] delayed start cancelled by user")
            return
        if self.spin_delay.value() > 0:
            minutes = self.spin_delay.value()
            res = QMessageBox.question(self, "Delayed start", f"Start download after {minutes} minute(s)?")
            if res != QMessageBox.Yes:
                return
            self.append_log(f"[scheduler] download will start after {minutes} minute(s)")
            self.delay_timer = QTimer(self)
            self.delay_timer.setSingleShot(True)
            self.delay_timer.timeout.connect(self._start_download_now)
            self.delay_timer.start(minutes * 60 * 1000)
            return
        self._start_download_now()

    def _start_download_now(
        self,
        *,
        run_source: str = "manual",
        account_profile_id: int | None = None,
        use_account_override: bool = False,
    ) -> bool:
        if not self._rebuild_from_text():
            return False
        if not self.jobs:
            self.show_compact_message("Start", "No URL or command loaded. Paste or import at least one row first.", "info")
            return False
        if not command_executable_available(self.gdl_cmd):
            self.show_compact_message(
                "gallery-dl missing",
                "The gallery-dl command is missing or cannot be executed. "
                "Install gallery-dl first, then set the path in System > gallery-dl.",
                "error",
            )
            return False
        indices = self.current_run_indices()
        if not indices:
            self.show_compact_message("Start", "No enabled jobs to run.", "info")
            return False
        if not self.validate_targets_before_start(indices):
            return False
        if self.audit_mode and not self.run_preflight_audit(indices):
            return False
        return self._start_indices(
            indices,
            reset_result_sets=True,
            run_source=run_source,
            account_profile_id=account_profile_id,
            use_account_override=use_account_override,
        )

    def _start_indices(
        self,
        indices: list[int],
        reset_result_sets: bool,
        *,
        run_source: str = "manual",
        account_profile_id: int | None = None,
        use_account_override: bool = False,
    ) -> bool:
        if self.active_workers > 0:
            self.show_compact_message("Queue", "Wait until the current run finishes before starting another run.", "info")
            return False
        indices = [i for i in indices if 0 <= i < len(self.jobs)]
        if not indices:
            self.show_compact_message("Queue", "No valid jobs selected.", "info")
            return False
        if any(REDACTED in self.jobs[i].raw for i in indices):
            self.show_compact_message(
                "Authentication required",
                "This queue contains redacted credentials restored from persistent storage. "
                "Re-enter authentication using Composer, cookies, or an Account profile before starting.",
                "warning",
            )
            return False
        try:
            effective_config_path = self.prepare_active_account_config(
                account_profile_id,
                use_active_default=not use_account_override,
            ) or self.config_path
        except Exception as exc:
            self.show_compact_message("Account profile", str(exc), "error")
            return False
        selected_account_id = (
            account_profile_id if use_account_override else self.active_account_profile_id
        )
        source = str(run_source or "manual")
        try:
            self.current_run_id = self.feature_store.begin_run(self.jobs, indices, source)
        except Exception as exc:
            self.cleanup_run_account_config()
            self.show_compact_message("Run database", f"Could not create the run record:\n{exc}", "error")
            return False
        self.task_queue = queue.Queue()
        for idx in indices:
            self.task_queue.put((idx, self.jobs[idx]))
            self.set_queue_status(idx, "queued")
            self.set_queue_stats(idx, 0, 0, 0, 0)
        self.running_indices = set(indices)
        self.active_job_indices.clear()
        # Reset the worker-facing cancel set for this run under its lock so a
        # stale cancel from a previous run cannot kill a freshly retried index.
        with self._worker_cancel_lock:
            self._worker_cancel.clear()
        self._prepare_result_sets_for_run(indices, reset_result_sets)
        self.stop_event = threading.Event()
        self.pause_event.clear()
        self.total_run = len(indices)
        self.processed_run = 0
        # Track which indices have been counted toward progress in THIS run.
        # Using a per-run set (instead of "idx in self.results", which persists
        # across retries) keeps progress/ETA correct when Retry Failed/Selected
        # reruns indices that already have a stale result from a previous run.
        self._processed_in_run = set()
        self._persistence_failures_reported = set()
        self.started_at = time.time()
        n = min(self.spin_workers.value(), len(indices))
        self.setup_worker_table(n)
        # Dispose the previous run's finished (parentless) worker QThreads via
        # Qt instead of leaving cleanup to Python GC alone.
        for old_worker in getattr(self, "workers", []):
            try:
                if not old_worker.isRunning():
                    old_worker.deleteLater()
            except Exception:
                pass
        self.workers = []
        # Count only threads whose ``start()`` call actually succeeded.  The
        # previous eager ``active_workers = n`` left the UI and recovery DB in
        # a permanent running state when QThread startup failed (for example
        # during OS thread/resource exhaustion).
        self.active_workers = 0
        service_locks: dict[str, threading.BoundedSemaphore] = {}
        for service, policy in self.service_policy.items():
            limit = safe_int(policy.get("workers"), 0, 0, 32)
            if limit > 0:
                service_locks[str(service).lower()] = threading.BoundedSemaphore(limit)
        started_workers: list[DownloadWorker] = []
        try:
            # Construct and connect every worker before starting any of them.
            # Constructor failure can therefore be rolled back without a
            # partially-running batch.
            for wid in range(n):
                worker = DownloadWorker(
                    wid,
                    self.task_queue,
                    self.gdl_cmd or "",
                    effective_config_path,
                    self.edit_output.text().strip(),
                    "none" if selected_account_id else self.combo_cookies.currentText(),
                    self.spin_retries.value(),
                    "",
                    self.pause_event,
                    self.stop_event,
                    self._worker_cancel,
                    self.chk_compress.isChecked(),
                    self.combo_archive.currentText(),
                    self.chk_convert_webp.isChecked(),
                    self.service_policy,
                    service_locks,
                    self._worker_cancel_lock,
                    self,
                )
                worker.log.connect(self.on_worker_log)
                worker.status.connect(self.on_worker_status)
                worker.job_started.connect(self.on_job_started)
                worker.job_done.connect(self.on_job_done)
                worker.finished_all.connect(self.on_worker_finished)
                worker.finished.connect(worker.deleteLater)
                self.workers.append(worker)
            for worker in self.workers:
                worker.start()
                started_workers.append(worker)
                self.active_workers += 1
        except Exception as exc:
            self._rollback_failed_worker_start(indices, started_workers, exc)
            return False
        self.append_log(f"=== Started: {len(indices)} job(s), {n} worker(s) ===")
        self.lbl_ready.setText("BERJALAN" if self._ui_is_indonesian() else "RUNNING")
        self.lbl_ready.setObjectName("good")
        self._set_running_ui(True)
        self._update_counts()
        return True

    def _rollback_failed_worker_start(
        self,
        indices: list[int],
        started_workers: list[DownloadWorker],
        exc: Exception,
    ) -> None:
        """Return a failed QThread startup to a clean, retryable state."""
        message = redact_sensitive_text(f"worker startup failed: {exc}")
        # A worker that started before a later QThread.start() failure can
        # finish while this method pumps Qt events below.  Its terminal signal
        # must update the row, but must not finalize the whole run as a normal
        # completion before we can record ``start_failed``.
        self._rolling_back_worker_start = True
        self.stop_event.set()
        for worker in started_workers:
            worker.stop()
        for worker in started_workers:
            worker.wait(3000)
        for worker in started_workers:
            if worker.isRunning():
                try:
                    worker.terminate_current_process()
                except Exception:
                    pass
                worker.wait(2000)

        # Deliver terminal signals from any worker that did start before
        # finalizing the remaining rows ourselves.  This avoids duplicate
        # history/progress accounting on a partial startup.
        QApplication.processEvents()
        for idx in indices:
            if idx in self._processed_in_run:
                continue
            result = JobResult(
                idx=idx,
                status="failed",
                rc=-1,
                errors=1,
                message=message,
                finished_at=time.time(),
            )
            self.results[idx] = result
            self.done_indices.discard(idx)
            self.stopped_indices.discard(idx)
            self.cancelled_indices.discard(idx)
            self.failed_indices.add(idx)
            self._processed_in_run.add(idx)
            self.set_queue_status(idx, "failed")
            self.set_queue_stats(idx, 0, 0, 1, 0)
            self.write_history(idx, result)
            try:
                self.feature_store.mark_run_item(
                    self.current_run_id,
                    idx,
                    "failed",
                    return_code=-1,
                    message=message,
                )
            except Exception as persistence_exc:
                self._report_persistence_failure(
                    "recording worker startup failure",
                    persistence_exc,
                )

        self.processed_run = min(self.total_run, len(self._processed_in_run))
        if self.current_run_id:
            try:
                self.feature_store.finish_run(self.current_run_id, "start_failed")
            except Exception as persistence_exc:
                self._report_persistence_failure(
                    "finalizing worker startup failure",
                    persistence_exc,
                )
        self.current_run_id = None
        self.active_workers = 0
        self.running_indices.clear()
        self.active_job_indices.clear()
        self._discard_pending_queue_items()
        self.cleanup_run_account_config()
        for worker in self.workers:
            if not worker.isRunning():
                worker.deleteLater()
        self._rolling_back_worker_start = False
        self._set_running_ui(False)
        self._update_counts()
        self.append_log(f"[ERROR] {message}")
        self.show_compact_message("Start failed", message, "error")

    def _prepare_result_sets_for_run(
        self,
        indices: list[int],
        reset_result_sets: bool,
    ) -> None:
        """Remove stale terminal state before new workers can observe a rerun."""
        if reset_result_sets:
            self.results.clear()
            self.failed_indices.clear()
            self.stopped_indices.clear()
            self.cancelled_indices.clear()
            self.done_indices.clear()
        else:
            # Retry Selected also permits rows that previously succeeded. A
            # stale done marker made Cancel Selected reject that active rerun
            # and made the dashboard count it as complete while it was running.
            self.done_indices.difference_update(indices)
            self.failed_indices.difference_update(indices)
            self.stopped_indices.difference_update(indices)
            self.cancelled_indices.difference_update(indices)

    def _current_run_has_errors(self) -> bool:
        """Return failure state scoped to items processed by this run only."""
        current = set(getattr(self, "_processed_in_run", set()))
        terminal_errors = (
            set(self.failed_indices)
            | set(self.stopped_indices)
            | set(self.cancelled_indices)
        )
        return bool(current & terminal_errors)

    def _discard_pending_queue_items(self) -> None:
        """Release queue entries left behind after a stopped run.

        Workers exit their outer loop as soon as ``stop_event`` is set, so
        jobs they never pulled remain in ``Queue`` and keep its unfinished-task
        counter nonzero. The GUI never joins this queue, but retaining up to
        20,000 commands until the next run wastes memory and leaves a broken
        lifecycle invariant. This is called only after every worker emitted
        ``finished_all``, when no consumer can race the drain.
        """
        task_queue = getattr(self, "task_queue", None)
        if task_queue is None:
            return
        while True:
            try:
                task_queue.get_nowait()
            except queue.Empty:
                break
            task_queue.task_done()

    def _report_persistence_failure(self, operation: str, exc: Exception) -> None:
        """Surface database failures once per operation during a run."""
        if not hasattr(self, "_persistence_failures_reported"):
            self._persistence_failures_reported: set[str] = set()
        key = str(operation or "database write")
        if key in self._persistence_failures_reported:
            return
        self._persistence_failures_reported.add(key)
        self.append_log(
            f"[WARNING] persistence failed during {key}: "
            f"{redact_sensitive_text(str(exc))}"
        )

    def setup_worker_table(self, n: int) -> None:
        self.tbl_workers.setRowCount(n)
        for wid in range(n):
            self.tbl_workers.setItem(wid, 0, QTableWidgetItem(f"W{wid+1}"))
            self.tbl_workers.setItem(wid, 1, self.status_item("queued"))
            self.tbl_workers.setItem(wid, 2, QTableWidgetItem(""))

    @Slot()
    def toggle_pause(self) -> None:
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.btn_pause.setText("Jeda" if self._ui_is_indonesian() else "Pause")
            self.append_log("[queue] resumed")
        else:
            self.pause_event.set()
            self.btn_pause.setText("Lanjut" if self._ui_is_indonesian() else "Resume")
            self.append_log("[queue] paused after active jobs")

    @Slot()
    def stop_download(self) -> None:
        if self.active_workers <= 0:
            return
        self.stop_event.set()
        for worker in self.workers:
            worker.stop()
        # Mark only not-yet-started queue items as stopped. Active jobs still emit
        # their own final result, which prevents double-counted progress/history.
        # NOTE: this must key on the per-run processed set, NOT ``self.results``.
        # ``self.results`` persists across Retry Failed/Selected runs, so a
        # retried-but-not-yet-started index still has a stale result entry and
        # would be silently skipped here, leaving its row stuck on "queued" and
        # the progress bar short of total_run.
        pending = set(self.running_indices) - set(self._processed_in_run) - set(self.active_job_indices)
        for idx in pending:
            was_cancelled = idx in self.cancelled_indices
            final_status = "cancelled" if was_cancelled else "stopped"
            final_message = "cancelled before start" if was_cancelled else "stopped before start"
            if was_cancelled:
                self.stopped_indices.discard(idx)
            else:
                self.cancelled_indices.discard(idx)
                self.stopped_indices.add(idx)
            self.set_queue_status(idx, final_status)
            result = JobResult(idx=idx, status=final_status, rc=-1, message=final_message)
            self.results[idx] = result
            if idx not in self._processed_in_run:
                self._processed_in_run.add(idx)
                self.write_history(idx, result)
            try:
                self.feature_store.mark_run_item(
                    self.current_run_id, idx, final_status, return_code=-1,
                    message=final_message,
                )
            except Exception as exc:
                self._report_persistence_failure("stopping queued item", exc)
        self.processed_run = min(self.total_run, len(self._processed_in_run))
        self.append_log("=== Stop requested ===")
        self.btn_stop.setEnabled(False)
        self._update_counts()

    @Slot()
    def retry_failed(self) -> None:
        candidates = sorted(self.failed_indices | self.stopped_indices | self.cancelled_indices)
        if not candidates:
            self.show_compact_message("Retry", "No failed/stopped/cancelled jobs to retry.", "info")
            return
        idxs: list[int] = []
        skipped: list[str] = []
        for idx in candidates:
            result = self.results.get(idx)
            err_type = classify_error(result.message if result else "")
            if self.retry_strategy.get(err_type, True):
                idxs.append(idx)
            else:
                skipped.append(f"#{idx+1} skipped by retry policy: {err_type}")
        if skipped:
            self.append_log("[retry-policy] " + " | ".join(skipped[:10]))
        if not idxs:
            self.show_compact_message("Retry", "Retry policy skipped all failed jobs. Open the main Queue tab > Retry Strategy to change it.", "warning")
            return
        self._start_indices(idxs, reset_result_sets=False)

    def retry_selected(self) -> None:
        if self.active_workers > 0:
            self.show_compact_message("Retry", "Wait until the current run finishes before retrying selected rows.", "info")
            return
        idxs = self.selected_indices()
        if not idxs:
            return
        self._start_indices(idxs, reset_result_sets=False)

    @Slot()
    def cancel_selected(self) -> None:
        if self.active_workers <= 0:
            self.show_compact_message("Cancel", "No download is currently running.", "info")
            return
        idxs = self.selected_indices()
        if not idxs:
            self.show_compact_message("Cancel", "Select one or more queue rows first.", "info")
            return
        valid: list[int] = []
        for idx in idxs:
            if idx not in self.running_indices:
                continue
            if idx in self.done_indices or idx in self.failed_indices or idx in self.stopped_indices or idx in self.cancelled_indices:
                continue
            valid.append(idx)
            self.cancelled_indices.add(idx)
            with self._worker_cancel_lock:
                self._worker_cancel.add(idx)
            self.set_queue_status(idx, "cancelled")
            for worker in self.workers:
                worker.cancel_job(idx)
        if not valid:
            self.show_compact_message("Cancel", "Selected rows are already completed, stopped, or already cancelled.", "info")
            return
        self.append_log(f"[queue] cancel requested for row(s): {', '.join(str(i+1) for i in valid)}")
        self._update_counts()

    @Slot(int, str)
    def on_worker_log(self, worker_id: int, text: str) -> None:
        self.append_log(f"[W{worker_id+1}] {text}")

    @Slot(int, int, str)
    def on_job_started(self, worker_id: int, idx: int, url: str) -> None:
        self.active_job_indices.add(idx)
        self.set_queue_status(idx, "running")
        try:
            self.feature_store.mark_run_item(self.current_run_id, idx, "running")
        except Exception as exc:
            self._report_persistence_failure("marking item running", exc)

    @Slot(int, str, str)
    def on_worker_status(self, worker_id: int, status: str, detail: str) -> None:
        if 0 <= worker_id < self.tbl_workers.rowCount():
            self.tbl_workers.setItem(worker_id, 1, self.status_item(status))
            display = detail if len(detail) <= 120 else detail[:117] + "..."
            self.tbl_workers.setItem(worker_id, 2, QTableWidgetItem(display))

    @Slot(int, int, str, int, int, int, int, int, str)
    def on_job_done(self, worker_id: int, idx: int, status: str, dl: int, skip: int, err: int, warn: int, rc: int, message: str) -> None:
        message = redact_sensitive_text(message)
        result = JobResult(idx=idx, status=status, rc=rc, downloaded=dl, skipped=skip, errors=err, warnings=warn, message=message, finished_at=time.time())
        counted_this_run = idx in getattr(self, "_processed_in_run", set())
        self.results[idx] = result
        self.active_job_indices.discard(idx)

        # Keep result sets mutually exclusive. This avoids stale cancelled/stopped
        # flags after Retry Selected/Retry Failed.
        self.done_indices.discard(idx)
        self.failed_indices.discard(idx)
        self.stopped_indices.discard(idx)
        self.cancelled_indices.discard(idx)
        if status == "done":
            self.done_indices.add(idx)
        elif status == "failed":
            self.failed_indices.add(idx)
        elif status == "stopped":
            self.stopped_indices.add(idx)
        elif status == "cancelled":
            self.cancelled_indices.add(idx)

        self.set_queue_status(idx, status)
        self.set_queue_stats(idx, dl, skip, err, warn)
        if not counted_this_run:
            self._processed_in_run.add(idx)
            self.processed_run = min(self.total_run, self.processed_run + 1)
            self.write_history(idx, result)
        try:
            self.feature_store.mark_run_item(
                self.current_run_id, idx, status, downloaded=dl, skipped=skip,
                return_code=rc, message=message,
            )
            if 0 <= idx < len(self.jobs):
                self.feature_store.update_library_result(self.jobs[idx].url, status, dl)
        except Exception as exc:
            self._report_persistence_failure("saving item result", exc)
        self._update_counts()

    @Slot(int)
    def on_worker_finished(self, worker_id: int) -> None:
        self.active_workers = max(0, self.active_workers - 1)
        if 0 <= worker_id < self.tbl_workers.rowCount():
            self.tbl_workers.setItem(worker_id, 1, self.status_item("idle"))
            self.tbl_workers.setItem(worker_id, 2, QTableWidgetItem(""))
        if getattr(self, "_rolling_back_worker_start", False):
            return
        if self.active_workers <= 0:
            self.active_workers = 0
            self._discard_pending_queue_items()
            self.pause_event.clear()
            self.btn_pause.setText("Jeda" if self._ui_is_indonesian() else "Pause")
            self.running_indices.clear()
            self.active_job_indices.clear()
            self._refresh_env()
            self._set_running_ui(False)
            self.append_log("=== All workers finished ===")
            run_status = (
                "finished_with_errors"
                if self._current_run_has_errors()
                else "finished"
            )
            try:
                self.feature_store.finish_run(self.current_run_id, run_status)
            except Exception as exc:
                self._report_persistence_failure("finalizing run", exc)
            self.current_run_id = None
            self.cleanup_run_account_config()
            # Scheduled snapshots temporarily replace the visible queue so the
            # normal worker pipeline can render and track them. Put the user's
            # previous manual queue back before autosave persists the session.
            self._restore_queue_after_scheduled_run()
            self.autosave_session()
            self.notify_finished()
            self._update_counts()


__all__ = [
    "MAX_XLSX_ARCHIVE_MEMBERS",
    "MAX_XLSX_UNCOMPRESSED_BYTES",
    "QueueControllerMixin",
    "validate_xlsx_archive",
]
