from __future__ import annotations

import csv
import importlib.util
import io
import math
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional
from urllib.parse import unquote_plus, urlparse

from .models import DownloadJob

IS_WINDOWS = platform.system() == "Windows"

IS_MACOS = platform.system() == "Darwin"

APP_NAME = "GdlScrape"

APP_VERSION = "1.0.3"

APP_DIR = Path.home() / ".gallery_dl_gui_dashboard"

HISTORY_FILE = APP_DIR / "job_history.jsonl"

HISTORY_MAX_BYTES = 5 * 1024 * 1024   # rotate job history past this size

HISTORY_TRIM_BYTES = 512 * 1024        # keep this much of the newest tail

AUTOSAVE_FILE = APP_DIR / "autosave_session.json"

BACKUP_DIR = APP_DIR / "backups"

PROFILES_DIR = APP_DIR / "profiles"

SETTINGS_FILE = APP_DIR / "settings.json"

MAX_LOG_BLOCKS = 7000

MAX_LOG_LINES = 20000

MAX_LOG_LINE_CHARS = 16 * 1024

STOP_GRACE_SECONDS = 3.0

MAX_IMPORT_BYTES = 50 * 1024 * 1024

MAX_QUEUE_JOBS = 20_000

MAX_COMMAND_LINE_CHARS = 64 * 1024

# Metadata/comment rows are useful, but a file with millions of disabled or
# comment-only rows must not bypass MAX_QUEUE_JOBS and monopolize the GUI
# thread. Four source rows per runnable job leaves room for tag/notes/spacing.
MAX_QUEUE_SOURCE_ROWS = MAX_QUEUE_JOBS * 4

REDACTED = "***REDACTED***"

SECRET_VALUE_FLAGS = frozenset({
    "-p", "--password",
    "-u", "--username",
    "-C",
    "--client-secret",
    "--refresh-token",
    "--access-token",
    "--api-key",
    "--cookies",
})

SECRET_OPTION_KEYS = ("password", "username", "client-secret", "client_secret",
                      "refresh-token", "refresh_token", "access-token", "access_token",
                      "api-key", "api_key", "x-api-key", "x_api_key", "authorization",
                      "proxy-authorization", "cookie", "cookies", "token", "secret")

# Authentication fields owned by the GUI's Composer and Account Profile flows.
# Clear these before switching methods so cookies, passwords, and tokens cannot
# be combined accidentally in a merged gallery-dl config.
MANAGED_AUTH_KEYS = frozenset({
    "cookies",
    "cookies-update",
    "username",
    "password",
    "api-key",
    "api-secret",
    "refresh-token",
    "access-token",
    "access-token-secret",
    "client-id",
    "client-secret",
})

def is_link_or_reparse(metadata: os.stat_result) -> bool:
    """Recognize links and Windows junctions without following their targets."""
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def iter_tree_entries(root: str | Path) -> Iterator[tuple[Path, os.stat_result]]:
    """Walk entries lazily without descending through links or junctions.

    Yield link metadata so callers can skip links or reject a linked tree.
    Read errors propagate rather than presenting an incomplete tree as complete.
    """
    pending = [Path(root)]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as children:
            for child in children:
                metadata = child.stat(follow_symlinks=False)
                path = directory / child.name
                yield path, metadata
                if stat.S_ISDIR(metadata.st_mode) and not is_link_or_reparse(metadata):
                    pending.append(path)


def process_is_running(pid: int) -> bool:
    """Probe ownership conservatively without sending a signal on Windows."""
    if not isinstance(pid, int) or not 0 < pid <= 0xFFFFFFFF:
        return False
    if pid == os.getpid():
        return True
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE only
        if not handle:
            # Access denied or another uncertain probe must not authorize
            # deleting credentials or recovering a potentially live run.
            return ctypes.get_last_error() not in (87, 1168)
        try:
            # WAIT_OBJECT_0 means exited. A zero-time wait avoids mistaking an
            # actual exit code of 259 (STILL_ACTIVE) for a running process.
            return kernel.WaitForSingleObject(handle, 0) != 0
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def jobs_to_database_text(jobs: Iterable[DownloadJob]) -> str:
    """Serialize jobs with sticky tags and per-job notes for text round trips."""
    lines: list[str] = []
    last_tag = ""
    for job in jobs:
        tag = re.sub(r"[\r\n]+", " ", job.tag).strip()
        if tag != last_tag:
            lines.append(f"# {tag}" if tag else "#")
            last_tag = tag
        if job.notes:
            lines.append(f"#@notes {' '.join(job.notes.split())}")
        lines.append(job.raw)
    return "\n".join(lines)


def is_sensitive_option_key(key: object) -> bool:
    """Recognize config keys that carry authentication material.

    Suffix matching catches user-defined names such as ``oauth-token`` while
    avoiding broad segment/substring matches that misclassify unrelated names
    such as ``tokenizer``, ``username-display``, or ``parent-session``.
    """
    normalized = str(key or "").strip().lower().replace("_", "-")
    if not normalized:
        return False
    known = {item.replace("_", "-") for item in SECRET_OPTION_KEYS}
    auth_names = set(MANAGED_AUTH_KEYS) | known | {
        "access-key",
        "credential",
        "credentials",
        "jwt",
        "private-key",
        "secret-key",
        "session",
        "session-id",
        "sessionid",
        "sid",
        "sig",
        "signature",
        "ticket",
    }
    if any(normalized == name or normalized.endswith("." + name) for name in auth_names):
        return True
    return normalized.endswith(tuple(
        suffix
        for name in (
            "credential",
            "credentials",
            "password",
            "secret",
            "signature",
            "token",
        )
        for suffix in ("-" + name, "." + name)
    ))

SENSITIVE_HEADER_NAMES = (
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
)

OFFICIAL_GALLERY_DL_LINKS = {
    "readme": "https://github.com/mikf/gallery-dl#dependencies",
    "configuration": "https://gdl-org.github.io/docs/configuration.html",
    "default_config": "https://github.com/mikf/gallery-dl/blob/master/docs/gallery-dl.conf",
}

OFFICIAL_GALLERY_DL_DEPENDENCIES = [
    ("Python 3.8+", "Required runtime for gallery-dl."),
    ("requests", "Required HTTP library used by gallery-dl."),
    ("yt-dlp / youtube-dl", "Optional: HLS/DASH video downloads and ytdl integration."),
    ("FFmpeg", "Optional: Pixiv Ugoira conversion and media processing."),
    ("mkvmerge", "Optional: accurate Ugoira frame timecodes."),
    ("PySocks", "Optional: SOCKS proxy support."),
    ("brotli / brotlicffi", "Optional: Brotli compression support."),
    ("zstandard", "Optional: Zstandard compression support."),
    ("PyYAML", "Optional: YAML config support."),
    ("toml", "Optional: TOML config support for Python < 3.11."),
    ("SecretStorage", "Optional: GNOME keyring passwords for browser cookies."),
    ("Psycopg", "Optional: PostgreSQL archive support."),
    ("truststore", "Optional: native system certificate support."),
    ("Jinja", "Optional: Jinja template support."),
]

def app_data_dir() -> Path:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    return APP_DIR

def timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def unique_path(path: str | Path) -> Path:
    """Return a non-existing path without overwriting an earlier artifact."""
    candidate = Path(path)
    if not candidate.exists():
        return candidate
    counter = 2
    while True:
        numbered = candidate.with_name(f"{candidate.name}.{counter}")
        if not numbered.exists():
            return numbered
        counter += 1

def human_size(num: int | float) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"

def safe_expand_path(text: str | Path) -> Path:
    raw = str(text).strip() or "."
    return Path(os.path.expandvars(os.path.expanduser(raw)))

def atomic_write_text(
    path: str | Path,
    text: str,
    encoding: str = "utf-8",
    newline: str | None = None,
    *,
    mode: int | None = None,
) -> None:
    """Write text using an atomic replace to reduce corrupt autosave/session files.

    Uses a per-call unique temp name so concurrent writers to the same target
    (e.g. autosave firing while a manual save runs) cannot clobber each other's
    temp file, flushes to disk before the rename so a power loss cannot leave a
    parsial/empty file behind the successful rename, and removes the temp file
    if the replace fails so no ``.tmp`` sampah is left on disk.

    ``newline`` is forwarded to ``open``; CSV callers pass ``""`` so csv's own
    ``\\r\\n`` line endings are not translated again into ``\\r\\r\\n`` on Windows.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    effective_mode = mode
    if effective_mode is None:
        try:
            # Replacing a 0600 config with a newly-created 0644 temp file would
            # silently expose stored cookies/tokens to other local users.
            effective_mode = target.stat().st_mode & 0o7777
        except OSError:
            effective_mode = None
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    tmp = Path(temporary_name)
    try:
        if effective_mode is not None:
            # Apply restrictive permissions before writing sensitive data;
            # chmod after the replace leaves a window where the temp file can
            # be read by other local users on POSIX systems. mkstemp itself
            # securely creates new files as 0600 when no prior mode exists.
            os.chmod(tmp, effective_mode)
        stream = os.fdopen(descriptor, "w", encoding=encoding, newline=newline)
        descriptor = -1
        with stream as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass

def is_false_value(value: str) -> bool:
    return str(value or "").strip().lower() in {"0", "false", "no", "n", "off", "disabled"}

def safe_bool(value: object, default: bool = False) -> bool:
    """Parse session/profile booleans without treating "false" as truthy."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return default
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "disabled", ""}:
            return False
    return default

