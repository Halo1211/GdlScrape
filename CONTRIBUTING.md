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

The project owner has not selected an open-source license yet. Discuss contribution licensing with the owner before submitting a pull request.
