"""Archive library, automation, secure profiles, options, and runtime UI."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QProcess, QTime, QTimer, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from .core import (
    APP_DIR,
    MANAGED_AUTH_KEYS,
    REDACTED,
    append_extra_args_to_database_text,
    atomic_write_text,
    command_string_to_argv,
    is_sensitive_option_key,
    parse_text_database,
    quote_arg_for_preview,
    read_text_safely,
    redact_sensitive_database_text,
    redact_sensitive_text,
)
from .feature_logic import build_filter_expression, next_schedule_time, parse_help_options
from .feature_store import FeatureStore
from .secure_vault import SecretVault


SECURE_RUNTIME_DIR = APP_DIR / "runtime"
MANAGED_VENV_DIR = SECURE_RUNTIME_DIR / "gallery-dl-venv"


def normalize_clipboard_url_candidate(value: str) -> str:
    """Trim prose punctuation without corrupting balanced URL parentheses."""
    url = str(value or "").rstrip(".,;")
    pairs = {")": "(", "]": "[", "}": "{"}
    while url and url[-1] in pairs:
        closing = url[-1]
        opening = pairs[closing]
        if url.count(closing) <= url.count(opening):
            break
        url = url[:-1]
    return url


def interrupted_recovery_commands(rows: list[dict[str, object]]) -> list[str]:
    """Keep one recovery command per unfinished run item, including duplicates."""
    return [
        str(row.get("command") or "").strip()
        for row in rows
        if str(row.get("command") or "").strip()
    ]


def library_records_to_database_text(rows: list[dict[str, object]]) -> str:
    """Serialize library selections without losing their sticky tag metadata."""
    lines: list[str] = []
    last_tag = ""
    for row in rows:
        tag = " ".join(str(row.get("tag") or "").split())
        if tag != last_tag:
            lines.append(f"# {tag}" if tag else "#")
            last_tag = tag
        command = str(row.get("command") or row.get("url") or "").strip()
        if command:
            lines.append(command)
    return "\n".join(lines)


def common_account_profile_id(rows: list[dict[str, object]]) -> int | None:
    """Return one shared account id; mixed/public selections use no profile."""
    profile_ids = {
        int(row["account_profile_id"]) if row.get("account_profile_id") else None
        for row in rows
    }
    if len(profile_ids) != 1:
        return None
    return profile_ids.pop()


def format_schedule_timestamp(value: object) -> str:
    """Format persisted schedule timestamps without trusting database values."""
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return "Invalid timestamp"


class ManagementMixin:
    """Feature center and lifecycle services mixed into :class:`MainWindow`."""

    def _initialize_management_state(self) -> None:
        self.feature_store = FeatureStore()
        self.feature_store.migrate_jsonl_history()
        self.secret_vault = SecretVault()
        self.current_run_id: int | None = None
        self._run_config_path: str | None = None
        self.active_account_profile_id: int | None = None
        self.clipboard_inbox_enabled = False
        self.clipboard_allowed_hosts = ""
        self.close_to_tray = False
        self._force_quit = False
        self._last_clipboard_text = ""
        self._management_dialog: QDialog | None = None
        self._runtime_process: QProcess | None = None
        self._runtime_stage = ""
        # A due schedule temporarily owns the visible queue while its workers
        # run. Preserve the user's manually prepared queue and restore it after
        # the scheduled run (or immediately when dispatch fails) so automation
        # never replaces unrelated work in the editor/autosave.
        self._scheduled_queue_restore: str | None = None

    def _start_management_services(self) -> None:
        self._cleanup_stale_secure_configs()
        recovered_db = getattr(self.feature_store, "recovered_corrupt_path", None)
        if recovered_db is not None:
            self.append_log(
                f"[database] corrupt library database preserved as: {recovered_db}"
            )
            QTimer.singleShot(
                0,
                lambda: self.show_compact_message(
                    "Library database recovered",
                    "The library database was corrupt and could not be opened. "
                    "It was preserved for recovery and a clean database was created:\n"
                    f"{recovered_db}",
                    "warning",
                ),
            )
        clipboard = QApplication.clipboard()
        clipboard.dataChanged.connect(self._on_clipboard_changed)
        self.schedule_timer = QTimer(self)
        self.schedule_timer.timeout.connect(self._poll_schedules)
        self.schedule_timer.start(30_000)
        if self.close_to_tray:
            self._ensure_persistent_tray()
        QTimer.singleShot(600, self._offer_crash_recovery)

    # ------------------------------------------------------------------ lifecycle
    def _offer_crash_recovery(self) -> None:
        if self.active_workers > 0:
            return
        interrupted = self.feature_store.interrupted_runs()
        if not interrupted:
            return
        commands = interrupted_recovery_commands(interrupted)
        run_ids: set[int] = set()
        for row in interrupted:
            run_ids.add(int(row["run_id"]))
        self.feature_store.resolve_interrupted_runs(run_ids)
        if not commands:
            return
        answer = QMessageBox.question(
            self,
            "Interrupted download",
            f"The previous session ended while {len(commands)} job(s) were unfinished.\n\n"
            "Load and resume them now?",
        )
        if answer == QMessageBox.Yes:
            self.txt_commands.setPlainText("\n".join(commands))
            if any(REDACTED in command for command in commands):
                QMessageBox.information(
                    self,
                    "Interrupted download",
                    "Inline credentials were removed from the recovery record. "
                    "Review the loaded commands and re-enter authentication before starting.",
                )
            else:
                QTimer.singleShot(
                    0,
                    lambda: self._start_download_now(run_source="recovery"),
                )

    def _ensure_persistent_tray(self) -> None:
        if self.tray_icon is None:
            from PySide6.QtWidgets import QStyle, QSystemTrayIcon

            self.tray_icon = QSystemTrayIcon(self)
            icon = self.windowIcon()
            if icon.isNull():
                icon = self.style().standardIcon(QStyle.SP_ComputerIcon)
            self.tray_icon.setIcon(icon)
            menu = QMenu(self)
            show_action = QAction("Open GdlScrape", self)
            show_action.triggered.connect(self._restore_from_tray)
            quit_action = QAction("Exit", self)
            quit_action.triggered.connect(self._quit_from_tray)
            menu.addAction(show_action)
            menu.addSeparator()
            menu.addAction(quit_action)
            self.tray_icon.setContextMenu(menu)
            self.tray_icon.activated.connect(
                lambda reason: self._restore_from_tray()
                if reason == QSystemTrayIcon.DoubleClick
                else None
            )
        self.tray_icon.show()

    def _restore_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self) -> None:
        self._force_quit = True
        self.close()

    # ------------------------------------------------------------------ clipboard
    def _on_clipboard_changed(self) -> None:
        if not self.clipboard_inbox_enabled or self.active_workers > 0:
            return
        text = QApplication.clipboard().text().strip()
        if not text or text == self._last_clipboard_text or len(text) > 65_536:
            return
        self._last_clipboard_text = text
        allowed = {
            host.strip().lower()
            for host in self.clipboard_allowed_hosts.split(",")
            if host.strip()
        }
        candidates: list[str] = []
        for match in re.findall(r"https?://[^\s<>\"']+", text)[:50]:
            url = normalize_clipboard_url_candidate(match)
            parsed = urlparse(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                continue
            host = parsed.hostname.lower()
            if allowed and not any(host == item or host.endswith("." + item) for item in allowed):
                continue
            if url not in candidates:
                candidates.append(url)
        if not candidates:
            return
        existing = {job.url for job in parse_text_database(self.txt_commands.toPlainText())}
        additions = [url for url in candidates if url not in existing]
        if not additions:
            return
        current = self.txt_commands.toPlainText().rstrip()
        self.txt_commands.setPlainText((current + "\n" if current else "") + "\n".join(additions))
        self.append_log(f"[clipboard inbox] added {len(additions)} URL(s)")
        self.statusBar().showMessage(f"Clipboard Inbox added {len(additions)} URL(s)", 4000)

    # ------------------------------------------------------------------ scheduler
    def _restore_queue_after_scheduled_run(self) -> bool:
        """Restore queue text temporarily replaced by a scheduler dispatch."""
        previous = self._scheduled_queue_restore
        if previous is None:
            return False
        self._scheduled_queue_restore = None
        self.txt_commands.setPlainText(previous)
        # Keep the queue model/table aligned with the restored editor before an
        # autosave or another scheduled poll can observe it.
        self._rebuild_from_text()
        self.append_log("[scheduler] restored the previous manual queue")
        return True

    def _poll_schedules(self) -> None:
        if self.active_workers > 0 or (self.delay_timer and self.delay_timer.isActive()):
            return
        due = self.feature_store.due_schedules()
        if not due:
            return
        schedule = due[0]
        command_text = str(schedule.get("command_text") or "").strip()
        next_run = next_schedule_time(schedule)
        schedule_id = int(schedule["id"])
        if not command_text:
            self.feature_store.advance_schedule(schedule_id, next_run)
            self.append_log(f"[scheduler] skipped empty schedule: {schedule.get('name')}")
            return
        self._scheduled_queue_restore = self.txt_commands.toPlainText()
        self.txt_commands.setPlainText(command_text)
        self.append_log(f"[scheduler] starting: {schedule.get('name')}")
        account_id = int(schedule["account_profile_id"]) if schedule.get("account_profile_id") else None
        try:
            started = self._start_download_now(
                run_source=f"schedule:{schedule_id}",
                account_profile_id=account_id,
                use_account_override=True,
            )
        except Exception as exc:
            # A Qt timer callback must not leak an exception into the event
            # loop. If workers became active before a later UI update failed,
            # retain the scheduled queue and treat the dispatch as started;
            # otherwise preserve the user's queue and retry shortly.
            started = self.active_workers > 0
            state = "workers are active" if started else "no workers became active"
            self.append_log(
                f"[scheduler] unexpected start error for {schedule.get('name')} "
                f"({state}): {exc}"
            )
        if started:
            self.feature_store.advance_schedule(schedule_id, next_run)
        else:
            self._restore_queue_after_scheduled_run()
            retry_at = time.time() + 5 * 60
            self.feature_store.defer_schedule(schedule_id, retry_at)
            self.append_log(
                f"[scheduler] start failed; retrying in 5 minutes: {schedule.get('name')}"
            )

    # ------------------------------------------------------------ secure profiles
    def prepare_active_account_config(
        self,
        account_profile_id: int | None = None,
        *,
        use_active_default: bool = True,
    ) -> str | None:
        """Create a short-lived merged config for the selected account profile."""
        self.cleanup_run_account_config()
        selected_account_id = (
            self.active_account_profile_id if use_active_default else account_profile_id
        )
        profile = self.feature_store.account(selected_account_id)
        if selected_account_id and not profile:
            raise RuntimeError(
                f"The selected account profile no longer exists: {selected_account_id}"
            )
        if not profile:
            return None
        base: dict[str, object] = {}
        if self.config_path and Path(self.config_path).is_file():
            try:
                loaded = json.loads(read_text_safely(self.config_path))
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"Active config cannot be merged with the account profile: {exc}") from exc
            if not isinstance(loaded, dict):
                raise RuntimeError("Active config must contain a JSON object")
            base = loaded
        extractor = base.setdefault("extractor", {})
        if not isinstance(extractor, dict):
            raise RuntimeError("The active config extractor section must be a JSON object")
        site = str(profile.get("site") or "").strip().lower()
        site_config = extractor.setdefault(site, {})
        if not isinstance(site_config, dict):
            raise RuntimeError(f"Config extractor.{site} must be a JSON object")
        auth_kind = str(profile.get("auth_kind") or "browser")
        auth_keys = set(MANAGED_AUTH_KEYS)
        auth_keys.add(str(profile.get("secret_key") or "api-key"))
        for key in list(site_config):
            if key in auth_keys or is_sensitive_option_key(key):
                site_config.pop(key, None)
        if auth_kind == "browser":
            source = str(profile.get("cookie_source") or "chrome")
            site_config["cookies"] = [source]
        elif auth_kind == "cookies_file":
            source = str(profile.get("cookie_source") or "")
            if not source:
                raise RuntimeError("The selected cookies.txt profile has no file path")
            cookie_path = Path(source).expanduser()
            if not cookie_path.is_file():
                raise RuntimeError(f"The selected cookies.txt file does not exist: {cookie_path}")
            site_config["cookies"] = str(cookie_path)
        else:
            secret = self.secret_vault.get(str(profile.get("secret_ref") or ""))
            if not secret:
                raise RuntimeError("The selected account secret is unavailable")
            username = str(profile.get("username") or "")
            if auth_kind == "username_password":
                site_config["username"] = username
                site_config["password"] = secret
            elif auth_kind == "api_key":
                site_config[str(profile.get("secret_key") or "api-key")] = secret
            else:
                raise RuntimeError(f"Unsupported account authentication method: {auth_kind}")
        SECURE_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        path = SECURE_RUNTIME_DIR / f"secure-config-{uuid.uuid4().hex}.json"
        atomic_write_text(
            path,
            json.dumps(base, ensure_ascii=False, indent=2),
            encoding="utf-8",
            mode=stat.S_IRUSR | stat.S_IWUSR,
        )
        self._run_config_path = str(path)
        return self._run_config_path

    def cleanup_run_account_config(self) -> None:
        path = getattr(self, "_run_config_path", None)
        self._run_config_path = None
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass

    def _cleanup_stale_secure_configs(self) -> None:
        if not SECURE_RUNTIME_DIR.exists():
            return
        for path in SECURE_RUNTIME_DIR.glob("secure-config-*.json"):
            try:
                path.unlink()
            except OSError:
                pass

    # ---------------------------------------------------------------- dialog shell
    def open_management_center(self) -> None:
        if self._management_dialog is not None and self._management_dialog.isVisible():
            self._management_dialog.raise_()
            self._management_dialog.activateWindow()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Library & Automation Center")
        dialog.resize(980, 700)
        dialog.setMinimumSize(820, 600)
        root = QVBoxLayout(dialog)
        intro = QLabel(
            "Persistent archive library, recurring schedules, Clipboard Inbox, secure account profiles, "
            "installed-version options, and an isolated gallery-dl runtime."
        )
        intro.setWordWrap(True)
        intro.setObjectName("subtle")
        root.addWidget(intro)
        tabs = QTabWidget()
        root.addWidget(tabs, 1)
        tabs.addTab(self._build_library_tab(), "Library")
        tabs.addTab(self._build_scheduler_tab(), "Scheduler")
        tabs.addTab(self._build_inbox_tab(), "URL Inbox")
        tabs.addTab(self._build_accounts_tab(), "Accounts")
        tabs.addTab(self._build_options_tab(), "Options & Filters")
        tabs.addTab(self._build_runtime_tab(), "Runtime")
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        root.addWidget(close, 0, Qt.AlignRight)
        self._management_dialog = dialog
        dialog.finished.connect(lambda _result: setattr(self, "_management_dialog", None))
        dialog.show()

    @staticmethod
    def _table(columns: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    # --------------------------------------------------------------------- library
    def _build_library_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        toolbar = QHBoxLayout()
        search = QLineEdit()
        search.setPlaceholderText("Search title, URL, service, or tag")
        import_current = QPushButton("Import Current Queue")
        queue_selected = QPushButton("Queue Selected")
        delete_selected = QPushButton("Delete Selected")
        toolbar.addWidget(search, 1)
        toolbar.addWidget(import_current)
        toolbar.addWidget(queue_selected)
        toolbar.addWidget(delete_selected)
        layout.addLayout(toolbar)
        table = self._table(["ID", "Title", "Service", "Tag", "Last status", "New files", "URL"])
        layout.addWidget(table, 1)

        def refresh() -> None:
            rows = self.feature_store.list_library(search.text())
            table.setRowCount(len(rows))
            for row_index, record in enumerate(rows):
                values = [
                    record["id"], record["title"], record["service"], record["tag"],
                    record["last_status"], record["last_downloaded"], record["url"],
                ]
                for column, value in enumerate(values):
                    table.setItem(row_index, column, QTableWidgetItem(str(value)))

        def import_queue() -> None:
            if not self._rebuild_from_text():
                return
            redacted_count = sum(
                redact_sensitive_text(job.raw) != job.raw for job in self.jobs
            )
            for job in self.jobs:
                self.feature_store.add_library_entry(
                    url=job.url,
                    title=job.ident if job.ident != "-" else "",
                    service=job.service,
                    tag=job.tag,
                    output_dir=job.dest if job.dest != "-" else "",
                    account_profile_id=self.active_account_profile_id,
                    command=job.raw,
                )
            refresh()
            self.append_log(f"[library] imported {len(self.jobs)} current job(s)")
            if redacted_count:
                QMessageBox.warning(
                    page,
                    "Library credentials removed",
                    f"Inline credentials were removed from {redacted_count} stored command(s). "
                    "Use an Account profile or saved cookies when queueing them again.",
                )

        def selected_ids() -> list[int]:
            return sorted({int(table.item(index.row(), 0).text()) for index in table.selectedIndexes()})

        def queue_rows() -> None:
            if self.active_workers > 0:
                QMessageBox.information(page, "Library", "Wait for the current download to finish before replacing the queue.")
                return
            selected = set(selected_ids())
            records = [row for row in self.feature_store.list_library(search.text()) if int(row["id"]) in selected]
            if not records:
                return
            self.txt_commands.setPlainText(library_records_to_database_text(records))
            self.active_account_profile_id = common_account_profile_id(records)
            if not self._rebuild_from_text():
                return
            if self._management_dialog is not None:
                self._management_dialog.accept()

        import_current.clicked.connect(import_queue)
        queue_selected.clicked.connect(queue_rows)
        def delete_rows() -> None:
            self.feature_store.delete_library_entries(selected_ids())
            refresh()

        delete_selected.clicked.connect(delete_rows)
        search.textChanged.connect(refresh)
        refresh()
        return page

    # ------------------------------------------------------------------- schedules
    def _build_scheduler_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        table = self._table(["ID", "Enabled", "Name", "Frequency", "Next run", "Account", "Commands"])
        table.setMaximumHeight(250)
        layout.addWidget(table)
        form = QGridLayout()
        name = QLineEdit("Daily archive check")
        frequency = QComboBox()
        frequency.addItems(["interval", "daily", "weekly"])
        interval = QSpinBox()
        interval.setRange(1, 10080)
        interval.setValue(60)
        interval.setSuffix(" min")
        run_time = QTimeEdit(QTime(2, 0))
        run_time.setDisplayFormat("HH:mm")
        weekdays = QLineEdit("0,1,2,3,4,5,6")
        weekdays.setToolTip("Monday=0 through Sunday=6; used only for weekly schedules.")
        enabled = QCheckBox("Enabled")
        enabled.setChecked(True)
        account = QComboBox()
        account.addItem("No account profile", None)
        for record in self.feature_store.list_accounts():
            account.addItem(str(record["name"]), int(record["id"]))
        commands = QPlainTextEdit(self.txt_commands.toPlainText())
        commands.setPlaceholderText("One URL or gallery-dl command per line")
        commands.setMinimumHeight(130)
        form.addWidget(QLabel("Name"), 0, 0)
        form.addWidget(name, 0, 1)
        form.addWidget(QLabel("Frequency"), 0, 2)
        form.addWidget(frequency, 0, 3)
        form.addWidget(QLabel("Interval"), 1, 0)
        form.addWidget(interval, 1, 1)
        form.addWidget(QLabel("Time"), 1, 2)
        form.addWidget(run_time, 1, 3)
        form.addWidget(QLabel("Weekdays"), 2, 0)
        form.addWidget(weekdays, 2, 1, 1, 2)
        form.addWidget(enabled, 2, 3)
        form.addWidget(QLabel("Account profile"), 3, 0)
        form.addWidget(account, 3, 1, 1, 3)
        form.addWidget(QLabel("Queue snapshot"), 4, 0)
        form.addWidget(commands, 4, 1, 1, 3)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        new_button = QPushButton("New")
        save_button = QPushButton("Save")
        delete_button = QPushButton("Delete")
        use_current = QPushButton("Use Current Queue")
        buttons.addWidget(new_button)
        buttons.addWidget(save_button)
        buttons.addWidget(delete_button)
        buttons.addWidget(use_current)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        current_id: list[int | None] = [None]

        def refresh() -> None:
            rows = self.feature_store.list_schedules()
            account_names = {
                int(record["id"]): str(record["name"])
                for record in self.feature_store.list_accounts()
            }
            table.setRowCount(len(rows))
            for row_index, record in enumerate(rows):
                next_text = format_schedule_timestamp(record.get("next_run_at"))
                if record.get("account_profile_id"):
                    account_name = account_names.get(
                        int(record["account_profile_id"]),
                        "Missing profile",
                    )
                else:
                    account_name = "None"
                values = [record["id"], "Yes" if record["enabled"] else "No", record["name"], record["frequency"], next_text, account_name, len(str(record["command_text"]).splitlines())]
                for column, value in enumerate(values):
                    table.setItem(row_index, column, QTableWidgetItem(str(value)))

        def clear_form() -> None:
            current_id[0] = None
            name.setText("Daily archive check")
            account.setCurrentIndex(0)
            commands.setPlainText(self.txt_commands.toPlainText())

        def load_selected() -> None:
            if table.currentRow() < 0:
                return
            item_id = int(table.item(table.currentRow(), 0).text())
            record = next((item for item in self.feature_store.list_schedules() if int(item["id"]) == item_id), None)
            if not record:
                return
            current_id[0] = item_id
            name.setText(str(record["name"]))
            frequency.setCurrentText(str(record["frequency"]))
            interval.setValue(int(record["interval_minutes"]))
            parsed_time = QTime.fromString(str(record["time_of_day"]), "HH:mm")
            if parsed_time.isValid():
                run_time.setTime(parsed_time)
            weekdays.setText(str(record["weekdays"]))
            enabled.setChecked(bool(record["enabled"]))
            account_index = account.findData(record.get("account_profile_id"))
            account.setCurrentIndex(max(0, account_index))
            commands.setPlainText(str(record["command_text"]))

        def save() -> None:
            raw_commands = commands.toPlainText()
            if not raw_commands.strip():
                QMessageBox.warning(page, "Schedule", "The schedule queue cannot be empty.")
                return
            safe_commands = redact_sensitive_database_text(raw_commands)
            if safe_commands != raw_commands:
                answer = QMessageBox.question(
                    page,
                    "Schedule credentials removed",
                    "Inline credentials cannot be stored in a schedule. They will be replaced "
                    "with a redacted placeholder, and the schedule will require an Account "
                    "profile, cookies, or config authentication. Save it anyway?",
                )
                if answer != QMessageBox.Yes:
                    return
                commands.setPlainText(safe_commands)
            values: dict[str, object] = {
                "name": name.text(), "command_text": safe_commands,
                "frequency": frequency.currentText(), "interval_minutes": interval.value(),
                "time_of_day": run_time.time().toString("HH:mm"), "weekdays": weekdays.text(),
                "enabled": enabled.isChecked(),
                "account_profile_id": account.currentData(),
            }
            try:
                values["next_run_at"] = next_schedule_time(values)
                current_id[0] = self.feature_store.save_schedule(values, current_id[0])
            except (OSError, ValueError) as exc:
                QMessageBox.warning(page, "Schedule", str(exc))
                return
            refresh()

        def delete() -> None:
            if current_id[0]:
                self.feature_store.delete_schedules([current_id[0]])
                clear_form()
                refresh()

        table.itemSelectionChanged.connect(load_selected)
        new_button.clicked.connect(clear_form)
        save_button.clicked.connect(save)
        delete_button.clicked.connect(delete)
        use_current.clicked.connect(lambda: commands.setPlainText(self.txt_commands.toPlainText()))
        refresh()
        return page

    # ----------------------------------------------------------------------- inbox
    def _build_inbox_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        card = QFrame()
        card.setObjectName("card")
        form = QFormLayout(card)
        enabled = QCheckBox("Monitor clipboard and add new HTTP(S) URLs to the queue")
        enabled.setChecked(self.clipboard_inbox_enabled)
        hosts = QLineEdit(self.clipboard_allowed_hosts)
        hosts.setPlaceholderText("Optional: pixiv.net, x.com, instagram.com")
        close_to_tray = QCheckBox("Keep application running in the system tray when the window is closed")
        close_to_tray.setChecked(self.close_to_tray)
        form.addRow(enabled)
        form.addRow("Allowed hosts", hosts)
        form.addRow(close_to_tray)
        layout.addWidget(card)
        note = QLabel(
            "Clipboard Inbox is opt-in, accepts at most 50 URLs per clipboard change, rejects URLs containing embedded credentials, "
            "and can be restricted to an allowlist. It does not start downloads automatically."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        save = QPushButton("Save Inbox Settings")
        layout.addWidget(save, 0, Qt.AlignLeft)
        layout.addStretch(1)

        def persist() -> None:
            self.clipboard_inbox_enabled = enabled.isChecked()
            self.clipboard_allowed_hosts = hosts.text().strip()
            self.close_to_tray = close_to_tray.isChecked()
            if self.close_to_tray:
                self._ensure_persistent_tray()
            elif self.tray_icon is not None and self.active_workers <= 0:
                self.tray_icon.hide()
            self.autosave_session()
            self.statusBar().showMessage("Inbox settings saved", 3000)

        save.clicked.connect(persist)
        return page

    # -------------------------------------------------------------------- accounts
    def _build_accounts_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        backend = QLabel(f"Secret backend: {self.secret_vault.backend_name}. Secrets are never stored in the library database.")
        backend.setWordWrap(True)
        backend.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(backend)
        table = self._table(["ID", "Active", "Name", "Site", "Method", "Username / source"])
        table.setMaximumHeight(230)
        layout.addWidget(table)
        form = QGridLayout()
        name = QLineEdit()
        site = QLineEdit()
        site.setPlaceholderText("pixiv, twitter, danbooru …")
        kind = QComboBox()
        kind.addItem("Browser cookies", "browser")
        kind.addItem("cookies.txt", "cookies_file")
        kind.addItem("Username + password", "username_password")
        kind.addItem("API key / token", "api_key")
        username = QLineEdit()
        source = QLineEdit()
        source.setPlaceholderText("chrome or C:/path/cookies.txt")
        secret_key = QLineEdit("api-key")
        secret = QLineEdit()
        secret.setEchoMode(QLineEdit.Password)
        secret.setPlaceholderText("Leave blank to keep the stored secret")
        fields = [("Name", name), ("Site", site), ("Method", kind), ("Username", username), ("Cookie source", source), ("Config secret key", secret_key), ("Secret", secret)]
        for index, (label, widget) in enumerate(fields):
            row, column = divmod(index, 2)
            form.addWidget(QLabel(label), row, column * 2)
            form.addWidget(widget, row, column * 2 + 1)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        new_button = QPushButton("New")
        save_button = QPushButton("Save Profile")
        activate_button = QPushButton("Use Selected")
        delete_button = QPushButton("Delete")
        for button in (new_button, save_button, activate_button, delete_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)
        current_id: list[int | None] = [None]
        current_ref: list[str] = [""]

        def refresh() -> None:
            rows = self.feature_store.list_accounts()
            table.setRowCount(len(rows))
            for row_index, record in enumerate(rows):
                detail = record["cookie_source"] if record["auth_kind"] in {"browser", "cookies_file"} else record["username"]
                values = [record["id"], "Yes" if int(record["id"]) == self.active_account_profile_id else "", record["name"], record["site"], record["auth_kind"], detail]
                for column, value in enumerate(values):
                    table.setItem(row_index, column, QTableWidgetItem(str(value)))

        def clear_form() -> None:
            current_id[0] = None
            current_ref[0] = ""
            for field in (name, site, username, source, secret):
                field.clear()
            secret_key.setText("api-key")
            kind.setCurrentIndex(0)

        def load_selected() -> None:
            if table.currentRow() < 0:
                return
            account_id = int(table.item(table.currentRow(), 0).text())
            record = self.feature_store.account(account_id)
            if not record:
                return
            current_id[0] = account_id
            current_ref[0] = str(record["secret_ref"])
            name.setText(str(record["name"]))
            site.setText(str(record["site"]))
            kind.setCurrentIndex(max(0, kind.findData(str(record["auth_kind"]))))
            username.setText(str(record["username"]))
            source.setText(str(record["cookie_source"]))
            secret_key.setText(str(record["secret_key"]))
            secret.clear()

        def save() -> None:
            auth_kind = str(kind.currentData())
            needs_secret = auth_kind in {"username_password", "api_key"}
            old_reference = current_ref[0]
            reference = old_reference if needs_secret else ""
            previous_secret: str | None = None
            wrote_secret = False
            if needs_secret and secret.text():
                if not self.secret_vault.available:
                    QMessageBox.warning(page, "Secure storage unavailable", "Install the optional keyring package first.")
                    return
                reference = reference or self.secret_vault.new_reference()
                try:
                    if old_reference and reference == old_reference:
                        previous_secret = self.secret_vault.get(old_reference)
                    self.secret_vault.set(reference, secret.text())
                    wrote_secret = True
                except Exception as exc:
                    QMessageBox.warning(page, "Secure storage unavailable", str(exc))
                    return
            if needs_secret and not reference:
                QMessageBox.warning(page, "Account profile", "This authentication method requires a secret.")
                return
            values = {
                "name": name.text(), "site": site.text(), "auth_kind": auth_kind,
                "username": username.text(), "cookie_source": source.text(),
                "secret_key": secret_key.text(), "secret_ref": reference,
            }
            try:
                current_id[0] = self.feature_store.save_account(values, current_id[0])
            except Exception as exc:
                if wrote_secret:
                    try:
                        if old_reference and reference == old_reference and previous_secret is not None:
                            self.secret_vault.set(old_reference, previous_secret)
                        elif reference != old_reference:
                            self.secret_vault.delete(reference)
                    except Exception:
                        pass
                QMessageBox.warning(page, "Account profile", str(exc))
                return
            if old_reference and not needs_secret:
                try:
                    self.secret_vault.delete(old_reference)
                except Exception as exc:
                    QMessageBox.warning(
                        page,
                        "Account profile",
                        f"The profile was saved, but its old secret could not be removed: {exc}",
                    )
            current_ref[0] = reference
            secret.clear()
            refresh()

        def activate() -> None:
            if current_id[0]:
                self.active_account_profile_id = current_id[0]
                self.autosave_session()
                refresh()

        def delete() -> None:
            if not current_id[0]:
                return
            references = self.feature_store.delete_accounts([current_id[0]])
            cleanup_errors: list[str] = []
            for reference in references:
                try:
                    self.secret_vault.delete(reference)
                except Exception as exc:
                    cleanup_errors.append(str(exc))
            if self.active_account_profile_id == current_id[0]:
                self.active_account_profile_id = None
            clear_form()
            self.autosave_session()
            refresh()
            if cleanup_errors:
                QMessageBox.warning(
                    page,
                    "Account profile",
                    "The profile was deleted, but secure-secret cleanup failed:\n"
                    + "\n".join(cleanup_errors),
                )

        table.itemSelectionChanged.connect(load_selected)
        new_button.clicked.connect(clear_form)
        save_button.clicked.connect(save)
        activate_button.clicked.connect(activate)
        delete_button.clicked.connect(delete)
        refresh()
        return page

    # -------------------------------------------------------------- options/filters
    def _build_options_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        splitter = QTabWidget()
        layout.addWidget(splitter, 1)

        options_page = QWidget()
        options_layout = QVBoxLayout(options_page)
        search = QLineEdit()
        search.setPlaceholderText("Search installed gallery-dl options")
        table = self._table(["Option", "Value?", "Description"])
        value = QLineEdit()
        value.setPlaceholderText("Value for the selected option")
        apply_button = QPushButton("Apply Selected Option to Current Input")
        options_layout.addWidget(search)
        options_layout.addWidget(table, 1)
        options_layout.addWidget(value)
        options_layout.addWidget(apply_button)
        splitter.addTab(options_page, "Options Catalog")

        # Show the safe fallback immediately and probe the installed version
        # asynchronously. Opening Management must never freeze the GUI for the
        # former eight-second subprocess timeout.
        catalog = parse_help_options("")
        catalog_process = QProcess(page)
        catalog_process.setProcessChannelMode(QProcess.MergedChannels)
        catalog_timeout = QTimer(page)
        catalog_timeout.setSingleShot(True)
        catalog_timeout.timeout.connect(
            lambda: catalog_process.kill()
            if catalog_process.state() != QProcess.NotRunning
            else None
        )

        def refresh_catalog() -> None:
            needle = search.text().strip().lower()
            rows = [item for item in catalog if not needle or needle in item[0].lower() or needle in item[1].lower()]
            table.setRowCount(len(rows))
            for row_index, (flag, description, needs_value) in enumerate(rows):
                table.setItem(row_index, 0, QTableWidgetItem(flag))
                table.setItem(row_index, 1, QTableWidgetItem("Yes" if needs_value else "No"))
                table.setItem(row_index, 2, QTableWidgetItem(description))

        def catalog_finished(_exit_code: int, _exit_status) -> None:
            # The dialog can be closed while ``gallery-dl --help`` is still
            # running. Its page-owned QProcess is then destroyed before the
            # queued ``finished`` signal reaches Python. Never dereference a
            # deleted C++ wrapper from this late callback.
            if not isValid(catalog_process) or not isValid(catalog_timeout):
                return
            catalog_timeout.stop()
            output_text = bytes(catalog_process.readAllStandardOutput()).decode(errors="replace")
            if output_text.strip():
                catalog[:] = parse_help_options(output_text)
                refresh_catalog()

        def apply_option() -> None:
            if self.active_workers > 0:
                QMessageBox.information(page, "Option", "Wait for the current download to finish before changing the queue.")
                return
            row = table.currentRow()
            if row < 0:
                return
            flag = table.item(row, 0).text()
            needs_value = table.item(row, 1).text() == "Yes"
            if needs_value and not value.text().strip():
                QMessageBox.warning(page, "Option", f"{flag} requires a value.")
                return
            extra = flag
            if needs_value:
                extra += " " + quote_arg_for_preview(value.text().strip())
            self.txt_commands.setPlainText(
                append_extra_args_to_database_text(self.txt_commands.toPlainText(), extra)
            )
            if not self._rebuild_from_text():
                return

        search.textChanged.connect(refresh_catalog)
        apply_button.clicked.connect(apply_option)
        refresh_catalog()
        command = command_string_to_argv(self.gdl_cmd or "gallery-dl")
        if command:
            catalog_process.finished.connect(catalog_finished)
            catalog_process.start(command[0], [*command[1:], "--help"])
            catalog_timeout.start(8_000)

        filter_page = QWidget()
        filter_layout = QVBoxLayout(filter_page)
        joiner = QComboBox()
        joiner.addItems(["and", "or"])
        grid = QGridLayout()
        grid.addWidget(QLabel("Metadata field"), 0, 0)
        grid.addWidget(QLabel("Operator"), 0, 1)
        grid.addWidget(QLabel("Value"), 0, 2)
        rule_fields: list[tuple[QLineEdit, QComboBox, QLineEdit]] = []
        defaults = [("extension", "in", '("jpg", "png")'), ("width", ">=", "1920"), ("", "==", "")]
        for index, (field_default, op_default, value_default) in enumerate(defaults, start=1):
            field = QLineEdit(field_default)
            operator = QComboBox()
            operator.addItems(["==", "!=", ">", ">=", "<", "<=", "in", "not in", "contains"])
            operator.setCurrentText(op_default)
            rule_value = QLineEdit(value_default)
            grid.addWidget(field, index, 0)
            grid.addWidget(operator, index, 1)
            grid.addWidget(rule_value, index, 2)
            rule_fields.append((field, operator, rule_value))
        filter_layout.addLayout(grid)
        filter_layout.addWidget(QLabel("Join rules with"))
        filter_layout.addWidget(joiner)
        preview = QLineEdit()
        preview.setReadOnly(True)
        filter_layout.addWidget(QLabel("Expression preview"))
        filter_layout.addWidget(preview)
        filter_buttons = QHBoxLayout()
        build_button = QPushButton("Build")
        copy_button = QPushButton("Copy")
        apply_filter = QPushButton("Apply --filter to Current Input")
        filter_buttons.addWidget(build_button)
        filter_buttons.addWidget(copy_button)
        filter_buttons.addWidget(apply_filter)
        filter_layout.addLayout(filter_buttons)
        filter_layout.addStretch(1)
        splitter.addTab(filter_page, "Visual Filter Builder")

        def build() -> str:
            try:
                expression = build_filter_expression(
                    [(field.text(), operator.currentText(), rule_value.text()) for field, operator, rule_value in rule_fields],
                    joiner.currentText(),
                )
            except ValueError as exc:
                QMessageBox.warning(page, "Filter", str(exc))
                return ""
            preview.setText(expression)
            return expression

        def apply_expression() -> None:
            if self.active_workers > 0:
                QMessageBox.information(page, "Filter", "Wait for the current download to finish before changing the queue.")
                return
            expression = build()
            if not expression:
                return
            extra = "--filter " + quote_arg_for_preview(expression)
            self.txt_commands.setPlainText(
                append_extra_args_to_database_text(self.txt_commands.toPlainText(), extra)
            )
            if not self._rebuild_from_text():
                return

        build_button.clicked.connect(build)
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(build()))
        apply_filter.clicked.connect(apply_expression)
        build()
        return page

    # --------------------------------------------------------------------- runtime
    def _managed_python(self) -> Path:
        return MANAGED_VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def _managed_gallery_dl(self) -> Path:
        return MANAGED_VENV_DIR / ("Scripts/gallery-dl.exe" if os.name == "nt" else "bin/gallery-dl")

    def _build_runtime_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        current = QLabel(f"Current command: {self.gdl_cmd or 'not found'}")
        managed = QLabel(f"Managed runtime: {self._managed_gallery_dl()}")
        managed.setWordWrap(True)
        output = QPlainTextEdit()
        output.setReadOnly(True)
        output.setPlaceholderText("Runtime installation output")
        buttons = QHBoxLayout()
        install = QPushButton("Install / Update Managed Runtime")
        use_managed = QPushButton("Use Managed Runtime")
        buttons.addWidget(install)
        buttons.addWidget(use_managed)
        buttons.addStretch(1)
        layout.addWidget(current)
        layout.addWidget(managed)
        layout.addLayout(buttons)
        layout.addWidget(output, 1)

        def runtime_message(text: str) -> None:
            if not text:
                return
            if isValid(output):
                output.appendPlainText(text)
            else:
                # Runtime installation intentionally survives closing the
                # Management dialog, so keep progress observable in main log.
                self.append_log(f"[runtime] {text}")

        def append_output() -> None:
            process = self._runtime_process
            if process is not None and isValid(process):
                text = bytes(process.readAllStandardOutput()).decode(errors="replace")
                if text:
                    runtime_message(text.rstrip())

        def start_install() -> None:
            if self.active_workers > 0 or (self._runtime_process and self._runtime_process.state() != QProcess.NotRunning):
                QMessageBox.information(page, "Runtime", "Wait for downloads or the current runtime operation to finish.")
                return
            answer = QMessageBox.question(
                page, "Managed Runtime",
                f"Create or update an isolated Python environment at:\n{MANAGED_VENV_DIR}\n\nContinue?",
            )
            if answer != QMessageBox.Yes:
                return
            SECURE_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            output.clear()
            process = QProcess(self)
            process.setProcessChannelMode(QProcess.MergedChannels)
            process.readyReadStandardOutput.connect(append_output)
            process.finished.connect(finish_stage)
            process.errorOccurred.connect(runtime_error)
            self._runtime_process = process
            if self._managed_python().is_file():
                self._runtime_stage = "install"
                process.start(str(self._managed_python()), ["-m", "pip", "install", "--upgrade", "gallery-dl"])
            else:
                self._runtime_stage = "venv"
                process.start(sys.executable, ["-m", "venv", str(MANAGED_VENV_DIR)])

        def runtime_error(_process_error) -> None:
            process = self._runtime_process
            detail = process.errorString() if process is not None and isValid(process) else "process unavailable"
            runtime_message(f"Runtime operation could not start: {detail}")
            self._runtime_stage = ""

        def finish_stage(exit_code: int, _exit_status) -> None:
            append_output()
            if exit_code != 0:
                runtime_message(f"Runtime operation failed with exit code {exit_code}.")
                self._runtime_stage = ""
                return
            if self._runtime_stage == "venv":
                self._runtime_stage = "install"
                assert self._runtime_process is not None
                self._runtime_process.start(str(self._managed_python()), ["-m", "pip", "install", "--upgrade", "gallery-dl"])
                return
            executable = self._managed_gallery_dl()
            if executable.is_file():
                self.gdl_cmd = str(executable)
                self._refresh_env()
                self.autosave_session()
                if isValid(current):
                    current.setText(f"Current command: {self.gdl_cmd}")
                runtime_message("Managed runtime is ready and selected.")
            else:
                runtime_message("Runtime operation finished, but gallery-dl was not created.")
            self._runtime_stage = ""

        def activate_managed() -> None:
            executable = self._managed_gallery_dl()
            if not executable.is_file():
                QMessageBox.warning(page, "Runtime", "Install the managed runtime first.")
                return
            self.gdl_cmd = str(executable)
            self._refresh_env()
            self.autosave_session()
            current.setText(f"Current command: {self.gdl_cmd}")

        install.clicked.connect(start_install)
        use_managed.clicked.connect(activate_managed)
        return page


__all__ = [
    "MANAGED_VENV_DIR",
    "ManagementMixin",
    "common_account_profile_id",
    "format_schedule_timestamp",
    "interrupted_recovery_commands",
    "library_records_to_database_text",
]