def append_extra_args_to_command(command: str, extra: str) -> str:
    extra_args = split_command(extra)
    if not extra_args:
        return command
    tokens = split_command(command)
    if "--" in tokens:
        boundary = tokens.index("--")
        tokens[boundary:boundary] = extra_args
        return " ".join(quote_arg_for_preview(part) for part in tokens)
    return command.rstrip() + " " + " ".join(quote_arg_for_preview(part) for part in extra_args)

def append_extra_args_to_database_text(text: str, extra: str) -> str:
    """Append argv only to job rows while preserving comments and spacing."""
    output: list[str] = []
    for line in str(text).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            output.append(line)
        else:
            output.append(append_extra_args_to_command(line, extra))
    return "\n".join(output)

def command_with_destination(job: DownloadJob, destination: str) -> str:
    """Return a runnable command with ``-d`` without dropping shorthand args."""
    tokens = split_command(job.raw)
    args = strip_gallery_dl_invocation(tokens) if job.is_command else (tokens or [job.raw])
    parts = ["gallery-dl", "-d", str(destination), *args]
    return " ".join(quote_arg_for_preview(part) for part in parts)

def looks_like_database_entry(value: str) -> bool:
    text = str(value or "").strip()
    if not text or text.startswith("#"):
        return False
    if text.startswith(("http://", "https://")):
        return True
    return is_gallery_dl_invocation(split_command(text))

def sanitize_service_policy(data: object) -> dict[str, dict[str, int]]:
    if not isinstance(data, dict):
        return {}
    cleaned: dict[str, dict[str, int]] = {}
    for key, value in data.items():
        service = str(key or "").strip().lower()
        if not service or not isinstance(value, dict):
            continue
        cleaned[service] = {
            "workers": safe_int(value.get("workers"), 0, 0, 32),
            "retries": safe_int(value.get("retries"), 0, 0, 20),
            "delay": safe_int(value.get("delay"), 0, 0, 3600),
        }
    return cleaned

def read_text_safely(path: str | Path) -> str:
    # Guard against loading a huge/binary file (e.g. an accidental drag-drop of
    # a video or archive) fully into memory, which would freeze or OOM the UI.
    # A link database is text and realistically far under this cap.
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        size = 0
    if size > MAX_IMPORT_BYTES:
        raise ValueError(
            f"File is too large to import as a text database "
            f"({size // (1024 * 1024)} MB > {MAX_IMPORT_BYTES // (1024 * 1024)} MB limit)."
        )
    # Re-check at the read boundary as well as via stat. A file can grow after
    # stat (or report size 0 on a special filesystem); read_bytes() would then
    # materialize it without any limit despite the guard above.
    with p.open("rb") as handle:
        raw = handle.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise ValueError(
            f"File is too large to import as text "
            f"(more than {MAX_IMPORT_BYTES // (1024 * 1024)} MB)."
        )
    # Windows tools commonly export Unicode Text as UTF-16 with a BOM.  The
    # former latin-1 fallback decoded it without error but produced NUL-filled
    # garbage rows.  Decode BOM-marked UTF-16 first, then reject remaining NUL
    # bytes as a likely binary/wrong-file import.
    if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return raw.decode("utf-32")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if b"\x00" in raw[:8192]:
        raise ValueError("File appears to be binary or uses an unsupported text encoding.")
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def validate_cookies_txt(path: str) -> tuple[bool, str]:
    """Perform a bounded, lightweight Netscape cookies.txt format check."""
    if not str(path or "").strip():
        return False, "No cookies.txt file selected."
    target = safe_expand_path(path)
    if not target.exists() or not target.is_file():
        return False, "cookies.txt file does not exist."
    try:
        with target.open("rb") as handle:
            text = handle.read(512 * 1024).decode("utf-8-sig", errors="replace")
    except Exception as exc:
        return False, f"Could not read cookies.txt: {exc}"
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
        elif line.startswith("#"):
            continue
        if len(line.split("\t")) >= 7:
            return True, "Netscape cookies.txt format detected."
    return False, "No Netscape-format cookie rows were found (expected 7 tab-separated columns)."

