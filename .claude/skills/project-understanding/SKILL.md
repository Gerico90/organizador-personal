---
name: project-understanding
description: Build an evidence-linked profile of one personal project (purpose, capabilities, architecture, limitations, objectives) from its repository, its GitHub activity reports and an optional context note, then validate and store it under the data root's profiles/ folder (data/ by default). Use when the user asks to profile, understand or analyze a project with this workflow, or runs /project-understanding.
---

# Project understanding

You produce a **project profile**: claims about one project, each tied to the files and lines that support it. You interpret the evidence yourself; no human writes the profile. Work only from files. Do not use anything you remember from earlier conversations about the project, and do not ask the user to fill gaps: record them as `missing`.

Everything is read-only. Never edit the project, its documentation, `vault/notes/`, reports, `state/` or `config/`. The only file you create outside the scratchpad is the saved profile.

## Inputs

Arguments: `<project-id> <project-dir> [owner/repo] [vault-dir]`.

- `project-id`: lowercase, digits and hyphens, e.g. `organizador-personal`.
- `project-dir`: top folder of the project's git repository.
- `owner/repo`: the GitHub repository, if the project has one.
- `vault-dir`: passing it authorizes reading `<vault>/notes/projects/<project-id>.md` and `<vault>/reports/github/<owner>__<repo>/*.md`. Omit it if the user did not authorize notes and reports.

Run all commands from this repository's folder with its virtual environment, e.g. `.venv\Scripts\python -m organizador.understanding ...` on Windows.

### Data root

Profiles are saved under a **data root**, written `<data-root>` below.

- Default: `data/` in this repository (git-ignored), the real personal data.
- If the user gives another data root, for example a scratchpad copy for a test, use that exact path for **every** `--data-root` in this run, and tell the user which one you used. Never mix the real `data/` and a test root in one run.
- The commands already take `--data-root`; nothing else needs configuring.

### Draft review vs. isolated test save

- **Draft review**: stop after step 4 and show the draft. Nothing is saved anywhere, so `/project-research` cannot use it yet.
- **Isolated test save**: the profile is saved for real, but into a copy of the data, so the real `data/` is not touched:
  1. Copy the real data root to a new scratchpad folder, e.g. `<scratchpad>/data-test` (so earlier profiles, recommendations and feedback are there too). Copy, never move.
  2. Run steps 1 to 4 as usual, then step 5 with `--data-root <scratchpad>/data-test`.
  3. The saved profile is immutable like any other. `/project-research` run with the same `--data-root <scratchpad>/data-test` finds it as the latest profile and can use it right away.
  4. Nothing in the test root reaches `data/` unless the user later asks for it to be copied there.

## Steps

### 1. Collect the evidence index

```
python -m organizador.understanding collect --project-dir <dir> --project-id <id> [--github-repo <owner/repo>] [--vault-dir <vault>] --out <scratchpad>/evidence-<id>.json
```

Read the index. Note `commit_sha`, `working_tree_clean`, `collected_at`, the `files`, `excluded`, `reports` and `notes` lists, and `notes_checked`.

### 2. Choose what to read

Use the index to decide; do not read the whole repository. Open **only** entries listed in `files`, `reports` or `notes`. Never open an `excluded` entry or any file that is not in the index (git-ignored files, `.env`, local configuration, `state/`, `data/`).

Read, in this order, stopping when the five topics are covered:

1. The README and the docs whose headings concern purpose, usage, design, scope, limitations, status or plans.
2. The context note, entirely, if `notes` has one.
3. Packaging and example configuration files (they show dependencies and entry points).
4. Source modules: start from each module's `docstring` and `definitions` in the index. Open a module only to confirm a capability or an architectural point, starting with entry points (`__main__`, `cli`).
5. GitHub reports: the front matter in the index gives each period and head commit; open the most recent ones to confirm current activity.
6. Tests: use their names in the index; open one only to confirm that a capability is tested.

