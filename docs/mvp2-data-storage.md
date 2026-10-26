# MVP 2 — Step 1: Local data storage

Status: storage, validation and offline tests only. There is no agent, research workflow, scheduler, CLI or UI yet. Nothing in this step reads or changes MVP 1 configuration, checkpoints or reports.

Code: `src/organizador/recommendations/`. Tests: `tests/test_recommendations_*.py`.

---

## 1. Folders and ownership

The caller passes the data root to every function; it is not read from `config/config.toml`. The intended location is `data/` in the project folder, which is git-ignored.

```text
data/
├── references/        <ref_id>.json        written by research (future agent)
├── recommendations/   <rec_id>.json        written by research (future agent), never changed
└── feedback/          <rec_id>.jsonl       written by the user only, append-only
```

| Data | Owner | Can change after creation? |
|---|---|---|
| Reference | research | only `last_reviewed_at` |
| Recommendation | research | never; a reconsideration is a new record |
| Feedback | user | never; a change of opinion is a new line |

The storage has no function that edits or deletes a recommendation or a feedback line. All files are UTF-8 with `\n` line endings. All timestamps are UTC, whole seconds, like `2026-10-26T17:06:04Z`.

---

## 2. Identifiers

| ID | Format | How it is made |
|---|---|---|
| Project | `organizador-personal` (lowercase letters, digits, single hyphens; up to 64 characters) | chosen by the user; does not need a GitHub repository |
| Reference | `ref-` + 16 hex characters | first 16 hex characters of SHA-256 of the canonical URL |
| Recommendation | `rec-20261026T170604Z-a1b2c3d4` | creation time (UTC) + 8 random hex characters |

File names are built only from validated IDs, and each path is checked to stay inside its folder.

### Canonical reference URLs

The same external project must give the same reference ID.

- **GitHub:** only a repository root is accepted, `https://github.com/<owner>/<repo>`. Owner and name are lowercased (GitHub treats them case-insensitively); `http`, `www.`, a trailing `/`, `.git`, `:443` and `#fragment` are removed. Links to files, issues or commits are rejected here: they belong in a recommendation's `evidence_urls`.
- **Other sites:** scheme and host lowercased, default port, `#fragment` and trailing `/` removed. The path and query are kept as given, so two spellings of the same page can still produce two references.

---

## 3. Schemas (`schema_version: 1`)

Every record has exactly the fields shown. A missing or unknown field is an error.

### Reference

```json
{
  "schema_version": 1,
  "id": "ref-…",
  "name": "Display name",
  "canonical_url": "https://github.com/owner/repo",
  "description": "Text, or null when none is available",
  "first_discovered_at": "2026-10-26T17:00:00Z",
  "last_reviewed_at": "2026-10-26T17:00:00Z"
}
```

`get_or_create_reference` returns the stored record unchanged if it already exists. When the given `name` or `description` differ from the stored ones, they are listed in `differences`; the file is not rewritten.

### Recommendation

```json
{
  "schema_version": 1,
  "id": "rec-…",
  "created_at": "2026-10-26T17:00:00Z",
  "project_id": "organizador-personal",
  "category": "free text",
  "title": "…",
  "proposal": "the improvement or alternative",
  "justification": "…",
  "benefits": ["…"],
  "limitations": ["…"],
  "relationship": "free text: how it relates to the target project",
  "reference_ids": ["ref-…"],
  "sources": [
    {"name": "…", "url": "https://… or null", "evidence_urls": ["https://…"]}
  ],
  "project_context": {
    "github_repo": "owner/repo or null",
    "commit_sha": "40 or 64 lowercase hex, or null",
    "analyzed_at": "timestamp or null",
    "analyzed_files": ["README.md"],
    "claims": [
      {"topic": "objective", "statement": "…", "basis": "observed", "sources": ["README.md"]},
      {"topic": "architecture", "statement": "…", "basis": "inferred", "sources": ["src/app.py"]},
      {"topic": "capability", "statement": "No deployment documentation was found", "basis": "missing", "sources": []}
    ]
  },
  "reconsiders": "rec-… or null"
}
```