def _strip_wrapping_quotes(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text

def _windows_cmdline_to_argv(text: str) -> list[str]:
    """Parse a Windows command line with the same API Windows uses.

    shlex with posix=False keeps quotes inside argv and breaks args such as
    --option="a b". Using CommandLineToArgvW prevents path-with-space and
    quote bugs on Windows. On non-Windows this function is never called.
    """
    import ctypes

    argc = ctypes.c_int()
    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    argv = shell32.CommandLineToArgvW(text, ctypes.byref(argc))
    if not argv:
        raise ValueError("CommandLineToArgvW failed")
    try:
        return [argv[i] for i in range(argc.value)]
    finally:
        kernel32.LocalFree(argv)

def split_command(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    try:
        if IS_WINDOWS:
            return _windows_cmdline_to_argv(text)
        return shlex.split(text, posix=True)
    except Exception:
        try:
            return shlex.split(text, posix=True)
        except ValueError:
            return text.split()

def command_string_to_argv(command: str | None) -> list[str]:
    if not command:
        return []
    command = command.strip()
    p = safe_expand_path(_strip_wrapping_quotes(command))
    if p.exists() and p.is_file():
        return [str(p)]
    argv = split_command(command)
    if argv:
        # Expand only the executable. URL and option values can legitimately
        # contain percent-encoded text that must reach gallery-dl unchanged.
        argv[0] = os.path.expandvars(os.path.expanduser(argv[0]))
    return argv


def _executable_file_available(path: Path) -> bool:
    if not path.is_file():
        return False
    if not IS_WINDOWS:
        return os.access(path, os.X_OK)
    if path.suffix.lower() in {".bat", ".cmd", ".com"}:
        return True
    if path.suffix.lower() != ".exe":
        return False
    # Windows treats arbitrary bytes named .exe as a file, but CreateProcess
    # rejects them. Check the inexpensive PE header before marking the GUI ready.
    try:
        with path.open("rb") as stream:
            header = stream.read(64)
            if len(header) < 64 or header[:2] != b"MZ":
                return False
            pe_offset = int.from_bytes(header[60:64], "little")
            if pe_offset < 64 or pe_offset > 16 * 1024 * 1024:
                return False
            stream.seek(pe_offset)
            return stream.read(4) == b"PE\0\0"
    except OSError:
        return False

def command_executable_available(command: str | None) -> bool:
    """Return whether the first argv item resolves to an executable file.

    This intentionally does not run the command. It is safe for frequent UI
    refreshes while still rejecting missing paths, directories, and unknown
    bare commands before a download worker is created.
    """
    argv = command_string_to_argv(command)
    if not argv:
        return False
    executable = str(argv[0]).strip()
    if not executable:
        return False
    candidate = Path(executable).expanduser()
    looks_like_path = candidate.is_absolute() or "/" in executable or "\\" in executable
    if looks_like_path:
        return _executable_file_available(candidate)
    resolved = shutil.which(executable)
    return resolved is not None and _executable_file_available(Path(resolved))

def find_gallery_dl() -> str | None:
    if getattr(sys, "frozen", False):
        bundled = Path(sys.executable).resolve().with_name("gallery-dl.exe" if IS_WINDOWS else "gallery-dl")
        if bundled.is_file():
            return str(bundled)
    exe = shutil.which("gallery-dl") or shutil.which("gallery-dl.exe")
    if exe:
        return exe
    if getattr(sys, "frozen", False):
        return None
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "gallery_dl", "--version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=8,
        )
        if completed.returncode == 0:
            return f'"{sys.executable}" -m gallery_dl'
    except Exception:
        pass
    return None

def detect_config_path() -> str | None:
    home = Path.home()
    candidates: list[Path] = []
    if IS_WINDOWS:
        appdata = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
        candidates.extend([
            appdata / "gallery-dl" / "config.json",
            home / "gallery-dl" / "config.json",
            home / "gallery-dl.conf",
            home / ".config" / "gallery-dl" / "config.json",
        ])
    else:
        xdg = Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config")))
        candidates.extend([
            xdg / "gallery-dl" / "config.json",
            home / ".gallery-dl.conf",
            Path("/etc/gallery-dl.conf"),
        ])
    for path in candidates:
        if path.is_file():
            return str(path)
    return str(candidates[0]) if candidates else None

def open_path(path: str | Path) -> bool:
    """Open a file/folder with the OS handler. Returns False on failure instead
    of raising, so GUI slots that call it never crash when no handler exists
    (e.g. headless Linux without xdg-open) or the target is inaccessible."""
    path = str(path)
    try:
        if IS_WINDOWS:
            os.startfile(path)  # type: ignore[attr-defined]
        elif IS_MACOS:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False

def parse_url_meta(url: str) -> tuple[str, str]:
    service, ident = "-", "-"
    if not url.lower().startswith(("http://", "https://")):
        return service, ident
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return service, ident
    if not host:
        return service, ident
    path = parsed.path.lower()
    # Alphanumeric IDs, not just \d+: coomer's main services (onlyfans,
    # fansly, candfans) use string usernames, which the old numeric-only
    # pattern missed — service fell back to the domain ("coomer"/"kemono"),
    # so per-service rate policy keyed on "onlyfans" never matched and
    # grouping/stats were wrong. Also accept kemono's /discord/server/<id>.
    # Match against the parsed host/path only: a nested URL in ?next= or a
    # fragment must never reclassify the actual target.
    normalized_host = host[4:] if host.startswith("www.") else host
    if normalized_host.startswith(("kemono.", "coomer.")):
        m = re.search(r"/(\w+)/(?:user|server)/([\w.\-@]+)", path)
        if m:
            return m.group(1), m.group(2)
    if host == "pixiv.net" or host.endswith(".pixiv.net"):
        m = re.search(r"/users/(\d+)", path)
        if m:
            return "pixiv", m.group(1)
    # Host aliases and common subdomains must map to gallery-dl's extractor
    # category.  Using the first hostname label classified x.com as "x" and
    # mobile.twitter.com / old.reddit.com as "mobile" / "old", so rate policy,
    # grouping, and library statistics never matched their real categories.
    category_domains = {
        "x.com": "twitter",
        "twitter.com": "twitter",
        "reddit.com": "reddit",
        "pixiv.net": "pixiv",
        "instagram.com": "instagram",
        "deviantart.com": "deviantart",
    }
    for domain, category in category_domains.items():
        if host == domain or host.endswith("." + domain):
            service = category
            break
    if service == "-":
        service = normalized_host.split(".", 1)[0] or "-"
    m = re.search(r"/(\d{3,})(?:/.*)?$", path)
    if m:
        ident = m.group(1)
    return service, ident

GALLERY_DL_SHORT_ZERO_VALUE_CHARS = "46EGJKSUghjqsvw"


def validate_finite_numbers(value: object) -> object:
    """Reject nonfinite numeric literals, including inside nested containers."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Numeric values must be finite")
    if isinstance(value, complex) and not (math.isfinite(value.real) and math.isfinite(value.imag)):
        raise ValueError("Numeric values must be finite")
    if isinstance(value, dict):
        for key, item in value.items():
            validate_finite_numbers(key)
            validate_finite_numbers(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            validate_finite_numbers(item)
    return value


def expand_gallery_dl_short_options(tokens: list[str]) -> list[str]:
    """Expand argparse short clusters while preserving option values and --."""
    result: list[str] = []
    remaining = 0
    for index, token in enumerate(tokens):
        if remaining:
            result.append(token)
            remaining -= 1
            continue
        if token == "--":
            result.extend(tokens[index:])
            break
        pieces = [token]
        if token.startswith("-") and not token.startswith("--") and len(token) > 2:
            candidate: list[str] = []
            for offset, char in enumerate(token[1:], 1):
                flag = "-" + char
                if char in GALLERY_DL_SHORT_ZERO_VALUE_CHARS:
                    candidate.append(flag)
                elif flag in GALLERY_DL_OPTION_VALUE_COUNTS:
                    candidate.append("-" + token[offset:])
                    break
                else:
                    candidate = []
                    break
            if candidate:
                pieces = candidate
        result.extend(pieces)
        last = pieces[-1]
        flag, separator, _value = last.partition("=")
        if flag in GALLERY_DL_VARIADIC_VALUE_FLAGS and not separator:
            result.extend(tokens[index + 1:])
            break
        if not separator:
            remaining = GALLERY_DL_OPTION_VALUE_COUNTS.get(flag, 0)
            if not remaining and last.startswith("-") and not last.startswith("--") and len(last) > 2:
                remaining = max(0, GALLERY_DL_OPTION_VALUE_COUNTS.get(last[:2], 0) - 1)
    return result


def insert_gallery_dl_arguments(base: list[str], arguments: list[str]) -> list[str]:
    """Place generated arguments before gallery-dl's positional boundary."""
    if is_gallery_dl_invocation(base) and "--" in base:
        boundary = base.index("--")
        return base[:boundary] + list(arguments) + base[boundary:]
    return list(base) + list(arguments)


def normalize_destination_argv(tokens: list[str]) -> list[str]:
    """Normalize unusable trailing spaces/dots in Windows destination values."""
    normalized = expand_gallery_dl_short_options(tokens)
    if not IS_WINDOWS:
        return normalized

    def clean(value: str) -> str:
        if value in {".", ".."} or value.endswith(("\\.", "\\..", "/.", "/..")):
            return value
        return value.rstrip(" .")

    index = 0
    while index < len(normalized):
        token = normalized[index]
        if token == "--":
            break
        if token in ("-d", "--destination", "-D", "--directory"):
            if index + 1 < len(normalized):
                normalized[index + 1] = clean(normalized[index + 1])
            index += 2
            continue
        for prefix in (
            "--destination=",
            "--directory=",
            "-d=",
            "-D=",
        ):
            if token.startswith(prefix):
                normalized[index] = prefix + clean(token[len(prefix):])
                break
        else:
            if token.startswith(("-d", "-D")) and len(token) > 2 and not token.startswith("--"):
                normalized[index] = token[:2] + clean(token[2:])
            elif token.startswith("-") and token != "-":
                flag, has_equals, _attached = token.partition("=")
                if flag in GALLERY_DL_VARIADIC_VALUE_FLAGS and not has_equals:
                    # argparse consumes every token up to ``--`` as a value;
                    # none of them can be a destination option.
                    break
                value_count = GALLERY_DL_OPTION_VALUE_COUNTS.get(flag, 0)
                if value_count and not has_equals:
                    attached_short = (
                        flag.startswith("-")
                        and not flag.startswith("--")
                        and len(flag) == 2
                        and len(token) > 2
                    )
                    index += value_count - 1 if attached_short else value_count
        index += 1
    return normalized


def extract_destination(tokens: list[str]) -> str:
    tokens = normalize_destination_argv(tokens)
    destination = "-"
    exact_directory: str | None = None
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--":
            break
        # ``-D/--directory`` is gallery-dl's exact-destination form.  It is
        # still an explicit per-job output path, so callers must not append a
        # second GUI default ``-d`` or disable safe post-processing for it.
        if token in ("-d", "--destination", "-D", "--directory") and i + 1 < len(tokens):
            if token in ("-D", "--directory"):
                exact_directory = tokens[i + 1]
            else:
                destination = tokens[i + 1]
            i += 2
            continue
        if token.startswith(("--destination=", "--directory=", "-d=", "-D=")):
            value = token.split("=", 1)[1]
            if token.startswith(("--directory=", "-D=")):
                exact_directory = value
            else:
                destination = value
            i += 1
            continue
        if token.startswith(("-d", "-D")) and len(token) > 2 and not token.startswith("--"):
            if token.startswith("-D"):
                exact_directory = token[2:]
            else:
                destination = token[2:]
            i += 1
            continue
        if token.startswith("-") and token != "-":
            flag, has_equals, _attached = token.partition("=")
            if flag in GALLERY_DL_VARIADIC_VALUE_FLAGS and not has_equals:
                break
            value_count = GALLERY_DL_OPTION_VALUE_COUNTS.get(flag, 0)
            if value_count and not has_equals:
                attached_short = (
                    flag.startswith("-")
                    and not flag.startswith("--")
                    and len(flag) == 2
                    and len(token) > 2
                )
                i += value_count - 1 if attached_short else value_count
        i += 1
    # gallery-dl applies repeated base-directory values in order, while its
    # exact -D directory takes priority over the base directory in either order.
    return exact_directory if exact_directory is not None else destination

def normalized_job_argv(job: DownloadJob) -> tuple[str, ...]:
    """Return a stable, execution-oriented identity for duplicate detection.

    A plain URL and ``gallery-dl URL`` represent the same job in this GUI, so
    the executable prefix is intentionally removed.  Options and destinations
    remain part of the identity; two rows for the same URL but with different
    ranges or output folders are therefore never removed as duplicates.
    """
    tokens = normalize_destination_argv(split_command(job.raw))
    if is_gallery_dl_invocation(tokens):
        tokens = strip_gallery_dl_invocation(tokens)
    identity = list(tokens or [job.raw.strip()])
    # Tags and notes are persistent queue metadata.  Treating two otherwise
    # identical commands in different projects as duplicates can discard the
    # user's grouping (and can change a later "Output by Tag" result), so the
    # safe deduper must require metadata equality as well.
    identity.extend((f"\0tag={job.tag}", f"\0notes={job.notes}"))
    return tuple(identity)

def find_exact_duplicate_groups(jobs: list[DownloadJob]) -> list[list[int]]:
    """Return zero-based index groups for safely removable duplicate jobs."""
    groups: dict[tuple[str, ...], list[int]] = {}
    for index, job in enumerate(jobs):
        groups.setdefault(normalized_job_argv(job), []).append(index)
    return [indices for indices in groups.values() if len(indices) > 1]

def parse_text_database(text: str) -> list[DownloadJob]:
    jobs: list[DownloadJob] = []
    current_tag = ""
    pending_notes = ""
    # Stream lines instead of splitlines(): a hostile 50 MB file containing
    # millions of tiny rows must hit MAX_QUEUE_JOBS before allocating a Python
    # string object for every line in the file.
    for row_number, line in enumerate(io.StringIO(str(text)), start=1):
        if row_number > MAX_QUEUE_SOURCE_ROWS:
            raise ValueError(
                f"Queue source exceeds the {MAX_QUEUE_SOURCE_ROWS:,} row safety limit. "
                "Remove excess comments/disabled rows or split it into smaller sessions."
            )
        if len(line) > MAX_COMMAND_LINE_CHARS:
            raise ValueError(
                f"One queue row exceeds the {MAX_COMMAND_LINE_CHARS // 1024} KB safety limit."
            )
        stripped = line.strip()
        if not stripped:
            continue
        # One-row metadata emitted by CSV/XLSX import and queue rewrites.
        # Keeping it separate from sticky ``# tag`` headers lets notes survive
        # text/autosave round-trips without changing the visible command.
        if stripped.startswith("#@notes "):
            pending_notes = stripped[len("#@notes "):].strip()
            continue
        if stripped.startswith("#"):
            current_tag = stripped.lstrip("#").strip()
            pending_notes = ""
            continue
        job = parse_line(stripped, tag=current_tag, notes=pending_notes)
        if job:
            jobs.append(job)
            if len(jobs) > MAX_QUEUE_JOBS:
                raise ValueError(
                    f"Queue exceeds the {MAX_QUEUE_JOBS:,} job safety limit. "
                    "Split it into smaller sessions."
                )
        pending_notes = ""
    return jobs

def _row_value(row: dict[str, str], *names: str) -> str:
    # Flatten embedded newlines (now preserved by the CSV reader, and always
    # possible in XLSX cells): a command/url cell spanning lines would
    # otherwise become a multi-line "raw" entry that splits into garbage rows
    # on reparse. One database entry must stay one line.
    lower = {}
    for key, raw_value in row.items():
        value = "" if raw_value is None else str(raw_value)
        # CSV exports prefix formula-triggering values with an apostrophe so
        # Excel opens them as text. Remove only that exact marker on import so
        # an export/import round-trip restores the original command argument.
        # Do this before whitespace flattening: otherwise ``'\t...`` becomes
        # ``' ...`` and leaves a stray apostrophe argv element in the command.
        if len(value) >= 2 and value[0] == "'" and value[1] in "=+-@\t\r":
            value = value[1:]
        # Only flatten row breaks. Whitespace inside a quoted command argument
        # (especially a Windows destination path) is significant.
        value = re.sub(r"[\r\n]+", " ", value).strip()
        lower[str(key).strip().lower()] = value
    for name in names:
        value = lower.get(name.lower())
        if value:
            return value
    return ""

def _redact_url_credentials(value: str) -> str:
    """Mask URL user-info and secret query values without changing other text."""
    value = re.sub(
        r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@]+@",
        lambda match: f"{match.group(1)}{REDACTED}@",
        value,
    )

    def is_sensitive_query_key(key: str) -> bool:
        # Query-string keys need stricter matching than config keys. Broad
        # substring checks corrupt ordinary URLs such as ``?tokenizer=...`` or
        # ``?username_display=...`` when they are saved to autosave/Library.
        # At the same time, signed media URLs commonly use vendor-prefixed
        # names (X-Amz-Signature, X-Goog-Credential) that were previously
        # missed completely and leaked into logs and history.
        decoded = unquote_plus(str(key or "")).strip().lower()
        canonical = re.sub(r"[^a-z0-9]+", "-", decoded).strip("-")
        compact = canonical.replace("-", "")
        exact = {
            "access-key",
            "apikey",
            "api-key",
            "auth",
            "authorization",
            "bearer",
            "client-secret",
            "cookie",
            "cookies",
            "credential",
            "credentials",
            "jwt",
            "key",
            "password",
            "passwd",
            "private-key",
            "proxy-authorization",
            "refresh-token",
            "secret",
            "secret-key",
            "session",
            "sessionid",
            "session-id",
            "sid",
            "sig",
            "signature",
            "ticket",
            "token",
            "username",
        }
        if canonical in exact or compact in {"apikey", "sessionid"}:
            return True
        return canonical.endswith((
            "-credential",
            "-credentials",
            "-password",
            "-secret",
            "-signature",
            "-token",
        ))

    def replace_query(match: re.Match[str]) -> str:
        separator, key, query_value = match.groups()
        if is_sensitive_query_key(key):
            query_value = REDACTED
        return f"{separator}{key}={query_value}"

    # Include URL fragments as OAuth-style redirects sometimes carry an
    # access token after '#'. Keep all separators and non-secret parameters
    # byte-for-byte intact so display redaction never changes URL structure.
    return re.sub(r"([?&#])([^=&#\s]+)=([^&#\s]*)", replace_query, value)


