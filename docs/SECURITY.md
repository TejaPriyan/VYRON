# Security

Status: **some controls are implemented and unit-tested at the HTTP-application level; no security
review, penetration test, dependency audit or load test has been done, and anything that needs a
deployed environment is UNVERIFIED.** Authentication does not exist yet. Nothing here is a claim that
VYRON is safe to expose to the internet; see [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

This document separates what is **implemented and tested**, what is **designed but not built**, and what
is **not addressed**. If you find a vulnerability, do not open a public issue; see "Reporting" at the end.

## Implemented and tested

### Secrets and configuration

- All secrets come from environment variables, validated once at start-up (`apps/api/src/vyron_api/config.py`).
  `.env` is git-ignored and `.dockerignore`d; `.env.example` ships empty keys and obvious `*_dev_only`
  placeholders marked "local dev only". A repository test scans every text file for credential-shaped
  strings and fails on stray `.env` files.
- Secrets are wrapped in `Secret`: `repr`, `str`, f-strings and `format()` show `***`; the value is
  reachable only through `.reveal()`; comparison is constant-time; instances are unhashable.
  **Limit:** this stops accidental leaks of the object. There is no structured logging or log-redaction
  filter yet, so a developer who calls `.reveal()` and logs the result leaks it.
- Validation reports every problem at once and never echoes a value.
- Production is stricter than development: `SESSION_SECRET` (at least 32 characters) is required,
  `DATA_MODE=seed` is refused unless `ALLOW_SEED_DATA=true`, and `DATA_MODE=database` currently fails
  at start-up with `AdapterNotImplementedError` because the adapters are not written.
- Pagination-cursor keys are derived from `SESSION_SECRET` (HMAC with a purpose label), and rotation is
  supported through `SESSION_SECRET_PREVIOUS`. Without a secret (development only) a random per-process
  key is used, so cursors die on restart.
- `NEXT_PUBLIC_*` variables are shipped to browsers; a test refuses secret-looking names there.
- Provider keys (GitHub, Gemini) are server-side only by design; no browser code reads them.

### HTTP application

- **Strict input validation.** Unknown query parameters, repeated scalars, non-ASCII digits, control
  characters, and out-of-range values are rejected with every problem reported at once. Body limit 64 KiB
  (`413`), query string 2,048 bytes, search text 100 characters.
- **Uniform error envelope** with a request id; messages never contain SQL, stack traces, upstream
  response bodies or tokens. A client-supplied `X-Request-ID` is honoured only if it matches
  `[A-Za-z0-9._-]{8,64}` (header-injection safe).
- **Security headers on every response, errors included:** `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`,
  `Cross-Origin-Resource-Policy: same-site`, `Strict-Transport-Security`, `X-Frame-Options: DENY`.
  Errors, health probes and authenticated responses are `no-store`.
- **Rate limiting** per client address and bucket (`health`, `read`, `search`, `graph`, `github`, `auth`,
  `ai`, `write`) with standard `RateLimit-*` headers and `Retry-After`. `X-Forwarded-For` is ignored unless
  `TRUSTED_PROXY_HOPS` says how many trusted proxies sit in front; the address used is the one the
  outermost trusted proxy saw, and fewer entries than hops falls back to the direct peer.
  **Limit:** the limiter is in-process. With several API instances the counters are not shared; the
  Redis-backed implementation is not written. Fixed windows allow up to twice the limit across a
  boundary.
- **Signed cursors.** HMAC-SHA256 over offset, expiry and a fingerprint of the operation and filters, so a
  client cannot jump to an arbitrary offset, replay a cursor on another query, or use one forever (24 hours).
- **Bounded responses.** No endpoint returns an unbounded list; neighbourhoods are capped at 300 nodes and
  1,000 relationships and say `truncated: true`.
- **Fail closed.** A route marked as needing a session but accidentally marked implemented answers `401`
  to everyone.

### Outbound requests and untrusted input

- **User-supplied URLs are never fetched.** The only accepted form is a GitHub repository reference parsed
  into `owner/repo`: github.com and www.github.com only; no credentials (except the `git` user on ssh), ports,
  percent-encoding, backslashes, non-ASCII or control characters; owner names follow GitHub's username rules.
  The parser makes no request at all. The repository route then builds `https://api.github.com/repos/{owner}/{repo}`
  from the validated owner and name alone; nothing from the pasted URL (host, path, query) reaches the request.
- **GitHub transport** (`github/transport.py`): base URL is fixed to `https://api.github.com`; request
  paths must be absolute API paths (no `//`, no `..`); **redirects are never followed** (a redirect
  could leave the host with the token attached); the token goes only in the `Authorization` header and is
  hidden from `repr`; responses are capped at 5 MiB; TLS uses the default verifying context. A token must be
  printable ASCII, 1 to 255 characters (so it cannot smuggle a header line), and the error for a bad one never
  repeats it. A response shorter than its declared length is a network failure, not an answer: it could
  otherwise be cached under its ETag and served again on every 304. A loopback base URL is possible only when
  code passes `allow_local_test_server=True`, which the environment cannot do.
- **Repository reading** (`github/provider.py`): a repository is used only if GitHub says it is public, so a
  token that can see private repositories cannot make the route read them (a private repository is "not found",
  cached copies included); the canonical name GitHub returns must match the name asked for; a renamed
  repository is reported and not followed; GitHub's error text, a gateway's, and any credential are never
  copied into a response (distinct fixed messages, the detail goes to the log with the request id); the request
  budget refuses to spend the last of the quota; the response says when it was read (`fetched_at`) and how
  (`cache_status`), so an old copy is never presented as new.
- **Contributors and the latest release** (the opt-in `/activity` read) keep to the same rules and add some:
  the repository is read first, so the public-only guard applies before the contributor list is asked; only a
  login, a type and a commit count are read from each contributor, so email addresses (which GitHub sends for
  `Anonymous` entries) and avatar addresses are never kept, and the page loads no image from GitHub; a profile
  link is built by VYRON from a login that passes GitHub's naming rules, never copied; a release link is used
  only when it is a `github.com` page under that repository's `/releases/`, else it falls back to the
  repository's releases page; release notes are not read; tags and names are refused or cleaned of control
  characters; the page draws everything as text. Written against GitHub's documentation, not against real
  answers: a field GitHub sends that this list does not anticipate is ignored, and one of the wrong type is an
  error.
- **Commit activity** (the opt-in `/commit-activity` read) keeps to the same rules and adds: the repository is read
  first, so the public-only guard applies before the statistics are asked; only the week, seven day counts and a total
  are read, each checked as a whole number, so nothing in the payload can become markup, a link or an address; the
  route sends `Cache-Control: no-store`, so a "still computing" answer is never reused by a browser or proxy; the
  server never waits for GitHub's 202, and the page never polls, so it cannot spend the quota by itself. Written against two real answers (2026-11-04), not more.
- **Token and network.** A `GITHUB_TOKEN` is refused by the development server on any address but loopback
  unless `--allow-token-on-network` is passed (which prints a warning), because anyone who can reach the port
  could spend the quota. `scripts/live_github_check.py` sends a token only to `api.github.com` and never to the
  loopback stand-in; it prints and saves no token (a test pins this).
- **Host-header allow-list.** A development server bound to loopback answers only to `localhost`, `127.0.0.1`
  and `::1` and refuses other Host values with 403, so a web page on another site cannot reach it through DNS
  rebinding (a script on `evil.example` resolving to 127.0.0.1) and make it spend the GitHub quota or read seed
  data. A server bound to another address has no such list; naming the allowed hosts for a deployment behind a
  proxy is not designed.
- **Link-header pagination URLs** come from a response, so they are untrusted: accepted only on the same
  host over https with no credentials or port.
- **Error text from GitHub** is stripped of control characters and truncated to 200 characters before it
  can reach a message.
- **Source URLs** stored as evidence must be public `http(s)` URLs: no credentials, no `localhost`,
  `.local`/`.internal` names, single-label hosts, or non-global IP addresses.
- **Model output is untrusted.** It is parsed as JSON only (duplicate keys, `NaN`/`Infinity`, over-deep
  nesting and oversize output are refused), every text field is normalised and length-limited, and a
  claim is `sourced` only if it cites evidence ids the run was actually shown. See [AI.md](AI.md).

### Containers and repository

- Compose binds every port to `127.0.0.1`; the application services run read-only with all capabilities
  dropped, `no-new-privileges`, a PID limit and a memory limit, and as a non-root numeric uid; no service
  mounts the Docker socket or uses host networking. All of this is asserted against the resolved compose
  model, but **no container has been started**.
- Dockerfile rules (`infrastructure/docker/dockerlint.py`): pinned base images, non-root user, exec-form
  commands, no secrets in `ENV` or `ARG`, no remote `ADD`, no pipe-to-shell, no `sudo`, health check present.
- `.dockerignore` keeps `.env*`, keys, certificates and credential files (at any depth) out of the image
  build context.

### Browser prototype (`apps/web/prototype`, development only)

- **Policy.** The page is served with `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'
  data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self';
  frame-ancestors 'none'`, plus `nosniff`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy:
  same-origin` and `Cache-Control: no-store`. There is no `unsafe-inline` or `unsafe-eval` because the page has no
  inline script, style or event-handler attribute; a test fails if one appears. Chromium reported no policy
  violation or console error across the 386-check run (the repository and dependencies pages' failure cases leave only the
  browser's own "Failed to load resource" status lines, which a check allows for and nothing else). No nonces are
  needed, so the nonce design under "Designed but not built" remains for the Next.js app.
- **No markup from strings.** Every element is built from text by `dom.ts`, which refuses `style` and `on*`
  attributes; text from the API (names, summaries, notices) is only ever assigned as text. A test scans the
  sources for `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write` and `eval`.
- **Static server.** Pages and the stylesheet are served from an exact-match table, so there is no path to
  traverse. Scripts are served only if they end in `.js`, contain no backslash or NUL, and resolve (after symlinks)
  inside the build directory. Tests cover `..` segments, backslashes, percent-encoded dots, a doubled slash that would make an
  absolute path, a NUL byte, and a symlink pointing outside the build directory. Deliberately removing the containment check made a test fail.
- **One origin.** The API and the page share an origin, so no CORS headers are sent and none are needed.
- **Inputs.** The user input is the search text, the address (`?focus=`, `?view=`), on the repository page the
  box and `?repo=`, and on the dependencies page the registry picker, the box and `?package=`. Search goes to the API, which validates it; the map's address is parsed strictly and anything
  unusable is reported and dropped. The repository box and `?repo=` go to the API's parser, which rejects what
  is not a repository reference; whatever was typed is shown back only as text (a script tag in `?repo=` renders
  as characters, and a Chromium check pins it). Links on the page are `http(s)` only. The page itself never
  contacts GitHub, PyPI or npm: it has no such host in its code (a test pins that) and its policy is `connect-src 'self'`.
  The dependencies page puts a registry's link in an `<a>` only if it starts with `http://` or `https://`, otherwise it
  is text. The API behind it refuses a package name that is not one a registry could issue before any request is made,
  reads only the one https registry host (or, for a local test, a loopback address given as an explicit argument and never
  from the environment), follows no redirect, caps the body at 16 MiB, and never repeats a registry's own words in an
  error ([REGISTRIES.md](REGISTRIES.md)).
- **Limits.** `serve.py` binds to `127.0.0.1` by default; passing `--host` to bind elsewhere would expose a
  development server with seed data and no authentication, and is not supported. `--no-rate-limit` removes the
  API's limiter and exists only for the automated browser run. No cookies, no storage, no sign-in, nothing is
  saved. **UNVERIFIED:** enforcement of this policy in Firefox and Safari; TLS, production headers and caching.

## Designed but not built

| Area | Design | Status |
| --- | --- | --- |
| Accounts and sessions | Argon2id password hashes, opaque session cookie stored only as a SHA-256 hash, lockout columns | tables written (UNVERIFIED), routes answer 501, **no hashing code** |
| Cookies and CSRF | `HttpOnly`, `Secure`, `SameSite=Lax`; `Origin` check plus a CSRF token on state-changing routes | not built |
| Browser policy | strict CSP with per-request nonces, `Permissions-Policy`, no `dangerouslySetInnerHTML`, allow-list Markdown renderer | specified in `docs/FRONTEND.md`; the app is not written (the prototype ships a static policy without nonces, see above) |
| Distributed rate limits | Redis-backed limiter; per-account limits on credential routes | not written |
| Log redaction | structured logs with a redaction filter and request ids | not written |
| Dependency auditing | `pip-audit` and `npm audit` in CI | not configured; the registries are unreachable here |
| Outbound allow-list | any future fetch other than GitHub needs a host allow-list and resolved-address checks | no such fetch exists |
| AI abuse controls | per-user quotas, prompt-injection testing against real retrieved text | not built |

## Not addressed

TLS termination and certificate handling; secret rotation procedure for GitHub and Gemini keys; backup
encryption; multi-tenant isolation; abuse of the GitHub quota by many users (the request budget and a per-client
limit of 30 a minute now sit in front of GitHub, but both are in-process, so several instances would each have their own, and a deployment
with one shared token has no per-user fairness); data-subject deletion requests;
privacy policy and retention; GitHub Actions are tagged by version, not pinned by commit hash.

## Threats to test once the application runs

1. SSRF through every route that accepts a URL or repository reference (`/github/parse` and the repository
   route exist today; the second builds its request from a validated owner and name only, which has been tested
   against a stand-in but not against GitHub's own behaviour on unusual names).
2. Prompt injection through README text or retrieved web text, checked against the real Gemini adapter.
3. Cursor tampering and replay across operations, users and restarts.
4. Rate-limit evasion through `X-Forwarded-For` once a proxy is in front.
5. Session fixation, credential stuffing and account enumeration, once authentication exists.
6. Stored XSS through entity names and AI output in the real web app.

## Reporting

There is no public security contact yet. Until one exists, report privately to the repository owner rather
than in a public issue, and include the request id from any error response.
