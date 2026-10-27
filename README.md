# Organizador

A small, local-first backend that tracks activity in **one GitHub repository** and writes it as Markdown reports.

Each run reads commits, issues and pull requests from the GitHub REST API (read-only), finds what changed since the last successful run, and writes one new report into a local Markdown vault. A small JSON checkpoint remembers where the last run stopped.

This is MVP 1. Design: [`docs/mvp1-architecture-proposal.md`](docs/mvp1-architecture-proposal.md). Real-API results: [`docs/mvp1-real-validation.md`](docs/mvp1-real-validation.md).

MVP 2 (in progress) adds local storage for project recommendations and personal feedback. Step 1 is storage and validation: [`docs/mvp2-data-storage.md`](docs/mvp2-data-storage.md). Its data lives in `data/`, which is git-ignored. Step 2 builds evidence-linked project profiles with Claude Code: [`docs/mvp2-project-understanding.md`](docs/mvp2-project-understanding.md). Step 3 adds external research with Claude Code (`/project-research`) and a small command line to read recommendations and record feedback: [`docs/mvp2-external-research.md`](docs/mvp2-external-research.md).

---

## What it does

- Tracks one GitHub repository, configured in `config/config.toml`.
- Reads commits on the default branch, issues, and pull requests. It never writes to GitHub.
- Detects changes using a saved checkpoint:
  - **Commits:** everything added to the default branch since the last saved head commit, using GitHub's compare endpoint. Commits merged in from older branches are included, even with old dates.
  - **Issues and pull requests:** everything updated since the last saved timestamp, without missing or repeating items that share the same second.
- Labels each issue or pull request as **opened**, **closed**, **merged** (pull requests only), or **updated**.
- Writes each report as a **new file** in a new local vault. Existing files are never overwritten or edited.

---

## Requirements

- Windows, with Python **3.11 or newer** (`requires-python = ">=3.11"`). The project was built and tested with Python 3.11.0.
- No runtime dependencies beyond the standard library. `pytest` is installed only for running tests.
- Internet access to `https://api.github.com`.

---

## Installation (Windows, PowerShell)

From the project folder:

```powershell
cd "C:\proyectos\Organizador de proyectos"

# 1. Create a virtual environment with Python 3.11
py -3.11 -m venv .venv

# 2. Install the project in editable mode, plus pytest
.venv\Scripts\python -m pip install -e ".[dev]"

# 3. Check that it works
.venv\Scripts\python -m organizador --help
```

The editable install (`-e`) is required. The program finds its project folder, and therefore `config/`, from where its source code lives.

The commands below call `.venv\Scripts\python` directly, so you do not need to activate the environment. If you prefer to activate it (`.venv\Scripts\Activate.ps1`), you can type `python` instead.

---

## Configuration

Copy the example and edit the copy:

```powershell
Copy-Item config\config.example.toml config\config.toml
notepad config\config.toml
```

`config/config.toml` is git-ignored. Only `config.example.toml` belongs in version control.

```toml
# Paths are relative to the project folder.
vault_path = "vault"
state_path = "state"

[github]
owner = "OWNER"
repo = "REPO"
initial_lookback_days = 15
```

| Field | Required | Meaning |
|---|---|---|
| `vault_path` | yes | Folder of this project's own **new** Markdown vault. Reports go to `<vault_path>/reports/`. Do not point it at an existing personal vault. |
| `state_path` | yes | Folder for checkpoints. Must not be inside the vault, and the vault must not be inside it. |
| `github.owner` | yes | Repository owner (user or organization), for example `Gerico90`. |
| `github.repo` | yes | Repository name, for example `cafenorte-data-challenge`. |
| `github.initial_lookback_days` | no | How many days back the **first** run looks. Default **15**. Later runs ignore it. |

Relative paths are resolved against the project folder, not the folder you run the command from.

The program refuses to start, with a clear message, if:
- the file is missing, or is not valid TOML
- a required field is missing or empty
- a value is invalid (for example, an owner name ending in `-`)
- the placeholders `OWNER` or `REPO` were not replaced
- a key is unknown, which usually means a typo
- a key looks like a token

### GitHub token (optional)

**Public repositories work without a token.** Without one, GitHub allows 60 API requests per hour. One run uses roughly 5–10 requests.

A token is needed for private repositories, or to raise the rate limit. The program reads it **only** from the `GITHUB_TOKEN` environment variable. Never put it in `config.toml` or any other file in this project, and never commit it.

Use a fine-grained personal access token limited to the one repository, with **read-only** access to *Metadata*, *Contents*, *Issues* and *Pull requests*.

To set it for the current PowerShell session only, without showing it on screen or saving it in the command history (works in Windows PowerShell 5.1 and PowerShell 7):

```powershell
$secure = Read-Host "GitHub token" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$env:GITHUB_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
Remove-Variable secure, bstr
```

`Read-Host -AsSecureString` shows `*` instead of the characters you type. The variable disappears when you close the window. To remove it earlier:

```powershell
Remove-Item Env:GITHUB_TOKEN
```