def _redact_key_value(pair: str) -> str:
    key, sep, _value = pair.partition("=")
    if sep and is_sensitive_option_key(key):
        return f"{key}={REDACTED}"
    return _redact_url_credentials(pair)


def redact_sensitive_argv(argv: list[str]) -> list[str]:
    """Return a DISPLAY-ONLY copy of argv with credentials masked.

    Callers must pass a copy or accept that this builds one: the argv actually
    handed to subprocess must never be altered, or the download would run with
    "***REDACTED***" as the real password. Used by every sink that renders a
    command to the screen, a log, or an exported report — those are routinely
    screenshotted and shared when reporting bugs.
    """
    out = list(argv)

    i = 0
    while i < len(out):
        token = out[i]
        low = token.lower()

        compact = re.fullmatch(rf"(-[{GALLERY_DL_SHORT_ZERO_VALUE_CHARS}]*)([puCo])(.*)", token)
        if compact:
            prefix, flag, attached = compact.groups()
            if attached:
                out[i] = prefix + flag + (_redact_key_value(attached) if flag == "o" else REDACTED)
                i += 1
            else:
                if i + 1 < len(out):
                    out[i + 1] = _redact_key_value(out[i + 1]) if flag == "o" else REDACTED
                i += 2
            continue

        # --option=password=value (single-token form).  The former redactor
        # handled only ``--option password=value`` and leaked this equivalent
        # syntax in previews and audit output.
        if low.startswith("--option="):
            prefix, _, pair = token.partition("=")
            out[i] = f"{prefix}={_redact_key_value(pair)}"
            i += 1
            continue

        # -opassword=value is also accepted by argparse for the short option.
        if token.startswith("-o") and not token.startswith("--") and len(token) > 2:
            out[i] = "-o" + _redact_key_value(token[2:])
            i += 1
            continue

        # --flag=value  (value redacted, flag kept readable)
        if low.startswith("--") and "=" in token:
            flag, _, _value = token.partition("=")
            if flag.lower() in SECRET_VALUE_FLAGS:
                out[i] = f"{flag}={REDACTED}"
                i += 1
                continue

        # -p secret  /  --cookies path
        if token in SECRET_VALUE_FLAGS or low in SECRET_VALUE_FLAGS:
            if i + 1 < len(out):
                out[i + 1] = REDACTED
            i += 2
            continue

        # Attached short values: -psecret, -uaccount, -Ccookies.txt.  Keep the
        # option recognizable while removing its value.  -C is intentionally
        # case-sensitive because lowercase -c means a non-secret config file.
        attached_flag = next(
            (flag for flag in ("-p", "-u", "-C") if token.startswith(flag) and len(token) > len(flag)),
            None,
        )
        if attached_flag:
            out[i] = attached_flag + REDACTED
            i += 1
            continue

        # -o key=value  /  --option key=value
        if low in ("-o", "--option") and i + 1 < len(out):
            out[i + 1] = _redact_key_value(out[i + 1])
            i += 2
            continue

        # Header-style secrets: "Authorization: Bearer xyz"
        if ":" in token:
            head, _, _rest = token.partition(":")
            if head.strip().lower() in SENSITIVE_HEADER_NAMES:
                out[i] = f"{head}: {REDACTED}"

        # Signed/private URLs are often pasted directly into a queue.  Mask
        # obvious credential query parameters in display-only copies without
        # changing the argv used by the subprocess.
        out[i] = _redact_url_credentials(out[i])

        i += 1
    # An option value can itself be a small command or JSON document (for
    # example ``--exec`` or ``--config-json``). Apply the arbitrary-text
    # boundary to every display token so nested credentials do not bypass the
    # top-level argv rules above.
    return [redact_sensitive_text(token) for token in out]

