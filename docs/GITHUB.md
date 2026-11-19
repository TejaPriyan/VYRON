# GitHub integration

Last updated: 2026-11-02 (the contributors and latest-release read seen once against the real GitHub; the rest as of 2026-11-01).

Status: **the live path is written, every layer of it has been run against a local stand-in for GitHub, and
the project owner reports two passing runs against the real GitHub, both unauthenticated and both on
`pallets/flask`: 12 of 12 checks on 2026-10-26, and 27 of 27 on 2026-11-02, which also covered the contributors
and latest-release read.** I did not see either run, and together they cover far less than the whole path: the
authenticated path, a real rate limit, a license-less repository, a private repository and the other error
shapes are still UNVERIFIED against GitHub (see "What the real run covered").
The build environment's GitHub gateway answers 403 "No linked GitHub account", so no real request was possible
there. See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

What exists is **repository reading**: one public repository, read from the GitHub REST API when asked, and,
as a separate opt-in read added on 2026-11-01, its top contributors and its latest release. There is no
ingestion job, no stored GitHub data, and nothing that crawls. A third opt-in read, **a year of weekly commit
activity**, was added on 2026-11-05 from two real answers captured on 2026-11-04 (see "Commit activity (opt-in)").
Dependency manifests and the Software Genome built on them are not started (see "Not built"). **The contributors and latest-release read
has been seen against the real GitHub once** (2026-11-02, `pallets/flask`, reported by the owner, capture kept in
`apps/api/tests/fixtures/github_real/pallets_flask_activity`): both answers parsed and passed every check. Its
other answers (a repository with no contributors, a 403 for a huge history, no release) are still only written
from GitHub's documentation and UNVERIFIED against GitHub (see "What the real run covered").

Field names and rate-limit behaviour were read from GitHub's REST documentation on 2026-10-05 (the "Get a
repository" response, rate limits, best practices, conditional requests and Link-header pagination). The
fixtures in `apps/api/tests` and everything the stand-in serves were written from those documents, **not
captured from real responses**, so a real response may differ in ways no test here can show. That is what
the capture option of the check script is for.

## Reading a repository

| | |
| --- | --- |
| Route | `GET /api/v1/github/repositories/{owner}/{repo}`, response schema `RepositoryAnalysis` |
| Quota | `GET /api/v1/github/rate-limit`, response schema `RateLimitStatus` (as of the last response from GitHub; a live read, which GitHub does not count, when none is known) |
| Page | `/repository.html` in the browser prototype ([PROTOTYPE.md](PROTOTYPE.md)); the address `?repo=owner/name` is its only state |
| Cost | two GitHub requests per repository (`/repos/{o}/{r}` and its `/languages`); a second look within a minute costs none |
| Limit | 30 requests a minute per client to these routes (bucket `github`), in addition to GitHub's own limit |

Everything in the answer is what GitHub reported, with the evidence for it: `source_urls` lists the exact API
URLs read and `provenance` is `sourced`. VYRON adds no figure of its own, stores nothing, and does not
describe stars or languages as quality, importance or effort.

### How old a figure is: `cache_status` and `fetched_at`

The server keeps each GitHub response in memory (ETag and Last-Modified included) and answers one of three ways:

| `cache_status` | Meaning |
| --- | --- |
| `fresh` | Read from GitHub in this request, or confirmed within the last 60 seconds (no request was made) |
| `revalidated` | An older copy was checked with `If-None-Match` or `If-Modified-Since` and GitHub answered 304: nothing had changed (without a token, a 304 costs one request like any other: measured, see "Does a 304 cost a request?") |
| `stale` | GitHub could not be reached or was rate limiting, so an older copy is shown; it is kept at most 24 hours and `fetched_at` is when it was really read |

When a result has several parts, the status claims only what is true of all of them: `stale` if any part is an
older copy; `revalidated` only if every part came from a stored copy and at least one was confirmed by a 304;
otherwise `fresh`. So a repository confirmed by a 304 next to a contributor list read for the first time is
`fresh`, never "nothing had changed", and so is a changed repository next to an unchanged language list. (Until
2026-11-02 the rule was "the least current part", which labelled those mixes "Confirmed"; the first real use of
the contributors section showed it. The earlier real observation of `Live` then `Confirmed`, where both parts were
confirmed, is unaffected.) `fetched_at` is the older of the reads, so the whole answer is never presented as newer
than its parts.
The page says "Live", "Confirmed" or "Stale", how long ago, and keeps the age moving while it is open.
The cache is per process: a restart empties it and two server instances do not share it. A Redis cache is
not written.

### What it refuses to do

- **Private repositories are "not found".** A repository is analysed only if GitHub says `private: false`,
  so a server token that can see private repositories can never turn this route into a way to read them.
  Cached copies are re-checked the same way.
- **The name must match.** The canonical `full_name` GitHub returns must equal the name asked for
  (case-insensitively); anything else is an error, not a quiet substitution.
- **Redirects are never followed.** A renamed repository answers 301; VYRON says it was renamed or moved
  and does not follow it.

### Failures, and what each one says

| What happened | HTTP status and code | What the page tells the person |
| --- | --- | --- |
| No such public repository, private, or moved | 404 `not_found` (moved has its own message) | No public repository called X, or X has moved |
| Not a repository name or link | 422 `validation_error` or a parse failure | Use owner/name or paste a link |
| GitHub's quota is used up, or it asked VYRON to wait | 503 `upstream_rate_limited` with `Retry-After` | The limit was reached, how long to wait, and that a token raises 60 to 5,000 an hour |
| GitHub refused, errored, was unreachable, or sent something unusable | 502 `upstream_error` (distinct safe messages) | GitHub did not give a usable answer, with a request id for a bug report, and Try again |
| GitHub access is switched off (`--no-github`) | 503 `service_unavailable` | GitHub access is switched off, and how to switch it on |

Upstream text is never repeated in a response: not GitHub's message, not a gateway's, not a credential. The
detail goes to the server log as a warning with the request id.

## Contributors and the latest release (opt-in)

