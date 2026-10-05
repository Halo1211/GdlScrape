# Architecture

GdlScrape separates GUI composition, configuration transformations, persistence,
and background work. `MainWindow` combines controller mixins; gallery-dl performs
the actual extraction and downloading in subprocesses.

## Module responsibilities

| Area | Modules | Responsibility |
| --- | --- | --- |
| Startup | `application.py`, `__main__.py`, `gdlscrape.py` | Application creation, fonts, icons, and event loop. |
| Main window | `window.py`, `dashboard_ui.py`, `ui_shell.py`, `themes.py` | Window state, dashboard layout, shared controls, and styling. |
| Job composition | `composer.py`, `models.py` | Shared composer state, per-job overrides, saved config workflow, and job records. |
| Configuration | `config_maker.py`, `config_helper.py`, `config_examples.py`, `postprocessor_config.py` | Config transformations, preservation, validation, and starter data. |
| Config editors | `config_value_editor.py`, `content_filter_editor.py`, `path_rules_editor.py`, `postprocessor_editor.py` | Typed values, filters, ordered naming rules, and processing actions. |
| Links | `url_builder.py`, `link_builder.py` | URL recipes, extractor recognition, batch validation, and link preparation. |
| Downloads | `queue_controller.py`, `workers.py` | Queue coordination, background processes, progress, and cancellation. |
| Management | `management.py`, `application_tools.py`, `system_tools.py`, `advanced_tools.py` | Accounts, schedules, maintenance, queue utilities, and version probes. |
| Storage | `feature_store.py`, `secure_vault.py`, `oauth_flow.py` | SQLite records, OS-backed secrets, isolated account caches, and OAuth. |
| Reports and core | `reports.py`, `core.py`, `feature_logic.py` | Diagnostics, parsing, redaction, filesystem helpers, and shared policies. |

`core.py` and configuration transformation helpers remain usable without creating
a Qt window. Workers must not import UI modules. Controller mixins interact
through the window instance rather than importing one another.

## Configuration scopes

- **Job options** apply only to the prepared jobs and become command arguments.
- **Saved defaults** belong to the active gallery-dl configuration file.
- **Account profiles** supply authentication through temporary runtime configs.

Importing a config replaces the editor draft. Moving between forms preserves
unedited values, named processing actions, and unknown options. Saving validates
the entire draft, writes atomically, and backs up an existing file.

Gallery-dl accumulates processing actions across matching configuration levels.
A site's action list does not replace shared actions. The helper and editors must
model this behavior consistently.

## Persistence and process boundaries

Application data resides in `~/.gallery_dl_gui_dashboard`. SQLite stores library,
history, schedule, recovery, and account metadata. Reusable passwords and tokens
are held by the OS credential vault, using keyring or Windows DPAPI.

Commands are built as argument lists. Downloads and version probes run outside
the GUI thread. Temporary credential configs use restricted permissions and are
removed after execution; startup also cleans up abandoned runtime files.

## Change rules

1. Keep blocking network and subprocess work off the GUI thread.
2. Execute argument lists without `shell=True` or `preexec_fn`.
3. Redact credentials before display, persistence, or export.
4. Use shared composer state rather than duplicating command and config fields.
5. Preserve imported values until an explicit edit changes them.
6. Require a user action or an enabled schedule before starting downloads.
7. Test parser, worker, persistence, and security-boundary changes.
8. Bundle dynamically loaded gallery-dl modules and config assets in releases.

The public version is defined in `core.py` and must match `pyproject.toml` and
`packaging/version_info.txt`. Architecture tests check these values and the GUI
version display.