def redact_sensitive_text(text: str) -> str:
    """Redact secrets from an arbitrary log/report line without reformatting it."""
    # Treat arbitrary output as text rather than pretending the whole line is
    # one argv token. The latter made a line beginning with ``-p`` collapse and
    # could not understand quoted values containing spaces.
    value = _redact_url_credentials(str(text))

    # Handle complete quoted config-option values before generic assignment
    # redaction, which cannot infer that spaces still belong to the secret.
    option_flags = rf"(?:(?i:--option)|-[{GALLERY_DL_SHORT_ZERO_VALUE_CHARS}]*o)"
    option_key = r"[A-Za-z][A-Za-z0-9_.-]*"

    def redact_quoted_option(match: re.Match[str]) -> str:
        if not is_sensitive_option_key(match.group("key")):
            return match.group(0)
        return f"{match.group('prefix')}{match.group('quote')}{match.group('key')}={REDACTED}{match.group('quote')}"

    value = re.sub(
        rf"(?P<prefix>(?:[\"']{option_flags}[\"']\s*,\s*|(?<!\S){option_flags}(?:=|\s+)?))"
        rf"(?P<quote>[\"'])(?P<key>{option_key})="
        rf"(?:\\.|(?!(?P=quote))[^\\\r\n])*(?P=quote)",
        redact_quoted_option,
        value,
    )

    def redact_attached_quoted_option(match: re.Match[str]) -> str:
        if not is_sensitive_option_key(match.group("key")):
            return match.group(0)
        return f"{match.group('prefix')}{match.group('key')}={REDACTED}{match.group('quote')}"

    value = re.sub(
        rf"(?P<prefix>(?P<quote>[\"']){option_flags})(?P<key>{option_key})="
        rf"(?:\\.|(?!(?P=quote))[^\\\r\n])*(?P=quote)",
        redact_attached_quoted_option,
        value,
    )

    def redact_option_quoted_assignment(match: re.Match[str]) -> str:
        if not is_sensitive_option_key(match.group("key")):
            return match.group(0)
        return f"{match.group('prefix')}{match.group('key')}={match.group('quote')}{REDACTED}{match.group('quote')}"

    value = re.sub(
        rf"(?P<prefix>(?<!\S){option_flags}(?:=|\s+)?)(?P<key>{option_key})="
        rf"(?P<quote>[\"'])(?:\\.|(?!(?P=quote))[^\\\r\n])*(?P=quote)",
        redact_option_quoted_assignment,
        value,
    )
    value = re.sub(
        rf"(?P<prefix>(?<!\S){option_flags}(?:=|\s+)?)"
        rf"(?P<quote>[\"'])(?P<key>{option_key})="
        rf"(?:\\.|(?!(?P=quote))[^\\\r\n])*$",
        redact_quoted_option,
        value,
    )

    # gallery-dl, Python exceptions, and diagnostic helpers can render config
    # data as dict/JSON or plain assignments rather than argv.  Redact these
    # structured forms before the command-line patterns below.  Query-string
    # assignments are deliberately excluded: _redact_url_credentials() has
    # already handled them with URL-aware boundaries and must retain adjacent
    # non-secret parameters.
    def redact_quoted_field(match: re.Match[str]) -> str:
        if not is_sensitive_option_key(match.group("key")):
            return match.group(0)
        return (
            f"{match.group('key_quote')}{match.group('key')}"
            f"{match.group('key_quote')}{match.group('separator')}"
            f"{match.group('value_quote')}{REDACTED}"
            f"{match.group('value_quote')}"
        )

    value = re.sub(
        r"(?P<key_quote>[\"'])(?P<key>[A-Za-z][A-Za-z0-9_.-]*)"
        r"(?P=key_quote)(?P<separator>\s*:\s*)"
        r"(?P<value_quote>[\"'])(?:\\.|(?!(?P=value_quote))[^\\\r\n])*"
        r"(?P=value_quote)",
        redact_quoted_field,
        value,
    )

    def redact_assignment(match: re.Match[str]) -> str:
        if not is_sensitive_option_key(match.group("key")):
            return match.group(0)
        quote = match.groupdict().get("quote") or ""
        return (
            f"{match.group('key')}{match.group('separator')}"
            f"{quote}{REDACTED}{quote}"
        )

    value = re.sub(
        r"(?<![?&#A-Za-z0-9_.-])(?P<key>[A-Za-z][A-Za-z0-9_.-]*)"
        r"(?P<separator>\s*=\s*)(?P<quote>[\"'])"
        r"(?:\\.|(?!(?P=quote))[^\\\r\n])*(?P=quote)",
        redact_assignment,
        value,
    )
    value = re.sub(
        r"(?<![?&#A-Za-z0-9_.-])(?P<key>[A-Za-z][A-Za-z0-9_.-]*)"
        r"(?P<separator>\s*=\s*)(?P<value>[^\s,;}\]&\"']+)",
        redact_assignment,
        value,
    )
    value = re.sub(
        r"(?<![A-Za-z0-9_.-])(?P<key>[A-Za-z][A-Za-z0-9_.-]*)"
        r"(?P<separator>\s*:\s*)(?P<value>[^\s,;}\]]+)",
        redact_assignment,
        value,
    )
    # redact_sensitive_argv handles structured argv tokens. These patterns add
    # defense for options and headers embedded in a longer subprocess line.
    long_flags = "|".join(re.escape(flag) for flag in SECRET_VALUE_FLAGS if flag.startswith("--"))
    short_flags = rf"-[{GALLERY_DL_SHORT_ZERO_VALUE_CHARS}]*[puC]"
    option_key = r"[A-Za-z][A-Za-z0-9_.-]*"
    quoted_value = r'(?:"(?:\\.|[^"\r\n])*"|\'(?:\\.|[^\'\r\n])*\')'

    # subprocess exceptions render argv as a Python list/tuple repr rather
    # than as a command line. Handle its quote/comma boundaries explicitly;
    # the normal whitespace-delimited patterns below cannot see through them.
    value = re.sub(
        rf"(?P<flag_quote>[\"'])(?P<flag>(?i:{long_flags})|{short_flags})(?P=flag_quote)"
        rf"(?P<separator>\s*,\s*)(?P<value_quote>[\"'])"
        rf"[^\"'\r\n]*(?P=value_quote)",
        lambda match: (
            f"{match.group('flag_quote')}{match.group('flag')}"
            f"{match.group('flag_quote')}{match.group('separator')}"
            f"{match.group('value_quote')}{REDACTED}{match.group('value_quote')}"
        ),
        value,
    )
    value = re.sub(
        rf"(?i)(?P<quote>[\"'])(?P<flag>{long_flags})="
        rf"[^\"'\r\n]*(?P=quote)",
        lambda match: (
            f"{match.group('quote')}{match.group('flag')}={REDACTED}"
            f"{match.group('quote')}"
        ),
        value,
    )
    value = re.sub(
        rf"(?P<quote>[\"'])(?P<flag>{short_flags})(?:=)?[^\"'\r\n]+(?P=quote)",
        lambda match: (
            f"{match.group('quote')}{match.group('flag')}{REDACTED}"
            f"{match.group('quote')}"
        ),
        value,
    )

    # Quoted credentials must be handled before whitespace-delimited forms.
    # Otherwise only the first word is replaced and the rest of a value such as
    # ``--password "top secret value"`` remains visible in every persistence
    # and logging sink.
    value = re.sub(
        rf"(?i)(?<!\S)({long_flags})(?:=|\s+)({quoted_value})",
        lambda match: f"{match.group(1)}={REDACTED}",
        value,
    )
    value = re.sub(
        rf"(?<!\S)({short_flags})(?:=|\s+)?({quoted_value})",
        lambda match: f"{match.group(1)}{REDACTED}",
        value,
    )

    def redact_option_pair(match: re.Match[str]) -> str:
        if not is_sensitive_option_key(match.group("key")):
            return match.group(0)
        return (
            f"{match.group(1)}{match.group('option_separator')}"
            f"{match.group('key')}={REDACTED}"
        )

    # Cover both --option password="..." and --option "password=...".
    value = re.sub(
        rf"(?<!\S)({option_flags})(?P<option_separator>=|\s+|(?=[A-Za-z]))"
        rf"(?P<key>{option_key})=({quoted_value})",
        redact_option_pair,
        value,
    )

    def redact_wrapped_option_pair(match: re.Match[str]) -> str:
        if not is_sensitive_option_key(match.group("key")):
            return match.group(0)
        return (
            f"{match.group(1)}{match.group('wrapped_separator')}"
            f"{match.group('quote')}{match.group('key')}="
            f"{REDACTED}{match.group('quote')}"
        )

    value = re.sub(
        rf"(?<!\S)({option_flags})(?P<wrapped_separator>=|\s+)"
        rf"(?P<quote>[\"'])(?P<key>{option_key})="
        rf"[^\r\n]*?(?P=quote)",
        redact_wrapped_option_pair,
        value,
    )
    # Malformed/unclosed quotes are not runnable commands, but they can still
    # reach logs or autosave while a user is typing. Fail closed and mask the
    # remainder of that line rather than exposing every word after the first.
    for unclosed_quote in (r'"[^"\r\n]*$', r"'[^'\r\n]*$"):
        value = re.sub(
            rf"(?i)(?<!\S)({long_flags})(?:=|\s+){unclosed_quote}",
            lambda match: f"{match.group(1)}={REDACTED}",
            value,
        )
        value = re.sub(
            rf"(?<!\S)({short_flags})(?:=|\s+)?{unclosed_quote}",
            lambda match: f"{match.group(1)}{REDACTED}",
            value,
        )
        value = re.sub(
            rf"(?<!\S)({option_flags})(?P<option_separator>=|\s+|(?=[A-Za-z]))"
            rf"(?P<key>{option_key})={unclosed_quote}",
            redact_option_pair,
            value,
        )
    value = re.sub(
        rf"(?i)(?<!\S)({long_flags})(?:=|\s+)([^\s]+)",
        lambda match: f"{match.group(1)}={REDACTED}",
        value,
    )
    value = re.sub(
        rf"(?<!\S)({short_flags})(?:=|\s+)?[^\s]+",
        lambda match: f"{match.group(1)}{REDACTED}",
        value,
    )
    value = re.sub(
        rf"(?<!\S)({option_flags})(?P<option_separator>=|\s+|(?=[A-Za-z]))"
        rf"(?P<key>{option_key})=[^\s]+",
        redact_option_pair,
        value,
    )
    header_names = "|".join(re.escape(name) for name in SENSITIVE_HEADER_NAMES)

    # A header passed as one quoted argv value must keep its closing quote and
    # every argument after it. The former catch-all pattern consumed the rest
    # of the line, turning a safe persisted command such as
    # ``--header \"Authorization: Bearer x\" URL`` into a malformed command
    # with no URL. Handle quote-bounded command values first.
    value = re.sub(
        rf"(?i)(?P<quote>[\"'])(?P<name>{header_names})\s*:\s*"
        rf"(?:\\.|(?!(?P=quote))[^\\\r\n])*(?P=quote)",
        lambda match: (
            f"{match.group('quote')}{match.group('name')}: "
            f"{REDACTED}{match.group('quote')}"
        ),
        value,
    )

    # Also cover dict/JSON-shaped diagnostic output, for example
    # ``{'Authorization': 'Bearer x'}``, without deleting adjacent fields.
    value = re.sub(
        rf"(?i)(?P<key_quote>[\"'])(?P<name>{header_names})(?P=key_quote)"
        rf"\s*:\s*(?P<value_quote>[\"'])"
        rf"(?:\\.|(?!(?P=value_quote))[^\\\r\n])*(?P=value_quote)",
        lambda match: (
            f"{match.group('key_quote')}{match.group('name')}"
            f"{match.group('key_quote')}: {match.group('value_quote')}"
            f"{REDACTED}{match.group('value_quote')}"
        ),
        value,
    )

    # Unquoted --header values cannot contain spaces, so their argv boundary
    # is the next whitespace. Redacting only that token preserves the target.
    value = re.sub(
        rf"(?i)(--header(?P<separator>=|\s+))"
        rf"(?P<name>{header_names})\s*:\s*[^\s]+",
        lambda match: (
            f"--header{match.group('separator')}\"{match.group('name')}: "
            f"{REDACTED}\""
        ),
        value,
    )

    # Finally redact free-form traffic/log header lines. Require a normal log
    # boundary rather than a quote so the quote-bounded command case above is
    # not consumed a second time.
    value = re.sub(
        rf"(?i)(?P<prefix>(?:^|[\s\[>])(?P<name>{header_names})\s*:\s*)"
        rf"(?!{re.escape(REDACTED)})[^\r\n]+",
        lambda match: f"{match.group('prefix')}{REDACTED}",
        value,
    )
    return value


