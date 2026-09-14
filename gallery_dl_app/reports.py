from __future__ import annotations

import csv
import heapq
import html
import json
import os
import platform
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStyle,
    QVBoxLayout,
)

from .core import (
    APP_DIR,
    APP_NAME,
    AUTOSAVE_FILE,
    BACKUP_DIR,
    IS_WINDOWS,
    app_data_dir,
    atomic_write_text,
    classify_error,
    dependency_status,
    human_size,
    open_path,
    read_text_safely,
    redact_sensitive_database_text,
    redact_sensitive_text,
    safe_expand_path,
    sanitize_spreadsheet_cell,
    timestamp_slug,
    unique_path,
)


def app_data_backup_members(
    app_dir: str | Path,
    backup_dir: str | Path,
    target: str | Path,
) -> list[Path]:
    """Return safe app-data files without nesting previous backups."""

    return [file for file, _relative in _app_data_backup_entries(app_dir, backup_dir, target)]


def _app_data_backup_entries(
    app_dir: str | Path,
    backup_dir: str | Path,
    target: str | Path,
) -> list[tuple[Path, Path]]:
    """Return source files paired with stable archive-relative paths.

    Windows runners can expose the same temporary directory through both an
    8.3 alias (``RUNNER~1``) and its long name (``runneradmin``). Tracking the
    relative path while walking avoids fragile string-based ``relative_to``
    comparisons between those equivalent spellings.
    """

    app_input = Path(os.path.abspath(Path(app_dir).expanduser()))
    backup_input = Path(os.path.abspath(Path(backup_dir).expanduser()))
    target_input = Path(os.path.abspath(Path(target).expanduser()))
    try:
        backup_relative = backup_input.relative_to(app_input)
    except ValueError:
        backup_relative = None
    try:
        target_relative = target_input.relative_to(app_input)
    except ValueError:
        target_relative = None

    entries: list[tuple[Path, Path]] = []
    pending: list[tuple[Path, Path]] = [(app_input, Path())]
    while pending:
        directory, relative_directory = pending.pop()
        try:
            children = list(os.scandir(directory))
        except OSError:
            continue
        for child in children:
            relative = relative_directory / child.name
            try:
                if child.is_symlink():
                    continue
                if child.is_dir(follow_symlinks=False):
                    pending.append((Path(child.path), relative))
                    continue
                if not child.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if target_relative is not None and relative == target_relative:
                continue
            if backup_relative is not None and (
                relative == backup_relative or relative.is_relative_to(backup_relative)
            ):
                continue
            entries.append((Path(child.path), relative))
    return entries


def write_app_data_backup(
    app_dir: str | Path,
    backup_dir: str | Path,
    target: str | Path,
    stop_event: threading.Event | None = None,
) -> None:
    """Create a ZIP atomically, preserving an existing target on failure."""
    target_path = Path(target).expanduser()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    entries = _app_data_backup_entries(app_dir, backup_dir, target_path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target_path.name}.",
        suffix=".tmp",
        dir=target_path.parent,
    )
    os.close(descriptor)
    tmp = Path(temporary_name)
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file, relative in entries:
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError("backup cancelled")
                zf.write(file, relative)
        os.replace(tmp, target_path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def scan_output_tree(
    folder: str | Path,
    *,
    scan_limit: int = 200_000,
    stop_event: threading.Event | None = None,
) -> dict[str, object]:
    """Scan an output tree with bounded memory and cooperative cancellation."""
    root = Path(folder)
    total = files = dirs = scanned = 0
    capped = False
    largest_heap: list[tuple[int, str, Path]] = []
    for item in root.rglob("*"):
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("scan cancelled")
        scanned += 1
        if scanned > scan_limit:
            capped = True
            break
        try:
            if item.is_symlink():
                continue
            if item.is_dir():
                dirs += 1
            elif item.is_file():
                files += 1
                size = item.stat().st_size
                total += size
                entry = (size, str(item), item)
                if len(largest_heap) < 10:
                    heapq.heappush(largest_heap, entry)
                elif entry > largest_heap[0]:
                    heapq.heapreplace(largest_heap, entry)
        except OSError:
            continue
    return {
        "folder": root,
        "files": files,
        "dirs": dirs,
        "total": total,
        "scanned": min(scanned, scan_limit),
        "capped": capped,
        "scan_limit": scan_limit,
        "largest": [
            (size, path)
            for size, _name, path in sorted(largest_heap, reverse=True)
        ],
    }


class CancellableFileTask(QThread):
    """Run one filesystem-heavy callable without blocking the Qt event loop."""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, task: Callable[[threading.Event], object], parent=None):
        super().__init__(parent)
        self._task = task
        self._stop_event = threading.Event()

    def run(self) -> None:
        try:
            result = self._task(self._stop_event)
        except InterruptedError:
            return
        except Exception as exc:
            self.failed.emit(redact_sensitive_text(str(exc)))
            return
        if not self._stop_event.is_set():
            self.completed.emit(result)

    def stop(self) -> None:
        self._stop_event.set()


