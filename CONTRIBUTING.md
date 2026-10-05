# Contributing to GdlScrape

For substantial changes, open an issue describing the problem and proposed
behavior before implementation. Keep pull requests focused on one change.

## Set up the project

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

On Linux or macOS, use `source .venv/bin/activate` to activate the environment.
Run the application with `python -m gallery_dl_app`.

## Validate changes

```powershell
python -m ruff check .
python -m pytest -q
```

Pytest runs both function-based tests and the existing unittest cases. CI runs
these checks on Windows with Python 3.10 and 3.12.

Add regression coverage for changes to parsing, config preservation, persistence,
worker lifecycles, or credential handling. For visual changes, check the relevant
screens in both themes. Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing
module boundaries.

## Prepare a pull request

1. Explain the problem and resulting behavior.
2. Include the checks you ran and any remaining limitations.
3. Update user documentation and the changelog when behavior changes.
4. Use demo data for screenshots; remove usernames, personal paths, and tokens.

Do not include account data, cookies, private URLs, downloaded media, application
backups, or generated build files. Report vulnerabilities privately as described
in [SECURITY.md](SECURITY.md).

## Build a Windows package

Run `.\scripts\build_windows.ps1` from PowerShell. The build includes gallery-dl,
its dynamic modules, configuration assets, and Windows version metadata. Test the
complete extracted folder, including a local download and any changed processing
features, before distributing the ZIP.

## License

Contributions are provided under the project's [MIT License](LICENSE).