def redact_oauth_output(text: str) -> str:
    """Mask OAuth helper tokens that gallery-dl prints as unlabeled value lines."""
    value = redact_sensitive_text(text)
    return re.sub(
        r"(?is)(Your\s+[^\r\n]*(?:token|secret|key)[^\r\n]*"
        r"\r?\n\s*\r?\n)(.*?)(?=\r?\n\s*\r?\n|$)",
        lambda match: match.group(1) + REDACTED,
        value,
    )


class OAuthOutputRedactor:
    """Show OAuth instructions as they arrive without showing issued tokens.

    gallery-dl prints a token label, a blank line, then one or more bare token
    values. Process complete lines so a value split between QProcess reads can
    never appear before its label has been classified. The no-newline input
    prompt is the sole fragment released early.
    """

    _token_heading = re.compile(
        r"^\s*Your\s+.*(?:token|secret|key).*\b(?:is|are)\s*$", re.I
    )
    _input_prompt = re.compile(
        r"^\s*(?:code|pin|verifier|verification code|authorization code)\s*:\s*$",
        re.I,
    )

    def __init__(self) -> None:
        self._pending = ""
        self._redacting_values = False
        self._masked_value = False
        self._discarding_line = False

    def _line(self, line: str) -> str:
        content = line.rstrip("\r\n")
        ending = line[len(content):]
        if self._token_heading.match(content):
            self._redacting_values = True
            self._masked_value = False
            return redact_sensitive_text(content) + ending
        if self._redacting_values:
            if not content.strip():
                if self._masked_value:
                    self._redacting_values = False
                return ending
            self._masked_value = True
            return REDACTED + ending
        return redact_sensitive_text(content) + ending

    def feed(self, chunk: str) -> str:
        self._pending += str(chunk)
        output: list[str] = []
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            if self._discarding_line:
                self._discarding_line = False
                output.append("[OAuth output line omitted]\n")
            else:
                output.append(self._line(line + "\n"))
        if self._input_prompt.fullmatch(self._pending):
            output.append(redact_sensitive_text(self._pending))
            self._pending = ""
        elif len(self._pending) > MAX_LOG_LINE_CHARS:
            # A helper that never emits a newline must not grow GUI memory or
            # make us reveal an incomplete token while trying to show progress.
            self._pending = ""
            self._discarding_line = True
        return "".join(output)

    def finish(self) -> str:
        if self._discarding_line:
            self._pending = ""
            self._discarding_line = False
            return "[OAuth output line omitted]"
        pending, self._pending = self._pending, ""
        return self._line(pending) if pending else ""


def redact_sensitive_database_text(text: str) -> str:
    """Redact every persisted queue line while retaining its text structure."""
    return "\n".join(redact_sensitive_text(line) for line in str(text).splitlines())

def quote_arg_for_preview(part: str) -> str:
    if not part:
        return '""' if IS_WINDOWS else "''"
    if IS_WINDOWS:
        # Preview/export only. The real subprocess call still receives argv.
        return subprocess.list2cmdline([part])
    return shlex.quote(part)

def build_raw_command_from_columns(row: dict[str, str]) -> Optional[str]:
    # Enabled is checked first so disabled CSV/XLSX rows are skipped even when
    # they use a raw command column.
    enabled = _row_value(row, "enabled", "enable", "active", "run")
    if enabled and is_false_value(enabled):
        return None

    extra = _row_value(row, "extra args", "extra_args", "args", "options")
    raw_command = _row_value(row, "command", "gallery-dl command", "gallery_dl_command", "raw", "cmd")
    if raw_command:
        return append_extra_args_to_command(raw_command, extra) if extra else raw_command

    url = _row_value(row, "url", "link", "links", "source", "target")
    if not url:
        return None

    # CSV/XLSX help documents allow the URL column to contain a full command.
    # Respect that command exactly. Do not prepend another gallery-dl or -d.
    if is_gallery_dl_invocation(split_command(url)):
        return append_extra_args_to_command(url, extra) if extra else url

    dest = _row_value(row, "destination", "folder", "output", "output_dir", "output dir")
    parts = ["gallery-dl"]
    if dest:
        parts += ["-d", dest]
    parts.append(url)
    if extra:
        parts += split_command(extra)
    return " ".join(quote_arg_for_preview(part) for part in parts)

