# MVP 2 — Step 3: External research

Status: implemented; one project, manually triggered cycles. No scheduler, frontend or AI SDK. Claude Code does the research and interpretation by following `.claude/skills/project-research/SKILL.md` (`/project-research`); the code collects evidence, checks drafts and stores results.

Code: `src/organizador/research/` and `src/organizador/recommendations/__main__.py`. Tests: `tests/test_research_*.py`, `tests/test_recommendations_cli.py`. Data: `data/research/` (git-ignored).

| Step | Who | Command |
|---|---|---|
| Start a cycle | code | `research start` |
| Brief: profile freshness, earlier recommendations and feedback | code | `research brief` |
| Questions, searches, screening | Claude Code | (skill) |
| Capture evidence pages | code | `research fetch` |
| Draft 0 to 3 recommendations | Claude Code | (skill) |
| Check, then save | code | `research check`, `research save` |
| Read and evaluate | user | `recommendations list / show / feedback` |

---

## 1. Cycles

One research cycle is one manual run for one project:

```
python -m organizador.research start --data-root data --project-id organizador-personal
```

It creates `data/research/<cycle_id>/cycle.json`, with an ID like `cyc-20261026T223537Z-525179f5`. Cycles are never edited.

---

## 2. Evidence snapshots

```
python -m organizador.research fetch --data-root data --cycle-id <cycle_id> <url> [<url> ...]
```

`fetch` stores the readable text of a page that research chose as evidence. It does not store search results or every page that was looked at. Each snapshot is `data/research/<cycle_id>/snapshots/<snap_id>.json`:

| Field | Meaning |
|---|---|
| `url` | the URL requested (without `#fragment`); `snap_id` is derived from it |
| `final_url` | the URL after redirects |
| `retrieved_at` | UTC time of retrieval |
| `http_status`, `content_type` | always 200 and a text type |
| `content_sha256`, `content_bytes` | hash and size of the bytes received |
| `text`, `text_sha256`, `text_truncated` | the extracted text, its hash, and whether it was cut at the size limit |

Rules:

- Created once and never rewritten. Fetching the same URL again in the cycle returns the stored snapshot without a new request.
- Loading verifies `text_sha256`, so an edited snapshot is reported as invalid.
- Raw HTML is not kept. Scripts, styles, `noscript`, `template`, `svg`, `iframe` and `object` content is dropped. Block elements become line breaks; source line breaks inside a paragraph become spaces, except in `<pre>`.
- Limits: at most 20 snapshots per cycle, 2,000,000 bytes per response and 200,000 characters of text.
- Accepted types: `text/html`, `application/xhtml+xml`, `text/plain`, `text/markdown`. Compressed responses are refused. A page that needs JavaScript to show its content yields little or no text and cannot support verified claims.

---

## 3. Network safety

Each request is a single read-only HTTPS GET, with no cookies and no credentials.

- Only `https://` on port 443, without user names or passwords in the URL.
- The host's addresses are resolved first. All of them must be public: loopback, private, link-local (including cloud metadata addresses such as `169.254.169.254`), shared, reserved, unspecified and multicast addresses are refused, including IPv4 addresses written as IPv6.
- The connection goes to the address that was checked, while the certificate is still verified against the host name. A second DNS answer cannot redirect the request.
- Redirects are followed by hand, at most 5. Each target is checked by the same rules before it is contacted; a redirect to `http://`, another port, a private address or a URL with credentials stops the fetch.

---

## 4. Quotes

A verified statement in a recommendation will carry an exact quote. `quote_problem(snapshot, quote)` checks that the quote appears in the snapshot text:

- The comparison ignores differences in whitespace, curly versus straight quotes, and dashes (Unicode NFKC). It does not ignore wording or capitalization.
- Quotes shorter than 20 characters are refused, because they would match too easily.
- Text from dropped elements, such as scripts, cannot be quoted.

A matching quote shows that the statement can be traced to the page. It does not show that our interpretation of the page is correct; that remains a review question.

---

## 5. Brief

```
python -m organizador.research brief --data-root data --cycle-id <cycle> --project-dir D [--github-repo O/R] [--vault-dir V]
```

Writes `brief.json` in the cycle folder (once) from a fresh evidence index:

- the project's commit, working-tree state and context-note hashes;
- the latest profile and whether it is current (`profile_freshness`: commit, repository, and context note added, removed or changed). The command exits with 1 when there is no current profile;
- every earlier recommendation of the project with its **full** feedback history. Rating and decision are reported separately and never combined; a recommendation without feedback is "not evaluated", not "pending".

---

## 6. Drafts: checks and saving

The draft format is documented at the top of `src/organizador/research/drafts.py`. `check` lists every problem; `save` runs the same checks and stores nothing unless all pass.

| Rule | Check |
|---|---|
| Current profile | the draft's profile is the project's and `profile_freshness` finds no reason it is stale (including uncommitted changes to tracked files, now or when the profile was made) |
| Project context | claims are copied from the profile **by number** (never retyped), with the profile's commit and only the files those claims cite; at least one claim is observed or inferred |
| Verified statements | each has an exact quote (at least 20 characters) found in a snapshot of this cycle, and its URL is one of the recommendation's evidence URLs; every evidence URL has a snapshot; at least one verified statement per recommendation |
| Interpretation | interpretations and assumptions are separate fields; on save the justification gets one labelled line each (`Verified: … \| Quote: "…" \| Source: …`, `Interpretation:`, `Assumption:`) |
| Relationship | starts with `functional:`, `technical:` or `analogical:` |
| References | GitHub references are repository roots; each is used by a recommendation; listed once |
| Possible duplicates | existing recommendations flagged by `overlap_candidates` (shared reference, similar wording, same category with some shared wording) must be explained in `distinct_from` or reconsidered; nothing is rejected only for being flagged. Two proposals in one draft with similar wording must be merged or made distinct |
| Reconsiderations | `reconsiders` names a recommendation of the same project and `new_since` says what is new. What is allowed depends on its **latest decision**, never on its rating: not evaluated or `pending` — not reconsidered; `future` — only when the project context changed after that decision, new evidence alone is not enough; `explore` — not reconsidered, propose a distinct follow-up with `distinct_from` instead; `discard` — with new evidence (a verified URL the original did not use) or context that changed after that decision. **Changed context** means a cited profile claim (same topic, statement and basis) that was not in the profile the user had when deciding: the latest stored profile created at or before the decision. A claim that existed then but was not used by the original is not a change. If no profile from before the decision exists, the earlier context cannot be established and no change is assumed. The saved line is `New since <last evaluation or creation>: …` |
| Budget (upper limits) | at most 8 web searches (standard only; extended needs the user's approval and is rejected here), 30 GitHub searches, 20 snapshots, 3 recommendations |

Zero recommendations is a valid draft. `save` creates the references and recommendations with the storage functions (never editing existing records or feedback), keeps the draft as `draft.json` and a summary as `result.json` in the cycle folder, and refuses a second save of the same cycle. If a save is interrupted after `draft.json` was written, it refuses to run again until the partial result is inspected.

**A matching quote is traceability, not proof.** It shows the statement comes from the page; whether the page supports the interpretation remains a judgment for research and review.

---

## 7. Feedback command line

```
python -m organizador.recommendations list --data-root data --project-id organizador-personal
python -m organizador.recommendations show --data-root data <rec_id>
python -m organizador.recommendations feedback --data-root data <rec_id> --decision pending|future|explore|discard [--rating like|dislike] [--notes "..."]
```

`feedback` appends an evaluation; earlier ones stay. Rating and decision are independent; without `--rating` no rating is recorded.

---

## 8. Limitations

- Only server-rendered text is captured.
- A snapshot shows what the page said at `retrieved_at`; the live page may change later.
- The DNS and address checks run in this process; a proxy configured elsewhere on the machine is not used.
- Duplicate signals are word-based; two proposals that say the same thing in different words may not be flagged, and the research instructions must still compare them.
- Whether a quote supports its statement, whether a source is reliable for it, and whether a recommendation is useful are judgments made by Claude Code and, during validation, by the user.
- GitHub repository metadata from the API (activity, licence) cannot be captured as a snapshot, so it can only appear as interpretation.
- A save that stops halfway (after some recommendations were created) is detected but not rolled back.