def is_restorable_autosave(data: object) -> bool:
    """Accept generated autosaves even when the queue text is intentionally empty."""
    return isinstance(data, dict) and ("schema" in data or "commands" in data)


class ReportsMixin:
    def _load_autosave_silently(self) -> None:
        if not AUTOSAVE_FILE.exists():
            return
        if not AUTOSAVE_FILE.is_file():
            # Never quarantine/move a directory that merely occupies the
            # autosave filename. It is not a corrupt autosave file and may
            # contain unrelated user data.
            self.append_log(f"[autosave] ignored path that is not a file: {AUTOSAVE_FILE}")
            return
        # Parse and apply are separated on purpose. A corrupt FILE should be
        # quarantined; a failure inside apply_session_data (e.g. one bad field
        # or a transient widget issue) must NOT move the autosave aside — that
        # silently threw away the user's saved queue even though the JSON on
        # disk was perfectly fine.
        try:
            data = json.loads(read_text_safely(AUTOSAVE_FILE))
        except Exception as exc:
            try:
                bad = unique_path(AUTOSAVE_FILE.with_name(AUTOSAVE_FILE.name + f".bad_{timestamp_slug()}"))
                AUTOSAVE_FILE.replace(bad)
                self.append_log(f"[autosave] ignored corrupt autosave and moved it to: {bad} ({exc})")
            except Exception:
                pass
            return
        if not is_restorable_autosave(data):
            return
        try:
            self.apply_session_data(data)
            self.lbl_loaded_file.setText("Autosave restored")
        except Exception as exc:
            self.append_log(f"[autosave] restore failed (file kept intact): {exc}")

    def _safe_write_file(self, path: str, text: str, title: str) -> bool:
        """Write text to a user-chosen path with clear error reporting.

        Several export actions previously wrote files with no error handling, so a
        read-only or locked destination raised an uncaught exception inside the Qt
        slot. This centralizes the write and surfaces a friendly message instead.
        """
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            # Keep an existing export intact if the process is interrupted or
            # the destination runs out of space halfway through the write.
            atomic_write_text(p, text, encoding="utf-8")
            self.append_log(f"[export] saved: {path}")
            return True
        except Exception as exc:
            self.show_compact_message(f"{title} failed", f"Could not write file:\n{path}\n\n{exc}", "error")
            return False

    def export_failed(self) -> None:
        idxs = [i for i in sorted(self.failed_indices | self.stopped_indices | self.cancelled_indices) if 0 <= i < len(self.jobs)]
        if not idxs:
            self.show_compact_message("Export failed", "No failed/stopped/cancelled jobs.", "info")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export failed commands", str(APP_DIR / "failed_links.txt"), "Text (*.txt)")
        if path:
            self._safe_write_file(
                path,
                redact_sensitive_database_text("\n".join(self.jobs[i].raw for i in idxs)),
                "Export failed",
            )

    def export_logs(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export logs", str(APP_DIR / "combined_log.txt"), "Text (*.txt)")
        if path:
            self._safe_write_file(
                path,
                redact_sensitive_database_text("\n".join(self.all_log_lines)),
                "Export logs",
            )

    def export_txt(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export TXT database", str(APP_DIR / "Link Database.txt"), "Text (*.txt)")
        if path:
            self._safe_write_file(
                path,
                redact_sensitive_database_text(self.txt_commands.toPlainText()),
                "Export TXT",
            )

    def error_report(self) -> None:
        if not self.results:
            self.show_compact_message("Error report", "No results yet.", "info")
            return
        rows = []
        for idx, result in sorted(self.results.items()):
            if result.status == "done":
                continue
            # Results can outlive a shrunken job list (same IndexError class as
            # the stab3.2 write_history fix); skip stale indices safely.
            if not (0 <= idx < len(self.jobs)):
                continue
            job = self.jobs[idx]
            safe_url = redact_sensitive_text(job.url)
            safe_message = redact_sensitive_text(result.message)
            rows.append(
                f"<tr><td>{idx+1}</td><td>{html.escape(result.status)}</td><td>{html.escape(classify_error(result.message))}</td>"
                f"<td>{html.escape(safe_url)}</td><td><pre>{html.escape(safe_message)}</pre></td></tr>"
            )
        if not rows:
            self.show_compact_message("Error report", "No errors to report.", "info")
            return
        doc = "<html><body><h1>Gallery-DL Error Report</h1><table border='1' cellspacing='0' cellpadding='6'>" \
              "<tr><th>#</th><th>Status</th><th>Type</th><th>URL</th><th>Message</th></tr>" + "\n".join(rows) + "</table></body></html>"
        path, _ = QFileDialog.getSaveFileName(self, "Save HTML error report", str(APP_DIR / "error_report.html"), "HTML (*.html)")
        if path:
            self._safe_write_file(path, doc, "Error report")

    def diagnostic_data(self) -> dict:
        out_path = safe_expand_path(self.edit_output.text() or ".")
        disk = {}
        try:
            if out_path.exists() and not out_path.is_dir():
                raise NotADirectoryError(f"Output path is not a directory: {out_path}")
            probe = out_path
            while probe != probe.parent and not probe.exists():
                probe = probe.parent
            if not probe.is_dir():
                raise NotADirectoryError(f"No existing parent directory for: {out_path}")
            usage = shutil.disk_usage(probe)
            disk = {
                "path": str(out_path),
                "exists": out_path.is_dir(),
                "capacity_checked_at": str(probe),
                "free": usage.free,
                "total": usage.total,
                "used": usage.used,
                "ok": True,
            }
        except Exception as exc:
            disk = {"path": str(out_path), "ok": False, "error": str(exc)}

        deps = dependency_status()
        config_state = "not detected"
        if self.config_path:
            cp = safe_expand_path(self.config_path)
            config_state = "file" if cp.is_file() else ("invalid path" if cp.exists() else "missing")

        return {
            "app": APP_NAME,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "platform": platform.platform(),
            "python": sys.version.replace("\n", " "),
            "gallery_dl_command": redact_sensitive_text(self.gdl_cmd or "not found"),
            "config_path": self.config_path or "not detected",
            "config_state": config_state,
            "output": disk,
            "jobs": len(self.jobs),
            "done": len(self.done_indices),
            "failed": len(self.failed_indices),
            "stopped": len(self.stopped_indices),
            "cancelled": len(self.cancelled_indices),
            "workers": self.spin_workers.value(),
            "cookies_browser": self.combo_cookies.currentText(),
            "retries": self.spin_retries.value(),
            "audit_mode": self.audit_mode,
            "compact_mode": self.compact_mode,
            "dependencies": deps,
            "log_summary": self.analyze_log_lines(),
        }

    def diagnostic_html(self) -> str:
        data = self.diagnostic_data()
        def esc(value) -> str:
            return html.escape(str(value))
        dep_rows = "".join(f"<tr><td>{esc(k)}</td><td>{'OK' if v else 'MISSING'}</td></tr>" for k, v in data["dependencies"].items())
        log_rows = "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in data["log_summary"].items())
        result_rows = "".join(
            f"<tr><td>{i+1}</td><td>{esc(self.jobs[i].service)}</td><td>{esc(self.jobs[i].ident)}</td><td>{esc(r.status)}</td><td>{esc(classify_error(r.message))}</td><td><pre>{esc(redact_sensitive_text(r.message))}</pre></td></tr>"
            for i, r in sorted(self.results.items()) if 0 <= i < len(self.jobs)
        ) or "<tr><td colspan='6'>No results yet.</td></tr>"
        return f"""<!doctype html>
<html><head><meta charset='utf-8'><title>GdlScrape Audit Report</title>
<style>body{{font-family:Arial,sans-serif;margin:24px;line-height:1.45}}table{{border-collapse:collapse;width:100%;margin:12px 0}}td,th{{border:1px solid #ccc;padding:6px;vertical-align:top}}pre{{white-space:pre-wrap;max-width:720px}}.ok{{color:#198754}}.warn{{color:#b26a00}}</style></head>
<body><h1>GdlScrape Audit Report</h1>
<p><b>Created:</b> {esc(data['time'])}</p>
<h2>Environment</h2><table>
<tr><th>Item</th><th>Value</th></tr>
<tr><td>Platform</td><td>{esc(data['platform'])}</td></tr>
<tr><td>Python</td><td>{esc(data['python'])}</td></tr>
<tr><td>gallery-dl command</td><td>{esc(data['gallery_dl_command'])}</td></tr>
<tr><td>Config</td><td>{esc(data['config_path'])} ({esc(data['config_state'])})</td></tr>
<tr><td>GUI default output</td><td>{esc(data['output'])}</td></tr>
</table>
<h2>Queue Summary</h2><table><tr><th>Jobs</th><th>Done</th><th>Failed</th><th>Stopped</th><th>Cancelled</th></tr>
<tr><td>{data['jobs']}</td><td>{data['done']}</td><td>{data['failed']}</td><td>{data['stopped']}</td><td>{data['cancelled']}</td></tr></table>
<h2>Dependencies</h2><table><tr><th>Dependency</th><th>Status</th></tr>{dep_rows}</table>
<h2>Log Analyzer</h2><table><tr><th>Type</th><th>Count</th></tr>{log_rows}</table>
<h2>Results</h2><table><tr><th>#</th><th>Service</th><th>ID</th><th>Status</th><th>Error Type</th><th>Message</th></tr>{result_rows}</table>
</body></html>"""

    def export_audit_report(self) -> None:
        app_data_dir()
        default = APP_DIR / f"audit_report_{timestamp_slug()}.html"
        path, _ = QFileDialog.getSaveFileName(self, "Export audit HTML", str(default), "HTML (*.html)")
        if path:
            # Route through the guarded writer: a locked/read-only target used
            # to raise an uncaught exception inside this Qt slot (same class as
            # the stab3.4 error-summary CSV fix).
            if self._safe_write_file(path, self.diagnostic_html(), "Export audit HTML"):
                self.append_log(f"[audit] exported: {path}")

    def analyze_log_lines(self) -> dict[str, int]:
        counts = {"network": 0, "auth/cookies": 0, "not-found": 0, "config": 0, "path": 0, "rate-limit": 0, "unknown": 0, "warnings": 0, "errors": 0}
        for line in self.all_log_lines:
            low = line.lower()
            if any(x in low for x in ("warning", "warn", "⚠")):
                counts["warnings"] += 1
            if any(x in low for x in ("error", "failed", "traceback", "exception", "❌")):
                counts["errors"] += 1
                counts[classify_error(line)] += 1
        return counts

    def show_log_analyzer(self) -> None:
        counts = self.analyze_log_lines()
        lines = [f"{k}: {v}" for k, v in counts.items()]
        self.show_scroll_message("Log Analyzer", "\n".join(lines), "info")

    def export_error_summary_csv(self) -> None:
        if not self.results:
            self.show_compact_message("Export Error CSV", "No results yet.", "info")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export error summary CSV", str(APP_DIR / f"error_summary_{timestamp_slug()}.csv"), "CSV (*.csv)")
        if not path:
            return
        import io
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["row", "status", "error_type", "service", "id", "url", "return_code", "message"])
        for idx, result in sorted(self.results.items()):
            if result.status == "done" or not (0 <= idx < len(self.jobs)):
                continue
            job = self.jobs[idx]
            writer.writerow([
                idx + 1,
                result.status,
                classify_error(result.message),
                sanitize_spreadsheet_cell(job.service),
                sanitize_spreadsheet_cell(job.ident),
                sanitize_spreadsheet_cell(redact_sensitive_text(job.url)),
                result.rc,
                sanitize_spreadsheet_cell(redact_sensitive_text(result.message)),
            ])
        try:
            atomic_write_text(path, buf.getvalue(), encoding="utf-8-sig", newline="")
        except Exception as exc:
            self.show_compact_message("Export Error CSV failed", f"Could not write file:\n{path}\n\n{exc}", "error")
            return
        self.append_log(f"[report] error CSV exported: {path}")

    def toggle_audit_mode(self) -> None:
        self.audit_mode = not self.audit_mode
        state = ("ON" if self.audit_mode else "OFF") if not self._ui_is_indonesian() else ("AKTIF" if self.audit_mode else "MATI")
        self.show_compact_message("Audit Mode", f"Audit Mode: {state}", "info")
        self.append_log(f"[audit] audit mode {'enabled' if self.audit_mode else 'disabled'}")
        self.autosave_session()

    def run_preflight_audit(self, indices: list[int]) -> bool:
        issues: list[str] = []
        # Check the drives the run will actually WRITE to. Most rows carry
        # their own -d on another drive (e.g. F:\Rips\...), so checking only
        # the GUI default output often examined the wrong disk entirely.
        roots: dict[str, Path] = {}
        out = safe_expand_path(self.edit_output.text() or ".")
        roots[str(out)] = out
        for i in indices:
            dest = self.jobs[i].dest if 0 <= i < len(self.jobs) else "-"
            if dest not in ("", "-"):
                p = safe_expand_path(dest)
                key = (p.drive or p.anchor or str(p)).lower() if IS_WINDOWS else str(p)
                roots.setdefault(key, p)
            if len(roots) >= 8:
                break
        try:
            out.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            issues.append(f"Output folder problem: {exc}")
        for p in roots.values():
            if p.exists() and not p.is_dir():
                issues.append(f"Destination is not a directory: {p}")
                continue
            probe = p
            while probe != probe.parent and not probe.exists():
                probe = probe.parent
            try:
                if not probe.is_dir():
                    raise NotADirectoryError(probe)
                free = shutil.disk_usage(probe).free
                if free < 1024 ** 3:
                    issues.append(f"Low disk space for {p}: {human_size(free)} free")
            except Exception:
                issues.append(f"Destination not reachable: {p}")
        if not self.gdl_cmd:
            issues.append("gallery-dl command is missing")
        if len(indices) > 1000 and self.spin_workers.value() > 10:
            issues.append("Large queue with more than 10 workers may hit rate limits")
        if issues:
            res = QMessageBox.question(self, "Audit Mode Preflight", "Issues found:\n\n" + "\n".join(issues[:20]) + "\n\nStart anyway?")
            return res == QMessageBox.Yes
        self.append_log("[audit] preflight passed")
        return True

    def show_dependency_helper(self) -> None:
        deps = dependency_status()
        lines = ["Dependency status:"]
        lines.extend(f"- {'OK' if ok else 'MISSING'}: {name}" for name, ok in deps.items())
        lines.extend([
            "",
            "Recommended install commands:",
            "pip install PySide6 gallery-dl",
            "pip install openpyxl Pillow",
            "",
            "Note: 7z/7za/7zz is needed only for 7z/tar style external compression paths. ZIP works with Python's standard library.",
        ])
        self.show_scroll_message("Dependency Helper", "\n".join(lines), "info")

    def open_gallery_dl_archive_help(self) -> None:
        """Explain gallery-dl archive clearly.

        The GUI no longer creates a separate SQLite queue database because gallery-dl
        already has an archive mechanism for downloaded IDs. GUI session files remain
        JSON because they store the visible queue and GUI settings, not media IDs.
        """
        text = """gallery-dl Archive Help

What gallery-dl already provides
- gallery-dl can use an archive database to remember downloaded media IDs.
- It is usually enabled through --download-archive FILE or through extractor.*.archive in config.
- When an ID is already in that archive, gallery-dl can skip it on the next run.
- The archive file is an SQLite database created and managed by gallery-dl.

What this GUI stores
- GUI sessions/autosave store the visible queue, settings, output field, cookies browser, worker count, and current input list.
- GUI sessions are not a replacement for gallery-dl's download archive.
- The GUI should not create another SQLite database for downloaded media because that duplicates gallery-dl's job and can confuse users.

Recommended setup
1) Use gallery-dl archive in your config or command when you want duplicate-download protection.
2) Use GUI Save Session when you want to reopen the same queue later.
3) Use Export Failed / Resume Unfinished for workflow recovery.

Command example
    gallery-dl --download-archive "D:\\Rips\\archive.sqlite3" "https://example.com/user/123"

Config example
{
  "extractor": {
    "archive": "~/gallery-dl/archive.sqlite3"
  }
}

Important
- Do not manually create a blank text file and rename it to .sqlite3.
- Let gallery-dl create the archive file automatically, or use a real SQLite tool only if you know what you are doing.
- Keep the archive path stable across projects if you want global duplicate protection.
"""
        dlg = QDialog(self)
        dlg.setWindowTitle("gallery-dl Archive Help")
        dlg.resize(760, 560)
        lay = QVBoxLayout(dlg)
        title = QLabel("gallery-dl Archive Help")
        title.setStyleSheet("font-size: 18px; font-weight: 900;")
        lay.addWidget(title)
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setPlainText(text)
        lay.addWidget(box, 1)
        close = QPushButton("Close")
        close.clicked.connect(dlg.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        dlg.exec()

    def export_archive_config_example(self) -> None:
        app_data_dir()
        default_path = APP_DIR / "gallery_dl_archive_config_example.json"
        path, _ = QFileDialog.getSaveFileName(self, "Export gallery-dl archive config example", str(default_path), "JSON (*.json);;All files (*.*)")
        if not path:
            return
        config = {
            "extractor": {
                "archive": "~/gallery-dl/archive.sqlite3"
            }
        }
        try:
            atomic_write_text(path, json.dumps(config, indent=2) + "\n", encoding="utf-8")
            QMessageBox.information(
                self,
                "Archive Config Example",
                "Archive config example exported.\n\nThis is for gallery-dl's own archive feature, not a separate GUI database."
                f"\n\n{path}",
            )
        except Exception as exc:
            self.show_compact_message("Archive config export failed", str(exc), "error")

    def open_app_data_dir(self) -> None:
        app_data_dir()
        if not open_path(APP_DIR):
            QApplication.clipboard().setText(str(APP_DIR))
            self.show_compact_message(
                "Open App Data Folder",
                f"Could not open the folder. Path copied to clipboard:\n{APP_DIR}",
                "warning",
            )

    def backup_config_file(self) -> None:
        if not self.config_path:
            self.show_compact_message("Backup Config", "No config path is selected/detected.", "warning")
            return
        src = safe_expand_path(self.config_path)
        if not src.is_file():
            self.show_compact_message("Backup Config", f"Config is not a file:\n{src}", "warning")
            return
        app_data_dir()
        dst = unique_path(BACKUP_DIR / f"config_backup_{timestamp_slug()}{src.suffix or '.conf'}")
        try:
            shutil.copy2(src, dst)
            self.show_compact_message("Backup Config", f"Config backed up to:\n{dst}", "info")
        except Exception as exc:
            self.show_compact_message("Backup Config failed", str(exc), "error")

    def backup_app_data(self) -> None:
        if self.active_workers > 0:
            self.show_compact_message(
                "Backup App Data",
                "Wait for the current download to finish before backing up app data.",
                "info",
            )
            return
        existing_worker = getattr(self, "_backup_file_worker", None)
        if existing_worker is not None and existing_worker.isRunning():
            self.show_compact_message("Backup App Data", "A backup is already running.", "info")
            return
        app_data_dir()
        default = BACKUP_DIR / f"app_data_backup_{timestamp_slug()}.zip"
        path, _ = QFileDialog.getSaveFileName(self, "Backup app data", str(default), "ZIP (*.zip)")
        if not path:
            return
        # Resolve both sides inside the worker. The default target lives in
        # BACKUP_DIR (inside APP_DIR), and write_app_data_backup excludes it and
        # all prior backups while preserving an existing target on failure.
        worker = CancellableFileTask(
            lambda stop: write_app_data_backup(APP_DIR, BACKUP_DIR, path, stop),
            self,
        )
        self._backup_file_worker = worker

        def completed(_result: object) -> None:
            self.show_compact_message("Backup App Data", f"Backup created:\n{path}", "info")

        worker.completed.connect(completed)
        worker.failed.connect(lambda error: self.show_compact_message("Backup failed", error, "error"))
        worker.finished.connect(
            lambda: setattr(self, "_backup_file_worker", None)
            if getattr(self, "_backup_file_worker", None) is worker
            else None
        )
        worker.finished.connect(worker.deleteLater)
        self.statusBar().showMessage("Backing up app data…", 5000)
        worker.start()

    def scan_output_folder(self) -> None:
        existing_worker = getattr(self, "_scan_file_worker", None)
        if existing_worker is not None and existing_worker.isRunning():
            self.show_compact_message("Scan Output", "An output scan is already running.", "info")
            return
        folder = safe_expand_path(self.edit_output.text() or ".")
        if not folder.is_dir():
            self.show_compact_message("Scan Output", f"Folder does not exist or is not a directory:\n{folder}", "warning")
            return
        worker = CancellableFileTask(lambda stop: scan_output_tree(folder, stop_event=stop), self)
        self._scan_file_worker = worker

        def completed(result: object) -> None:
            if not isinstance(result, dict):
                return
            largest = result.get("largest", [])
            detail = "\n".join(
                f"{human_size(size)} - {path}" for size, path in largest
            )
            msg = (
                f"Folder: {result['folder']}\nFiles: {result['files']}\n"
                f"Directories: {result['dirs']}\nTotal size: {human_size(result['total'])}"
            )
            if result.get("capped"):
                msg += (
                    f"\n\nNote: scan stopped after {result['scan_limit']} entries. "
                    "Totals are partial."
                )
            if detail:
                msg += "\n\nLargest files:\n" + detail
            self.show_scroll_message("Output Folder Scanner", msg, "info")

        worker.completed.connect(completed)
        worker.failed.connect(lambda error: self.show_compact_message("Scan failed", error, "error"))
        worker.finished.connect(
            lambda: setattr(self, "_scan_file_worker", None)
            if getattr(self, "_scan_file_worker", None) is worker
            else None
        )
        worker.finished.connect(worker.deleteLater)
        self.statusBar().showMessage(f"Scanning output folder: {folder}", 5000)
        worker.start()

    def _ui_translate_text(self, text: str) -> str:
        """Translate common runtime dialog text when Indonesian UI is active."""
        if not self._ui_is_indonesian():
            return str(text)
        raw = str(text)
        pairs = self._translation_pairs()
        dialog_pairs = {
            "Command Builder": "Pembuat Command",
            "TXT Example": "Contoh TXT",
            "TXT example exported:": "Contoh TXT diekspor:",
            "TXT export failed": "Ekspor TXT gagal",
            "CSV Template": "Template CSV",
            "CSV template exported:": "Template CSV diekspor:",
            "CSV export failed": "Ekspor CSV gagal",
            "XLSX Template": "Template XLSX",
            "XLSX template exported:": "Template XLSX diekspor:",
            "XLSX export failed": "Ekspor XLSX gagal",
            "Template pack export failed": "Ekspor paket template gagal",
            "Quick Guide": "Panduan Singkat",
            "Quick guide copied to clipboard.": "Panduan singkat disalin ke clipboard.",
            "Import": "Impor",
            "Import failed": "Impor gagal",
            "Stop the current download before importing a new database.": "Hentikan download yang sedang berjalan sebelum mengimpor database baru.",
            "Paste": "Tempel",
            "Stop the current download before changing the input.": "Hentikan download yang sedang berjalan sebelum mengubah input.",
            "Clipboard": "Clipboard",
            "Clipboard is empty.": "Clipboard kosong.",
            "Clear": "Bersihkan",
            "Stop the current download before clearing the input.": "Hentikan download yang sedang berjalan sebelum membersihkan input.",
            "Open destination": "Buka tujuan",
            "Destination is unknown.": "Tujuan belum diketahui.",
            "Folder does not exist:": "Folder tidak ada:",
            "Move": "Pindah",
            "Select exactly one row.": "Pilih tepat satu baris.",
            "Start": "Mulai",
            "A download is already running.": "Download sedang berjalan.",
            "Delayed start": "Mulai tertunda",
            "A delayed start is already scheduled.": "Jadwal mulai tertunda sudah ada.",
            "No URL or command loaded. Paste or import at least one row first.": "Belum ada URL atau command. Tempel atau impor minimal satu baris dulu.",
            "gallery-dl missing": "gallery-dl belum ada",
            "gallery-dl was not found. Install gallery-dl first, then set the path in System > gallery-dl.": "gallery-dl tidak ditemukan. Install gallery-dl dulu, lalu atur path di Sistem > gallery-dl.",
            "No enabled jobs to run.": "Tidak ada job aktif untuk dijalankan.",
            "Queue": "Antrean",
            "Wait until the current run finishes before starting another run.": "Tunggu proses saat ini selesai sebelum memulai proses baru.",
            "No valid jobs selected.": "Tidak ada job valid yang dipilih.",
            "Retry": "Coba ulang",
            "No failed/stopped/cancelled jobs to retry.": "Tidak ada job gagal/dihentikan/dibatalkan untuk dicoba ulang.",
            "Retry policy skipped all failed jobs. Open the main Queue tab > Retry Strategy to change it.": "Kebijakan retry melewati semua job gagal. Buka tab Antrean > Strategi Coba Ulang untuk mengubahnya.",
            "Wait until the current run finishes before retrying selected rows.": "Tunggu proses saat ini selesai sebelum mencoba ulang baris terpilih.",
            "Cancel": "Batal",
            "Select one or more queue rows first.": "Pilih satu atau beberapa baris antrean dulu.",
            "Selected rows are already completed, stopped, or already cancelled.": "Baris terpilih sudah selesai, dihentikan, atau dibatalkan.",
            "Config": "Config",
            "Stop the current download before changing the config file.": "Hentikan download yang sedang berjalan sebelum mengubah file config.",
            "Config path is not detected.": "Path config belum terdeteksi.",
            "Config JSON is valid.": "Config JSON valid.",
            "Invalid config": "Config tidak valid",
            "Config exists. Non-JSON config was not parsed.": "Config ada. Config non-JSON tidak diparse.",
            "Create config failed": "Gagal membuat config",
            "Open config failed": "Gagal membuka config",
            "Output directory": "Folder output",
            "Stop the current download before changing the output directory.": "Hentikan download yang sedang berjalan sebelum mengubah folder output.",
            "Command Preview": "Pratinjau Command",
            "Check gallery-dl version": "Cek versi gallery-dl",
            "Version check is already running.": "Pengecekan versi sedang berjalan.",
            "Check gallery-dl version failed": "Cek versi gallery-dl gagal",
            "Save session failed": "Simpan sesi gagal",
            "Load session": "Muat sesi",
            "Stop the current download before loading a session.": "Hentikan download yang sedang berjalan sebelum memuat sesi.",
            "Load session failed": "Muat sesi gagal",
            "Session file cannot be loaded:": "File sesi tidak bisa dimuat:",
            "Export failed": "Ekspor gagal",
            "No failed/stopped/cancelled jobs.": "Tidak ada job gagal/dihentikan/dibatalkan.",
            "Error report": "Laporan error",
            "No results yet.": "Belum ada hasil.",
            "No errors to report.": "Tidak ada error untuk dilaporkan.",
            "Export Error CSV": "Ekspor CSV Error",
            "Audit Mode": "Mode Audit",
            "Archive config export failed": "Ekspor config arsip gagal",
            "Backup Config": "Backup Config",
            "No config path is selected/detected.": "Path config belum dipilih/terdeteksi.",
            "Config does not exist:": "Config tidak ada:",
            "Config backed up to:": "Config dibackup ke:",
            "Backup Config failed": "Backup Config gagal",
            "Backup App Data": "Backup Data App",
            "Backup created:": "Backup dibuat:",
            "Backup failed": "Backup gagal",
            "Scan Output": "Pindai Output",
            "Scan failed": "Pindai gagal",
            "Save TXT failed": "Simpan TXT gagal",
            "TXT saved:": "TXT disimpan:",
            "Save CSV failed": "Simpan CSV gagal",
            "CSV saved:": "CSV disimpan:",
            "Save XLSX failed": "Simpan XLSX gagal",
            "XLSX saved:": "XLSX disimpan:",
            "openpyxl is required for XLSX export.\n\nInstall with:\npip install openpyxl": "openpyxl diperlukan untuk ekspor XLSX.\n\nInstall dengan:\npip install openpyxl",
            "Command copied.": "Command disalin.",
            "Guide copied.": "Panduan disalin.",
            "URL is required.": "URL wajib diisi.",
            "Save Policy": "Simpan Kebijakan",
            "Save": "Simpan",
            "Health Check": "Cek Kesehatan",
            "gallery-dl command not found": "command gallery-dl tidak ditemukan",
            "config file does not exist yet": "file config belum dibuat",
            "config exists but JSON is invalid": "config ada tetapi JSON tidak valid",
            "config path is not a file": "path config bukan file",
            "output dir problem": "masalah folder output",
            "loaded jobs": "job dimuat",
            "SSL certificate bypass": "Bypass sertifikat SSL",
            "HypnoHub cautious mode": "Mode hati-hati HypnoHub",
            "Rule34 cautious mode": "Mode hati-hati Rule34",
            "Booru tag batch": "Batch tag booru",
            "Resume large batch": "Lanjutkan batch besar",
            "Select adult booru": "Pilih booru dewasa",
            "No certificate check / skip SSL cert": "Tanpa cek sertifikat / skip SSL",
            "No skip / overwrite existing": "Tanpa skip / timpa file lama",
            "Ignore default config": "Abaikan config default",
            "Use .netrc auth": "Pakai auth .netrc",
            "Sleep skip": "Jeda saat skip",
            "Sleep extractor": "Jeda extractor",
            "Sleep retries": "Jeda retry",
            "Chunk size": "Ukuran chunk",
            "Abort after skip": "Abort setelah skip",
            "Terminate after skip": "Terminate setelah skip",
            "Cookies export": "Ekspor cookies",
            "Close": "Tutup",
        }
        if raw in pairs:
            return pairs[raw]
        if raw in dialog_pairs:
            return dialog_pairs[raw]
        # Translate common prefixes while keeping dynamic file paths/details.
        for en, idi in sorted({**pairs, **dialog_pairs}.items(), key=lambda kv: len(kv[0]), reverse=True):
            if raw.startswith(en + "\n") or raw.startswith(en + ":"):
                return raw.replace(en, idi, 1)
        return raw

    def show_compact_message(self, title: str, message: str, level: str = "info") -> None:
        """Compact message dialog with no wasted QMessageBox whitespace."""
        title = self._ui_translate_text(title)
        # Dialogs are a final user-visible sink for errors from subprocesses,
        # config parsing, file tasks, and database operations. Redact here as
        # defense in depth so a new caller cannot accidentally expose auth.
        message = self._ui_translate_text(redact_sensitive_text(str(message)))
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setModal(True)
        dlg.setMinimumWidth(320)
        dlg.setMaximumWidth(500)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(10)
        icon_type = {
            "info": QStyle.SP_MessageBoxInformation,
            "warning": QStyle.SP_MessageBoxWarning,
            "error": QStyle.SP_MessageBoxCritical,
        }.get(level, QStyle.SP_MessageBoxInformation)
        icon_label = QLabel()
        icon_label.setPixmap(self.style().standardIcon(icon_type).pixmap(20, 20))
        icon_label.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        icon_label.setFixedWidth(24)
        body = QLabel(str(message))
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setMinimumWidth(270)
        body.setMaximumWidth(440)
        row.addWidget(icon_label)
        row.addWidget(body, 1)
        lay.addLayout(row)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        ok = QPushButton("OK")
        ok.setFixedSize(72, 28)
        ok.clicked.connect(dlg.accept)
        btn_row.addWidget(ok)
        lay.addLayout(btn_row)
        dlg.adjustSize()
        dlg.exec()

    def show_scroll_message(self, title: str, message: str, level: str = "info") -> None:
        """Show longer messages in a bounded, scrollable dialog."""
        title = self._ui_translate_text(title)
        msg = self._ui_translate_text(redact_sensitive_text(str(message)))
        if len(msg) < 700 and "\n" not in msg[:120]:
            self.show_compact_message(title, msg, level)
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(600, 380)
        dlg.setMinimumSize(460, 260)
        dlg.setMaximumWidth(760)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(7)
        header = QLabel(title)
        header.setObjectName("sectionTitle")
        header.setWordWrap(True)
        lay.addWidget(header)
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        box.setPlainText(msg)
        lay.addWidget(box, 1)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        ok = QPushButton("OK")
        ok.setFixedSize(72, 28)
        ok.clicked.connect(dlg.accept)
        btn_row.addWidget(ok)
        lay.addLayout(btn_row)
        dlg.exec()


__all__ = [
    "CancellableFileTask",
    "ReportsMixin",
    "app_data_backup_members",
    "is_restorable_autosave",
    "scan_output_tree",
    "write_app_data_backup",
]
