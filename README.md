<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/gdlscrape-logo-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/images/gdlscrape-logo-light.png">
    <img src="docs/images/gdlscrape-logo-light.png" alt="GdlScrape logo" width="180">
  </picture>
</p>

<h1 align="center">GdlScrape</h1>

<p align="center">
  <img alt="Version 1.0.3" src="https://img.shields.io/badge/version-1.0.3-35e5c7">
  <a href="https://github.com/Halo1211/GdlScrape/actions/workflows/ci.yml"><img alt="CI status" src="https://github.com/Halo1211/GdlScrape/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-48bfe3">
  <img alt="PySide6" src="https://img.shields.io/badge/UI-PySide6-f038d1">
</p>

GdlScrape is a desktop download manager powered by
[gallery-dl](https://github.com/mikf/gallery-dl). Build links, edit configuration,
and manage downloads through a visual queue.

![GdlScrape dashboard](docs/images/gdlscrape-dashboard.png)

## Features

- Link Builder with 97 recipes for 27 websites, URL detection, batch input, and
  TXT import/export.
- Config Maker with 15 starter examples, guided site settings, a config helper,
  and a searchable reference of 645 documented configuration paths.
- Parallel downloads with pause, cancellation, retries, progress, and worker logs.
- TXT, CSV, and XLSX job imports, a searchable library, history, and schedules.
- Account profiles for cookies, passwords, API tokens, and supported OAuth flows.
- Metadata files, download archives, CBZ output, and Pixiv animation processing.
- English and Indonesian interfaces with dark and light themes.

## Installation

### Windows portable build

1. Download an available Windows ZIP from
   [GitHub Releases](https://github.com/Halo1211/GdlScrape/releases).
2. Extract the complete archive.
3. Run `GdlScrape.exe` inside the extracted `GdlScrape` folder.

Keep `gallery-dl.exe` and the `_internal` folder beside the application. Portable
builds include Python and gallery-dl. Use the supplied `SHA256SUMS.txt` to verify
release files.

### Run from source

Requirements:

- Python 3.10 or newer.
- [`gallery-dl`](https://gdl-org.github.io/docs/) 1.32.14 or newer.
- Windows, Linux, or macOS with a graphical desktop.

```powershell
git clone https://github.com/Halo1211/GdlScrape.git
cd GdlScrape
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m gallery_dl_app
```

On Linux or macOS, activate the environment with `source .venv/bin/activate`.
You can also launch the source tree with `python gdlscrape.py`.

Optional tools depend on the selected workflow: FFmpeg for animation conversion,
`yt-dlp` for delegated video downloads, and 7-Zip for the queue's 7z/tar archive
output. The config helper reports requirements for configured actions.

## First download

1. Select **Add downloads**.
2. Paste complete URLs or use **Link Builder** to build them from usernames or IDs.
3. Choose the destination and any options for these jobs.
4. Review **Preview**, then select **Add to Queue**.
5. Close the dialog and select **DOWNLOAD**, or press `Ctrl+Enter`.

Adding jobs does not start downloads. For a simulation, select **Prepare safe
test** before adding the jobs. A simulation still contacts the website.

Use **Config** to save reusable defaults. Leaving the job's filename and subfolder
fields blank allows the site's saved rules to apply. Enable **Exact folder (-D)**
only when you want to bypass those subfolders.

## Link Builder

Choose a website and page type, then enter the requested username or ID. Select
**Several targets (one per line)** for batches of up to 200 rows. Separate multiple
fields with `|`, following the example shown for the selected recipe.

**Paste complete links** and **Import links TXT…** detect supported websites.
Invalid rows are reported with their line numbers; fix them before adding the
batch. Validation checks the installed extractor's URL patterns. It does not
verify that the content exists or that your account can access it.

You can retain several batches in the prepared-links list, remove duplicates,
copy links, or export a UTF-8 TXT file. **Open saved config for this website…**
opens the relevant site settings.

## Configuration and login

Open **Config → Start here** to import a JSON configuration or load a starter
example. Choose your download folder, edit site settings, review the config
helper, and save. Imports preserve settings you have not edited. Updating an
existing config creates a backup; **Save As...** writes a separate copy.

The [configuration guide](docs/CONFIG_MAKER_GUIDE.md) covers filters, folder rules,
archives, metadata, and animation formats. The
[example reference](docs/CONFIG_EXAMPLES_REVIEW.md) lists each preset and its source.

Open **Manage → Accounts / Login** when a website requires authentication. Choose
one supported method and use your own account. OAuth instructions appear in the
account editor. Saved passwords and tokens use the operating-system credential
vault; temporary credential configs are cleaned up after use.

Configs, cookies, caches, backups, and downloaded files may contain private data.
Review them before sharing. Screenshots in this repository use an isolated demo
session.

## Application data

Settings, library records, history, and backups are stored in
`~/.gallery_dl_gui_dashboard`. **Manage → Application and Files** provides version
checks, file locations, backups, and diagnostics. Upgrading the application keeps
this data directory.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and checks,
[ARCHITECTURE.md](ARCHITECTURE.md) for module boundaries, and
[CHANGELOG.md](CHANGELOG.md) for release changes.

To build the Windows package:

```powershell
.\scripts\build_windows.ps1
```

The script produces `release/GdlScrape/GdlScrape.exe`,
`release/GdlScrape-v1.0.3-win64.zip`, and checksums. Building locally does not publish
a GitHub release.

## License and support

GdlScrape is an independent project and is not affiliated with gallery-dl. Use it
for content you are permitted to access and retain.

Licensed under the [MIT License](LICENSE). Report reproducible bugs through
[GitHub Issues](https://github.com/Halo1211/GdlScrape/issues). For private security
reports, follow [SECURITY.md](SECURITY.md).
