# MVP 1 — Architecture Proposal: GitHub Project Tracking

Status: **Revision 3 — approved. Implementation step 1 in progress.**
Date: 2026-10-25

Revision 2 changes: tie-safe cursors (§2, §3.1), explicit history-rewrite policy (§3.4a), unique report filenames (§3.4b), `__main__.py` entry point (§1), new-vault requirement (§1), 15-day first-run lookback (§3.2).

Revision 3 changes: first run always writes a baseline (§3.2, §3.3); one-second overlap on issue queries (§3.1); compare 404 only resets history when the missing commit is confirmed (§3.4a); minimum Python lowered to 3.11 because 3.12 is not installed and no 3.12 feature is used (§4).

Revision 4 (Step 5): incremental commits now come from the paginated compare result instead of walking `GET /commits` until `last_head_sha`. The walk is ordered by date, so it stopped at the old head before reaching commits merged in from older branches (§3.1).

Revision 5 (documentation cleanup): §3.5 report example and frontmatter fields match the implemented renderer; §3.4a and §7 name `iter_compare()` instead of a single compare call.

---

## 0. Scope and exclusions

**In scope.** A command you run by hand. It reads one GitHub repository (commits on the default branch, issues, pull requests). It finds what changed since the last successful run. It writes one Markdown report into the vault. It saves the small amount of state the next run needs.

**Out of scope for MVP 1.**
- More than one repository.
- Any write to GitHub. The integration is read-only.
- Schedulers, background services, webhooks, servers, frontends.
- AI agents, summaries, or recommendations.
- Databases. Section 4 explains why a JSON file is enough.
- A local mirror of GitHub history, bodies, comments, diffs, or reviews.
- Commits on branches other than the default branch.
- Abstractions or plugin systems for future integrations.

---

## 1. Directory structure

```
Organizador de proyectos/
├── README.md                  How to install, configure, and run
├── pyproject.toml             Package metadata; pytest as the only dev dependency
├── .gitignore                 Ignores config/config.toml, state/, vault/, .venv/
│
├── config/
│   ├── config.example.toml    Committed template
│   └── config.toml            Real config (git-ignored)
│
├── src/organizador/           Backend implementation
│   ├── __init__.py            Marks the package (empty)
│   ├── __main__.py            Makes `python -m organizador` work; calls cli.main()
│   ├── cli.py                 Argument parsing: `github run` subcommand
│   ├── config.py              Loads and validates config.toml
│   ├── fsutil.py              Atomic write + exclusive create (two small functions)
│   └── github/
│       ├── __init__.py        Marks the subpackage (empty)
│       ├── client.py          HTTP calls, pagination, error mapping
│       ├── changes.py         Change detection (pure functions, no I/O)
│       ├── report.py          Markdown rendering (pure functions, no I/O)
│       └── state.py           Load/save the state file
│
├── tests/
│   ├── fixtures/              Recorded GitHub API JSON responses
│   └── test_*.py              Offline tests
│
├── docs/                      Architecture and decision notes (this file)
│
├── state/                     Operational state — machine-owned, outside the vault
│   └── github/
│       └── <owner>__<repo>.json
│
└── vault/                     Markdown vault (path is configurable)
    ├── notes/                 Human-authored. The program never writes here.
    └── reports/
        └── github/
            └── <owner>__<repo>/
                └── 2026-10-25T183011Z.md
```

**Entry point.** The command is `python -m organizador github run`. It works because:
- `src/organizador/__main__.py` exists. Python runs this file for `python -m organizador`. It only calls `cli.main()`.
- The package uses the `src/` layout, so it must be installed once in the virtual environment with `pip install -e .`. Without that step, Python cannot find `organizador`. The README documents this.

No console-script alias is added. There is one way to run it.

**Responsibility of each directory**

| Directory | Owner | Purpose |
|---|---|---|
| `src/organizador/` | Code | All backend logic. `github/` holds everything specific to this module. |
| `config/` | Human | Which repository, where the vault and state live, first-run lookback. No secrets. |
| `state/` | Program | Cursors that let the next run know where the last one stopped. Not knowledge. |
| `vault/notes/` | Human | Your own notes. Read-only to the program. |
| `vault/reports/` | Program | Generated reports. Written once, never overwritten. |
| `tests/` | Code | Offline tests using recorded responses. |
| `docs/` | Human | Architecture decisions. |