Rules:
- **At least one external source is required:** an ID in `reference_ids`, or an entry in `sources` with a `name` and a `url`. A source with `"url": null` (for example a personal note) is allowed, but it never counts as that evidence, even if it lists `evidence_urls`. A recommendation without external evidence is rejected when it is created, and reported as invalid if found on disk.
- Every ID in `reference_ids` must exist and be valid when the recommendation is created; no duplicates.
- `reconsiders`, when set, must name an existing recommendation of the same project. The original is not changed.
- Facts in `project_context` are plain fields: `null` or an empty list when unknown. `analyzed_at` cannot be later than `created_at`.
- Claims about the project carry a `basis`:
  - `observed`: read directly from a source; at least one source.
  - `inferred`: concluded from sources; at least one source.
  - `missing`: looked for and not found; no sources.
- `topic`, `category` and `relationship` are free text, not a fixed list.
- `benefits`, `limitations`, `analyzed_files` and `claims` may be empty lists.

### Feedback (one JSON object per line)

```json
{"schema_version": 1, "recommendation_id": "rec-…", "evaluated_at": "2026-10-26T18:00:00Z", "rating": "like", "decision": "explore", "notes": null}
```

- `rating`: `"like"`, `"dislike"` or `null` (no rating).
- `decision`: `"pending"`, `"future"`, `"explore"` or `"discard"`.
- `notes`: text or `null`.
- The recommendation must exist. A new line cannot be dated before the last one; equal times keep file order.
- With no file (or an empty file) there is no evaluation, and `latest_feedback` returns `None`.

---

## 4. Operations

| Need | Function |
|---|---|
| Create or retrieve a reference | `references.get_or_create_reference(root, name=, url=, description=)` |
| Read a reference | `references.load_reference(root, ref_id)` |
| Record a new review date | `references.mark_reference_reviewed(root, ref_id)` |
| Create a recommendation | `records.create_recommendation(root, draft)` |
| Read one / list by project | `records.load_recommendation(root, rec_id)`, `records.list_recommendations(root, project_id)` |
| Add an evaluation | `feedback.append_feedback(root, rec_id, rating=, decision=, notes=)` |
| History / latest evaluation | `feedback.feedback_history(root, rec_id)`, `feedback.latest_feedback(root, rec_id)` |
| Check everything | `check.check_data(root)` → list of problems |

Errors: `DataError` (invalid input or file), with subclasses `NotFoundError` (missing record) and `ConflictError` (would overwrite or contradict existing data). Filesystem failures are raised as `OSError`.

---

## 5. Safety rules

- Recommendations and references are created with exclusive create (`open(…, "x")`); an existing file is never opened for writing. A failed write removes the partial file.
- The only rewrite is `last_reviewed_at`, done atomically with `fsutil.atomic_write_text`; on failure the old file stays.
- Feedback is appended in binary mode and flushed to disk. If the write fails, only the partial new line is cut off.
- Links are refused. If `references/`, `recommendations/` or `feedback/` is a symlink or a Windows junction, or a record file is a symlink (even a broken one), the operation raises `DataError` before reading or writing, and `check_data` reports it without following it. The data root itself may be given through a link; it is resolved once.
- Reading is strict. Malformed JSON, invalid values, a file name that does not match its ID, an empty line, a missing final newline (interrupted write) or out-of-order feedback raise `DataError`. Nothing is skipped or repaired.
- `check_data` reports every problem across the three folders (invalid files, unexpected files such as leftover temporary files, missing references, missing or cross-project reconsiderations, feedback for a missing recommendation) and changes nothing.

---

## 6. Limitations

- No protection against two processes writing at the same time (single user, manual use).
- The link check runs just before each read or write. A link created in the short moment between the check and the file access is not detected (same single-user assumption). Hard links are not detected.
- Listing reads every recommendation file; fine for hundreds, not for very large numbers.
- Non-GitHub URL normalization is deliberately light (see section 2).
- `analyzed_files` and claim `sources` are free text; they are not checked against the project.
- No CLI yet: the functions are only called from tests.
- The external-source rule checks that a reference or a URL exists, not that the URL is reachable or supports the claim. A reference that is deleted or corrupted after the recommendation was created is reported by `check_data`, not prevented.
