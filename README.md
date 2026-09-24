<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/gdlscrape-logo-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/images/gdlscrape-logo-light.png">
    <img src="docs/images/gdlscrape-logo-light.png" alt="GdlScrape logo" width="180">
  </picture>
</p>

<h1 align="center">GdlScrape</h1>

<p align="center"><strong>Gallery downloads, composed.</strong></p>

<p align="center">
  <img alt="Version v1.0.2" src="https://img.shields.io/badge/version-v1.0.2-35e5c7">
  <a href="https://github.com/Halo1211/GdlScrape/actions/workflows/ci.yml"><img alt="CI status" src="https://github.com/Halo1211/GdlScrape/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-48bfe3">
  <img alt="PySide6" src="https://img.shields.io/badge/UI-PySide6-f038d1">
  <img alt="gallery-dl powered" src="https://img.shields.io/badge/powered%20by-gallery--dl-0b2530">
</p>

GdlScrape is a desktop download manager for [`gallery-dl`](https://github.com/mikf/gallery-dl). It turns gallery collection work into a visual workflow with a composer, parallel queue, download history, scheduling, secure account profiles, and post-processing tools.

> [!IMPORTANT]
> GdlScrape is an independent community interface and is not affiliated with the gallery-dl project. Download only content you are permitted to access and retain.

![GdlScrape dashboard](docs/images/gdlscrape-dashboard.png)

## Highlights

- Compose jobs from URLs or complete `gallery-dl` commands without duplicating settings.
- Run a bounded parallel queue with pause, stop, cancel, retry, live worker status, and per-worker log filters.
- Preview the final command and run a safe simulation before downloading.
- Import TXT, CSV, or XLSX databases with tags, notes, destinations, and per-job arguments.
- Keep a searchable SQLite library, persistent history, interrupted-run recovery, and schedules.
- Use browser cookies, `cookies.txt`, OAuth, or secure OS-backed account profiles.
- Build site presets, filters, archive settings, and post-processing rules visually.
- Switch between dark and light themes and English or Indonesian UI text.
- Use the gallery-dl executable included in the Windows release folder; source installs can manage an isolated runtime from the management center.
- Protect exported logs, reports, sessions, and previews with credential redaction.

## Requirements

- Python 3.10 or newer
- Windows, Linux, or macOS
- [`gallery-dl`](https://gdl-org.github.io/docs/) 1.32.10 or newer

Optional tools extend specific workflows:

- FFmpeg for Pixiv Ugoira and media conversion
- 7-Zip for `.7z` output
- `yt-dlp` for extractors that delegate video downloads

## Quick start

Windows users can download the `GdlScrape-v1.0.2-win64.zip` package from the [v1.0.2 GitHub Release](https://github.com/Halo1211/GdlScrape/releases/tag/v1.0.2). Extract the whole archive and run `GdlScrape.exe`. Verify it with the accompanying `SHA256SUMS.txt`; no separate Python installation is required.

For a build from this source tree, run `scripts/build_windows.ps1` and open `release/GdlScrape/GdlScrape.exe`. Keep the entire `GdlScrape` folder together.

```powershell
git clone https://github.com/Halo1211/GdlScrape.git
cd GdlScrape
py -m pip install -r requirements.txt
py gdlscrape.py
```

You can also launch the package directly:

```powershell
py -m gallery_dl_app
```

## Basic workflow

1. Open **Composer** and add one or more supported URLs.
2. Configure authentication under **Login & Cookies** only when the site requires it.
3. Apply one-time job overrides or save reusable defaults to the active config.
   Use the separate **Config** button when you only want to edit saved defaults.
4. Select **Prepare safe test** and review the command preview.
5. Add the jobs to the queue and press `Ctrl+Enter` to start.

GdlScrape runs gallery-dl as a subprocess. It does not replace gallery-dl's extractor, configuration, or archive behavior.

## Account profiles

Open **Library & Automation Center → Accounts**, then:

1. Choose a site from the installed extractor list. Typed names are validated and likely typos are suggested.
2. Choose one method: browser cookies, `cookies.txt`, username/password, API key/token, or OAuth.
3. Save the profile and select **Use Selected**. Leaving a password/token field blank while editing keeps its stored value.

**Secret** is the generic security term for a password, API key, or token. Passwords and tokens are stored in the operating-system credential vault; the account database stores only an opaque reference. Browser-cookie paths and usernames are profile metadata, not secrets.

For OAuth, select Pixiv, DeviantArt, Flickr, Reddit, SmugMug, Tumblr, or Mastodon and press **Connect OAuth**. Site-specific instructions and gallery-dl output appear live in the account panel. Mastodon also needs its instance hostname. Pixiv asks for a short-lived code from the browser's Developer Tools; paste it into **Authorization response** and press **Send Pixiv Code** within 30 seconds. The other sites normally return through a browser redirect to gallery-dl's local listener on `localhost:6414`. If that redirect fails, paste the complete URL from the official gallery-dl redirect page or the local callback into **Authorization response** and press **Send Callback URL**. Flickr requires your own `extractor.flickr.api-key` and `api-secret` before OAuth; the default blank keys cannot authorize. The GUI sends the Pixiv code or local callback without a separate console. Issued refresh tokens are masked in the output. Each account profile has an isolated cache so separate accounts do not overwrite each other's sessions. **Cache Tools** can inspect, clear expired entries, clear the whole profile cache, or optimize it; **Clear Site Cache** logs out only the selected extractor in that profile.

The Composer's **URL guide** builds URLs for supported booru tag, post, pool, and favorite pages, including Hypnohub, Rule34, Safebooru, TBIB, XBooru, and Gelbooru. It also covers Paheal, Danbooru, E621-family sites, Pixiv, DeviantArt, Reddit, Twitter/X, Kemono, Fanbox, and Flickr. Each generated URL is checked against the installed gallery-dl extractor before it is added. For Hypnohub and Paheal presets, output-directory templates now fall back to post or pool identifiers when a tag name is unavailable.

## Site-specific config

Open **Composer → Sites → Site Config Studio** to edit extractor-specific settings without writing JSON manually:

- **All Options** can edit either **General defaults** (`extractor.<option>`, used by every site) or a **Per-site override** (`extractor.<site>.<option>`). Choose a site from the installed extractor list, search its option catalog, review the current value and its source, then use **Set value** or **Remove / inherit**. The catalog combines common settings, the pinned official gallery-dl 1.32.12 manual, and options discovered from the installed extractors.
- **Full Reference** searches an offline index of 645 documented configuration paths, including extractor, downloader, output, and postprocessor sections. Select a concrete path to set or remove a typed value directly. Postprocessor item fields are reference-only because they must be placed inside an `extractor.postprocessors` entry. The tab links to the current official manual.
- **Archive** creates a separate SQLite archive path for any site, controls `duplicates`, and sets `archive-format`. A copied `\_` is normalized to `_` because JSON does not accept `\_` as an escape sequence.
- **Reddit** writes the current `client-id` and `user-agent-oauth` keys. After changing the client ID, clear the Reddit site cache from the matching Account profile before reconnecting OAuth.
- **Pixiv** separates artwork/profile settings (`extractor.pixiv`) from novel settings (`extractor.pixiv-novel`), with aligned groups for include, metadata, comments, captions, tags, covers, embeds, full-series, and ugoira. Account OAuth is recommended; protected `refresh-token` and `cookies.PHPSESSID` fields are available only for legacy manual configs and require a plain-text storage confirmation.
- **Advanced** adds any documented extractor option with an explicit text, boolean, integer, number, JSON, or null type. Sensitive option previews are always hidden.

General values flow down to every site; a per-site value wins only for that extractor. **Remove / inherit** deletes the selected saved value instead of writing a fake empty value, allowing the next broader level or gallery-dl's built-in default to take effect. Studio changes are merged with the active config, preserving unknown options. They remain unsaved until **Save Defaults** is used, and an existing config is backed up before replacement.

## Input formats

Use one entry per line:

```text
https://example.com/user/123
https://example.com/user/456 --range 1-20
gallery-dl -d "D:/Media/Creator" https://example.com/user/789
python -m gallery_dl --cookies-from-browser firefox https://example.com/post/42
```

Destination-only templates such as `gallery-dl -d "F:\\Rips\\Download\\Creator"`
may stay in the TXT file. They remain editable in the input editor but are ignored
by queue counts and downloads until a URL is appended.

CSV and XLSX imports support `url`, `destination`, `extra_args`, `enabled`, `tag`, `notes`, or a raw `command` column.

## Privacy and security

- Secrets are never intended to be committed to this repository.
- Account profile secrets use the system keyring or Windows DPAPI.
- Each profile uses a separate permission-restricted gallery-dl cache for login sessions and OAuth tokens.
- Temporary merged config files are permission-restricted and removed after a run.
- Sensitive command arguments, headers, URLs, and subprocess output are redacted before persistence or export.
- Clipboard monitoring is opt-in, host allowlisted, and never starts a download automatically.

Plain gallery-dl config files, their backups, `cookies.txt`, and OAuth cache files can contain account access data. Review them before sharing diagnostics or backups.

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+O` | Import a database |
| `Ctrl+S` | Save the current session |
| `Ctrl+Enter` | Start downloads |
| `Ctrl+Shift+P` | Preview final commands |
| `Ctrl+L` | Focus the input editor |
| `F5` | Run the health check |

## Development

Create an isolated environment, install the project with development tools, and run the checks:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e ".[dev]"
py -m unittest discover -s tests -v
ruff check .
```

Build a Windows executable with:

```powershell
.\scripts\build_windows.ps1
```

The script produces `release/GdlScrape/GdlScrape.exe`, a bundled `gallery-dl.exe`, and a versioned ZIP containing the entire application folder. Distribute the ZIP or the whole `release/GdlScrape` folder, including `_internal`; the application does not require a separate Python or gallery-dl installation. The ZIP checksum is in `release/SHA256SUMS.txt`, and executable checksums are inside the application folder. This one-folder build avoids the temporary extraction performed by one-file bundles and disables UPX compression. Antivirus classification cannot be guaranteed by packaging alone; release signing and a false-positive report to the vendor may still be needed. The release directory is ignored by Git. See [ARCHITECTURE.md](ARCHITECTURE.md) for component boundaries and [CONTRIBUTING.md](CONTRIBUTING.md) before submitting changes.

## Data location

Application data is stored outside the repository under `~/.gallery_dl_gui_dashboard`. The legacy directory name is intentionally retained in v1.0 so existing libraries, settings, schedules, profiles, and backups are not lost during the GdlScrape rebrand.

## Release status

This repository represents **GdlScrape v1.0.2**. See [CHANGELOG.md](CHANGELOG.md) for release notes.

## License

GdlScrape is released under the [MIT License](LICENSE).
