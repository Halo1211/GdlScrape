from __future__ import annotations

import os
import queue
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from .core import (
    IS_WINDOWS,
    MAX_LOG_LINE_CHARS,
    STOP_GRACE_SECONDS,
    classify_error,
    command_string_to_argv,
    ensure_no_option,
    normalize_destination_argv,
    normalize_process_return_code,
    redact_sensitive_argv,
    redact_sensitive_text,
    sanitize_service_policy,
    split_command,
    strip_gallery_dl_invocation,
)
from .models import DownloadJob


def _secure_temporary_path(target: Path) -> Path:
    """Reserve a private, collision-resistant sibling path for atomic output."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    os.close(descriptor)
    return Path(temporary_name)


def _redact_exception_with_argv(exc: Exception, argv: list[str]) -> str:
    """Redact argv values embedded in subprocess exception representations."""
    message = redact_sensitive_text(str(exc))
    for original, redacted in zip(argv, redact_sensitive_argv(argv), strict=True):
        if original and original != redacted:
            # subprocess exceptions render argv through ``repr(list)``. Match
            # that exact token boundary: replacing a short raw secret such as
            # ``a`` would otherwise corrupt every word containing that letter.
            message = message.replace(repr(original), repr(redacted))
    return redact_sensitive_text(message)


class DownloadWorker(QThread):
    log = Signal(int, str)
    status = Signal(int, str, str)
    job_started = Signal(int, int, str)
    job_done = Signal(int, int, str, int, int, int, int, int, str)
    finished_all = Signal(int)

    re_skip = re.compile(r"^# ")
    re_error = re.compile(r"\b(error|exception|traceback|failed)\b", re.I)
    re_warn = re.compile(r"\b(warning|warn)\b", re.I)
    re_download = re.compile(r"(?:^|\s)(?:[A-Za-z]:[\\/]|/|\\\\|\./|\.\\)")
    # Two jobs can legitimately point at the same explicit destination. Keep
    # conversion/archive passes serialized so they never rewrite one archive
    # concurrently or race over the same PNG files.
    _postprocess_lock = threading.Lock()

    def __init__(
        self,
        worker_id: int,
        task_queue: "queue.Queue[tuple[int, DownloadJob]]",
        gdl_cmd: str,
        config_path: str | None,
        output_dir: str,
        cookies_browser: str,
        retries: int,
        extra_args: str,
        pause_event: threading.Event,
        stop_event: threading.Event,
        cancelled_indices: set[int],
        compress_enabled: bool,
        compress_format: str,
        convert_png_webp: bool,
        service_policy: Optional[dict[str, dict[str, int]]] = None,
        service_locks: Optional[dict[str, threading.BoundedSemaphore]] = None,
        cancel_lock: Optional[threading.Lock] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.worker_id = worker_id
        self.task_queue = task_queue
        self.gdl_cmd = gdl_cmd
        self.config_path = config_path
        self.output_dir = output_dir.strip()
        self.cookies_browser = cookies_browser.strip().lower()
        self.retries = retries
        self.extra_args = extra_args.strip()
        self.pause_event = pause_event
        self.stop_event = stop_event
        self.cancelled_indices = cancelled_indices
        # Lock guarding reads of the shared cancel set across threads. When not
        # supplied (e.g. preview construction) a private lock keeps behaviour safe.
        self._cancel_lock = cancel_lock or threading.Lock()
        self.compress_enabled = compress_enabled
        self.compress_format = compress_format
        self.convert_png_webp = convert_png_webp
        self.service_policy = sanitize_service_policy(service_policy)
        self.service_locks = service_locks or {}
        self._proc: Optional[subprocess.Popen] = None
        self.current_job_idx: Optional[int] = None
        self._lock = threading.Lock()

    def job_policy(self, job: DownloadJob) -> dict[str, int]:
        return self.service_policy.get(str(job.service or "-").lower(), {})

    def is_cancelled(self, idx: int) -> bool:
        """Thread-safe membership test against the shared cancel set."""
        with self._cancel_lock:
            return idx in self.cancelled_indices

    def wait_for_service_slot(self, job: DownloadJob, idx: int | None = None) -> Optional[threading.BoundedSemaphore]:
        key = str(job.service or "-").lower()
        lock = self.service_locks.get(key)
        if lock is None:
            return None
        self.status.emit(self.worker_id, "queued", f"waiting service slot: {key}")
        while not self.stop_event.is_set():
            # A job cancelled while queued for a service slot must not keep the
            # worker blocked here (under a tight service limit this wait can be
            # minutes long). Bail out; the caller's cancel check finalizes it.
            if idx is not None and self.is_cancelled(idx):
                return None
            if lock.acquire(timeout=0.2):
                return lock
        return None

    def apply_service_delay(self, job: DownloadJob, idx: int | None = None) -> None:
        delay = int(self.job_policy(job).get("delay", 0) or 0)
        if delay <= 0:
            return
        self.status.emit(self.worker_id, "paused", f"service delay {delay}s: {job.service}")
        end = time.time() + delay
        while (
            time.time() < end
            and not self.stop_event.is_set()
            and not (idx is not None and self.is_cancelled(idx))
        ):
            time.sleep(min(0.2, max(0.0, end - time.time())))

    def build_command(self, job: DownloadJob) -> list[str]:
        base = command_string_to_argv(self.gdl_cmd)
        if not base:
            raise FileNotFoundError("gallery-dl command is empty")

        if job.is_command:
            tokens = normalize_destination_argv(split_command(job.raw))
            args = strip_gallery_dl_invocation(tokens)
        else:
            # A hand-written row may be ``URL --range 1-10``.  Passing the
            # entire line as one argv element makes gallery-dl treat the spaces
            # and options as part of the URL.  Tokenize option-bearing rows,
            # while preserving a single raw URL as one argument.
            tokens = normalize_destination_argv(split_command(job.raw))
            args = tokens if len(tokens) > 1 else [job.raw]

        final = list(base)
        config_path = Path(self.config_path).expanduser() if self.config_path else None
        if config_path and config_path.is_file() and ensure_no_option(
            args,
            ("-c", "--config", "--config-ignore"),
        ):
            final += ["--config", str(config_path)]
        if self.output_dir and job.dest == "-" and ensure_no_option(
            args,
            ("-d", "--destination", "-D", "--directory"),
        ):
            final += ["-d", self.output_dir]
        if self.cookies_browser and self.cookies_browser != "none" and ensure_no_option(
            args,
            ("--cookies-from-browser", "-C", "--cookies"),
        ):
            final += ["--cookies-from-browser", self.cookies_browser]
        # gallery-dl accepts both -R and --retries; recognize the short form so a
        # user-supplied "-R 5" in the row is not duplicated by a GUI --retries
        # (same class of bug as the -c/--config fix in stab3.0).
        policy_retries = int(self.job_policy(job).get("retries", self.retries) or 0)
        if policy_retries > 0 and ensure_no_option(args, ("-R", "--retries")):
            final += ["--retries", str(policy_retries)]
        if self.extra_args:
            final += split_command(self.extra_args)
        final += args
        return final

    def terminate_current_process(self) -> None:
        proc = self._proc
        if not proc or proc.poll() is not None:
            return
        try:
            if IS_WINDOWS:
                # The child is started with CREATE_NEW_PROCESS_GROUP, but the
                # old code called proc.terminate() (TerminateProcess), which
                # kills ONLY the direct child: ffmpeg/yt-dlp grandchildren that
                # gallery-dl spawns (ugoira conversion, ytdl downloads) kept
                # running orphaned and held file locks in the output folder.
                # CTRL_BREAK_EVENT reaches the whole process group and lets
                # gallery-dl shut down cleanly (.part cleanup).
                os.kill(proc.pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        deadline = time.time() + STOP_GRACE_SECONDS
        while time.time() < deadline:
            if proc.poll() is not None:
                return
            time.sleep(0.05)
        try:
            if IS_WINDOWS:
                # Force-kill the whole child TREE. taskkill /T /F also takes
                # grandchildren down; plain proc.kill() does not.
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True,
                    check=False,
                    timeout=10,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if proc.poll() is None:
                    proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _terminate_current_process_async(self) -> None:
        threading.Thread(
            target=self.terminate_current_process,
            name=f"gallery-dl-kill-w{self.worker_id + 1}",
            daemon=True,
        ).start()

    def cancel_job(self, idx: int) -> None:
        # Cancel only the selected active process. Do not set the shared stop_event,
        # because that would stop the whole batch when the user cancels one row.
        with self._lock:
            is_current = self.current_job_idx == idx
        if is_current:
            self._terminate_current_process_async()

    def postprocessing_interrupted(self) -> bool:
        with self._lock:
            idx = self.current_job_idx
        return self.stop_event.is_set() or (idx is not None and self.is_cancelled(idx))

    def maybe_compress(self, job: DownloadJob) -> str:
        if not self.compress_enabled and not self.convert_png_webp:
            return ""
        if self.postprocessing_interrupted():
            return "post-processing stopped"
        # The fallback output is shared by every worker. Recursive conversion
        # or compression there would race and write the same files after every
        # completed job, so post-processing requires a per-job destination.
        if job.dest in ("", "-"):
            return "post-processing skipped: set an explicit per-job destination to avoid shared-output races"
        target = job.dest
        if not target:
            return "post-processing skipped: destination unknown"
        folder = Path(target).expanduser()
        if not folder.exists() or not folder.is_dir():
            return f"compression skipped: folder not found: {folder}"

        conversion_errors: list[str] = []
        if self.convert_png_webp:
            try:
                from PIL import Image  # type: ignore
                converted = 0
                failed = 0
                skipped_existing = 0
                for png in folder.rglob("*.png"):
                    if self.postprocessing_interrupted():
                        self.log.emit(self.worker_id, "[post] PNG to WebP stopped")
                        break
                    if png.is_symlink():
                        continue
                    webp = png.with_suffix(".webp")
                    # Retries and multiple jobs sharing one destination hit the
                    # same folder repeatedly; do not redo finished conversions.
                    if webp.exists():
                        skipped_existing += 1
                        continue
                    # Per-file guard: one corrupt/truncated PNG must not abort
                    # the whole conversion pass (previously the loop-level
                    # except skipped every remaining file). A failed save also
                    # must not leave a partial .webp behind.
                    temporary_webp: Path | None = None
                    try:
                        temporary_webp = _secure_temporary_path(webp)
                        with Image.open(png) as im:
                            im.save(temporary_webp, "WEBP", quality=90)
                        if self.postprocessing_interrupted():
                            temporary_webp.unlink(missing_ok=True)
                            break
                        os.replace(temporary_webp, webp)
                        converted += 1
                    except Exception as exc:
                        failed += 1
                        try:
                            if temporary_webp is not None:
                                temporary_webp.unlink(missing_ok=True)
                        except Exception:
                            pass
                        self.log.emit(self.worker_id, f"[post] PNG to WebP failed for {png.name}: {exc}")
                if converted or failed or skipped_existing:
                    self.log.emit(
                        self.worker_id,
                        f"[post] PNG to WebP: converted {converted}, failed {failed}, already done {skipped_existing}",
                    )
                if failed:
                    conversion_errors.append(
                        f"conversion failed: {failed} PNG file(s) could not be converted"
                    )
            except Exception as exc:
                # Import error (Pillow missing) or folder-level failure.
                self.log.emit(self.worker_id, f"[post] PNG to WebP skipped: {exc}")
                conversion_errors.append(f"conversion failed: {exc}")

        if not self.compress_enabled:
            return "; ".join(conversion_errors)
        if self.postprocessing_interrupted():
            return "post-processing stopped"

        fmt = self.compress_format.lower()
        if fmt == "zip":
            # Use the full folder name as the archive base. ``with_suffix`` would
            # corrupt names that contain dots (e.g. "Creator.v2" -> "Creator.zip").
            archive = folder.parent / (folder.name + ".zip")
            temporary: Path | None = None
            try:
                temporary = _secure_temporary_path(archive)
                with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    for file in folder.rglob("*"):
                        if self.postprocessing_interrupted():
                            raise InterruptedError("post-processing stopped")
                        # Never include the archive we are currently writing.
                        if (
                            not file.is_symlink()
                            and file.is_file()
                            and file.resolve() not in {archive.resolve(), temporary.resolve()}
                        ):
                            zf.write(file, file.relative_to(folder.parent))
                os.replace(temporary, archive)
                success = f"compressed: {archive}"
                return "; ".join([*conversion_errors, success])
            except Exception as exc:
                try:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                return "; ".join([*conversion_errors, f"compression failed: {exc}"])

        sevenz = shutil.which("7z") or shutil.which("7za") or shutil.which("7zz")
        if not sevenz:
            return "; ".join([
                *conversion_errors,
                "compression failed: 7z/7za/7zz not found",
            ])
        # Comic-book formats are ordinary archives with a renamed extension;
        # 7-Zip has no "-tcbz" type and rejects it with "Unsupported archive
        # type" (rc=2), so cbz/cb7/cbr compression produced NO archive at all.
        # Map the extension to the real 7-Zip type but keep the .cb* filename.
        cb_type = {"cbz": "zip", "cb7": "7z", "cbr": "zip"}.get(fmt)
        archive_type = cb_type or fmt
        archive_path = Path(str(folder) + f".{fmt}")
        temporary_path: Path | None = None
        try:
            # Reserve a private name, then remove the empty placeholder because
            # 7-Zip expects to create the archive itself. The random mkstemp
            # component prevents stale/colliding predictable names.
            temporary_path = _secure_temporary_path(archive_path)
            temporary_path.unlink()
            cmd = [
                sevenz,
                "a",
                f"-t{archive_type}",
                str(temporary_path),
                str(folder),
            ]
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if IS_WINDOWS else 0
            cp = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
                start_new_session=not IS_WINDOWS,
            )
            self._proc = cp
            while True:
                try:
                    stdout, stderr = cp.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    if self.postprocessing_interrupted():
                        self.terminate_current_process()
            if self.postprocessing_interrupted():
                temporary_path.unlink(missing_ok=True)
                return "post-processing stopped"
            if cp.returncode == 0:
                os.replace(temporary_path, archive_path)
                success = f"compressed: {archive_path}"
                return "; ".join([*conversion_errors, success])
            temporary_path.unlink(missing_ok=True)
            return "; ".join([
                *conversion_errors,
                f"compression failed rc={cp.returncode}: {(stderr or stdout)[-300:]}",
            ])
        except Exception as exc:
            return "; ".join([*conversion_errors, f"compression failed: {exc}"])
        finally:
            try:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    def run(self) -> None:
        while not self.stop_event.is_set():
            if self.pause_event.is_set():
                # Emit once per pause, then wait quietly. The old loop emitted
                # "paused" every 150 ms PER WORKER (8 workers ≈ 53 queued
                # signals/sec into the GUI thread for the whole pause).
                self.status.emit(self.worker_id, "paused", "waiting")
                while self.pause_event.is_set() and not self.stop_event.is_set():
                    time.sleep(0.15)
                if self.stop_event.is_set():
                    break
            try:
                idx, job = self.task_queue.get_nowait()
            except queue.Empty:
                break

            if self.is_cancelled(idx):
                self.job_done.emit(self.worker_id, idx, "cancelled", 0, 0, 0, 0, -1, "cancelled before start")
                self.task_queue.task_done()
                continue

            with self._lock:
                self.current_job_idx = idx
            downloaded = skipped = errors = warnings = 0
            last_progress_log = time.monotonic()
            last_progress_total = 0
            tail_lines: list[str] = []
            # Keep the real URL in job.raw/build_command, but never surface
            # obvious access tokens or signed query values in worker status,
            # activity logs, notifications, or their exported copies.
            display_url = redact_sensitive_argv([job.url])[0]
            self.status.emit(self.worker_id, "running", display_url)
            self.job_started.emit(self.worker_id, idx, display_url)
            self.log.emit(self.worker_id, f"[START] [{idx+1}] {display_url}")

            status = "failed"
            rc = -1
            message = ""
            acquired_service_lock: Optional[threading.BoundedSemaphore] = None
            try:
                acquired_service_lock = self.wait_for_service_slot(job, idx)
                if self.stop_event.is_set():
                    status = "stopped"
                    message = "stopped before subprocess start"
                    raise RuntimeError(message)
                # Cancellation must be honored before and during a configured
                # service delay. Otherwise a selected row can appear cancelled
                # while its worker remains asleep for as long as one hour.
                if self.is_cancelled(idx):
                    status = "cancelled"
                    message = "cancelled before service delay"
                    raise RuntimeError(message)
                self.apply_service_delay(job, idx)
                if self.stop_event.is_set() or self.is_cancelled(idx):
                    cancelled_now = self.is_cancelled(idx)
                    status = "cancelled" if cancelled_now else "stopped"
                    message = "cancelled before subprocess start" if cancelled_now else "stopped before subprocess start"
                    raise RuntimeError(message)
                cmd = self.build_command(job)
                creationflags = 0
                if IS_WINDOWS:
                    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=creationflags,
                    # Safe process-group creation from a QThread. preexec_fn
                    # can deadlock after fork in a multi-threaded process.
                    start_new_session=not IS_WINDOWS,
                )

                if self._proc.stdout is None:
                    raise RuntimeError("subprocess stdout is unavailable")

                interrupted = False
                while True:
                    # ``for line in pipe``/unbounded ``readline()`` allocates
                    # the complete line before the GUI can truncate it.  A
                    # malformed downloader (or remote metadata echoed without
                    # newlines) could therefore force an arbitrarily large
                    # allocation. Read one bounded fragment and drain the rest
                    # of an oversized logical line in bounded chunks.
                    line = self._proc.stdout.readline(MAX_LOG_LINE_CHARS + 1)
                    if not line:
                        break
                    if self.stop_event.is_set() or self.is_cancelled(idx):
                        self.terminate_current_process()
                        interrupted = True
                        break
                    raw_text = line.rstrip("\r\n")
                    line_ended = line.endswith(("\n", "\r"))
                    was_truncated = len(raw_text) > MAX_LOG_LINE_CHARS
                    if not line_ended and len(line) > MAX_LOG_LINE_CHARS:
                        was_truncated = True
                        while line and not line.endswith(("\n", "\r")):
                            if self.stop_event.is_set() or self.is_cancelled(idx):
                                self.terminate_current_process()
                                interrupted = True
                                break
                            line = self._proc.stdout.readline(MAX_LOG_LINE_CHARS + 1)
                        if interrupted:
                            break
                    if not raw_text:
                        continue
                    text = redact_sensitive_text(raw_text)
                    if was_truncated or len(text) > MAX_LOG_LINE_CHARS:
                        text = text[:MAX_LOG_LINE_CHARS] + " ... [truncated]"
                    tail_lines.append(text)
                    tail_lines = tail_lines[-20:]
                    if self.re_skip.search(text):
                        skipped += 1
                    elif self.re_error.search(text):
                        errors += 1
                    elif self.re_warn.search(text):
                        warnings += 1
                    elif self.re_download.search(text):
                        downloaded += 1
                    self.log.emit(self.worker_id, text)
                    progress_total = downloaded + skipped + errors + warnings
                    now = time.monotonic()
                    if progress_total > last_progress_total and (progress_total - last_progress_total >= 25 or now - last_progress_log >= 30):
                        self.log.emit(
                            self.worker_id,
                            f"[PROGRESS] [{idx+1}] downloaded={downloaded} skipped={skipped} errors={errors} warnings={warnings}",
                        )
                        last_progress_total = progress_total
                        last_progress_log = now

                # Always close the stdout pipe so its file descriptor is not
                # leaked across jobs, and so a terminated process with a full
                # output buffer cannot leave ``wait()`` blocking indefinitely
                # on Windows. Closing is safe even if the iterator drained.
                try:
                    self._proc.stdout.close()
                except Exception:
                    pass
                # Bound the wait: if the child was interrupted it has already
                # been sent SIGTERM/SIGKILL, so it should exit promptly. A
                # timeout guard prevents a hung child from freezing the worker.
                try:
                    self._proc.wait(timeout=STOP_GRACE_SECONDS + 2 if interrupted else None)
                except subprocess.TimeoutExpired:
                    self.terminate_current_process()
                    try:
                        self._proc.wait(timeout=2)
                    except Exception:
                        pass
                rc = normalize_process_return_code(self._proc.returncode)
                if self.is_cancelled(idx):
                    status = "cancelled"
                    message = "cancelled by user"
                elif self.stop_event.is_set():
                    status = "stopped"
                    message = "stopped by user"
                elif rc == 0:
                    status = "done"
                    message = "done"
                    with self._postprocess_lock:
                        post_msg = self.maybe_compress(job)
                    if post_msg:
                        self.log.emit(self.worker_id, f"[post] {post_msg}")
                        if re.search(
                            r"(?:^|; )(?:compression|conversion|post-processing) failed",
                            post_msg,
                            re.I,
                        ):
                            status = "failed"
                            errors = max(1, errors)
                            rc = -1
                            message = post_msg
                    if self.stop_event.is_set() or self.is_cancelled(idx):
                        cancelled_now = self.is_cancelled(idx)
                        status = "cancelled" if cancelled_now else "stopped"
                        message = "cancelled during post-processing" if cancelled_now else "stopped during post-processing"
                        rc = -1
                else:
                    status = "failed"
                    errors = max(1, errors)
                    message = "\n".join(tail_lines[-8:]) or f"gallery-dl exited with rc={rc}"
            except FileNotFoundError as exc:
                status = "failed"
                errors += 1
                message = f"gallery-dl not found: {exc}"
                self.log.emit(self.worker_id, f"[ERROR] {message}")
            except Exception as exc:
                # A stop/cancel that races with the read loop or process wait can
                # surface as an exception. Classify it by intent so the queue shows
                # "stopped"/"cancelled" instead of a misleading "failed".
                if self.is_cancelled(idx):
                    status = "cancelled"
                    message = "cancelled by user"
                elif self.stop_event.is_set():
                    status = "stopped"
                    message = "stopped by user"
                else:
                    status = "failed"
                    errors += 1
                    message = redact_sensitive_text(str(exc))
                    self.log.emit(self.worker_id, f"[ERROR] [{idx+1}]: {message}")
            finally:
                message = redact_sensitive_text(message)
                if acquired_service_lock is not None:
                    try:
                        acquired_service_lock.release()
                    except Exception:
                        pass
                self._proc = None
                with self._lock:
                    self.current_job_idx = None
                if status == "done":
                    self.log.emit(self.worker_id, f"[DONE] [{idx+1}] downloaded={downloaded} skipped={skipped} errors={errors} warnings={warnings}")
                elif status == "failed":
                    self.log.emit(self.worker_id, f"[FAILED] [{idx+1}] Failed ({classify_error(message)})")
                elif status == "cancelled":
                    self.log.emit(self.worker_id, f"[CANCELLED] [{idx+1}] Cancelled")
                elif status == "stopped":
                    self.log.emit(self.worker_id, f"[STOPPED] [{idx+1}] Stopped")
                self.job_done.emit(self.worker_id, idx, status, downloaded, skipped, errors, warnings, rc, message)
                self.task_queue.task_done()

        self.status.emit(self.worker_id, "idle", "")
        self.finished_all.emit(self.worker_id)

    def stop(self) -> None:
        self.stop_event.set()
        self._terminate_current_process_async()

class CommandProbeWorker(QThread):
    """Run a short command outside the GUI thread, then report stdout/stderr."""

    done = Signal(str, int, str)

    def __init__(self, command: str, args: list[str], timeout: int = 12, parent=None):
        super().__init__(parent)
        self.command = command
        self.args = list(args)
        self.timeout = timeout
        self._probe_lock = threading.Lock()
        self._probe_proc: subprocess.Popen | None = None
        self._probe_stopped = threading.Event()

    def run(self) -> None:
        cmd: list[str] = []
        try:
            if self._probe_stopped.is_set():
                return
            cmd = command_string_to_argv(self.command) + self.args
            if not cmd:
                raise FileNotFoundError("command is empty")
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if IS_WINDOWS else 0
            cp = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
                start_new_session=not IS_WINDOWS,
            )
            with self._probe_lock:
                self._probe_proc = cp
            if self._probe_stopped.is_set():
                self.stop()
            try:
                stdout, stderr = cp.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                was_user_stopped = self._probe_stopped.is_set()
                self.stop()
                try:
                    stdout, stderr = cp.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    cp.kill()
                    stdout, stderr = cp.communicate()
                if not was_user_stopped:
                    self.done.emit("", -1, _redact_exception_with_argv(exc, cmd))
                return
            streams = [
                stream.strip()
                for stream in (stdout, stderr)
                if stream and stream.strip()
            ]
            output = redact_sensitive_text(
                "\n".join(streams) or f"Return code: {cp.returncode}"
            )
            if self._probe_stopped.is_set():
                return
            self.done.emit(output, normalize_process_return_code(cp.returncode), "")
        except Exception as exc:
            if not self._probe_stopped.is_set():
                self.done.emit("", -1, _redact_exception_with_argv(exc, cmd))
        finally:
            with self._probe_lock:
                self._probe_proc = None

    def stop(self) -> None:
        self._probe_stopped.set()
        with self._probe_lock:
            proc = self._probe_proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if IS_WINDOWS:
                os.kill(proc.pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            try:
                proc.terminate()
            except OSError:
                pass

    def force_stop(self) -> None:
        """Force a probe child down when graceful shutdown did not finish."""
        self._probe_stopped.set()
        with self._probe_lock:
            proc = self._probe_proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.kill()
        except OSError:
            pass

__all__ = ['DownloadWorker', 'CommandProbeWorker']
