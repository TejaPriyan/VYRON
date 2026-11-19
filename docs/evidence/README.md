# Evidence from runs on the project owner's machine

**REAL GITHUB DATA, reported by the project owner (Windows, no `GITHUB_TOKEN`, run through Antigravity) and kept as
they sent them.** I did not see these runs happen; I read the files. They are not fixtures: no test depends on them.

| File | Taken (UTC) | What it is |
| --- | --- | --- |
| `live_flow_pallets_flask_2026-11-03.txt` | 2026-11-03, quota window ending 14:02 | The complete output of `python scripts/live_github_check.py --flow --repo pallets/flask`, with the exit code the owner appended (0): 21 checks passed. Each request VYRON sent, whether it asked "has this changed?", GitHub's status and the quota left |
| `repository_page_flask_live_2026-11-03.png` | the same hour | The repository page on a freshly started server: `pallets/flask` analysed, a 70-second wait, "Load contributors and latest release" pressed. The section's badge says **Live**; the footer says 44 of 60 requests left |
| `repository_page_linux_licence_2026-11-03.png` | the same hour | The top of the page for `torvalds/linux`: the licence tag reads "License Other (no standard identifier)"; the footer says 40 of 60 left |

The terminal output of the verification run is not kept here because it contains a user name and local paths.