**Vault requirements.**
- The project starts with a **new, empty Markdown vault**. The default is `./vault`.
- Existing personal Obsidian vaults are **not imported or reused**.
- Obsidian is **optional**. It is only a viewer/editor. The program does not read or need any Obsidian files (such as `.obsidian/`) or plugins.
- `vault_path` stays configurable, for example to move the new vault somewhere else. It must point to this project's vault, not to an existing personal one.
- If `vault_path` does not exist on the first run, the program creates `<vault_path>/reports/github/<owner>__<repo>/`. It only writes inside `<vault_path>/reports/`.

**Why `state/` sits outside the vault.** It is machine data, not knowledge. Keeping it out stops Obsidian from indexing it, stops accidental hand edits, and keeps it out of any vault sync.

---

## 2. Data ownership

| Information | Lives in | Notes |
|---|---|---|
| Full commits, diffs, issue/PR bodies, comments, reviews, labels, history | **GitHub only** | Never copied. Reports link to them. |
| What changed in one time window: number, title, state, author, timestamps, URL | **Markdown report** | A deliberate point-in-time snapshot, not a mirror. Every item carries its `html_url`. |
| Where the last run stopped (cursors), last run status | **`state/` JSON** | Small, machine-owned, rewritable. |
| Your interpretations, decisions, plans | **`vault/notes/`** | Human-owned. Notes can link to reports. |
| Repository identity, paths, lookback | **`config/config.toml`** | Human-edited. |
| GitHub token | **Environment variable `GITHUB_TOKEN`** | Never written to any file by the program. |

**Duplication rule.** A report copies only the title and metadata needed to read it without opening GitHub. Everything else is a link.

**Report immutability.** A report is never edited after it is written. To comment on one, write a note in `vault/notes/` that links to it. This removes any chance of the program overwriting human text.

### State file contents

```json
{
  "repo": "owner/name",
  "default_branch": "main",
  "last_head_sha": "a1b2c3…",
  "issues_cursor": { "ts": "2026-10-25T18:21:04Z", "ids": [57, 61] },
  "pulls_cursor":  { "ts": "2026-10-25T17:02:55Z", "ids": [42] },
  "last_success_at": "2026-10-25T18:30:11Z",
  "last_report": "reports/github/owner__name/2026-10-25T183011Z.md"
}
```

Each cursor has two parts:
- `ts`: the newest `updated_at` seen, taken from GitHub, not from the local clock. This avoids errors from clock differences between your machine and GitHub.
- `ids`: the numbers of the issues or PRs already reported **at exactly** `ts`. This list is what makes ties safe (§3.1). It only holds items that share one timestamp, so it stays tiny.

---

## 3. Data flow

```
manual run
  → load config + state (state may be missing)
  → fetch from GitHub (everything in memory; nothing written yet)
  → detect changes (pure function)
  → if no changes: print message, exit 0
  → write report (exclusive create)
  → write state (atomic replace)
  → exit 0
```

### 3.1 Retrieval and change detection

| Resource | How it is fetched | What counts as "changed" |
|---|---|---|
| Commits | `GET /repos/{r}/branches/{default_branch}` for the current head SHA, then `GET /repos/{r}/compare/{last_head_sha}...{head_sha}`, all pages. The first page's `status` decides (§3.4a). For `ahead`, the commits are exactly `git log last_head_sha..head_sha`, so commits merged in from an older branch are included even with old dates. The count must equal `total_commits`, or the run fails | Every commit in the comparison, listed newest first |
| Issues | `GET /repos/{r}/issues?state=all&since={issues_cursor.ts − 1 second}&sort=updated`; drop entries that have a `pull_request` key. The one-second overlap means we do not rely on whether `since` includes items exactly at `ts` | `after_cursor(updated_at, number)` |
| Pull requests | `GET /repos/{r}/pulls?state=all&sort=updated&direction=desc`, stop paging once `updated_at < pulls_cursor.ts` (strictly less, so ties are still fetched) | `after_cursor(updated_at, number)` |

**Tie-safe comparison.** One rule decides whether a timestamp is new:

```
after_cursor(t, number) =
    t >  cursor.ts
 or (t == cursor.ts and number not in cursor.ids)
```

- Fetching always includes `cursor.ts` itself (one-second overlap for issues, strict `<` stop for PRs), so items tied at `cursor.ts` are never skipped. Items from the overlap second that are older than `cursor.ts` fail `after_cursor` and are dropped locally.
- Items already reported at `cursor.ts` are in `cursor.ids`, so they are not reported twice.
- If an already-reported item changes again, its `updated_at` becomes greater than `cursor.ts`, so it is correctly reported as updated.

