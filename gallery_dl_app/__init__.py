"""Public API for GdlScrape."""

from . import core as _core
from .application import create_application, main
from .composer import ComposerState, build_composer_argv, build_composer_config
from .models import DownloadJob, JobResult
from .themes import DARK_QSS, LIGHT_QSS, SERVICE_COLORS, STATUS_COLORS
from .window import MainWindow
from .workers import CommandProbeWorker, DownloadWorker

__version__ = _core.APP_VERSION

for _name in _core.__all__:
    globals()[_name] = getattr(_core, _name)

__all__ = list(_core.__all__) + [
    "CommandProbeWorker",
    "ComposerState",
    "DARK_QSS",
    "DownloadJob",
    "DownloadWorker",
    "JobResult",
    "LIGHT_QSS",
    "MainWindow",
    "SERVICE_COLORS",
    "STATUS_COLORS",
    "create_application",
    "build_composer_argv",
    "build_composer_config",
    "main",
    "__version__",
]

del _name
