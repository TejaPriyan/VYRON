# Security Policy

## Supported Status

VYRON is in early development and is **not production ready**. Security controls implemented at the HTTP application and prototype level are documented in [docs/SECURITY.md](docs/SECURITY.md), alongside planned controls that are not yet built.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Instead, report vulnerabilities privately to the repository maintainers via GitHub Security Advisories (**Security → Advisories → Report a vulnerability**) with:

- A clear description of the issue and affected component or route
- Minimal reproduction steps (never include real tokens, passwords, or private credentials)
- Expected versus observed behavior

## Core Security Invariants

Contributors must preserve these invariants (guarded by automated tests in `tests/`):

- **Strict CSP**: No inline scripts, no inline styles, no `eval`, no `innerHTML`, and same-origin `connect-src 'self'` on all prototype pages.
- **No secrets in the tree or client**: Secrets live only in server-side environment variables (`.env` is gitignored; `.env.example` ships empty external credentials).
- **SSRF & host protection**: GitHub and PyPI/npm readers validate strict names/URLs before constructing requests, enforce fixed HTTPS hosts, disable redirects, and cap response sizes.
- **No silent errors or upstream leakage**: Error responses return fixed messages and never leak stack traces, upstream bodies, or tokens.