Budget: about 20 files and 3,000 lines. Prefer reading the relevant line ranges (use the headings' line numbers) over whole files. Every file you open goes into `analyzed_files`; never list a file you did not open.

### 3. Write the draft

Write `<scratchpad>/profile-draft-<id>.json`:

```json
{
  "project_id": "<id>",
  "context": {
    "github_repo": "<owner/repo or null>",
    "commit_sha": "<commit_sha from the index>",
    "analyzed_at": "<current UTC time, e.g. 2026-10-27T10:15:00Z>",
    "analyzed_files": ["README.md", "report:2026-10-26T170604Z.md"],
    "claims": [
      {"topic": "capability", "statement": "...", "basis": "observed", "sources": ["README.md:13-24"]}
    ]
  }
}
```

**Topics**: every topic needs at least one claim.

| topic | what it covers |
|---|---|
| `purpose` | what the project is for and for whom |
| `capability` | what it does **today** (implemented, not planned) |
| `architecture` | structure, components, data flow, storage, dependencies, key design decisions |
| `limitation` | known limits, unsupported cases, untested areas, scope boundaries |
| `objective` | planned or intended future work |

**Basis**: say how you know.

- `observed`: the cited lines state it directly, or the cited code does it. Paraphrase closely; do not add.
- `inferred`: your conclusion from the cited sources, not stated by them. Write it as a conclusion ("appears to", "suggests") and cite everything it rests on.
- `missing`: you looked and did not find it; no sources. Say what was looked for and say it is "not documented in the inspected sources". Never claim that something does not exist or was never decided: you only know what the files you read say. Example: `"Long-term objectives beyond the current milestone are not documented in the inspected sources."`

**Planned vs. implemented**:
- An `objective` needs explicit evidence of intent: the source says the work is planned, future, in progress or a next step. Use `observed` for it.
- A source saying something is "not implemented", "not yet" available, out of scope or untested is **not** evidence of a plan. Record it as a `limitation`, or leave it out; do not infer a plan from it.
- Planned work is never a `capability`. A capability documented but not visible in the code you read may be `observed` from the documentation; write "documented as" in the statement.
- A source stating that something is **not decided yet** (priorities, timeline, which projects come next) is an observed fact about the current status: record it as `observed` and cite it. What will eventually be decided remains unknown; never infer or suggest it.

**Scope qualifiers**: keep the scope a source gives a statement. A restriction listed under a heading or phrase that limits it ("Scope boundaries (MVP 1)", "for now", "in this step") applies only to that scope. It is not a contradiction or an inconsistency with later phases that do something else, unless a source applies the restriction to them too. Example of an error to avoid: the README's "no AI agents" appears under "Scope boundaries (MVP 1)"; it is not in conflict with MVP 2 work on agents or research.

**Claims are about the project, not about your inputs**:
- Facts about the evidence itself (activity reports that stop before the analyzed commit, a missing context note, files you did not read) are coverage observations. Put them in your final report, not in claims.
- Disagreement between documents (an outdated status line, conflicting numbers) is a documentation inconsistency: state it as such (`limitation`, `inferred`, citing both sides) and do not present it as a software defect.
- An issue that the sources attribute to the environment rather than the project (a specific machine, a local setup) is stated as an environment issue.

**Sources**: `"<ref>"`, `"<ref>:<line>"` or `"<ref>:<start>-<end>"`, using the `ref` from the index (project path, `report:<file>`, `note:<file>`). Cite the narrowest range that supports the claim, ideally under 40 lines. A source must also appear in `analyzed_files`.

**Statements**: one fact or conclusion per claim, in English, plain and specific. Do not copy secrets, tokens, personal data or long passages. Aim for roughly 15 to 30 claims. If the working tree is not clean, do not make that a claim: mention it in your final report (the saved profile records it in `inputs.working_tree_clean`).

### 4. Check, then self-review

```
python -m organizador.understanding check --project-dir <dir> --project-id <id> [--github-repo <owner/repo>] [--vault-dir <vault>] --draft <scratchpad>/profile-draft-<id>.json
```

Fix every problem it reports and run it again until it says the draft is valid. The checker only verifies structure and that cited lines exist. Then reopen each cited range and confirm it supports its claim; narrow the range, downgrade `observed` to `inferred`, or remove the claim when it does not.

### 5. Save

Unless the user asked for a draft only, run the same `check` command with `--save --data-root <data-root>`. Saved profiles are immutable; a later run creates a new profile. Never edit or replace a saved profile, including one in a test root: if it is wrong, save a corrected new one.

### 6. Report

Tell the user: the data root used, the saved profile path (or the draft path), the commit analyzed and whether the working tree was clean, the number of claims per topic and per basis, the files read, whether a context note and reports were used, coverage observations (for example reports that end before the analyzed commit), and the most important `missing` items.