def csv_rows_first_column_fallback(text: str, dialect: csv.Dialect, skip_header: bool = False) -> list[str]:
    out: list[str] = []
    # io.StringIO, not splitlines(): preserves newlines inside quoted cells
    # (see read_csv_as_commands).
    reader = csv.reader(io.StringIO(text), dialect=dialect)
    for row_index, values in enumerate(reader):
        if row_index >= MAX_QUEUE_SOURCE_ROWS:
            raise ValueError(
                f"CSV exceeds the {MAX_QUEUE_SOURCE_ROWS:,} source-row safety limit."
            )
        if not values:
            continue
        # Flatten embedded newlines from quoted cells: one entry, one line.
        first = _row_value({"command": values[0]}, "command")
        if not first or first.startswith("#"):
            if first.startswith("#"):
                out.append(first)
            continue
        if skip_header and row_index == 0 and not looks_like_database_entry(first):
            continue
        if len(first) > MAX_COMMAND_LINE_CHARS:
            raise ValueError(
                f"One CSV command exceeds the {MAX_COMMAND_LINE_CHARS // 1024} KB safety limit."
            )
        out.append(first)
    return out

def classify_error(text: str) -> str:
    low = text.lower()
    # Specific causes determine retry policy and must win when an exception is
    # also wrapped in generic connection/network wording. Match ``rate`` only
    # at word boundaries so unrelated text such as ``generated`` is not
    # classified as a rate limit.
    if re.search(r"\brate(?:[- ]?limit(?:ed|ing)?)?\b|\b429\b|too many requests", low):
        return "rate-limit"
    if any(x in low for x in ["401", "403", "forbidden", "unauthorized", "login", "cookie"]):
        return "auth/cookies"
    if any(x in low for x in ["404", "not found", "does not exist"]):
        return "not-found"
    if any(x in low for x in ["config", "json", "syntax"]):
        return "config"
    if any(x in low for x in ["permission", "access is denied", "no such file", "path"]):
        return "path"
    if any(x in low for x in ["timed out", "connection", "network", "temporary failure", "ssl"]):
        return "network"
    return "unknown"

def ensure_no_option(args: list[str], names: tuple[str, ...]) -> bool:
    args = expand_gallery_dl_short_options(args)
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            break
        for name in names:
            if arg == name or arg.startswith(name + "="):
                return False
            # argparse accepts attached values for one-letter options (-R5,
            # -cFILE, -dDIR). Treat those as explicit user overrides too.
            if (
                name.startswith("-")
                and not name.startswith("--")
                and len(name) == 2
                and arg.startswith(name)
                and len(arg) > 2
            ):
                return False
        if arg.startswith("-") and arg != "-":
            flag, has_equals, _attached = arg.partition("=")
            value_count = GALLERY_DL_OPTION_VALUE_COUNTS.get(flag, 0)
            if value_count and not has_equals:
                attached_short = (
                    flag.startswith("-")
                    and not flag.startswith("--")
                    and len(flag) == 2
                    and len(arg) > 2
                )
                i += value_count - 1 if attached_short else value_count
        i += 1
    return True

def safe_int(value: object, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        out = int(value)
    except Exception:
        out = default
    if minimum is not None:
        out = max(minimum, out)
    if maximum is not None:
        out = min(maximum, out)
    return out

def normalize_process_return_code(value: object, default: int = -1) -> int:
    """Normalize native process codes to the signed 32-bit range Qt expects."""
    try:
        code = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if code > 0x7FFFFFFF:
        code = ((code + 0x80000000) % 0x100000000) - 0x80000000
    return max(-0x80000000, min(code, 0x7FFFFFFF))

def safe_filename(name: str) -> str:
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]+', '_', str(name).strip())
    name = re.sub(r'\s+', ' ', name).strip(' .')
    name = name[:120].rstrip(' .') or 'untagged'
    # Windows reserves these device names even when an extension is present
    # ("CON", "con.txt", "LPT1", ...).  Tags are used as folder names by
    # Output by Tag, so leaving them unchanged makes otherwise valid jobs fail.
    stem = name.split(".", 1)[0].upper()
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if stem in reserved:
        name = "_" + name
    return name

def sanitize_spreadsheet_cell(value: object) -> str:
    """Neutralize CSV/spreadsheet formula injection.

    A cell whose text starts with =, +, -, @ (or a leading tab/CR that some
    apps strip before parsing) can execute as a formula when the exported
    CSV/XLSX is opened in Excel/LibreOffice. gallery-dl URLs and error messages
    are user/remote controlled, so any value written into a spreadsheet cell is
    prefixed with a single quote when it begins with a dangerous character. The
    visible text is preserved
    only the formula trigger is defused.
    """
    text = "" if value is None else str(value)
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text

def _gdl_basename(token: str) -> str:
    token = _strip_wrapping_quotes(str(token or ""))
    name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name

def _is_python_launcher(token: str) -> bool:
    """Recognize normal Python launchers without accepting arbitrary programs."""
    name = _gdl_basename(token)
    return name == "py" or re.fullmatch(r"pythonw?(?:\d+(?:\.\d+)?)?", name) is not None

def is_gallery_dl_invocation(tokens: list[str]) -> bool:
    if not tokens:
        return False
    first = _gdl_basename(tokens[0])
    if first == "gallery-dl":
        return True
    if len(tokens) >= 3 and _is_python_launcher(tokens[0]) and tokens[1] == "-m" and tokens[2].replace("-", "_") == "gallery_dl":
        return True
    return False

def strip_gallery_dl_invocation(tokens: list[str]) -> list[str]:
    if not tokens:
        return []
    first = _gdl_basename(tokens[0])
    if first == "gallery-dl":
        return tokens[1:]
    if len(tokens) >= 3 and _is_python_launcher(tokens[0]) and tokens[1] == "-m" and tokens[2].replace("-", "_") == "gallery_dl":
        return tokens[3:]
    return tokens


# Number of following argv values consumed by gallery-dl options. Keeping the
# arity explicit lets URL extraction distinguish the real positional target
# from URLs embedded in --exec commands, headers, proxies, output paths, or the
# two-value --print-to-file option. Unknown option tokens themselves are still
# ignored, so an embedded URL in ``--flag=https://...`` is never selected.
GALLERY_DL_VARIADIC_VALUE_FLAGS = {"--list-extractors"}


GALLERY_DL_OPTION_VALUE_COUNTS: dict[str, int] = {
    "-f": 1, "--filename": 1,
    "-d": 1, "--destination": 1,
    "-D": 1, "--directory": 1,
    "--restrict-filenames": 1,
    "-X": 1, "--extractors": 1,
    "-a": 1, "--user-agent": 1, "--clear-cache": 1,
    "-i": 1, "--input-file": 1,
    "-I": 1, "--input-file-comment": 1,
    "-x": 1, "--input-file-delete": 1,
    "-e": 1, "--error-file": 1,
    "-N": 1, "--print": 1,
    "--Print": 1,
    "--print-to-file": 2,
    "--Print-to-file": 2,
    "--list-extractors": 1,
    "--write-log": 1, "--write-unsupported": 1,
    "--xff": 1,
    "-R": 1, "--retries": 1,
    "--http-timeout": 1, "--proxy": 1, "--source-address": 1,
    "-r": 1, "--limit-rate": 1, "--chunk-size": 1,
    "--sleep": 1, "--sleep-request": 1, "--sleep-extractor": 1,
    "-o": 1, "--option": 1,
    "-c": 1, "--config": 1, "--config-yaml": 1, "--config-toml": 1,
    "--config-json": 1, "--config-type": 1,
    "--cache-file": 1, "--cache-show": 1, "--cache-clear": 1,
    "-u": 1, "--username": 1,
    "-p": 1, "--password": 1,
    "-C": 1, "--cookies": 1,
    "--cookies-export": 1, "--cookies-from-browser": 1,
    "-A": 1, "--abort": 1,
    "-T": 1, "--terminate": 1,
    "--filesize-min": 1, "--filesize-max": 1,
    "--download-archive": 1,
    "--blacklist": 1, "--whitelist": 1,
    "--tags-blacklist": 1, "--tags-whitelist": 1,
    "--range": 1, "--chapter-range": 1,
    "--file-range": 1, "--image-range": 1,
    "--filter": 1, "--chapter-filter": 1,
    "--file-filter": 1, "--image-filter": 1,
    "-P": 1, "--postprocessor": 1,
    "-O": 1, "--postprocessor-option": 1,
    "--mtime": 1, "--rename": 1, "--rename-to": 1,
    "--ugoira": 1, "--exec": 1, "--exec-after": 1,
    # Compatibility/config override options accepted by older releases and
    # by hand-written databases supported by this GUI.
    "--referer": 1, "--header": 1,
    "--date-after": 1, "--date-before": 1,
    "--post-filter": 1, "--child-filter": 1,
    "--post-range": 1, "--child-range": 1,
    "--sleep-429": 1, "--sleep-skip": 1, "--sleep-retries": 1,
}


