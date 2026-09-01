"""Persistent storage for library, scheduler, recovery, and account metadata.

The gallery-dl download archive and this database serve different purposes.
gallery-dl's archive tracks downloaded media IDs; this database tracks GUI
jobs, runs, schedules, and user-facing library records.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from .core import (
    APP_DIR,
    HISTORY_FILE,
    HISTORY_MAX_BYTES,
    MAX_LOG_LINE_CHARS,
    redact_sensitive_text,
    redact_sensitive_database_text,
    safe_bool,
    safe_int,
    timestamp_slug,
    unique_path,
)


FEATURE_DB = APP_DIR / "library.sqlite3"


def _recent_bounded_jsonl_lines(path: Path) -> Iterator[str]:
    """Yield complete recent JSONL rows without allocating an oversized row."""
    with path.open("rb") as source_file:
        size = path.stat().st_size
        start = max(0, size - HISTORY_MAX_BYTES)
        remaining = HISTORY_MAX_BYTES

        def read_fragment() -> bytes:
            nonlocal remaining
            if remaining <= 0:
                return b""
            fragment = source_file.readline(
                min(MAX_LOG_LINE_CHARS + 1, remaining)
            )
            remaining -= len(fragment)
            return fragment

        if start:
            source_file.seek(start)
            # The tail normally begins inside a row. Drain that partial row in
            # bounded chunks; an unlimited readline could allocate the giant
            # row this migration is specifically trying to avoid.
            while True:
                fragment = read_fragment()
                if not fragment or fragment.endswith((b"\n", b"\r")):
                    break
        while True:
            raw = read_fragment()
            if not raw:
                break
            if len(raw) > MAX_LOG_LINE_CHARS:
                while raw and not raw.endswith((b"\n", b"\r")):
                    raw = read_fragment()
                continue
            yield raw.decode("utf-8", errors="replace")


class FeatureStore:
    """Small connection-per-operation SQLite repository.

    A connection is never shared with a worker thread. WAL mode keeps short UI
    reads responsive while result records are being committed.
    """

    def __init__(self, path: str | Path = FEATURE_DB) -> None:
        self.path = Path(path)
        self.recovered_corrupt_path: Path | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._initialize()
        except sqlite3.DatabaseError as exc:
            message = str(exc).lower()
            corruption_markers = (
                "file is not a database",
                "database disk image is malformed",
                "database is malformed",
                "file is encrypted",
            )
            if not any(marker in message for marker in corruption_markers) or not self.path.exists():
                raise
            backup = unique_path(
                self.path.with_name(self.path.name + f".corrupt_{timestamp_slug()}")
            )
            os.replace(self.path, backup)
            # WAL/SHM files belong to the corrupt database too. Leaving them at
            # the original basename can poison the freshly-created replacement.
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(self.path) + suffix)
                if sidecar.exists():
                    os.replace(sidecar, Path(str(backup) + suffix))
            self.recovered_corrupt_path = backup
            self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    service TEXT NOT NULL DEFAULT '-',
                    item_id TEXT NOT NULL DEFAULT '-',
                    url TEXT NOT NULL,
                    return_code INTEGER,
                    error_type TEXT NOT NULL DEFAULT 'unknown',
                    downloaded INTEGER NOT NULL DEFAULT 0,
                    skipped INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_history_time ON history(id DESC);
                CREATE INDEX IF NOT EXISTS idx_history_url ON history(url);

                CREATE TABLE IF NOT EXISTS library_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL UNIQUE,
                    command TEXT NOT NULL DEFAULT '',
                    service TEXT NOT NULL DEFAULT '-',
                    tag TEXT NOT NULL DEFAULT '',
                    output_dir TEXT NOT NULL DEFAULT '',
                    account_profile_id INTEGER,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_run_at REAL,
                    last_status TEXT NOT NULL DEFAULT 'never',
                    last_downloaded INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_library_service ON library_entries(service);

                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'manual'
                );
                CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

                CREATE TABLE IF NOT EXISTS run_items (
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    job_index INTEGER NOT NULL,
                    command TEXT NOT NULL,
                    url TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    downloaded INTEGER NOT NULL DEFAULT 0,
                    skipped INTEGER NOT NULL DEFAULT 0,
                    return_code INTEGER,
                    message TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (run_id, job_index)
                );

                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    command_text TEXT NOT NULL,
                    frequency TEXT NOT NULL DEFAULT 'daily',
                    interval_minutes INTEGER NOT NULL DEFAULT 1440,
                    time_of_day TEXT NOT NULL DEFAULT '02:00',
                    weekdays TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    account_profile_id INTEGER,
                    next_run_at REAL NOT NULL,
                    last_run_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_schedule_due ON schedules(enabled, next_run_at);

                CREATE TABLE IF NOT EXISTS account_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    site TEXT NOT NULL,
                    auth_kind TEXT NOT NULL,
                    username TEXT NOT NULL DEFAULT '',
                    cookie_source TEXT NOT NULL DEFAULT '',
                    secret_key TEXT NOT NULL DEFAULT 'password',
                    secret_ref TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            columns = {str(row[1]) for row in db.execute("PRAGMA table_info(library_entries)")}
            if "command" not in columns:
                db.execute("ALTER TABLE library_entries ADD COLUMN command TEXT NOT NULL DEFAULT ''")
            schedule_columns = {
                str(row[1]) for row in db.execute("PRAGMA table_info(schedules)")
            }
            if "account_profile_id" not in schedule_columns:
                db.execute("ALTER TABLE schedules ADD COLUMN account_profile_id INTEGER")

    def _meta(self, key: str) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else None

    def _set_meta(self, key: str, value: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def migrate_jsonl_history(self, source: str | Path = HISTORY_FILE) -> int:
        """Import legacy history once and leave the source untouched."""
        if self._meta("jsonl_history_migrated") == "1":
            return 0
        path = Path(source)
        imported = 0
        # A directory (or another special filesystem entry) at the legacy
        # filename must not prevent the entire application from starting.
        # Leave the migration marker unset so a repaired file can be imported
        # on a later launch.
        if path.exists() and not path.is_file():
            return 0
        if path.is_file():
            try:
                lines = _recent_bounded_jsonl_lines(path)
                with self._connect() as db:
                    for line in lines:
                        try:
                            record = json.loads(line)
                        except (TypeError, ValueError):
                            continue
                        if not isinstance(record, dict) or not record.get("url"):
                            continue
                        db.execute(
                            """INSERT INTO history(
                                occurred_at, status, service, item_id, url,
                                return_code, error_type, downloaded, skipped, message
                            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                str(record.get("time") or ""),
                                str(record.get("status") or "unknown"),
                                str(record.get("service") or "-"),
                                str(record.get("id") or "-"),
                                redact_sensitive_text(str(record.get("url"))),
                                record.get("rc"),
                                str(record.get("error_type") or "unknown"),
                                safe_int(record.get("downloaded"), 0, 0),
                                safe_int(record.get("skipped"), 0, 0),
                                redact_sensitive_text(str(record.get("message") or "")),
                            ),
                        )
                        imported += 1
            except OSError:
                # Leave the marker unset so transient filesystem errors can be
                # retried at the next startup instead of breaking initialization.
                return 0
        self._set_meta("jsonl_history_migrated", "1")
        return imported

    def record_history(self, record: dict[str, object]) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO history(
                    occurred_at, status, service, item_id, url, return_code,
                    error_type, downloaded, skipped, message
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(record.get("time") or time.strftime("%Y-%m-%d %H:%M:%S")),
                    str(record.get("status") or "unknown"),
                    str(record.get("service") or "-"),
                    str(record.get("id") or "-"),
                    redact_sensitive_text(str(record.get("url") or "")),
                    record.get("rc"),
                    str(record.get("error_type") or "unknown"),
                    safe_int(record.get("downloaded"), 0, 0),
                    safe_int(record.get("skipped"), 0, 0),
                    redact_sensitive_text(str(record.get("message") or "")),
                ),
            )

    def list_history(self, limit: int = 500, search: str = "") -> list[dict[str, object]]:
        limit = max(1, min(int(limit), 5000))
        query = "SELECT * FROM history"
        params: list[object] = []
        if search.strip():
            query += " WHERE url LIKE ? OR service LIKE ? OR status LIKE ?"
            needle = f"%{search.strip()}%"
            params.extend([needle, needle, needle])
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return [dict(row) for row in reversed(rows)]

    def clear_history(self) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM history")

    def add_library_entry(
        self,
        *,
        url: str,
        title: str = "",
        service: str = "-",
        tag: str = "",
        output_dir: str = "",
        account_profile_id: int | None = None,
        command: str = "",
    ) -> int:
        now = time.time()
        with self._connect() as db:
            db.execute(
                """INSERT INTO library_entries(
                    title, url, command, service, tag, output_dir, account_profile_id,
                    created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    title=CASE WHEN excluded.title='' THEN library_entries.title ELSE excluded.title END,
                    command=CASE WHEN excluded.command='' THEN library_entries.command ELSE excluded.command END,
                    service=excluded.service,
                    tag=excluded.tag,
                    output_dir=excluded.output_dir,
                    account_profile_id=excluded.account_profile_id,
                    updated_at=excluded.updated_at""",
                (
                    title,
                    redact_sensitive_text(url.strip()),
                    redact_sensitive_text(command),
                    service or "-",
                    tag,
                    output_dir,
                    account_profile_id,
                    now,
                    now,
                ),
            )
            row = db.execute(
                "SELECT id FROM library_entries WHERE url=?",
                (redact_sensitive_text(url.strip()),),
            ).fetchone()
        return int(row[0])

    def list_library(self, search: str = "") -> list[dict[str, object]]:
        query = "SELECT * FROM library_entries"
        params: list[object] = []
        if search.strip():
            query += " WHERE title LIKE ? OR url LIKE ? OR service LIKE ? OR tag LIKE ?"
            needle = f"%{search.strip()}%"
            params.extend([needle] * 4)
        query += " ORDER BY updated_at DESC, id DESC"
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def delete_library_entries(self, ids: Iterable[int]) -> None:
        values = [(int(item_id),) for item_id in ids]
        if not values:
            return
        with self._connect() as db:
            db.executemany("DELETE FROM library_entries WHERE id=?", values)

    def update_library_result(self, url: str, status: str, downloaded: int) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE library_entries SET last_run_at=?, last_status=?,
                   last_downloaded=?, updated_at=? WHERE url=?""",
                (
                    time.time(),
                    status,
                    int(downloaded),
                    time.time(),
                    redact_sensitive_text(url),
                ),
            )

    def begin_run(self, jobs: Sequence[object], indices: Sequence[int], source: str = "manual") -> int:
        now = time.time()
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO runs(started_at, status, source) VALUES(?, 'running', ?)",
                (now, source),
            )
            run_id = int(cursor.lastrowid)
            for index in indices:
                job = jobs[index]
                db.execute(
                    """INSERT INTO run_items(
                        run_id, job_index, command, url, status, updated_at
                    ) VALUES(?, ?, ?, ?, 'queued', ?)""",
                    (
                        run_id,
                        int(index),
                        redact_sensitive_text(str(getattr(job, "raw", ""))),
                        redact_sensitive_text(str(getattr(job, "url", ""))),
                        now,
                    ),
                )
        return run_id

    def mark_run_item(
        self,
        run_id: int | None,
        job_index: int,
        status: str,
        *,
        downloaded: int = 0,
        skipped: int = 0,
        return_code: int | None = None,
        message: str = "",
    ) -> None:
        if not run_id:
            return
        with self._connect() as db:
            db.execute(
                """UPDATE run_items SET status=?, downloaded=?, skipped=?,
                   return_code=?, message=?, updated_at=?
                   WHERE run_id=? AND job_index=?""",
                (
                    status,
                    int(downloaded),
                    int(skipped),
                    return_code,
                    redact_sensitive_text(message),
                    time.time(),
                    int(run_id),
                    int(job_index),
                ),
            )

    def finish_run(self, run_id: int | None, status: str = "finished") -> None:
        if not run_id:
            return
        with self._connect() as db:
            db.execute(
                "UPDATE runs SET status=?, finished_at=? WHERE id=?",
                (status, time.time(), int(run_id)),
            )

    def interrupted_runs(self) -> list[dict[str, object]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT r.id AS run_id, r.started_at, i.job_index,
                          i.command, i.url, i.status
                   FROM runs r JOIN run_items i ON i.run_id=r.id
                   WHERE r.status='running' AND i.status NOT IN ('done', 'cancelled')
                   ORDER BY r.id, i.job_index"""
            ).fetchall()
        return [dict(row) for row in rows]

    def resolve_interrupted_runs(self, run_ids: Iterable[int]) -> None:
        values = [(time.time(), int(run_id)) for run_id in set(run_ids)]
        if not values:
            return
        with self._connect() as db:
            db.executemany(
                "UPDATE runs SET status='interrupted', finished_at=? WHERE id=? AND status='running'",
                values,
            )

    def save_schedule(self, values: dict[str, object], schedule_id: int | None = None) -> int:
        now = time.time()
        payload = (
            str(values.get("name") or "Scheduled download"),
            redact_sensitive_database_text(str(values.get("command_text") or "")),
            str(values.get("frequency") or "daily"),
            safe_int(values.get("interval_minutes"), 1440, 1, 10080),
            str(values.get("time_of_day") or "02:00"),
            str(values.get("weekdays") or "0,1,2,3,4,5,6"),
            1 if safe_bool(values.get("enabled"), True) else 0,
            safe_int(values.get("account_profile_id"), 0, 0) or None,
            float(values.get("next_run_at") or now),
            now,
        )
        with self._connect() as db:
            if schedule_id:
                db.execute(
                    """UPDATE schedules SET name=?, command_text=?, frequency=?,
                       interval_minutes=?, time_of_day=?, weekdays=?, enabled=?,
                       account_profile_id=?, next_run_at=?, updated_at=? WHERE id=?""",
                    (*payload, int(schedule_id)),
                )
                return int(schedule_id)
            cursor = db.execute(
                """INSERT INTO schedules(
                    name, command_text, frequency, interval_minutes,
                    time_of_day, weekdays, enabled, account_profile_id,
                    next_run_at, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*payload[:-1], now, payload[-1]),
            )
            return int(cursor.lastrowid)

    def list_schedules(self) -> list[dict[str, object]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM schedules ORDER BY enabled DESC, next_run_at, id").fetchall()
        return [dict(row) for row in rows]

    def due_schedules(self, now: float | None = None) -> list[dict[str, object]]:
        moment = float(now if now is not None else time.time())
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM schedules WHERE enabled=1 AND next_run_at<=? ORDER BY next_run_at, id",
                (moment,),
            ).fetchall()
        return [dict(row) for row in rows]

    def advance_schedule(self, schedule_id: int, next_run_at: float, ran_at: float | None = None) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE schedules SET next_run_at=?, last_run_at=?, updated_at=? WHERE id=?",
                (
                    float(next_run_at),
                    float(ran_at if ran_at is not None else time.time()),
                    time.time(),
                    int(schedule_id),
                ),
            )

    def defer_schedule(self, schedule_id: int, next_run_at: float) -> None:
        """Move a failed dispatch forward without claiming that it ran."""
        with self._connect() as db:
            db.execute(
                "UPDATE schedules SET next_run_at=?, updated_at=? WHERE id=?",
                (float(next_run_at), time.time(), int(schedule_id)),
            )

    def delete_schedules(self, ids: Iterable[int]) -> None:
        values = [(int(item_id),) for item_id in ids]
        if not values:
            return
        with self._connect() as db:
            db.executemany("DELETE FROM schedules WHERE id=?", values)

    def save_account(self, values: dict[str, object], account_id: int | None = None) -> int:
        now = time.time()
        payload = (
            str(values.get("name") or "").strip(),
            str(values.get("site") or "").strip().lower(),
            str(values.get("auth_kind") or "browser"),
            str(values.get("username") or ""),
            str(values.get("cookie_source") or ""),
            str(values.get("secret_key") or "password"),
            str(values.get("secret_ref") or ""),
            now,
        )
        if not payload[0] or not payload[1]:
            raise ValueError("Account name and site are required")
        with self._connect() as db:
            if account_id:
                db.execute(
                    """UPDATE account_profiles SET name=?, site=?, auth_kind=?,
                       username=?, cookie_source=?, secret_key=?, secret_ref=?,
                       updated_at=? WHERE id=?""",
                    (*payload, int(account_id)),
                )
                return int(account_id)
            cursor = db.execute(
                """INSERT INTO account_profiles(
                    name, site, auth_kind, username, cookie_source,
                    secret_key, secret_ref, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*payload[:-1], now, payload[-1]),
            )
            return int(cursor.lastrowid)

    def list_accounts(self) -> list[dict[str, object]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM account_profiles ORDER BY name COLLATE NOCASE").fetchall()
        return [dict(row) for row in rows]

    def account(self, account_id: int | None) -> dict[str, object] | None:
        if not account_id:
            return None
        with self._connect() as db:
            row = db.execute("SELECT * FROM account_profiles WHERE id=?", (int(account_id),)).fetchone()
        return dict(row) if row else None

    def delete_accounts(self, ids: Iterable[int]) -> list[str]:
        values = [int(item_id) for item_id in ids]
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        with self._connect() as db:
            rows = db.execute(
                f"SELECT secret_ref FROM account_profiles WHERE id IN ({placeholders})", values
            ).fetchall()
            db.execute(f"DELETE FROM account_profiles WHERE id IN ({placeholders})", values)
        return [str(row[0]) for row in rows if row[0]]


__all__ = ["FEATURE_DB", "FeatureStore"]
