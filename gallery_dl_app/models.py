from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DownloadJob:
    raw: str
    is_command: bool
    url: str
    service: str = "-"
    ident: str = "-"
    dest: str = "-"
    tag: str = ""
    notes: str = ""
    enabled: bool = True

@dataclass
class JobResult:
    idx: int
    status: str
    rc: Optional[int] = None
    downloaded: int = 0
    skipped: int = 0
    errors: int = 0
    warnings: int = 0
    message: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float = field(default_factory=time.time)

__all__ = ['DownloadJob', 'JobResult']
