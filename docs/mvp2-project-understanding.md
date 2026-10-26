# MVP 2 — Step 2: Project understanding

Status: evidence collection, profile validation and storage, plus the Claude Code instructions that turn evidence into a profile. No AI SDK, external research, recommendations, scheduler or UI. Nothing here changes the analyzed project, its documentation or personal notes.

Code: `src/organizador/understanding/`. Instructions: `.claude/skills/project-understanding/SKILL.md` (run with `/project-understanding` in Claude Code). Tests: `tests/test_understanding_*.py`.

---

## 1. Workflow

| Step | Who | What |
|---|---|---|
| 1. Collect | code | `collect` builds an evidence index of the project (read-only) |
| 2. Interpret | Claude Code | chooses relevant files from the index, reads them, writes a profile draft |
| 3. Check | code | `check` collects the evidence again and validates the draft against it |
| 4. Save | code | `check --save` stores the profile as an immutable record |

The user triggers the run manually. Interpretation needs no human; human review was part of validating this MVP step only.

```
python -m organizador.understanding collect --project-dir D --project-id ID [--github-repo OWNER/REPO] [--vault-dir V] [--out FILE]
python -m organizador.understanding check   --project-dir D --project-id ID [--github-repo OWNER/REPO] [--vault-dir V] --draft FILE [--save --data-root R]
```

Exit codes: 0 = valid, 1 = problems or error. No command reads `config/config.toml`.

---

## 2. Evidence index (regenerated, never stored)

What it reads:

- **Project files:** only files tracked by git (`git ls-files`), read from the working tree. Ignored and untracked files are never indexed. `git` is called with `--no-optional-locks`, so `.git/index` is not rewritten.
- **Authorized outside inputs**, only when `--vault-dir` is given:
  - `<vault>/notes/projects/<project_id>.md`, the optional context note;
  - `<vault>/reports/github/<owner>__<repo>/*.md`, the GitHub activity reports.

  They may be git-ignored. A note or report is skipped, without being read, if it or any folder between the vault root and it is a symlink or a Windows junction. The vault root itself may be given through a link.

What it records per inspectable file: `ref`, kind, bytes, lines, SHA-256, plus:

- Markdown: headings with line numbers, ignoring `#` lines inside fenced code.
- Python: the first paragraph of the module docstring and top-level classes and functions with line numbers (parsed with `ast`, never executed).
- Reports: selected front-matter fields (period, head commit, baseline).

Excluded, with the reason, and their content not indexed:

| Reason | Rule |
|---|---|
| `secret` | file name matches `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.keystore`, `*.jks`, `*.kdbx`, `id_rsa*` and other SSH key names, `.netrc`, `.pypirc`, `.npmrc`, `credentials*`, `*secret*`, `*token*`. **Never opened.** |
| `link` | the tracked file, or any folder between the project root and it, is a symlink or a Windows junction. **Never opened.** (The project root itself may be given through a link.) |
| `deleted` | tracked but missing from the working tree |
| `irrelevant` | empty file, `.gitkeep`, `*.lock`, `package-lock.json`, `*.min.js`, `*.min.css`, `*.map` |
| `too_large` | over 1,000,000 bytes |
| `binary` | contains a NUL byte or is not UTF-8 |

Also recorded: `commit_sha` (`HEAD`), `working_tree_clean` (tracked files only), `collected_at`, and `notes_checked` (where the note was looked for).

**Refs** are the strings profiles cite: the project path (`README.md`), `report:<file name>` or `note:<file name>`.

---

## 3. Profile (`data/profiles/<profile_id>.json`)

```json
{
  "schema_version": 1,
  "id": "prof-20261026T203022Z-286c96d8",
  "created_at": "2026-10-26T20:30:22Z",
  "project_id": "organizador-personal",
  "context": {
    "github_repo": "Gerico90/organizador-personal",
    "commit_sha": "…",
    "analyzed_at": "…",
    "analyzed_files": ["README.md", "report:2026-10-26T170604Z.md"],
    "claims": [
      {"topic": "capability", "statement": "…", "basis": "observed", "sources": ["README.md:15-16"]}
    ]
  },
  "inputs": {"working_tree_clean": true, "reports": ["report:…"], "notes": []}
}
```

- `context` has exactly the shape of a recommendation's `project_context` (`docs/mvp2-data-storage.md`) and is validated by the same code, so a recommendation can copy it unchanged. No existing schema was changed.
- `inputs` is filled from the evidence, not by the draft.
- Profiles are created with exclusive create and never rewritten; a new run makes a new profile. The link and path rules of the data storage apply.

### Claims

| topic | covers |
|---|---|
| `purpose` | what the project is for |
| `capability` | what it does today |
| `architecture` | structure, components, data, dependencies, design decisions |
| `limitation` | known limits, untested areas, scope boundaries |
| `objective` | planned or intended future work |

The basis is `observed` (stated by the source or done by the cited code), `inferred` (a conclusion from the sources) or `missing` (looked for, not found; no sources). A planned feature is an `objective` claim, never a `capability`.

### Checks made by `check`

- Draft fields, claim shape and basis rules: the shared `project_context` rules.
- `project_id`, `commit_sha` and `github_repo` (case-insensitive) match the evidence; `analyzed_at` is present and not in the future.
- `analyzed_files` is not empty; each entry is an inspectable ref, not an excluded one.
- Each source is `<ref>`, `<ref>:<line>` or `<ref>:<start>-<end>`; the ref is in the index and in `analyzed_files`; the lines exist.
- Every topic has at least one claim.

All problems are listed together. Nothing is saved unless the draft is valid.

---

## 4. Limitations

- The checker verifies structure and that cited lines exist, **not** that they support the claim. That depends on the instructions and on review.
- Secret detection is by file name only. A secret inside an ordinary file is not detected, and the instructions forbid copying secrets into statements.
- The index reads the working tree. If tracked files have uncommitted changes, `working_tree_clean` is false and the content may differ from `commit_sha`.
- The checker cannot tell whether every file in `analyzed_files` was actually read.
- Reports and notes are matched by path convention only.
