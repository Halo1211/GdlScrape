# GdlScrape Architecture

## Dependency map

```text
       gdlscrape.py / gallery_dl_app.__main__
                         |
                   application.py
                         |
                      window.py
             +-----------+-------------------+
             |                               |
       dashboard_ui.py                 controller mixins
                                             |
                                        composer.py
                                             |
                                         workers.py
                                             |
                                           core.py
                                             |
                                          models.py
```

Dependencies point downward. `core.py` does not import Qt, so parsers and command builders can be tested without creating a window. `workers.py` does not import UI modules. Feature mixins may call methods from another mixin through a `MainWindow` instance, but they must not import one another.

## Responsibilities

- **Core:** pure data transformations, argument parsing, error classification, redaction, and small filesystem helpers.
- **Workers:** blocking processes, process groups, cancellation, subprocess logs, and post-processing.
- **Dashboard UI:** main visual composition, live metrics, pipeline state, and the activity graph.
- **Composer:** builds command arguments and config data from one shared state.
- **Feature store:** SQLite library, history, run recovery, schedules, and account metadata without secret values.
- **Management:** library and automation UI, clipboard inbox, account profiles, option catalog, and managed runtime.
- **Secure vault:** system keyring or Windows DPAPI storage; secrets do not enter the application database.
- **Controller mixins:** event handlers and feature UI grouped by domain.
- **Window:** application state and controller composition; no large feature implementation.
- **Application:** font, initial stylesheet, `QApplication` creation, icon setup, and the event loop.

## Change rules

1. Never run long network or subprocess operations on the GUI thread.
2. Never use `shell=True` or `preexec_fn` in workers.
3. Build commands as argument lists, not shell strings.
4. Pass credentials through `redact_sensitive_argv` before display, persistence, or export.
5. Add regression coverage for parser and worker-lifecycle changes.
6. Add shared fields to `ComposerState`; do not duplicate them across command and config dialogs.
7. Schedulers and runtime tools must use argv or `QProcess` and require an explicit user action or schedule.
8. Remove temporary credential configs after each run and during the next startup after a crash.

## Entrypoints

- `gdlscrape.py` is the preferred source-tree launcher.
- `python -m gallery_dl_app` launches the package directly.
- The installed `gdlscrape` command is declared in `pyproject.toml`.