| | |
| --- | --- |
| Route | `GET /api/v1/github/repositories/{owner}/{repo}/activity`, response schema `RepositoryActivity` |
| Page | the section "Contributors and latest release" at the end of `/repository.html`; nothing is asked until the button is pressed |
| Cost | two GitHub requests (`/contributors?per_page=10` and `/releases/latest`) after the repository read. The repository read is free when its copy is under a minute old (the usual case, right after the basic analysis) and one request otherwise, so two, or three |
| Why it is separate | the basic analysis costs two requests and, without a token, GitHub allows 60 an hour. Making every analysis cost four would halve the number of repositories a person can look at, for figures most do not need |
| Limit | the same 30 a minute per client (bucket `github`) |

What it returns, and what it deliberately does not:

- **Contributors:** up to ten accounts, each with `login`, `contributions` (the commits GitHub attributes to the
  account), `is_bot` (GitHub's `type` is `Bot`) and `profile_url`. The link is **built by VYRON** as
  `https://github.com/<login>`, and only for a non-bot whose login passes GitHub's naming rules; it is never
  copied from the payload. Avatars, email addresses and every other field are ignored, so the page loads
  nothing from GitHub's image servers. An `Anonymous` entry (GitHub only sends those when asked with `anon=1`,
  which VYRON never does) is dropped, because it carries an email address. The list is ordered by
  `contributions` (GitHub's own order is kept among equals) and cut to ten.
- **`contributors_status`** is one of three things GitHub said, so an empty list is never ambiguous: `listed`;
  `none` (GitHub answered 204 No Content, or sent an empty list: it has nothing to list); `declined` (GitHub
  answered a plain 403, which its documentation gives for repositories whose history or contributor list is too
  large; seen once, for `torvalds/linux`, with no `Retry-After` and quota left). A 403 that is a rate limit is
  **not** `declined`: it is the usual rate-limit error, with its wait.
- **Latest release:** `tag_name`, `name`, `published_at`, `prerelease` and `html_url`. Release notes, assets
  and the author are not read. It is `null` when GitHub answers 404 to "latest release", which GitHub gives when
  there is no published release. GitHub's own definition is repeated on the page: the most recent release that
  is not a draft or a pre-release, ordered by `created_at` (GitHub's documentation, read on 2026-11-02 through a
  page-fetch tool that summarises, says that is the date of the commit used for the release, not of drafting or
  publishing), **not necessarily the highest version number**. My first wording said "newest published
  release"; that was mine, not GitHub's, and it named the wrong ordering. The real answer for `pallets/flask`
  has `name` as the empty string, which VYRON reports as no name. A draft in that answer
  is refused as malformed, because "latest" is never a draft. The link is GitHub's `html_url` only when it is a
  `github.com` page under this repository's `/releases/`; otherwise it falls back to the repository's releases
  page, so a link can never lead anywhere else.
- **Order of reads:** the repository first, for the same reasons as the basic analysis (a private repository is
  "not found" before anything else is asked; GitHub's spelling of the name is checked), then the contributor
  list, then the release. A rate limit on the list stops the read before the release is asked.
- **Caching:** only a 200 is stored. A 204, a 403 or a 404 is asked again the next time, which costs GitHub
  requests, so a repeat read of a repository with no release or no contributors is not free. (This also fixed a
  latent flaw: the cache used to store any answer that carried a validator, and a stored 204 would have been
  served back later as an empty 200.)
- **`fetched_at`** is the oldest of the reads, and `cache_status` follows the rule above, the same as the basic
  analysis; a 403 or a 404 that is reported counts as read just now, because it is never stored.

Limits that are not VYRON's to fix, and that I have not re-read in GitHub's documentation today: as I recall it,
GitHub links only the first 500 author email addresses of a repository to accounts, so for a repository with a very
long history the counts can leave people out. I tried to confirm that on 2026-11-02; the fetch returned only part
of the long documentation page and the note was not in it, so it is still recall. A commit count says nothing about
the quality or importance of the work, and the page says so.

## Commit activity (opt-in)

| | |
| --- | --- |
| Route | `GET /api/v1/github/repositories/{owner}/{repo}/commit-activity`, response schema `CommitActivity` |
| Page | the section "Commit activity" after "Contributors and latest release"; nothing is asked until the button is pressed |
| Cost | one GitHub request (`/stats/commit_activity`) after the repository read, which is free when its copy is under a minute old. **A 202 costs a request too** (seen: the quota fell by one for each of three 202s) |
| Limit | the same 30 a minute per client (bucket `github`) |
| Caching | only a 200 is stored (for a minute, then confirmed with the ETag). The route sends `Cache-Control: no-store`, so a "still computing" answer is never reused by a browser or a proxy |

What GitHub really did (2026-11-04, unauthenticated, reported by the owner; captures kept in
`apps/api/tests/fixtures/github_real/commit_activity_flask_linux`, and I read them):

- **`pallets/flask` was not ready on the first ask.** GitHub answered **202** with the body `{}` (a JSON content type,
  no `ETag`, no `Retry-After`) at 0.7, 5.8 and 11.0 seconds, and **200** on the fourth ask at 16.1 seconds. That is one
  repository, once; it says GitHub can take at least sixteen seconds, not how long it usually takes.
- **`torvalds/linux` answered 200 on the first ask** (at 1.8 seconds), presumably already computed. Its 52 weeks add up
  to 76,597 commits. That does not show a huge history is refused here, as it is for contributors; it shows this
  endpoint answered for the largest repository tried.
- The 200 is a list of exactly **52** objects `{"days": [7 counts, Sunday first], "total", "week"}`. `week` is a Unix
  time, always a Sunday at 00:00 UTC, exactly 604,800 seconds after the one before; `total` always equalled the sum of
  `days`. It carries `cache-control: public, max-age=60, s-maxage=60` and a weak `ETag`, and no `Last-Modified`.
- The **last week is the one in progress**: `linux` was captured on a Wednesday and its last week has counts for
  Sunday to Tuesday only. The page says so, and decides which bar is unfinished from the time GitHub was asked
  (`fetched_at`), never from the reader's clock.

What VYRON does with that:

- **It never waits for GitHub.** A 202 is returned at once as `status: computing` with no weeks, never retried inside
  the request, never kept. The reader asks again with a button. The page does not poll by itself, because every
  ask, a 202 included, spends one of the 60 requests an hour an unauthenticated server has.
- **`status`** is one of GitHub's three answers, so an empty `weeks` is never ambiguous: `ready`; `computing` (202);
  `none` (204, or an empty list). Other failures propagate as for every other read (a 404 is not found, a plain 403
  is the forbidden error, a rate limit is the rate-limit error and is not slept through).
- **The shape is checked, not assumed.** The normaliser refuses a list that is not a list, more than 60 weeks, a week
  that is not an object, `days` that is not seven non-negative whole numbers, a `total` that is not their sum, a
  `week` that is not a whole Unix time falling on a Sunday at midnight UTC, and weeks that are not consecutive and
  oldest first. Each is reported as "GitHub did not give a usable answer"; nothing is repaired. **GitHub's own
  documentation example has week times five hours past midnight UTC; the two real answers did not, and VYRON would
  refuse such a payload.** That strictness is a judgement from two repositories: if GitHub sends other offsets, the
  page will say it cannot use the answer rather than label days wrongly.
- **Only the weekly counts are GitHub's.** The page also shows a total for the year, the busiest week and the number
  of weeks with no commits; those are plain sums VYRON makes from GitHub's counts and the page says so beside them.
  Bars are scaled to the busiest week, a week with commits is never drawn as zero, and the same figures are
  available as a table.
- **`cache_status` and `fetched_at`** follow the same rule as the other reads. A 202 or a 204 counts as read just now,
  because it is never stored, so it can never be called "confirmed unchanged".
- **A year GitHub is recomputing** (a 202 after a stored copy is a minute old) is shown as `computing`, not as the
  old copy. A stored copy is shown, labelled stale, only when GitHub cannot be reached.

Not covered, so UNVERIFIED against GitHub: the 204 and an empty list; a rate limit during this read; a token; whether
GitHub's 304 for this endpoint behaves as for the others (the weak `ETag` is sent back as written, and the tests show it
is; no real 304 was seen for it); a repository with no commits in the year (GitHub may send 52 zero weeks; the page
would draw no bars, say "0 commits in 52 weeks" and name no busiest week); how often a repository is already computed; a private
repository (refused before this is asked). Two repositories, one machine, one day.

## What exists (`apps/api/src/vyron_api/github`)

| Module | Job | Tests |
| --- | --- | --- |
| `provider.py` | `LiveGitHubProvider`: composes transport, retry, cache, budget and normaliser; `build_live_provider`; `repository_activity` (contributors and latest release) | 32, and 26 in `test_github_activity.py` |
| `url.py` | Parse a pasted link into an owner, a repository, or a classified target; never fetches | 15 |
| `ratelimit.py` | Read `x-ratelimit-*` and `retry-after`; decide whether and how long to wait | 15 |
| `retry.py` | Send one logical request with documented retry; map statuses to typed errors | 25 |
| `pagination.py` | Parse `Link` headers; accept a next-page URL only on the API host | 11 |
| `conditional.py` | ETag / Last-Modified revalidation, fresh-cache hits, stale-on-error, the "as of" time of each read; only a 200 is stored | 23 |
| `budget.py` | Track quota per resource; refuse to start a request below a reserve | (in the retry and provider tests) |
| `normalize.py` | Turn repository, language, rate-limit, contributor and release payloads into typed values; refuse wrong types | 15, and 17 in `test_github_activity.py` |
| `transport.py` | Standard-library `urllib` transport; fixed host; no redirects; size cap; token check; truncation check | 16 |

The interfaces other code depends on are in `providers/github.py` (`GitHubProvider`, `RepoSnapshot`,
`RepoAnalysisResult`, `RepoActivityResult`, `Contributor`, `LatestRelease`, `ContributorsStatus`, `CacheStatus`, the error hierarchy). Counts are the number of test functions in each
module's test file as of 2026-10-25; the stand-in, route and browser tests are listed separately below.

## The token

`GITHUB_TOKEN` is optional and server-side only. Without it GitHub allows 60 requests an hour per address,
which is 30 repositories if none repeats; with a token that has no scopes it allows 5,000. A public-repository
read needs no scope at all.

- It must be printable ASCII with no spaces or line breaks and at most 255 characters; anything else makes
  the development server refuse to start, with a message that does not repeat the value.
- It is sent only to `api.github.com`, only in the `Authorization` header, and is hidden from `repr`, `str`
  and logs. The transport refuses any other host; the one exception is a loopback address, which only tests
  and the stand-in can switch on, never the environment.
- The development server **refuses to start with a token while listening on a non-loopback address**, because
  anyone who can reach that port could spend its quota. `--allow-token-on-network` overrides it (for a
  container's published port, say) and prints a warning in the start-up banner.
- Whether a token works is unknown until a real call. The check script's quota read is the first test of it.
- `--no-github` starts the server with GitHub off. The settings-driven composition used for deployment
  (`build_application(settings)`) has no such switch yet: it always builds the live provider. Known gap.

## Checking it against the real GitHub

Run this on a machine that can open https://api.github.com. It needs nothing installed (run once on 2026-10-26,
see below):

```bash
python3 scripts/live_github_check.py                       # 3 of GitHub's 60 requests an hour
python3 scripts/live_github_check.py --capture captures    # also saves what GitHub sent (3 more)
python3 scripts/live_github_check.py --activity --capture captures_activity   # also the contributors / latest-release read (about 10 in all)
python3 scripts/live_github_check.py --flow                # replay the page's own sequence: about 11 requests and two real waits of 65 s
python3 scripts/live_github_check.py --capture captures_stats --stats --repo pallets/flask --repo torvalds/linux   # commit activity, saved as sent (up to 22 requests)
GITHUB_TOKEN=... python3 scripts/live_github_check.py      # token read from the environment, never printed
python3 scripts/github_304_probe.py                        # does a 304 cost a request? about 3 of the 60
```

It drives VYRON's own application in-process, so it exercises the same provider, cache, validation and error
mapping the server uses, and checks the answers against the API's own response schemas: the quota route
answers; a real repository (`pallets/flask` unless `--repo` names others) answers 200 in the `RepositoryAnalysis`
schema, labelled `sourced`, as the repository asked for, first read `fresh`, evidence URLs on `api.github.com`,
`fetched_at` within five minutes of now; a second request is served from the cache with the same `fetched_at`
and uses none of GitHub's quota; a name that does not exist answers 404 `not_found`.

`--activity` adds, per repository, a check of the opt-in read: the route answers 200 in the `RepositoryActivity`
schema, labelled `sourced`, as the repository asked for, with three `api.github.com` evidence URLs; the
contributor list agrees with its status and is ordered by contributions; every link is `github.com/<login>`
and bots have none; the release link is on the repository's releases page and the release is not dated in the
future; `fetched_at` is recent; the read cost two or three of GitHub's requests (the figure this document
states, so a different number is a documentation error to fix); and, only when GitHub answered 200 to both
parts, that a second read is served from the cache and uses no quota. Any of GitHub's valid answers passes (a
list, a 204, a 403 for a huge history, a release or none), so run it on a repository without releases too, for
example `--repo torvalds/linux`, which I believe has none but have not checked. It is an experiment, and may also
show `declined`. The capture then holds those real answers: the first real look at a 204, a 403 or a 404 for
these endpoints, if GitHub gives one. File names replace `?` and `=` so they are legal on Windows.