The token is sent only to `https://api.github.com`. It is never printed, logged, or written to reports or checkpoints.

---

## Usage

```powershell
cd "C:\proyectos\Organizador de proyectos"
.venv\Scripts\python -m organizador github run
```

Runs are manual. There is no scheduler.

### First run: baseline

With no checkpoint for the repository, the first run:

- Looks back `initial_lookback_days` (default 15) from now.
- Writes a **baseline report** listing commits, issues and pull requests in that window. The report is written **even if there was no activity**; it then says "No activity in the last N days".
- Saves the first checkpoint.

```
Baseline report written: C:\proyectos\Organizador de proyectos\vault\reports\github\<owner>__<repo>\2026-10-26T021927Z.md
  14 commits, 0 pull requests, 0 issues
```

### Later runs: incremental

Every later run starts from the **saved checkpoint**, not from a fixed window. If you do not run it for two months, the next report covers the full two months.

An incremental run **creates a report only when it detects changes**: new commits, new or updated issues or pull requests, or a history rewrite. The report lists only that new activity. The new checkpoint is saved only after the report file has been written.

Only the **first run** always creates a report, even with no activity.

```
Report written: C:\proyectos\Organizador de proyectos\vault\reports\github\<owner>__<repo>\2026-10-27T090000Z.md
  2 commits, 1 pull request, 3 issues
```

### No changes

If an incremental run finds nothing new since the last successful run:

```
No changes since 2026-10-26T02:19:27Z.
```

No report is written, not even an empty one. The checkpoint file is not touched. The exit code is 0.

### Where files go

| What | Location |
|---|---|
| Reports | `vault/reports/github/<owner>__<repo>/YYYY-MM-DDTHHMMSSZ.md` |
| Checkpoint | `state/github/<owner>__<repo>.json` |
| Your own notes (optional) | `vault/notes/`. The program never writes there. |

Report names use the UTC time of the run, so a run in the evening in Mexico (UTC−6) is named with the next day's date. If a name is already taken (for example, two runs in the same second), the program uses `-2`, `-3`, … up to `-99`. It never overwrites a file.

---

## Understanding the output

### Report structure

Every report is a new Markdown file:

1. **Frontmatter** (YAML) with:
   - `source` and `repo`
   - `branch`
   - `period_from`, `period_to` and `generated_at` (UTC)
   - `generated_by`
   - `baseline` (`true` or `false`)
   - `commits_status` (`baseline`, `incremental` or `history_rewritten`)
   - `previous_head` and `head_sha`
2. **Title and period**, for example `# owner/repo — activity 2026-10-18 → 2026-10-25`.
3. A **baseline note** (first run only) or a **history-rewrite warning** (when needed).
4. A **summary line**, for example `2 commits · 2 pull requests (1 merged, 1 opened) · 3 issues (1 opened, 1 closed, 1 updated)`.
5. **Commits on `<branch>`**: short SHA (linked), first line of the message, author, date. Newest first.
6. **Pull requests** and **Issues**: label, number (linked), title, author, and the time of that activity.

Reports keep only titles and metadata. Bodies, comments, diffs and reviews stay on GitHub; every item links to it.

