# MVP 1 — Real GitHub Validation (Step 8A)

Date: 2026-10-25 (local, UTC−6) / 2026-10-26 UTC
Repository: [Gerico90/cafenorte-data-challenge](https://github.com/Gerico90/cafenorte-data-challenge) (public, default branch `master`)
Authentication: none (`GITHUB_TOKEN` not set)
Python: 3.11.0, project virtual environment, run from the project folder (not `C:\`)
Application code: unchanged during this step

---

## 1. Setup

| Check | Result |
|---|---|
| Existing checkpoint before the first run | None (`state/` empty) |
| Existing reports before the first run | None (`vault/` empty except empty folders) |
| `config/config.toml` | Created (git-ignored): owner `Gerico90`, repo `cafenorte-data-challenge`, `vault_path = "vault"`, `state_path = "state"`, `initial_lookback_days = 45` |
| Program default lookback | Unchanged at 15 (`config.py`, `DEFAULT_LOOKBACK_DAYS`) |

The 45-day lookback was used for this validation only. It affects only the first run; now that a checkpoint exists, later runs ignore it.

---

## 2. Run 1 — initial baseline (real API)

```
> python -m organizador github run
Baseline report written: C:\proyectos\Organizador de proyectos\vault\reports\github\Gerico90__cafenorte-data-challenge\2026-10-26T021927Z.md
  14 commits, 0 pull requests, 0 issues
exit=0
```

### Generated report

`vault/reports/github/Gerico90__cafenorte-data-challenge/2026-10-26T021927Z.md`:

```markdown
---
source: "github"
repo: "Gerico90/cafenorte-data-challenge"
branch: "master"
period_from: "2026-09-11T02:19:27Z"
period_to: "2026-10-26T02:19:27Z"
generated_at: "2026-10-26T02:19:27Z"
generated_by: "organizador"
baseline: true
commits_status: "baseline"
previous_head: null
head_sha: "f0fab30cb175b48463868732472d8a6f6de7b096"
---

# Gerico90/cafenorte-data-challenge — initial baseline 2026-09-11 → 2026-10-26

Period: 2026-09-11 02:19 UTC → 2026-10-26 02:19 UTC

> **Initial baseline.** This is the first report for this repository. It lists activity in the period above, not changes since a previous report.

14 commits · 0 pull requests · 0 issues

## Commits on master

- [`f0fab30`](https://github.com/Gerico90/cafenorte-data-challenge/commit/f0fab30cb175b48463868732472d8a6f6de7b096) Update README.md — @Gerico90, 2026-09-18
- [`b9d553e`](…/commit/b9d553ec2663b0982463efde7d70229a7f1f9a47) Update README.md — @Gerico90, 2026-09-18
- [`60d6506`](…/commit/60d650638431975f4eacc8069cfe72bd3ca5e902) Update aws\_proposal.md — @Gerico90, 2026-09-18
- [`65c5ab2`](…/commit/65c5ab27c2cfcc1ebe80dc217732883ea6123863) Update AI\_LOG.md — @Gerico90, 2026-09-18
- [`cb0927b`](…/commit/cb0927bc36be6ff6cf44e458d8435418b22700df) Translate README documentation to Spanish — @Gerico90, 2026-09-18
- [`84a7435`](…/commit/84a7435e7d659b24b81d81e3c8103021e7ee829e) Finalize business analysis and AWS proposal — @Gerico90, 2026-09-18
- [`512e92e`](…/commit/512e92e9ad3e931a873defa4896b8fef999024b1) Complete CaféNorte data challenge solution — @Gerico90, 2026-09-18
- [`57eb1dc`](…/commit/57eb1dcf0f7e41d18d53d976cf1069bd7d53a585) Implement and test stockout analysis — @Gerico90, 2026-09-17
- [`2369624`](…/commit/23696243bfdb82ae07ecc32d27a18ad8347a30c9) Implement and test inventory turnover question — @Gerico90, 2026-09-17
- [`e9584e6`](…/commit/e9584e63ab720ad7f17d1361f3ed98dbaba65ced) Add reconciliation and transformation tests — @Gerico90, 2026-09-17
- [`500edf1`](…/commit/500edf133744d68b9b10a4b2739c2a4bce9f7112) Build and document local analytical pipeline — @Gerico90, 2026-09-17
- [`ae0816f`](…/commit/ae0816f66da2f2991239948974df0b832bbd33bb) Document validated product reconciliation strategy — @Gerico90, 2026-09-17
- [`ec54f36`](…/commit/ec54f36147dcf75160f4c0423e0808ce9deb80c0) Validate source discovery findings — @Gerico90, 2026-09-17
- [`c1acfc7`](…/commit/c1acfc7aa340076ce52375cfbf71fd3ebd6e7cdc) Initialize technical challenge repository — @Gerico90, 2026-09-16

## Pull requests

_No pull request activity._

## Issues

_No issue activity._
```

(Links shortened here with `…`; the file has full `https://github.com/Gerico90/cafenorte-data-challenge/commit/<sha>` URLs.)

### Saved checkpoint

`state/github/Gerico90__cafenorte-data-challenge.json`:

```json
{
  "repo": "Gerico90/cafenorte-data-challenge",
  "default_branch": "master",
  "last_head_sha": "f0fab30cb175b48463868732472d8a6f6de7b096",
  "issues_cursor": { "ts": "2026-09-11T02:19:27Z", "ids": [] },
  "pulls_cursor":  { "ts": "2026-09-11T02:19:27Z", "ids": [] },
  "last_success_at": "2026-10-26T02:19:27Z",
  "last_report": "reports/github/Gerico90__cafenorte-data-challenge/2026-10-26T021927Z.md"
}
```

### Comparison with GitHub data

Checked with `curl` directly against the API (independent of the program's client), right after run 1.

| Item | GitHub (curl) | Report / checkpoint | Match |
|---|---|---|---|
| Visibility | public | — | ✅ |
| Default branch | `master` | `branch: "master"`, `default_branch: "master"` | ✅ |
| Head of `master` | `f0fab30cb175…b096` | `head_sha`, `last_head_sha` | ✅ |
| Commits on `master` | 14 (single page, no `Link` header) | 14 listed | ✅ |
| Commit SHAs, order, first-line messages | 14 entries, newest first | identical | ✅ |
| Authors | all `Gerico90` | all `@Gerico90` | ✅ |
| Dates | 2026-09-16 … 2026-09-18 (author = committer dates) | same dates | ✅ |
| Latest commit | 2026-09-18T23:17:15Z (matches the user's "September 18, 2026") | `f0fab30`, 2026-09-18 | ✅ |
| Oldest commit inside the 45-day window? | 2026-09-16T23:12:06Z > window start 2026-09-11T02:19:27Z | all 14 included | ✅ |
| Issues (`state=all`, includes PRs) | 0 | 0, cursor = window start, `ids: []` | ✅ |
| Pull requests (`state=all`) | 0 | 0, cursor = window start, `ids: []` | ✅ |
| Non-ASCII text | `CaféNorte` | written as UTF-8 `CaféNorte` | ✅ |
| Checkpoint ↔ report | — | `last_report` points to the file; `head_sha`, `generated_at` = `last_success_at`, `branch`, `repo` agree | ✅ |

---

## 3. Run 2 — no changes (real API)

No external changes were made to the repository between runs.

```
> python -m organizador github run
No changes since 2026-10-26T02:19:27Z.
exit=0
```

| Check | Result |
|---|---|
| Exit code | 0 ✅ |
| New report file | none (still only `2026-10-26T021927Z.md`) ✅ |
| State file | byte-identical (`cmp`), same size and modification time ✅ |
| Compare call | not made (head unchanged), as designed ✅ |

---

## 4. Real compare endpoint (read-only, separate from runs)

Script: the program's own `GitHubClient.iter_compare` and `changes.commits_from_compare`, without loading or saving state and without touching the vault. Historical SHAs from `master`:
`FIRST = c1acfc7…` (oldest), `MID = 57eb1dc…`, `HEAD = f0fab30…`, plus a well-formed SHA that does not exist (`0123456789abcdef…`).

| # | Request | Result | Matches design |
|---|---|---|---|
| 1 | `FIRST...HEAD`, 5 per page (forced) | 3 pages, `Link rel="next"` on pages 1–2 only; `status: ahead`, `total_commits: 13`, `ahead_by: 13`; 13 unique commits, `FIRST` excluded, `HEAD` last (oldest first); `commits_from_compare` → `incremental`, 13 commits, newest first | ✅ compare pagination works and the count check passes |
| 2 | `FIRST...HEAD`, 100 per page (production) | 1 page; same 13 commits in the same order as the paginated run | ✅ |
| 3 | `MID...HEAD` | `incremental`, 7 commits (`f0fab30` … `512e92e`) — exactly the commits after `57eb1dc` | ✅ |
| 4 | `HEAD...FIRST` | `status: behind` → `history_rewritten`, no commits listed | ✅ |
| 5 | `HEAD...HEAD` | `status: identical` → `incremental`, 0 commits | ✅ |
| 6 | `MISSING...HEAD` | HTTP 404 → `GitHubNotFoundError` | ✅ assumption of §3.4a holds |
| 7 | `GET /commits/MISSING` | HTTP 422 (not 404) → `GitHubError(status=422)` | ✅ the 404-or-422 rule is needed; 422 is what GitHub actually returns |

**New fact:** GitHub's `next` links for compare pages 2–3 use the repository-ID path `https://api.github.com/repositories/1375489202/compare/...` instead of `/repos/Gerico90/cafenorte-data-challenge/...`. The client trusts any `https://api.github.com` URL, so they were followed correctly. A stricter path-based trust rule would have broken pagination.

After all checks the files under `state/` and `vault/` were exactly the two from run 1.

---

## 5. Discrepancies

None between the program's output and GitHub's data.

Observations (not errors):
- Report dates are UTC by design. The run was on 2026-10-25 local time (UTC−6), so the report name and period end show 2026-10-26.
- Markdown escaping makes raw file text noisier (`aws\_proposal.md`); it renders correctly.
- Rate-limit counter: GitHub reported 55/60 remaining after run 1 + 5 curl requests, and 53/60 at the end. These numbers do not add up to the requests made (~25 in total). The likely cause is the hourly window resetting during the test, but this was not verified.

---

## 6. Not tested against the real API

These scenarios were **not** exercised with real GitHub data in this step. The offline test suite covers them with a fake API unless noted.

> **Later update:** Parts of the first two scenarios were subsequently validated against `Gerico90/organizador-personal`. See section 8 for the specific cases tested and remaining limitations. The original Step 8A results are preserved below.

| Scenario | Why not tested here |
|---|---|
| Incremental run with new commits, issues, or PRs | No external changes were made (instructed not to modify the repository) |
| Real issue and pull request data (shapes, classification, cursor advance, ties) | Repository has 0 issues and 0 PRs |
| Pagination of commits, issues, and pulls listings | All results fit in one page; only compare pagination was verified |
| Full CLI flow for a history rewrite (warning report, exit 2) | Requires a force-push; compare `behind` and the 404/422 responses were verified separately (§4) |
| Full CLI flow for a compare 404 with a confirmed missing commit | Requires a rewritten history; building blocks verified separately (§4) |
| Authenticated requests, private repositories | No token, by instruction |
| Rate-limit responses (403/429) | Not triggered |
| Server errors (5xx), timeouts, network failures | Not triggered |
| Branch names containing `/` | Default branch is `master` |
| Empty repository (409 / branch 404) | Not available |
| Renamed or transferred repository (3xx) | Not available |
| Default branch change | Not available |
| Report name collision, state-save failure | Local filesystem cases; covered offline only |
| Running from `C:\` | Known environment problem (loose Python 3.12 modules in `C:\`); runs were made from the project folder as instructed |

---

## 7. Conclusion

Against the real GitHub API, the initial baseline, the checkpoint, and the no-change run behave as designed and match GitHub's data exactly. The compare endpoint behaves as the design assumes: it pages with `Link` headers, its counts are consistent, it returns `ahead`, `behind` and `identical` correctly, a missing base SHA returns 404, and a missing commit lookup returns 422. Incremental runs with real new activity, and anything involving issues or PRs, remain unverified against real data.

---

## 8. Incremental validation — Gerico90/organizador-personal

Date: 2026-10-26 UTC
Repository: [Gerico90/organizador-personal](https://github.com/Gerico90/organizador-personal) (public, default branch `main`)
Authentication: none (`GITHUB_TOKEN` not set)
Application code: unchanged
Configuration: only `github.repo` changed; `initial_lookback_days = 15`. The CaféNorte checkpoint and report were preserved (SHA-256 unchanged).

Two kinds of evidence are used in this section:
- **GitHub API check:** data read directly from the API with `curl`, separately from the program's own client, and compared with the report.
- **Local check:** checkpoint contents, file lists, SHA-256 hashes and modification times of the files under `state/` and `vault/`, compared before and after each run.

Sections 8.1 and 8.2 were written before this split was made, so their "checked against the API" lists also include local checks such as cursor values. Sections 8.3 to 8.5 keep the two apart.

### 8.1 Baseline and no-change run

| Run | Time (UTC) | Output | Exit |
|---|---|---|---|
| 1 | 16:27:39Z | Baseline report `2026-10-26T162739Z.md` — 1 commit, 0 pull requests, 0 issues | 0 |
| 2 | 16:27:58Z | `No changes since 2026-10-26T16:27:39Z.` | 0 |

Checked with `curl` against the API: default branch `main`, head `02278df`, 1 commit in the 15-day window (the repository's only commit), 0 issues, 0 pull requests. Run 2 left every file under `state/` and `vault/` identical (SHA-256 and modification time).

### 8.2 Real issue detection

Issue #1, "Validate real incremental GitHub activity detection", was created manually on GitHub at 16:32:16Z.

| Run | Time (UTC) | Output | Exit |
|---|---|---|---|
| 3 | 16:33:00Z | Report `2026-10-26T163300Z.md` — 0 commits, 0 pull requests, 1 issue (1 opened) | 0 |
| 4 | 16:33:13Z | `No changes since 2026-10-26T16:33:00Z.` | 0 |

Checked against the API:
- Issue #1 is classified as **opened**: it is open, has no `closed_at`, and was created after the previous checkpoint.
- The report period is 16:27:39Z → 16:33:00Z.
- `issues_cursor` advanced to `{ts: 16:32:16Z, ids: [1]}`.
- `pulls_cursor` and `last_head_sha` did not change.
- Run 4 created no report and left the checkpoint identical.

### 8.3 Real incremental commit

Commit `480a8b2` ("docs: record real incremental validation for organizador-personal") was pushed to `main`. It changed only this file.

| Run | Time (UTC) | Output | Exit |
|---|---|---|---|
| 5 | 16:40:30Z | Report `2026-10-26T164030Z.md` — 1 commit, 0 pull requests, 0 issues | 0 |
| 6 | 16:40:51Z | `No changes since 2026-10-26T16:40:30Z.` | 0 |

GitHub API check:
- Head of `main`: `480a8b29a0f5a5fba2883ee398ca32df6625413f`, the same as the report's `head_sha`.
- Compare `02278df...main`: status `ahead`, 1 commit (`480a8b2`, author Gerico90, 2026-10-26T16:38:52Z). The report lists that commit only.
- The previous commit `02278df` is the parent of `480a8b2` and is not repeated in the report.
- Issue #1 still had `updated_at` 16:32:16Z, and there were 0 pull requests. The report shows no issue or pull request activity.

Local check:
- `last_head_sha` advanced from `02278df…` to `480a8b2…`. `issues_cursor` and `pulls_cursor` did not change.
- The report period is 16:33:00Z → 16:40:30Z, starting at the previous `last_success_at`.
- Run 6 created no report and left all files identical.

### 8.4 Real pull request opened

Pull request #2, "Update README validation status", was opened at 16:53:23Z. Its base is `main` and its head is branch `test/pr-incremental-validation` at `ab29afb`.

| Run | Time (UTC) | Output | Exit |
|---|---|---|---|
| 7 | 16:55:05Z | Report `2026-10-26T165505Z.md` — 0 commits, 1 pull request (1 opened), 0 issues | 0 |
| 8 | 16:55:20Z | `No changes since 2026-10-26T16:55:05Z.` | 0 |

GitHub API check:
- PR #2 is open, not a draft, with no `closed_at` or `merged_at`. `created_at` and `updated_at` are both 16:53:23Z, and the author is Gerico90. The report classifies it as **opened**.
- The issues endpoint also returns #2, marked with a `pull_request` field, because GitHub lists pull requests as issues. The report does **not** show it as an issue. The only real issue, #1, was unchanged.
- Head of `main` was still `480a8b2`. Compare `480a8b2...main` returned `identical`. The report shows 0 commits. Pushing the PR branch did not appear as commits on `main`.

Local check:
- `pulls_cursor` advanced to `{ts: 16:53:23Z, ids: [2]}`. `issues_cursor` and `last_head_sha` did not change.
- Run 8 created no report and left all files identical.

### 8.5 Real pull request merged

PR #2 was merged at 17:05:04Z with a merge commit.

| Run | Time (UTC) | Output | Exit |
|---|---|---|---|
| 9 | 17:06:04Z | Report `2026-10-26T170604Z.md` — 2 commits, 1 pull request (1 merged), 0 issues | 0 |
| 10 | 17:06:36Z | `No changes since 2026-10-26T17:06:04Z.` | 0 |

GitHub API check:
- PR #2: `merged: true`. `merged_at`, `closed_at` and `updated_at` are all 17:05:04Z, and `merge_commit_sha` is `817e89f`. The report classifies it as **merged**.
- Head of `main`: `817e89f9afe8671cbff24ad8199b70eb26429a6c`, with parents `480a8b2` and `ab29afb`.
- Compare `480a8b2...main`: status `ahead`, exactly 2 commits. The report lists the same two, newest first:
  - `817e89f9afe8671cbff24ad8199b70eb26429a6c` — "Merge pull request #2 from Gerico90/test/pr-incremental-validation", 2026-10-26T17:05:04Z
  - `ab29afbf51c0a11595b6a01caa2e5f13e6ee4dc9` — "docs: update README validation status", 2026-10-26T16:47:59Z
- Earlier commits `480a8b2` and `02278df` are not repeated.
- The issues endpoint returned #2 (pull request) and #1 (unchanged). The report shows 0 issues.

Local check:
- `last_head_sha` advanced to `817e89f…`. `pulls_cursor` advanced to `{ts: 17:05:04Z, ids: [2]}`. `issues_cursor` did not change.
- Run 10 created no report and left all files identical.

### 8.6 File integrity across runs 1–10 (local check)

After every run, the file list and SHA-256 hashes under `state/` and `vault/` were compared with the previous run:
- Each run that reported activity added exactly one new report and rewrote only the `organizador-personal` checkpoint.
- Each "No changes" run (2, 4, 6, 8, 10) left every file identical, including the checkpoint's modification time.
- No earlier report was ever changed.

Final hashes:

| File | SHA-256 | Written by |
|---|---|---|
| `state/github/Gerico90__cafenorte-data-challenge.json` | `9d20ef10…eb41e9` | Step 8A (unchanged since) |
| `vault/…/Gerico90__cafenorte-data-challenge/2026-10-26T021927Z.md` | `1d86f295…14b299` | Step 8A (unchanged since) |
| `vault/…/Gerico90__organizador-personal/2026-10-26T162739Z.md` | `c1a7a00a…5dde6c` | Run 1 |
| `vault/…/Gerico90__organizador-personal/2026-10-26T163300Z.md` | `9773873b…ce15950` | Run 3 |
| `vault/…/Gerico90__organizador-personal/2026-10-26T164030Z.md` | `c574782f…7205` | Run 5 |
| `vault/…/Gerico90__organizador-personal/2026-10-26T165505Z.md` | `b678c5bc…46ea4` | Run 7 |
| `vault/…/Gerico90__organizador-personal/2026-10-26T170604Z.md` | `8a006356…50fc3` | Run 9 |
| `state/github/Gerico90__organizador-personal.json` | `04f429ba…ef564` | Run 9 |

### 8.7 Observations (not errors)

- Commit messages are shown by their first line only. The `Co-Authored-By` line in `ab29afb` does not appear in the report.
- In the raw report text the merge commit reads `Merge pull request \#2`. The backslash is Markdown escaping and renders as "#2".
- Without a token GitHub allows 60 requests per hour. Runs 9 and 10 plus the `curl` checks brought the remaining count down to 17 before run 10. The limit was not reached.
- The test branch `test/pr-incremental-validation` was kept after the merge.

### 8.8 Still not tested against the real API

These have not been exercised with real GitHub data. Some are covered only by the offline tests with a fake API (see section 6):

- Issues being closed, reopened, or updated (comments, edits, labels). Only opening was tested.
- Pull requests closed without merging, or updated (new commits, comments, edits). Draft pull requests. Only opening and merging were tested.
- An issue and a pull request changing in the same run. Several items sharing the same timestamp.
- Pagination of the commit, issue and pull request lists. All results fit in one page.
- Squash and rebase merges. Only a merge commit was tested.
- A full history-rewrite run (force-push), and the full compare-404 flow.
- Authenticated requests and private repositories.
- Rate-limit responses (403/429), server errors, timeouts and network failures.
- Default branch names containing `/`, a default branch change, an empty repository, a renamed or transferred repository.
- Report name collision and checkpoint-save failure (local filesystem cases).
- Running from `C:\` (known environment problem, see README).

### 8.9 Conclusion

Against the real GitHub API, the program produced a correct baseline and correctly detected:
- a newly opened issue,
- a direct commit on `main`,
- a newly opened pull request, which did not appear as an issue,
- the same pull request after it was merged, together with both commits the merge added to `main`.

In every case the checkpoint advanced as designed. Every following run with no activity reported "No changes" and wrote nothing. Earlier reports and the CaféNorte checkpoint were never modified. The scenarios in 8.8 remain unverified against real data.
