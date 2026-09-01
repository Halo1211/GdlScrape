# Security Policy

## Supported versions

Security fixes currently target the latest `1.x` release.

## Reporting a vulnerability

Do not publish credentials, private URLs, cookies, tokens, or working exploits in a public issue. Submit a private report through [GitHub Security Advisories](https://github.com/Halo1211/GdlScrape/security/advisories/new).

Include the affected version, operating system, reproduction conditions, impact, and the smallest safe proof of concept. Please allow reasonable time for validation and a coordinated fix before public disclosure.

## Security boundaries

GdlScrape starts `gallery-dl` and optional media tools as subprocesses. A vulnerability in those dependencies should also be reported to the relevant upstream project. Downloaded files and third-party extractors must be treated as untrusted input.