Text from GitHub is treated as untrusted. It is kept on one line, and Markdown or Obsidian syntax characters are escaped with `\`, so a title cannot add headings, links or frontmatter. In the raw file this looks like `aws\_proposal.md`; it displays normally.

Reports are **never edited after they are written**. To comment on one, write a note in `vault/notes/` that links to it.

### Checkpoint (`state/github/<owner>__<repo>.json`)

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

It records where the last successful run stopped:
- `last_head_sha`: the head commit last reported.
- Each cursor: `ts` is the newest update time seen. `ids` lists the items already reported at exactly that second.
- `last_success_at`: the time of the last successful run. The next report starts here.

**Do not edit or delete it casually:**
- **Editing it** can make the next run miss activity, or report it again. The program also refuses to load a file with any invalid or unknown field.
- **Deleting it** makes the next run a new baseline, which covers only the lookback window. Anything between that window and the last run is not reported.
- If the program reports a broken checkpoint, **back up the file first**. Then fix it, or delete it to start a new baseline. The program never resets it by itself.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success: a report was written, or there were no changes. |
| `1` | Failure. A clear message is printed. In every case except the one below, nothing was written and the checkpoint did not move. |
| `2` | Completed with a warning: a history rewrite (see below). The report and checkpoint were written. |

**Exception to exit code 1:** if the report was written but saving the checkpoint failed, the report is kept and the message says so. The checkpoint did not move, so the next run reports the same period again in a new file. Repeated content is possible; overwriting never happens.

Common causes of exit code 1:
- configuration errors
- network errors or timeouts
- GitHub errors: bad token (401/403), not found (404), rate limit (403/429), server error (5xx)
- unexpected API data
- an empty repository
- a changed default branch

### History rewrite warning (exit code 2)

If the default branch was force-pushed or rebased, the old head commit is no longer part of its history. Comparing by date could silently miss rebased commits, so the program does not guess. Instead:

- The report has a **"Warning: history rewritten"** line with the previous and the new head SHA, and `commits_status: "history_rewritten"` in the frontmatter.
- **No commit list** is shown for that run.
- Issues and pull requests are still reported normally.
- The current head becomes the new commit checkpoint, so the next run is normal again.

```
Warning: the history of 'main' was rewritten. Previous head 1a2b3c4 is no longer reachable; commit comparison was skipped. New commit baseline: e4f5a6b.
```

If GitHub answers the comparison with 404, the program treats it as a rewrite **only after confirming** two things: the branch still exists, and the old commit is gone (GitHub answers 404 or 422). If it cannot confirm both, it stops with exit code 1 and does not move the checkpoint.

If the repository's **default branch itself changes** (for example, `master` to `main`), the program stops with exit code 1 and explains how to start a new baseline.

---

## Windows notes

- **Run commands from the project folder** (`C:\proyectos\Organizador de proyectos`), or any folder other than the drive root.
- **Known issue on this machine:** the root of `C:\` contains loose Python 3.12 module files (`_socket.pyd`, `_ctypes.pyd`, …). `python -m` searches the current folder first. So when the command is run **from `C:\`**, Python 3.11 loads those files and fails at startup with:
  ```
  ImportError: Module use of python312.dll conflicts with this version of Python.
  ```
  This is caused by those files in `C:\`, not by Python or this project. It affects any `python -m` command started from `C:\`.
- **Workaround** if you must run from `C:\`: add `-P` (Python 3.11+), which keeps the current folder off the import path:
  ```powershell
  & "C:\proyectos\Organizador de proyectos\.venv\Scripts\python" -P -m organizador github run
  ```
- Console messages are plain ASCII, so they display correctly in the default Windows console encoding. Reports are always written as UTF-8.

---

## Running the tests

```powershell
.venv\Scripts\python -m pytest -q
```

The tests run offline. They use temporary folders and a fake GitHub API, and never touch `vault/`, `state/` or `data/`.

---

## Validation status

**Offline:** 813 tests pass: 310 for MVP 1, 244 for the MVP 2 data storage and its command line ([`docs/mvp2-data-storage.md`](docs/mvp2-data-storage.md)), 111 for project understanding ([`docs/mvp2-project-understanding.md`](docs/mvp2-project-understanding.md)) and 148 for external research ([`docs/mvp2-external-research.md`](docs/mvp2-external-research.md)). The MVP 1 tests cover:
- configuration, file safety, checkpoints, the API client and change detection
- report rendering
- full runs end to end against a fake GitHub, including baselines, incremental runs, ties, long gaps, history rewrites, uncertain 404s, failures on later pages, name collisions, checkpoint-save failures, empty repositories, and protection of human notes

**Against the real GitHub API** (public repositories `Gerico90/cafenorte-data-challenge` and `Gerico90/organizador-personal`, no token; details in [`docs/mvp1-real-validation.md`](docs/mvp1-real-validation.md)):

| Scenario | Result |
|---|---|
| First run (baseline, 45-day lookback set for that validation) | ✅ 14 commits, matching GitHub exactly; checkpoint consistent with the report |
| Second run with no external changes | ✅ "No changes", no new report, checkpoint byte-identical |
| Compare endpoint: pagination (5 per page), `ahead`, `behind`, `identical` | ✅ as designed |
| Missing commit: compare → 404, commit lookup → 422 | ✅ as the rewrite check assumes |
| Incremental run with a real new commit (`organizador-personal`) | ✅ only the new commit reported, previous head not repeated; checkpoint advanced to the new head |
| Incremental run with a real newly opened issue (`organizador-personal`) | ✅ reported as opened; issue cursor advanced; no duplicate on the next run |
| Incremental run with a real newly opened pull request (`organizador-personal`) | ✅ reported as opened, not listed as an issue, no commits on `main`; pull request cursor advanced; no duplicate on the next run |
| Incremental run after merging that pull request with a merge commit (`organizador-personal`) | ✅ reported as merged; exactly the 2 new commits on `main` (branch commit and merge commit), earlier commits not repeated; checkpoint advanced; no duplicate on the next run |

**Not yet validated against the real API:**
- real issues being closed, reopened or updated (only opening has been validated)
- real pull requests closed without merging, updated, or in draft (only opening and merging with a merge commit have been validated)
- squash and rebase merges
- paging of the commit, issue and pull request lists
- a full history-rewrite run
- authenticated or private repositories
- rate limits and server errors
- branch names containing `/`

Most of these are covered only by the offline tests. Full list: [`docs/mvp1-real-validation.md`](docs/mvp1-real-validation.md), section 8.8.

---

## Scope boundaries (MVP 1)

- One repository, read-only. **No GitHub write operations.**
- Manual runs only. **No scheduler** or background service.
- **No database.** State is one JSON file per repository.
- **No frontend** and **no AI agents**.
- **Obsidian is optional.** The vault is plain Markdown; any editor works.
- Report text is taken as-is from GitHub data. Nothing is summarized or inferred.