**Advancing the cursor** after a successful run, using all items that passed `after_cursor`:
- New `ts` = the greatest `updated_at` among them.
- If new `ts` > old `ts`: `ids` = numbers of those items whose `updated_at` equals the new `ts`.
- If new `ts` = old `ts` (only new tied items appeared): `ids` = old `ids` + the new numbers.
- If no items passed: the cursor does not change.

**Cursor initialization.** On the first run, each cursor starts as `{ ts: <first-run window start>, ids: [] }`. If the first run finds items, the cursor advances as above. If it finds **no** issues (or no PRs), that cursor keeps the window start. The next run queries from that point and finds only items updated since. This is correct and costs one small request. The cursor only ever moves to timestamps GitHub actually returned.

Each changed issue or PR gets one label, in this order, using the same `after_cursor` rule:
- PR **merged** if `after_cursor(merged_at)`
- **closed** if `after_cursor(closed_at)`
- **opened** if `after_cursor(created_at)`
- otherwise **updated** (the report does not say which field changed; that needs history we choose not to store)

### 3.2 First run (no state file)

- Commits, issues, and PRs are limited to the last `initial_lookback_days` (**default 15**).
- Window start = `now − 15 days` (UTC), formatted the way GitHub formats timestamps.
- Commits use `GET /repos/{r}/commits?sha={head_sha}&since=<window start>` because there is no `last_head_sha` yet. Listing from the head SHA (not the branch name) keeps the list and the saved checkpoint consistent if someone pushes during the run.
- The report is marked `Initial baseline` in its frontmatter and title.
- The state file is created. Cursors are initialized as described in §3.1.
- **The first run always writes the baseline report and the state file, even when GitHub returns no activity in the window.** In that case the report says "No activity in the last 15 days" and the state stores the current head SHA and the initialized cursors. The no-change early exit (§3.3) never applies to the first run.

**After the first run, the 15-day window is never used again.** Every later run starts from the previous successful checkpoint in the state file (`last_head_sha` and the two cursors), however long ago that was. If the program is not run for two months, the next report covers the full two months.

### 3.3 Repeated run with no changes

This applies only when a state file already exists (after initialization).

- No report is written. One empty report per run would only fill the vault with noise.
- The console prints `No changes since <last_success_at>`.
- The state file is **not** rewritten. Cursors stay the same.

### 3.4 Failure during execution

The order of steps makes failure safe:

| Where it fails | Result |
|---|---|
| Config invalid, token missing or rejected (401/403), repo not found (404) | Clear message, exit code ≠ 0, nothing written |
| Network error or rate limit during any page | Same. Partial results are discarded. No partial reports. |
| Report target file already exists | Never overwrite. A new name is chosen (§3.4b). |
| Report written, then state write fails | Exit ≠ 0. The next run starts from the same checkpoint and writes a **new** report file with the same `period_from`. Some items repeat; nothing is lost and nothing is overwritten. |
| `last_head_sha` no longer in the branch history (force-push or rebase) | No guessing. Explicit recovery (§3.4a). |

State is written to a temp file and then renamed over the old file. A crash mid-write leaves the old state intact.

### 3.4a History rewrite policy

The first page from `GitHubClient.iter_compare()` (§3.1) carries a `status`. `changes.commits_from_compare()` acts on it as follows:

| Compare result | Meaning | Action |
|---|---|---|
| `identical` | No new commits | Commits section empty |
| `ahead` | Old SHA is an ancestor of the current head | Normal incremental listing |
| `behind` or `diverged` | History was rewritten | Recovery below |
| 404 | Unknown: could be a missing commit, a missing branch, or no access | Confirmation check below |

**Confirming a 404.** A 404 from compare alone is not trusted. The program makes two more calls:
- `GET /repos/{r}/branches/{default_branch}` must return 200 (repo reachable, branch exists), **and**
- `GET /repos/{r}/commits/{last_head_sha}` must return 404 or 422 (the old commit does not exist).

Only if both hold is the missing commit identified, and the recovery below runs. In every other case the program **stops safely**: exit 1, no report, state unchanged (checkpoint not advanced), and a message naming the failed check (for example, "default branch `main` not found").

