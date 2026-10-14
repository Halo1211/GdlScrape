"""Download execution through the application without a second executable."""

from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from gallery_dl_app import application, core
from tests.test_core import make_worker


def test_frozen_downloader_uses_the_application_without_a_sibling_binary(tmp_path):
    executable = tmp_path / "Portable App" / "GdlScrape.exe"
    executable.parent.mkdir()
    executable.touch()
    with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(executable)), patch.object(core.shutil, "which", return_value=None):
        command = core.find_gallery_dl()
    assert core.command_string_to_argv(command) == [str(executable.resolve()), "--gallery-dl"]
    assert not executable.with_name("gallery-dl.exe").exists()


def test_internal_command_round_trip_preserves_overrides_and_simulation(tmp_path):
    executable = str(tmp_path / "Portable App" / "GdlScrape.exe")
    prefix = [executable, "--gallery-dl"]
    source = subprocess.list2cmdline([*prefix, "--simulate", "-R7", "https://example.com/item"])
    job = core.parse_line(source)
    assert job.is_command
    assert job.url == "https://example.com/item"
    worker = make_worker(gdl_cmd=subprocess.list2cmdline(prefix), retries=2)
    command = worker.build_command(job)
    assert command[:2] == prefix
    assert command.count("--gallery-dl") == 1
    assert "--retries" not in command
    assert not worker.command_allows_postprocessing(command)


@pytest.mark.parametrize("status", [0, 2])
def test_internal_mode_routes_to_upstream_without_opening_the_gui(status):
    with patch.object(sys, "argv", ["GdlScrape.exe", "--gallery-dl", "--version"]), patch("gallery_dl.main", return_value=status) as engine, patch.object(application, "create_application", side_effect=AssertionError("GUI opened for an engine command")):
        assert application.main() == status
        assert sys.argv == ["GdlScrape.exe", "--version"]
    engine.assert_called_once_with()


@pytest.mark.parametrize("arguments, status", [(["--version"], 0), (["--gdlscrape-invalid-option"], 2)])
def test_source_entrypoint_runs_the_engine_and_propagates_its_exit_status(arguments, status):
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "gdlscrape.py"), "--gallery-dl", *arguments],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == status, result.stdout + result.stderr
    if status == 0:
        from gallery_dl.version import __version__
        assert result.stdout.strip() == __version__
