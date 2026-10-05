# Changelog

## 1.0.3 — 2026-10-05

### Added

- Link Builder with 97 recipes across 27 websites, multi-field batches, automatic
  URL detection, and TXT import/export.
- Fifteen starter configs with source links and explanations.
- Config helper for inherited settings, processing actions, filters, and required
  external tools.
- Guided editors for folder and filename rules, metadata locations, archive
  contents, media filters, and Pixiv animation formats.

### Fixed

- Version checks no longer reference a removed sidebar button.
- Prepared links are revalidated after manual edits and deduplicated before use.
- Blank job naming fields preserve saved site rules and download history behavior.
- Config imports reject invalid JSON without losing the current draft; editors
  preserve unknown values and named processing definitions.
- Windows bundles include dynamically loaded gallery-dl processing and downloader
  modules.

### Changed

- Application maintenance is consolidated under **Application and Files**.
- Project documentation is concise, in English, and uses sanitized screenshots.
- CI uses pytest to include both function-based and unittest regression tests.

## 1.0.2 — 2026-09-24

- Added a dedicated Config Builder and 645-path offline configuration reference.
- Added guided URL recipes, worker log filters, and site-specific OAuth guidance.
- Improved config draft saving, account selection, and URL handling.
- Included gallery-dl in the Windows portable folder and configuration assets in
  Python distributions.

## 1.01 — 2026-09-14

- Added guided account profiles, OAuth connection, and isolated account caches.
- Added site configuration, archive controls, and typed advanced options.
- Improved command parsing, credential redaction, import validation, crash
  recovery, and scheduling.
- Updated dependency requirements and strengthened temporary-file handling.

## 1.0 — 2026-09-01

- Introduced the GdlScrape desktop interface with job composition, parallel queue,
  history, library, schedules, and account profiles.
- Added TXT, CSV, and XLSX imports, diagnostic reports, and post-processing tools.
- Added English and Indonesian interfaces, dark and light themes, Windows
  packaging, and CI.