`--flow` replays what the repository page does, for the first `--repo`, on a **fresh cache** and with a recorder on
the transport: analyse the repository; wait 65 seconds (VYRON treats a copy under 60 as current and does not ask
GitHub); load the contributors and release; wait again; load them again. For every step it lists each request VYRON
sent (asked "has this changed?" or asked outright, GitHub's status, the quota GitHub said was left) and checks
that the freshness label VYRON gave is the one those answers justify. The expectation is worked out from the
answers alone and independently of the provider: "revalidated" (the page's "Confirmed") only if every request
was answered 304, otherwise "fresh" (the page's "Live"). It also checks that the second step is "fresh" because the
contributors and release were first reads, that the repository copy and later every stored copy were asked about
with conditional requests rather than downloaded again, and it prints which endpoints answered 304 and how much
quota went. It exists because a screenshot cannot say whether a "Confirmed" came from a defect or from every part
really being unchanged. Against GitHub the waits are real; `--simulate-wait` (a stand-in only, refused otherwise)
moves VYRON's clock on instead, which is how its own tests run. The flow costs about eight requests (2 + 3 + 3), and
a run with only `--flow` about eleven with the checks that always run.

`--capture DIR --stats` saves the answers for the **last year of commit activity**
(`GET /repos/{owner}/{repo}/stats/commit_activity`) of each `--repo`, and nothing else new. It was run once against the
real GitHub (2026-11-04, reported by the owner; the answers are kept in
`apps/api/tests/fixtures/github_real/commit_activity_flask_linux`), and VYRON's commit-activity read was then written
from what came back (see "Commit activity (opt-in)"). Before that run, GitHub's documentation (read on 2026-11-04
through a summarising fetch tool, from the GitHub AE edition of the page, which may differ from github.com) says
the statistics are computed in the background and cached by the SHA of the default branch; that a first request
may answer **202** while "a background job is also fired"; that one should "allow the job a short time to complete,
and then submit the request again", when a **200** carries the statistics; that 204 is "a header with no content";
and that the `days` array is "a group of commits per day, starting on Sunday". The page does not say how many weeks
come back, how long the job takes, whether a 202 carries a body or a `Retry-After`, how long results stay cached,
or whether this endpoint has a size limit (its sibling `code_frequency` refuses repositories with 10,000 or more
commits with a 422). The capture therefore asks, waits `--stats-wait` seconds (default 5, 0 to 60) and asks again,
up to `--stats-attempts` times (default 6, 1 to 10), stopping at the first answer that is not 202, and saves **every
attempt** as `<path>__attempt-N.json` with the usual record plus `attempt` and `seconds_since_first_attempt`. It
asks GitHub outright (never through VYRON's cache or with validators) and each attempt costs one request. If GitHub
is still saying 202 after the last attempt, that is saved and said, and is itself a finding. The header allow-list
gained `cache-control`; it holds nothing that could identify the caller.

Exit status: 0 every check passed, 1 a check failed, 2 it could not run (GitHub unreachable, bad arguments),
with a hint. **Only a clean pass against the real GitHub prints VERIFIED**, and only for what it checked: the
repositories named, on that date, from that machine. A run against a local stand-in prints that it is fixture
data and that the live path stays UNVERIFIED.

`--capture DIR` saves each response GitHub sent (the quota, the repository, its languages, the not-found
answer) as one JSON file with its status and an allow-listed set of headers, plus a `MANIFEST.json` saying it
is real GitHub data, when it was taken and whether a token was used (never the token). Those files are the real
fixtures the current ones were written without; commit them, then compare them with the fixtures in
`apps/api/tests/github_fakes.py` and fix whichever is wrong.

### What the real run covered

On 2026-10-26 the project owner ran `python scripts/live_github_check.py --capture captures` on their own machine
(Python 3.12.14, no `GITHUB_TOKEN`, no proxy in the way), through Antigravity, and relayed the output. **That is
a report; I did not see it run.** It printed 12 PASS lines and `RESULT: PASS, 12 checks against api.github.com`,
exit code 0, for `pallets/flask` (75,088 stars, 4 languages, license BSD-3-Clause) and a repository that does not
exist (404 `not_found`), and saved four responses and a manifest. The same person opened `/repository.html`, analysed
`pallets/flask` and reported the figures, the evidence links and the language table, "Sourced" and a "Confirmed"
badge, and no console errors.

| Reported as verified against the real GitHub | Still UNVERIFIED against it |
| --- | --- |
| Unauthenticated access to `/rate_limit`, `/repos/{o}/{r}` and `/repos/{o}/{r}/languages` | A request with `GITHUB_TOKEN` (the token path, the 5,000 an hour limit) |
| The repository and quota responses pass VYRON's own response schemas for one large public repository | Forks, repositories whose `license` is null, or without a description, or with very many languages (an archived one and a renamed one, a real 301, were captured on 2026-10-27: see `fixtures/github_real`) |
| A name that does not exist answers 404 and becomes `not_found` | A private repository, a real 403 or 429 and its headers, a 5xx, GitHub being unreachable |
| A second request inside a minute is served from VYRON's cache with the same `fetched_at` and costs no quota | The stale path, and long-running behaviour of the cache |
| A real 304 revalidation (`Live`, then `Confirmed` after the 60-second window) and what it costs without a token: one request each | Whether a 304 is free with a token (not run) |
| The page shows the figures, the evidence links and no console errors in the reporter's browser | Any browser but the reporter's (which one was not named), and non-ASCII descriptions |

### The contributors and latest release, seen once (2026-11-02)

The project owner ran `python scripts/live_github_check.py --activity --capture captures_activity` on their own
machine (Windows, no `GITHUB_TOKEN`), through Antigravity, and relayed the output; the capture was taken at
03:46 UTC. **That is a report; I did not see it run.** It printed 27 PASS lines and `RESULT: PASS, 27 checks`,
exit code 0, for `pallets/flask` and for a repository that does not exist (404 `not_found` from both routes), and
saved six responses. The capture is committed as `apps/api/tests/fixtures/github_real/pallets_flask_activity` and
exercised by 7 tests in `test_github_real_captures.py`. I read it myself and ran it through the provider and the
route.

| Seen in the real answers | What VYRON does with it |
| --- | --- |
| `/contributors?per_page=10`: 200, ten entries already in descending order of `contributions` (davidism 1,865; mitsuhiko 1,189; untitaker 274 ...), two of them bots (`dependabot-preview[bot]`, `dependabot[bot]`) whose `html_url` is under `github.com/apps/` | The list is read as it stands; bots get no link; each user's link is built from the login. The route answers in its schema |
| The same answer carries a `Link` header with `rel="next"` and a last page of 40, a weak `ETag` and a `Last-Modified` | Only the first page is asked for, as designed. Both validators are sent back exactly as written (tested against the captured headers) |
| Entries carry fields VYRON does not read (`user_view_type`, `gravatar_id`, avatar and API URLs); no entry has an email address | Nothing extra reaches the response; a test asserts that no avatar address is in it |
| `/releases/latest`: 200, tag `3.1.3`, published 2026-02-19T05:01:30Z, not a pre-release, with a weak `ETag`, a `Last-Modified` and a field `immutable` | Read as designed; the release link is GitHub's own `html_url`, which passes VYRON's check |
| The release's `name` is the empty string, not null and not text | Reported as no name (two separate guards do that, and a test says it) |
| `used` in the response headers went 7, 8, 9, 10 over the repository, languages, contributors and release reads | One request each, so the opt-in read costs two (three when the repository copy is over a minute old), as documented |

The hand-written fixtures differed from this in the ways listed in `github_fakes.py` (weak ETags, validators the
fixtures lacked, the `Link` header, the extra fields, the empty release name). None changed VYRON's behaviour.
The one correction this work led to was in wording, not code: see "latest release" above.

What this does **not** show: a repository with no contributors (204 or an empty list), a pre-release or a draft, a
rate limit in the middle of this read, a token, or GitHub's own 304 answer to a conditional request for these two
endpoints (only that the validators are sent back correctly). One repository, one day, one machine.

#### The same day: `torvalds/linux`, and the page against real data

A second run the same day, `--activity --capture captures_linux --repo torvalds/linux` (reported by the owner;
exit 0, 22 PASS lines, about 8 requests; the capture is kept as
`apps/api/tests/fixtures/github_real/torvalds_linux_huge` and exercised by 7 tests), is the first look at the two
answers that are not a healthy list. I have read the capture:

| Seen from GitHub | What VYRON does with it |
| --- | --- |
| `/contributors?per_page=10` answers **403** with `"The history or contributor list is too large to list contributors for this repository via the API."` (and a `documentation_url`). There is **no** `Retry-After`, and the quota was not used up (51 of 60 left) | It is not a rate limit, so it is not waited for or retried; the answer is `contributors_status: "declined"`. GitHub's sentence is never repeated in VYRON's response (a test asserts that) |
| `/releases/latest` answers **404** `"Not Found"` | `latest_release: null` |
| Both cost one request each, like a 200 (`used` went 9, 10 after 7, 8 for the repository and its languages) | The documented cost holds |
| Neither answer is stored by VYRON, so the check skipped its "second read is served from the cache" step | A repeat read asks GitHub again (tested with the real answers) |
| The repository's `license` is `{"key": "other", "name": "Other", "spdx_id": "NOASSERTION", "url": null}`: **a licence GitHub found and could not name.** The repository also has `has_issues: false` and an empty topics list | See below: this exposed a wording problem |

**The licence wording.** The check and the page said `license none declared` / "No license declared" for this
repository. That was wrong twice over: GitHub had found a licence file (it calls it "Other"), and "declared" claims
something about the authors that GitHub does not say. VYRON read `NOASSERTION` as "no licence", which threw the name
away. The fix keeps GitHub's name: `RepositoryAnalysis` has a new nullable `license_name`, and the page says
"License BSD-3-Clause" for an identifier, "License Other (no standard identifier)" for this case, and "No license
identified by GitHub" only when GitHub reports none at all (`license: null`, still never seen from GitHub). It does not
say which licence the kernel is under, because GitHub does not.

The owner's scripted browser (a Chromium-family browser driven from Node, not a person clicking) opened
`/repository.html`, analysed `pallets/flask` and pressed "Load contributors and latest release" with the real
GitHub behind it: the ten contributors with their commit counts, the two bots marked, the release 3.1.3 and its link,
the three evidence URLs and "Check again" appeared, and the console had no messages or exceptions. It also
showed the badge **Confirmed, "nothing had changed since the previous read"** on a first read of the list and the
release. That was wrong: the repository copy had been confirmed by a 304, and the other two parts were new. The
cause was the "least current part" rule; it is fixed (see "How old a figure is") and a test uses the real capture to
show the case. The browser report did not say which browser it was. A screenshot from it reached me: it shows the
page laid out correctly with the real figures (75,164 stars, four languages, the ten contributors, release 3.1.3), and
the old "Confirmed" labels that the fix removed.

A later screenshot (2026-11-03, `pallets/flask`, analyse, wait 70 seconds, press Load, footer at 34 of 60) still
showed "Confirmed". It could not say whether that server was running code from before the fix, or already held the
contributors and the release (three 304s, which would make "Confirmed" correct). `--flow` was written to settle it
and was run against the real GitHub the same day: see "The page's sequence on the real GitHub" below.

The "Confirmed" badge needs a cached copy with an ETag *in the same server process*, and the cache is per
process, so it cannot have come from the check script that ran first. A clean repeat, reported on 2026-10-27
(fresh server, one Analyze on `pallets/flask`, then "Check again" after 70 seconds), showed the badge `Live` and
then `Confirmed`, which is the first real observation of a 304 revalidation. The same repeat showed the footer
quota going from 46 left to 44 left across that "Confirmed" read. A revalidation sends two conditional requests
(the repository and its languages), so a drop of 2 was consistent with each 304 being counted. The probe below
then measured it directly.

### The page's sequence on the real GitHub (2026-11-03)

The owner ran `python scripts/live_github_check.py --flow --repo pallets/flask` (reported; the complete output is kept
in [evidence/](evidence/) and I read it). It printed `RESULT: PASS, 21 checks against api.github.com`, exit code 0.
Each line below is what GitHub answered, not what I expected:

| Step | Requests VYRON sent | GitHub answered | Label VYRON gave |
| --- | --- | --- | --- |
| 1. Analyse | the repository, its languages (both asked outright) | 200, 200 | fresh ("Live") |
| 2. After 65 s, load contributors and release | the repository **asked if it changed**; the contributors and the release asked outright | **304**, 200, 200 | fresh ("Live"), because two parts were first reads |
| 3. After another 65 s, load them again | all three **asked if they had changed** | **304, 304, 304** | revalidated ("Confirmed"), because every part was confirmed unchanged |

What this settles and what it does not:

- **A first read of the contributors and release is "Live", not "Confirmed"**, on the real GitHub, and "Confirmed"
  appears only when all three answers are 304. The rule in `_overall_status` matches what GitHub did.
- **The first real 304 for `/contributors?per_page=10` and `/releases/latest`.** Until now only the repository
  endpoint had been seen answering 304 (the stand-in always did). The validators VYRON sent back (the ETag and, where
  GitHub gave one, `Last-Modified`) were accepted; the log does not say which of the two GitHub went by.
- **A 304 cost one request, as the earlier probe found.** The quota GitHub reported after each of the eight requests
  was 56, 55, 54, 53, 52, 51, 50, 49: one for each, including the four 304s. This is without a token; the token case
  is still unmeasured.
- **The page, on a freshly started server, said "Live"** for the same sequence (screenshot in [evidence/](evidence/);
  44 left is what the 49 left after the check run would give once five requests were spent: two for the analysis and
  three for the read, the 304 included; I am inferring that nothing else used the quota in between).
  The earlier "Confirmed" screenshot therefore did not come from the current code on a fresh server. The likeliest
  cause is that its server was still running the code from before the label fix, which gives exactly that label for
  this sequence; I cannot prove it, because that process is gone.
- **`torvalds/linux`**: the licence tag reads "License Other (no standard identifier)" on the real page
  (screenshot in [evidence/](evidence/)), which closes the licence-wording item.
- Not covered: a repository whose contributor list is empty (204), a rate limit during the read, a token, a
  repository whose `license` is null, and whether a person (rather than a script) driving the browser sees anything
  different. The run used one repository and one machine.

## Does a 304 cost a request?

I had written that a conditional request answered 304 does not count against the primary rate limit, from my
understanding of GitHub's documentation, which I had not re-read. The reported drop from 46 to 44 contradicted
it, and the local stand-in could not arbitrate (it counts every request, so it only reproduces whichever way I
wrote it). `scripts/github_304_probe.py` asks GitHub directly with plain `urllib` and no VYRON code: it reads the
quota, reads one repository, repeats the read twice with `If-None-Match`, reads the quota after each step, and
prints what each cost. About 3 of the 60 requests an hour.

Reported result (run by the project owner through Antigravity on 2026-10-28, Python on Windows, no token, one
run on `pallets/flask`; I did not see it run):

| Step | HTTP | `x-ratelimit-remaining` | `/rate_limit` used |
| --- | --- | --- | --- |
| Before | | | 0 |
| Plain read | 200 (with an ETag) | 59 | 1 |
| Conditional read 1 | 304 | 58 | 2 |
| Conditional read 2 | 304 | 57 | 3 |

Each 304 cost one request, by two independent readings: GitHub's own response header and its `/rate_limit`
count. So without a token, revalidating saves bandwidth and gives VYRON a confirmed answer, but not quota. A
"Check again" after the 60-second window costs the same two requests as a fresh read (the repository and its
languages), which is about 30 repository analyses an hour from one address. The only thing that saves quota is
the 60-second window, in which VYRON makes no request at all.

What this does not show: that a 304 costs a request *with* a token (not run; the probe takes `GITHUB_TOKEN` from
the environment for exactly that, about 3 of 5,000), that it holds on other days or other networks, or why it
differs from what I understood the documentation to say. VYRON's own accounting is not affected: its quota
figure comes from GitHub's response headers, including those on a 304, which is why the page showed 44 left.
The probe's verdict logic and its behaviour against the stand-in are tested in `tests/test_github_304_probe.py`
(15 tests); those say the script reads its own numbers correctly, not what GitHub does.

The four captured responses are the real fixtures the existing ones were written without. They have not been
compared with `apps/api/tests/github_fakes.py` yet because they have not been sent to me.

## The stand-in, and what a pass against it means

`scripts/standin_github.py` is a local server that answers the way GitHub's documentation says GitHub does.
It serves a made-up organisation, `fixture-org`, whose repository descriptions say they are not from GitHub,
so a screenshot taken against it labels itself. Each repository name is a scenario: `example-repo` (healthy),
`quiet-repo` (archived, no license, no topics, no languages), `private-repo`, `moved-repo` (301),
`missing-repo` (404), `limited-repo` (403 with the quota used up), `gateway-repo` (403 "No linked GitHub
account", as the build gateway answered), `broken-repo` (500), `flaky-repo` (503 once, then fine) and
`garbled-repo` (200 with a body that is not JSON) and `slow-stats-repo` (healthy, with commit activity that answers
202 every time). For commit activity the others answer 202 with the body `{}` twice and then 200 with a weak ETag and a
one-minute `cache-control` and a year of made-up weekly counts (`quiet-repo` 204, the failing ones 404). The 202 and 200
follow what GitHub really sent on 2026-11-04; **the 204 is written from the documentation and has not been seen**. For
the opt-in read: `huge-repo` (the contributor list is
refused with a plain 403), `unreleased-repo` (contributors but no release: 404), `burst-repo` (the list answers 403
with the quota used up) and `garbled-list-repo` (the list is an object); `quiet-repo` answers 204 for its
contributors and 404 for its release, and `example-repo` answers a list of three accounts (one a bot) and a
release. Those answers were written from GitHub's documentation; only the healthy list and release have since been
seen from GitHub, once (above). It counts requests (and records each
query string), so a test can show that a second click did not reach it, and it listens on loopback only.

```bash
python3 scripts/standin_github.py --mode prototype    # the map and repository page, wired to it
python3 scripts/standin_github.py --mode api          # the API alone, wired to it
```

What the tests that use it show: VYRON's transport, retry, cache, validation, error mapping, HTTP layer and
page behave correctly when GitHub answers in those shapes. What they do not show: that GitHub answers in
those shapes. Passing them is not evidence about `api.github.com`.

### Verified here, against the stand-in

| What | Where | Result (2026-10-25) |
| --- | --- | --- |
| Provider: fresh, revalidated and stale reads, private and mismatched repositories, quota, error mapping | `test_github_provider.py` | 32 pass |
| Routes, Host-header allow-list, error responses, response schema | `test_api_app.py`, `test_api_hostcheck.py`, `test_api_params_errors.py` | pass (API suite 504) |
| Real sockets: transport, cache, ETag, retry, budget, HTTP server, TypeScript client | `tests/test_standin_github.py`, `tests/test_ts_integration.py` | 27 and 4 pass |
| The check script itself: passes, fails, cannot run, token never sent or printed, captures, honest labelling | `tests/test_live_github_check.py` | 64 pass in all (2026-11-04) |
| The test server in `test_github_transport.py` no longer prints a traceback when a client hangs up (it did on Windows); other errors are still printed | `apps/api/tests/test_github_transport.py` (2 new) | pass; three one-line breakages were each caught |
| The check script's `--flow` option: the page's sequence on a fresh cache, the recorder, the expected label, the real and simulated waits, the refusals | `tests/test_live_github_check.py` (18 of its 64) | pass against the stand-in, with the waits simulated; 16 deliberate one-line breakages of the script were each caught, and it fails when the provider is given the old "least current part" label rule. **Run once against the real GitHub (reported 2026-11-03): 21 checks passed**, see above |
| The 304 probe: verdict logic, stand-in run, unreachable and non-loopback refusal, token never printed or sent | `tests/test_github_304_probe.py` | 15 pass |
| The repository page in Chromium: healthy, bare, private, moved, missing, gateway, broken, garbled, flaky, rate limited, switched off, phone width, forced colours | `scripts/verify_prototype_browser.py` | 205 checks pass in the whole run (2026-11-05), 138 of them on this page: 39 on the contributors section, 34 on the commit-activity section |
| Contributors and latest release: normalisers, provider (order and cost of requests, private guard, 204, 403, 404, rate limit, 5xx, malformed, 304, stale), route and schema | `apps/api/tests/test_github_activity.py` | 53 pass (four added on 2026-11-02 for the status wording); 21 one-line breakages of the code were each caught by a failing test (the first run left 2 undetected, which two added tests now catch), and 7 more for the status rule |
| The real contributors and latest-release answers read by the provider and the route; validators sent back as written; the cost per read; the empty release name | `apps/api/tests/test_github_real_captures.py` (7 of its 19) | pass; six deliberate one-line breakages of the code were each caught, and the empty-name guard, which has two layers, was caught only when both were broken |
| The same read over real sockets, including the typed TypeScript client | `tests/test_standin_github.py` (46 in all; 14 for the opt-in read, 4 for the commit-activity fixture), `packages/api-client/src/integration.test.ts` (5 new for commit activity, 29 in all) | pass |
| The check script's `--activity` option and its capture of the two new endpoints | `tests/test_live_github_check.py` | 16 tests; against the stand-in `--activity` runs 27 checks instead of 12 |
| The check script's `--capture --stats` option: asks again while the answer is 202, saves every attempt with its number and the time since the first, stops at the first other answer, says when it never settles, refuses bad numbers | `tests/test_live_github_check.py` (12 new), `tests/test_standin_github.py` (3 new, for the fixture itself) | pass against the stand-in; 21 deliberate one-line breakages of the capture and the fixture were each caught. **Run once against the real GitHub (2026-11-04, reported): see "Commit activity (opt-in)"** |
| Commit activity: the normaliser (every shape it refuses), the provider (never waits, a 202 is reported and not kept, a ready year is reused for a minute then confirmed with the ETag as written, a 204, an empty list, stale on error, a rate limit, a private repository, a renamed one), the route and schema in every state, `Cache-Control: no-store` | `apps/api/tests/test_github_commit_activity.py` | 30 pass; 13 deliberate one-line breakages of the code were each caught (the first run left 3 undetected: one was logic that turned out to be redundant and was removed, two needed new tests), and 7 of the page's figures code in `repository-model.ts` |
| Commit activity as GitHub really sent it: three 202s then a 200 for `pallets/flask`, a 200 first time for `torvalds/linux`; the shape the normaliser holds to; the weak ETag sent back; the route in both states; one request per ask, a 202 included | `apps/api/tests/test_github_real_captures.py` (8 of its 35) | pass |
| Commit activity on the page, in Chromium: nothing asked until pressed, the keyboard path, "still computing" with focus on Check again and no automatic polling, the third ask drawing 52 bars, the figures and their labels, the table, a never-finished year, nothing to report, phone width, forced colours | `scripts/verify_prototype_browser.py` | 34 of the 205 checks (stand-in fixture data, not GitHub) |
| The page's commit-activity figures and words: scaling, a minimum visible bar, the sums, the busiest week, the week still in progress (decided from `fetched_at`, never the reader's clock), the table, the real `torvalds/linux` answer | `apps/web/prototype/src/repository-model.test.ts` | 11 new (67 in the file) |

One real defect was found and fixed along the way, by a test written first: a response whose body was
shorter than its declared `Content-Length` was returned as a normal answer and would have been cached under
its ETag and served again on every 304. A short body is now a network failure, which the retry layer handles.

## Accepted input (`url.py`)

Accepted: `owner/repo`, `github.com/owner/repo`, `http(s)` URLs on `github.com` or `www.github.com`,
`git@github.com:owner/repo.git`, and `ssh://git@github.com/owner/repo.git`. A URL may also point inside a
repository; `parse_github_target` classifies it as `owner`, `repository`, `issue`, `pull_request`,
`commit`, `release`, `tree` or `blob` and extracts the number or reference. `GET /api/v1/github/parse?input=...` exposes the classification and contacts no one. The repository page uses it
first, then reads the repository the link belongs to and says so ("Analyzing owner/name, the repository that
this folder belongs to").

Rejected, not repaired: other hosts, credentials (except the `git` user on ssh), explicit ports,
percent-encoding, backslashes, non-ASCII, control characters, input over 300 characters, and first path
segments that are site routes rather than accounts (`orgs`, `settings`, `topics`, `search` and others).
Owner names follow GitHub's documented username rules (ASCII alphanumerics and single hyphens, at most 39
characters), so a login containing an underscore is not accepted, on purpose. Existence is for the GitHub
API to decide. The URL a person pastes is never fetched: only `owner` and `repo`, validated, are used to
build `https://api.github.com/repos/{owner}/{repo}`.

## Rate limits and retries

Policy (`ratelimit.py`, `retry.py`):

- Quota state comes from the response headers: `x-ratelimit-limit`, `-remaining`, `-used`, `-reset`,
  `-resource`, plus `retry-after` (integer seconds only; the HTTP-date form is ignored).
- `5xx` transient statuses (500, 502, 503, 504) and network errors: exponential backoff from one second.
- `429`, or a `403` that is evidently a limit (a `retry-after` header, remaining quota of zero, or "rate
  limit" in the message): wait `retry-after` if given, else until the reset time plus one second when the
  quota is exhausted, else at least one minute with exponential backoff.
- A plain `403` (permissions, blocks) is **never retried blindly**; it becomes `GitHubForbiddenError`.
- The generic runner allows up to five retries (six sends) and will not sleep through a wait longer than
  `max_wait_seconds` (default 120): it raises `GitHubRateLimitedError` carrying the wait, so a background job
  can be rescheduled.
- **A person is waiting on a repository read**, so the live provider is stricter: one retry, never a wait
  over 3 seconds. A longer wait is reported (503 with `Retry-After`) rather than slept through.
- The clock, sleep and jitter are injected, so all of this is tested without a network.

`RateLimitBudget` keeps the latest quota per resource (`core`, `search`, `graphql`) and refuses to start a
request when the remaining quota is at or below a reserve (default 10) before the window resets, leaving
headroom for interactive requests. Once GitHub has reported the quota spent, a click reaches GitHub no more
until the reset; the page says so and shows when.

Typed errors: `GitHubAuthError` (401), `GitHubForbiddenError` (403, 451), `GitHubNotFoundError` (404, 410),
`GitHubMovedError` (301, 302, 307, 308), `GitHubValidationError` (400, 422), `GitHubUnavailableError`,
`GitHubRateLimitedError`, `MalformedGitHubPayloadError`. Redirects are errors, not followed.

## Caching (`conditional.py`)

Three behaviours, behind any `CacheProvider` (an in-memory one exists; the Redis one is not written):
serve a fresh entry without calling GitHub, revalidate an older entry with `If-None-Match` or
`If-Modified-Since` (without a token a `304` still costs one request, see "Does a 304 cost a request?"), and serve a stale entry when GitHub
is down or rate limited. Cached bodies are capped at 2 MiB. Each result carries the time it was read, which is
what `fetched_at` reports.

## Data handling

- Payloads are untrusted: `normalize.py` checks every field's type and length, refuses a wrong type
  instead of guessing, and caps descriptions (2,000 characters), topics (100) and languages (200).
- `open_issues` is GitHub's `open_issues_count`, which **includes open pull requests**; the field is not
  relabelled as issues, and the page says "Open issues and pull requests".
- Values are as reported at fetch time and carry `fetched_at`. Nothing is written to a database yet; when
  repository rows are, they are to carry provenance `sourced` only when evidence rows exist, like every other
  fact.
- The page draws every value as text, links only `http(s)` addresses, and keeps nothing: no storage, no cookies.

## Not built, and to verify

1. The real runs not yet made: with a token, a rate-limited request (403 or 429), a repository whose `license` is
   null or without a description, a private repository, a 5xx. (The captures of an active, an archived and a renamed repository
   are kept and tested; the differences from the hand-written fixtures are listed in `github_fakes.py`.) Each is
   UNVERIFIED against GitHub until observed.
2. Ingestion jobs, the job queue, the projector to Neo4j, and organisation ingestion; a Redis cache shared by
   several server instances; an off-switch for GitHub in the settings-driven composition.
3. **Slice 2 of repository analysis**: contributors and the latest release are built (2026-11-01) as the opt-in
   read above and were seen once against the real GitHub (2026-11-02, `pallets/flask`); the answers for no
   contributors, a huge history and no release are still only from the documentation and UNVERIFIED. **Commit
   activity** is built (2026-11-05, see "Commit activity (opt-in)"). Still not started: release history, and
   dependency manifests, each of which needs real responses to be written against. Metrics must only ever be shown when measured from fetched data and shown
   with their method.
4. Software Genome and Technology DNA formulas: none are written.
5. GraphQL or the dependency-graph API for dependency data: not evaluated.
6. Private repositories, GitHub Apps and OAuth: out of scope by design (the route reads public repositories only).
