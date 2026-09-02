# Contributing to GdlScrape

Thank you for helping improve GdlScrape. Before substantial work, open an issue describing the problem and proposed behavior so the scope can be agreed first.

## Local setup

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e ".[dev]"
```

## Before a pull request

```powershell
ruff check .
py -m unittest discover -s tests -v
```

Keep changes focused and add a regression test for parser, worker, persistence, or security-boundary changes. Do not include account details, cookies, tokens, downloaded media, application data, or generated caches.

The architecture and security rules in [ARCHITECTURE.md](ARCHITECTURE.md) are part of the contribution contract.

## Licensing note

By contributing to GdlScrape, you agree that your contributions are provided under the project's [MIT License](LICENSE).