def _extract_url_from_tokens(tokens: list[str], fallback: str) -> str:
    tokens = expand_gallery_dl_short_options(tokens)
    values_remaining = 0
    variadic_values = False
    positional_only = False
    for token in tokens:
        if variadic_values:
            if token == "--":
                variadic_values = False
                positional_only = True
            continue
        if values_remaining:
            values_remaining -= 1
            continue
        if not positional_only and token == "--":
            positional_only = True
            continue
        if not positional_only and token.startswith("-") and token != "-":
            flag, has_equals, _attached = token.partition("=")
            if flag in GALLERY_DL_VARIADIC_VALUE_FLAGS and not has_equals:
                variadic_values = True
                continue
            count = GALLERY_DL_OPTION_VALUE_COUNTS.get(flag, 0)
            if count and not has_equals:
                # One-letter options accept attached values (-dPATH, -R5,
                # -NFORMAT). In that form the current token already contains
                # the first value; only any additional values remain.
                attached_short = (
                    flag.startswith("-")
                    and not flag.startswith("--")
                    and len(flag) == 2
                    and len(token) > 2
                )
                values_remaining = count - 1 if attached_short else count
            continue
        match = re.search(r"https?://\S+", token)
        if match:
            return match.group(0)
    return fallback


def _gallery_dl_command_has_source(tokens: list[str]) -> bool:
    """Return whether an invocation has a positional or input-file source."""
    input_file_flags = {
        "-i",
        "--input-file",
        "-I",
        "--input-file-comment",
        "-x",
        "--input-file-delete",
    }
    values_remaining = 0
    value_option = ""
    positional_only = False
    has_source = False
    for token in expand_gallery_dl_short_options(strip_gallery_dl_invocation(tokens)):
        if values_remaining:
            if value_option in input_file_flags and token:
                has_source = True
            values_remaining -= 1
            if not values_remaining:
                value_option = ""
            continue
        if not positional_only and token == "--":
            positional_only = True
            continue
        if not positional_only and token.startswith("-") and token != "-":
            flag, separator, attached = token.partition("=")
            # gallery-dl handles this as an informational top-level mode and
            # never reaches its URL downloader, even if a URL/input file also
            # appears elsewhere in argv.
            if flag in GALLERY_DL_VARIADIC_VALUE_FLAGS:
                return False
            if flag in input_file_flags and separator:
                if attached:
                    has_source = True
                # An explicitly empty input-file value is invalid, but a
                # later positional source can still make the invocation a
                # runnable job. Keep scanning instead of discarding it.
                continue
            attached_input_flag = next(
                (
                    candidate
                    for candidate in ("-i", "-I", "-x")
                    if token.startswith(candidate) and len(token) > len(candidate)
                ),
                None,
            )
            if attached_input_flag:
                has_source = True
                continue
            count = GALLERY_DL_OPTION_VALUE_COUNTS.get(flag, 0)
            if count and not separator:
                attached_short = (
                    flag.startswith("-")
                    and not flag.startswith("--")
                    and len(flag) == 2
                    and len(token) > 2
                )
                values_remaining = count - 1 if attached_short else count
                value_option = flag if values_remaining else ""
            continue
        has_source = True
    return has_source

def parse_line(line: str, tag: str = "", notes: str = "") -> Optional[DownloadJob]:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None
    tokens = split_command(raw)
    is_cmd = is_gallery_dl_invocation(tokens)
    # Destination parsing is useful for both full commands and shorthand rows
    # such as ``URL -d folder``.  It also prevents the GUI from appending a
    # second, conflicting default destination later in build_command().
    dest = extract_destination(tokens)
    if is_cmd:
        # Destination-only rows are useful editable templates, but they are
        # not runnable jobs. Keep them in the editor while excluding them from
        # queue counts and worker dispatch. Input-file commands and special
        # positional targets remain valid without an inline HTTP URL.
        if not _gallery_dl_command_has_source(tokens):
            return None
        url = _extract_url_from_tokens(tokens, raw)
    else:
        url = _extract_url_from_tokens(tokens or [raw], raw)
    service, ident = parse_url_meta(url)
    return DownloadJob(
        raw=raw,
        is_command=is_cmd,
        url=url,
        service=service,
        ident=ident,
        dest=dest,
        tag=tag,
        notes=notes,
    )

def dependency_status() -> dict[str, bool]:
    return {
        "PySide6": importlib.util.find_spec("PySide6") is not None,
        "gallery_dl module": importlib.util.find_spec("gallery_dl") is not None,
        "requests": importlib.util.find_spec("requests") is not None,
        "openpyxl": importlib.util.find_spec("openpyxl") is not None,
        "Pillow": importlib.util.find_spec("PIL") is not None,
        "keyring": importlib.util.find_spec("keyring") is not None,
        "yt-dlp": importlib.util.find_spec("yt_dlp") is not None,
        "youtube-dl": importlib.util.find_spec("youtube_dl") is not None,
        "PySocks": importlib.util.find_spec("socks") is not None,
        "brotli/brotlicffi": (importlib.util.find_spec("brotli") is not None or importlib.util.find_spec("brotlicffi") is not None),
        "zstandard": importlib.util.find_spec("zstandard") is not None,
        "PyYAML": importlib.util.find_spec("yaml") is not None,
        "toml/tomllib": (importlib.util.find_spec("tomllib") is not None or importlib.util.find_spec("toml") is not None),
        "SecretStorage": importlib.util.find_spec("SecretStorage") is not None,
        "Psycopg": (importlib.util.find_spec("psycopg") is not None or importlib.util.find_spec("psycopg2") is not None),
        "truststore": importlib.util.find_spec("truststore") is not None,
        "Jinja": importlib.util.find_spec("jinja2") is not None,
        "FFmpeg": shutil.which("ffmpeg") is not None,
        "mkvmerge": shutil.which("mkvmerge") is not None,
        "7z/7za/7zz": (shutil.which("7z") is not None or shutil.which("7za") is not None or shutil.which("7zz") is not None),
    }

__all__ = ['IS_WINDOWS', 'IS_MACOS', 'APP_NAME', 'APP_VERSION', 'APP_DIR', 'HISTORY_FILE', 'HISTORY_MAX_BYTES', 'HISTORY_TRIM_BYTES', 'AUTOSAVE_FILE', 'BACKUP_DIR', 'PROFILES_DIR', 'SETTINGS_FILE', 'MAX_LOG_BLOCKS', 'MAX_LOG_LINES', 'MAX_LOG_LINE_CHARS', 'STOP_GRACE_SECONDS', 'MAX_IMPORT_BYTES', 'MAX_QUEUE_JOBS', 'MAX_COMMAND_LINE_CHARS', 'MAX_QUEUE_SOURCE_ROWS', 'REDACTED', 'SECRET_VALUE_FLAGS', 'SECRET_OPTION_KEYS', 'MANAGED_AUTH_KEYS', 'GALLERY_DL_OPTION_VALUE_COUNTS', 'GALLERY_DL_VARIADIC_VALUE_FLAGS', 'OFFICIAL_GALLERY_DL_LINKS', 'OFFICIAL_GALLERY_DL_DEPENDENCIES', 'app_data_dir', 'timestamp_slug', 'unique_path', 'human_size', 'safe_expand_path', 'atomic_write_text', 'is_false_value', 'safe_bool', 'is_sensitive_option_key', 'append_extra_args_to_command', 'append_extra_args_to_database_text', 'command_with_destination', 'looks_like_database_entry', 'sanitize_service_policy', 'read_text_safely', 'validate_cookies_txt', '_strip_wrapping_quotes', '_windows_cmdline_to_argv', 'split_command', 'command_string_to_argv', 'command_executable_available', 'find_gallery_dl', 'detect_config_path', 'open_path', 'parse_url_meta', 'extract_destination', 'normalized_job_argv', 'find_exact_duplicate_groups', 'parse_text_database', '_row_value', 'redact_sensitive_argv', 'redact_sensitive_text', 'redact_oauth_output', 'OAuthOutputRedactor', 'redact_sensitive_database_text', 'quote_arg_for_preview', 'build_raw_command_from_columns', 'csv_rows_first_column_fallback', 'classify_error', 'ensure_no_option', 'safe_int', 'normalize_process_return_code', 'safe_filename', 'sanitize_spreadsheet_cell', '_gdl_basename', '_is_python_launcher', 'is_gallery_dl_invocation', 'strip_gallery_dl_invocation', '_extract_url_from_tokens', 'parse_line', 'dependency_status']