**Recovery** (no time-based fallback, because rebased commits can keep old dates and would be missed):
1. Commits are **not** listed. The report does not claim to know which commits are new.
2. The report frontmatter has `commits_status: "history_rewritten"`, `previous_head` and `head_sha`. A warning line before the summary says: *"**Warning: history rewritten.** The history of main was rewritten since the last run. Previous head `a1b2c3d` is no longer reachable. Commit comparison was skipped. New commit baseline: `e4f5g6h`."* Both SHAs link to GitHub. The Commits section says *"Commit comparison unavailable (history rewritten)."*
3. Issues and PRs are processed normally. They do not depend on commit history.
4. State saves the current head as the new `last_head_sha`. The next run is incremental again.
5. The console prints a warning and the program exits with code **2** ("completed with warning"), not 0. Exit codes: 0 = success, 1 = failure (nothing written), 2 = completed with warning.

In normal runs, `commits_status` is `incremental` (or `baseline` on the first run).

### 3.4b Report filenames

- Name: UTC time of the run with seconds, `YYYY-MM-DDTHHMMSSZ.md`, for example `2026-10-25T183011Z.md`. No `:` characters, so the name is valid on Windows.
- The file is created with **exclusive create** (Python mode `"x"`). This fails if the file exists; it never overwrites.
- If the name is taken, the program tries `…Z-2.md`, `…Z-3.md`, and so on, up to `-99`, then fails with exit 1.

This covers each case:
- **Two runs in the same minute or second:** different seconds give different names; the same second gets a `-2` suffix.
- **Report written, state save failed:** the report stays. It is never edited or deleted.
- **Retry of the same activity:** the retry is a new run with a new name. Its frontmatter has the same `period_from` as the earlier report, so the overlap is visible. Duplicate content is accepted; overwriting is not.

### 3.5 Report format

As implemented in `github/report.py` (this example is the exact-output test, with URLs shortened):

```markdown
---
source: "github"
repo: "octo-cat/my.repo"
branch: "main"
period_from: "2026-10-18T18:30:11Z"
period_to: "2026-10-25T18:30:11Z"
generated_at: "2026-10-25T18:30:11Z"
generated_by: "organizador"
baseline: false
commits_status: "incremental"
previous_head: "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d"
head_sha: "e4f5a6b7c8d9e0f1a2b3c4d5e6f708192a3b4c5d"
---

# octo-cat/my.repo — activity 2026-10-18 → 2026-10-25

Period: 2026-10-18 18:30 UTC → 2026-10-25 18:30 UTC

2 commits · 2 pull requests (1 merged, 1 opened) · 3 issues (1 opened, 1 closed, 1 updated)

## Commits on main

- [`e4f5a6b`](https://github.com/…/commit/e4f5…) Merge branch 'feature/export' — @alice, 2026-10-24
- [`5b6c7d8`](https://github.com/…/commit/5b6c…) Add CSV export — Bob Builder, 2026-01-05

## Pull requests

- **merged** [#42](https://github.com/…/pull/42) Add export — @bob, 2026-10-24 09:15 UTC
- **opened** [#43](https://github.com/…/pull/43) Draft: faster parser — @carol, 2026-10-23 16:00 UTC

## Issues

- **opened** [#57](https://github.com/…/issues/57) Crash on empty input — @carol, 2026-10-22 11:00 UTC
- **closed** [#51](https://github.com/…/issues/51) Typo in README — @dave, 2026-10-21 08:00 UTC
- **updated** [#12](https://github.com/…/issues/12) Support Windows paths — @erin, 2026-10-19 20:45 UTC
```

Frontmatter fields:

| Field | Value |
|---|---|
| `source`, `generated_by` | Always `"github"` and `"organizador"` |
| `repo`, `branch` | `owner/name` and the default branch |
| `period_from` | Baseline: window start. Otherwise: the previous run's `last_success_at` |
| `period_to`, `generated_at` | Time of this run (UTC) |
| `baseline` | `true` only on the first run |
| `commits_status` | `"baseline"`, `"incremental"`, or `"history_rewritten"` |
| `previous_head` | Saved head before this run; `null` on the baseline |
| `head_sha` | Head saved by this run |

Every value is written as a JSON scalar (quoted string, `true`/`false`, or `null`), which is valid YAML. A newline or quote inside a value cannot break the frontmatter.

Body rules:
- Commits are newest first. Each shows the linked short SHA, the first line of the message, the GitHub login (or the git author name), and the author date.
- Issues and PRs show the label, the linked number, the title, the login, and the time of that activity (`merged_at`, `closed_at`, `created_at`, or `updated_at`).
- Missing fields are left out, never guessed.
- GitHub text is kept on one line, with Markdown and Obsidian syntax escaped. Only `https://github.com` URLs are linked.
- Baseline reports add an "Initial baseline" note. With no activity, they add "No activity in the last N days."
- History-rewrite reports add a warning line before the summary (§3.4a).

