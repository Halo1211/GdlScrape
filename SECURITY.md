# Security policy

## Supported versions

Security fixes target the latest GdlScrape 1.x release. Keep gallery-dl and optional
media tools updated when running from source.

## Report a vulnerability

Submit private reports through
[GitHub Security Advisories](https://github.com/Halo1211/GdlScrape/security/advisories/new).
Do not post credentials, private URLs, cookies, tokens, or working exploits in a
public issue.

Include the affected version, operating system, reproduction steps, impact, and a
minimal proof of concept using demo data. Allow time for validation and a
coordinated fix before public disclosure.

## Data and dependency boundaries

GdlScrape starts gallery-dl and optional media tools as subprocesses. Report
vulnerabilities in those dependencies to their upstream maintainers as well.

Reusable account secrets use the operating-system credential vault. Plain-text
configs, browser cookies, account caches, backups, downloaded metadata, and media
can still contain private information. Review exported diagnostics and screenshots
before sharing them.

Third-party configs may specify external commands or processing actions. Inspect
them before running downloads. The config helper provides validation feedback;
it does not establish that an imported configuration is safe to execute.
