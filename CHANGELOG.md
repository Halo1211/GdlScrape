# Changelog

All notable changes to GdlScrape are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
- Uses argv-based subprocess execution without `shell=True`.