---

## 4. Technology recommendations

| Choice | Recommendation | Why | Alternative rejected |
|---|---|---|---|
| Language | **Python 3.11+** (3.11 is the installed version; `tomllib` is the newest stdlib module used, available since 3.11) | Standard library covers HTTP, JSON, TOML, CLI. Easy to read. | Node/TypeScript: needs a build step or runtime deps for the same work |
| HTTP | **`urllib.request` (stdlib)** | ~3 endpoints, GET only. A 40-line wrapper handles headers, pagination (`Link` header), and errors. | `requests`/`httpx`: nicer API, but a dependency we do not need yet. Easy to swap later. |
| GitHub API | **REST v3** | Simple, one call per list, well documented. | GraphQL: fewer calls but harder queries, unnecessary at one repo. `gh` CLI: requires separate install and login; harder to test. |
| Config | **TOML via `tomllib` (stdlib)** | Readable, comments allowed, no dependency. | YAML: needs a dependency |
| State | **One JSON file** | About seven values. Human-inspectable. Atomic replace is trivial. | SQLite: justified only when we need to query history. We do not. |
| Secrets | **`GITHUB_TOKEN` env var** | Never touches disk via the program. | `.env` file: needs `python-dotenv` and is easy to commit by mistake |
| CLI | **`argparse` (stdlib)** | One subcommand now. | Click/Typer: dependency for no gain |
| Tests | **`pytest`** (dev only) | The only dependency in the project | `unittest`: works, but more boilerplate |
| Environment | **`python -m venv`** + `pip install -e .[dev]` | Nothing extra to install | `uv`/Poetry: fine, but not needed |

**Dependencies:** runtime — none beyond Python 3.11. Development — `pytest`.

**Token.** A fine-grained personal access token limited to the one repository, read-only: *Metadata*, *Contents*, *Issues*, *Pull requests*. A public repository can run without a token but is limited to 60 requests per hour.

**Testability.** `client.py` takes a `fetch(url, headers) -> (status, headers, body)` function as a parameter (request headers are passed so the client controls authentication). Tests pass a fake that returns fixtures or raises errors. `changes.py` and `report.py` are pure functions on plain dicts. No test touches the network.

---

## 5. Acceptance criteria

Run against a sandbox repository you control, plus the offline test suite.

**Setup**
0. After `pip install -e .[dev]` in a fresh virtual environment, `python -m organizador --help` runs from any directory.

**First run**
1. With no state file and a non-existent `vault_path`, `python -m organizador github run` exits 0. It creates the report folder, one report marked `baseline: true` with only items from the last 15 days, and the state file.
1a. Test: first run where GitHub returns no commits, no issues, and no PRs → exit 0, a baseline report saying "No activity in the last 15 days" is written, and the state file is created with the current head SHA and both cursors equal to the window start with empty `ids`. A second run with one new issue reports exactly that issue.
1b. Test: a run 40 days after the last successful one (fixture) queries from the saved cursors, not from `now − 15 days`.

**Repeated run**
2. Running again right away exits 0, prints `No changes since …`, writes no report, and leaves the state file byte-identical.

**New activity**
3. In the sandbox: push 2 commits, open 1 issue, close 1 issue, open 1 PR, merge 1 PR. The next run produces a report that lists exactly those 6 items with the correct labels. Every link opens the right GitHub page.
4. No item from the previous report appears again, unless it was updated after that report.

**Timestamp ties** (fixture test, all three runs in sequence)
4a. Run 1: GitHub returns issues #1 and #2, both with `updated_at = T`. Both are reported. Cursor = `{ts: T, ids: [1, 2]}`.
4b. Run 2: GitHub returns #1, #2, and #3, all at `T`. Only #3 is reported. Cursor = `{ts: T, ids: [1, 2, 3]}`.
4c. Run 3: GitHub returns the same three items. Nothing is reported, no report file is written, state is byte-identical.
4d. Run 4: #2 now has `updated_at = T+1s`. Only #2 is reported, as **updated**. Cursor = `{ts: T+1s, ids: [2]}`.
4e. The same four steps pass for pull requests.
4f. Test: the issues request is sent with `since = cursor.ts − 1s`. A fixture that also returns an item at `cursor.ts − 1s` (already reported earlier) does not report it again.

