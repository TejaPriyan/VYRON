# Real GitHub responses

**REAL GITHUB DATA, captured unauthenticated from https://api.github.com.** They are the only fixtures in this
repository that GitHub actually sent. Everything else under `apps/api/tests` that looks like GitHub data was
written by hand (see `github_fakes.py`).

| Folder | Captured (UTC) | What it shows |
| --- | --- | --- |
| `pallets_flask` | 2026-10-26 07:53 | A large active public repository (200 with a weak `ETag`), its languages, the quota, and a 404 for a name that does not exist |
| `atom_atom_archived` | 2026-10-27 04:55 | An archived repository (`archived: true`, last push in 2023) |
| `joyent_node_renamed` | 2026-10-27 04:55 | A renamed repository: **301**, with the new location only in the body (`url`: `https://api.github.com/repositories/211666`) |
| `pallets_flask_activity` | 2026-11-02 03:46 | The same repository again, with its **top contributors** (`/contributors?per_page=10`: ten entries, a weak `ETag`, a `Last-Modified`, and a `Link` header saying there are 40 pages) and its **latest release** (`/releases/latest`, whose `name` is the empty string). It holds the public logins GitHub sent for that repository, unchanged |
| `torvalds_linux_huge` | 2026-11-02 10:32 | A very large public repository whose **contributor list GitHub refuses**: `/contributors` answers a plain **403** ("The history or contributor list is too large to list contributors for this repository via the API.", with the quota not used up and no `Retry-After`), `/releases/latest` answers **404** (no release), and the repository's `license` is `{"key": "other", "name": "Other", "spdx_id": "NOASSERTION", "url": null}`: a licence GitHub found and could not name |
| `commit_activity_flask_linux` | 2026-11-04 10:01 | **`/stats/commit_activity`**, GitHub's weekly commit counts for the last year. `pallets/flask` answered **202** with the body `{}` three times (0.7, 5.8 and 11.0 seconds in) and **200** on the fourth ask (16.1 seconds); `torvalds/linux` answered 200 on the first ask. The 200 is a list of 52 weeks, each `{days: [7 counts, Sunday first], total, week}`, with a weak `ETag` and `cache-control: public, max-age=60`. The same folder holds the two repositories' own answers, the quota and a 404, as the capture tool wrote them |

How they were made: `python scripts/live_github_check.py --capture <folder>` (with `--activity` for the two activity
folders and `--stats` for the last) on the project owner's machine (reported, not run by me), then copied here unchanged. Response headers were limited to an allow-list and no
credential was involved (`"authenticated": false` in each `MANIFEST.json`). The `Location` header is **not** in
the 301 capture because the allow-list did not include it when the capture was taken; the script has since been
fixed to keep it, so a new capture would carry it.

What these are not: a promise that GitHub will send the same thing tomorrow. Counts and dates are what GitHub said
on the day. Tests that use them check *shape and handling* (which fields, which statuses, how VYRON reacts) and
state the figures as "as captured".

To replace one, run the check script again and copy the folder over, then update the figures the tests name.
