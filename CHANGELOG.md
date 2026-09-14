# Changelog

All notable changes to GdlScrape are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.01] - 2026-09-14

### Added

- Account profiles now provide guided method-specific editors for browser cookies, `cookies.txt`, username/password, API keys, and OAuth.
- OAuth account connection supports Pixiv callback-code input, Mastodon instances, safe transcript redaction, and profile-isolated caches.
- Account cache tools can show status, clear one site, clear expired or all profile entries, and vacuum the cache database.
- Site Config Studio provides guided per-site archive databases, current Reddit OAuth app keys, Pixiv include/download settings, protected legacy credential import, and typed advanced options.
- Site Config Studio now has a searchable All Options editor for General defaults and per-site overrides across installed gallery-dl extractors, with typed values, value-source visibility, inheritance removal, and safe secret handling.

### Fixed

- Raised the Pillow floor to 12.3.0 so new installs and release builds include the upstream security fixes reported for 12.2.0.
- Account management now scrolls at smaller window sizes instead of clipping its editor and action buttons.
- Table fonts are constructed with a normalized positive point size, preventing Qt's `Point size <= 0 (-1)` warning.

- Command parsing now tracks every value-taking option and alias in gallery-dl 1.32, including `-a`, two-value print options, cache/config filters, and variadic extractor listing.
- Destination normalization now skips values owned by other options, so text such as `--exec -d URL` is never rewritten as an output path.
- Empty attached input-file options no longer hide a valid positional URL later in the same command, while truly source-less templates remain excluded.
- Informational `--list-extractors` commands are excluded from the download queue instead of being misidentified as downloadable URL jobs.
- Preview/log redaction now masks secrets nested inside JSON or command-valued options, including attached `-o` forms and escaped quote characters, without reformatting argv.
- Interrupted runs are resolved only after the recovery decision and queue restoration succeed, preserving crash recovery if the prompt or UI fails.
- Version probes retain both stdout warnings and stderr failures instead of hiding the decisive diagnostic stream.
- Audit/log exports and session base-command persistence now reapply credential redaction at their final storage boundary.
- Multi-file drag-and-drop labels only successfully loaded files, and Dry-run Validator recognizes input-file and OAuth sources without false errors.
- README installation guidance now matches the packaged gallery-dl 1.32.10 minimum.
- URL metadata detection now ignores nested URLs in query strings and fragments instead of misclassifying the outer job.
- Headerless XLSX imports now keep a multi-line URL cell as one normalized queue entry.
- Home-relative config and destination paths are expanded before validation and use.
- Scheduler interval calculations now contain infinite or overflowing legacy values instead of crashing the timer callback.
- Probe failures and Python-style argv exception messages now redact embedded credentials.
- UTF-32 text imports are detected before UTF-16, preserving their contents correctly.
- Spreadsheet boolean `False` values are preserved, and disable their corresponding imported jobs.
- Explicit POSIX command paths must have execute permission before they are accepted.
- Error classification prioritizes specific HTTP/config/path causes over generic network wrappers and no longer finds `rate` inside unrelated words.
- XLSX database templates now store README separators and database values as literal text, preventing Microsoft Excel from rejecting the workbook as malformed.
- CSV database templates now neutralize formula-like values while preserving their original arguments when imported back into GdlScrape.
- CSV and XLSX templates now expose the documented `notes` column and use the same recommended column order as the importer guide.
- Account deletion now clears library references and disables affected schedules instead of leaving unusable profile IDs behind.
- Account site selection now comes from installed extractor categories; OAuth and username/password choices are filtered to documented sites and typed mistakes are rejected with suggestions.
- Composer OAuth now uses a fixed supported-site picker and a separate Mastodon instance field, eliminating free-form OAuth site typos.
- Existing library databases now migrate the account-profile column before account-linked entries are written.
- Scheduler persistence rejects non-finite timestamps, and weekly weekday lists tolerate a trailing comma without changing the selected days.
- The visual filter builder now rejects malformed, arithmetic, and introspection-style metadata fields before they reach gallery-dl.
- Session/profile loading now validates command and path field types before mutating application state.
- History tail migration no longer drops a valid row when its byte limit begins exactly at a line boundary.
- Service detection now maps X/Twitter and Reddit subdomains to their gallery-dl extractor categories.
- Malformed IPv6-like clipboard URLs are now ignored instead of escaping the inbox callback.
- Clipboard Inbox now contains queue-parse failures instead of leaking them through Qt's event loop.
- The installed-option catalog now treats fully optional values such as `--list-extractors [CATEGORIES]` as optional.
- Audit preflight now rejects per-job destinations that point to ordinary files.
- Managed-runtime setup now reports an unusable runtime directory instead of raising from the button callback.
- Session booleans and Composer numeric defaults now handle non-finite JSON numbers without silently enabling settings or crashing.
- Output-folder actions now expand `~` consistently with downloads, scans, and preflight checks.
- Windows config detection now includes the official `%USERPROFILE%\gallery-dl\config.json` location.
- Command-guide examples no longer advertise date, child/post range, or tag flags unsupported by gallery-dl 1.29.
- Schedules containing removed credential placeholders are now saved disabled, and legacy copies are disabled instead of retrying forever.
- XLSX imports reject hostile declared row, column, and cell dimensions before worksheet iteration can exhaust memory or CPU.
- Legacy history import tolerates malformed return codes and releases its source file even when migration fails.
- Atomic text saves, app-data backups, spreadsheet exports, and archive/image post-processing now use private collision-resistant temporary files.
- Error details are redacted at dialog and background-task boundaries, including direct account, schedule, and filter warnings.
- Corrupt scheduled account references are deferred instead of crashing the timer or silently running without authentication.
- Download workers sanitize malformed persisted per-service retry and delay values before use.
- Destination-only `gallery-dl -d PATH` template rows remain editable in text databases but are excluded from runnable queue jobs.
- Windows command destinations discard unusable trailing spaces and dots before duplicate detection and execution.
- The minimum gallery-dl version is now 1.32.10 so Pawchive URLs supported by current databases have an available extractor.

## [1.0] - 2026-09-01

### Added

- First public release under the GdlScrape name.
- Original symmetrical winged emblem with a central spark, downward point, and split cyan/magenta accents.
- Composer for shared command, config, login, and site-preset workflows.
- Parallel queue, live workers, history, retry, pause, stop, cancellation, and run recovery.
- SQLite library, scheduler, URL inbox, secure account profiles, option catalog, and managed runtime.
- TXT, CSV, and XLSX import/export with tags, notes, filters, and destination rules.
- Diagnostic reports, backups, session persistence, health checks, and post-processing tools.
- English and Indonesian UI modes with dark and light themes.
- GitHub-ready documentation, packaging metadata, CI, and Windows build script.

### Security

- Redacts credentials from previews, logs, sessions, library records, recovery records, exports, and reports.
- Stores reusable secrets with the operating-system vault and deletes temporary merged configs after use.
- Buffers OAuth subprocess output until token-bearing paragraphs can be redacted, and keeps each profile's OAuth/session cache isolated.
- Uses argv-based subprocess execution without `shell=True`.