**Failure handling**
5. With an invalid token: exit ≠ 0, a clear message, no new file under `vault/reports/`, state file byte-identical.
6. Test: the fake fetcher raises on page 2 of issues → same result as 5.
7. Test: state save raises after the report is written → exit 1. The next run writes a **second, differently named** report with the same `period_from` and the same changes. The first report is byte-identical.
8. Test: a file with the target report name already exists → the new report is written as `…Z-2.md`; the existing file is byte-identical.
8a. Test: two runs with the clock fixed to the same second → two report files, `…Z.md` and `…Z-2.md`.

**History rewrite** (fixture tests)
9. Compare returns `diverged` (and, separately, `behind`; and, separately, 404 with branch 200 and commit 404) → no commits listed, frontmatter `commits_status: history_rewritten`, warning text with both SHAs, issues and PRs still reported, `last_head_sha` = current head, exit code 2.
9c. Compare returns 404 and the confirmation is not conclusive (branch 404, or commit lookup 200, or any network error) → exit 1, no report file, state byte-identical.
9a. The next run after 9 is a normal incremental run with `commits_status: incremental` and exit code 0.
9b. Manual check in the sandbox: force-push `main`, run, confirm the result matches 9.

**Safety**
10. A hash of every file under `vault/notes/` is identical before and after any run.
11. The token string does not appear in any file under `state/` or `vault/`.
12. `pytest` passes with networking disabled.

---

## 6. Future compatibility

A second integration is added beside GitHub, not inside it:

- Code: `src/organizador/<name>/` (new sibling package)
- State: `state/<name>/…`
- Reports: `vault/reports/<name>/…`
- Config: a new `[<name>]` section in `config.toml`
- CLI: a new subcommand, `python -m organizador <name> run`

`config.py` and `fsutil.py` are already generic. Anything else is extracted into shared code only when a second integration actually duplicates it. No plugin registry or base classes are created ahead of time.

---

## 7. Implementation sequence

Each step ends with passing tests before the next one starts.

1. `git init`, `.gitignore`, `pyproject.toml`, `config.example.toml`, package layout including `__init__.py` files and `__main__.py`; `pip install -e .[dev]`; check criterion 0.
2. `config.py` + `fsutil.py` (atomic write, exclusive create with `-2…-99` suffix) + tests (criteria 8, 8a).
3. `github/state.py`: load (missing file → `None`), save, cursor shape `{ts, ids}` + tests.
4. `github/client.py`: authenticated GET, `Link` pagination, paginated compare (`iter_compare()`), error mapping + tests with a fake fetcher.
5. `github/changes.py`: `after_cursor`, cursor advance and initialization, compare-status handling, labeling, 15-day first-run window + fixture tests (criteria 1a, 1b, 4a–4e, 9, 9a).
6. `github/report.py`: Markdown + frontmatter rendering (including `commits_status`) + snapshot test.
7. `cli.py`: wire the steps in the order from section 3, with exit codes 0/1/2.
8. Manual validation against the sandbox repository (criteria 1–5, 9b).
9. `README.md`: setup, token creation, how to run.

---

## 8. Open questions

Resolved in revision 2: Python approach accepted; new vault, not reused (§1); first-run lookback 15 days (§3.2).

Still open. Neither blocks the design:

1. **Which repository**, and is it public or private? (Decides whether a token is mandatory. Needed before step 8, not before step 1.)
2. **No-change runs.** Proposed: no report file, console message only. Do you want an empty report instead?

### Known technical risks (accepted for MVP 1)

- **Two updates to one item within the same second.** If an item already in `cursor.ids` changes again without its `updated_at` changing, the second change is not seen. GitHub timestamps have one-second precision, so this cannot be fixed without storing per-item history.
- **GitHub API delay.** If GitHub returns an update later than its `updated_at` suggests (eventual consistency), and the cursor has already moved past that time, the update is missed. Rare for one personal repository. A fix (re-querying a small overlap window) is deferred until it is seen in practice.
- **Pages shifting during a run.** Items updated while the program is paging can move between pages. With one repository and runs of a few seconds, this is unlikely. Anything missed this way has a newer `updated_at` and is picked up by the next run.
- **Default branch renamed or changed.** The 404 confirmation fails on the branch check, so the run stops with exit 1 and a clear message. Recovery is manual (delete the state file to re-baseline). This is handled explicitly, not silently.
