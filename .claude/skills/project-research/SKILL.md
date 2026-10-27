---
name: project-research
description: Run one manual external-research cycle for one personal project. Uses the current project profile, its optional context note and earlier recommendations with their feedback; searches for external projects, tools, documentation and approaches; stores evidence snapshots; and saves 0 to 3 evidence-backed recommendations. Use when the user asks to research ideas or recommendations for a project with this workflow, or runs /project-research.
---

# Project research

You run one research cycle for one project and propose **0 to 3** recommendations that could improve it. Zero is a valid result: never force a recommendation. You interpret the evidence yourself. Work only from the stored data, the files named here and the pages you fetch; do not rely on memory of earlier conversations.

Never edit the project, its documentation, `vault/notes/`, profiles, existing recommendations, references or feedback. You only create a cycle, its snapshots and draft, and (when saving) new references and recommendations.

## Inputs

Arguments: `<project-id> <project-dir> [owner/repo] [vault-dir]`, as in `/project-understanding`. Run commands from this repository with its virtual environment (`.venv\Scripts\python -m …` on Windows). Below, `PROJECT` stands for `--project-dir <dir> [--github-repo <owner/repo>] [--vault-dir <vault>]`.

**Data root**, written `<data-root>` below: `data/` in this repository by default (the real personal data). If the user gives another data root, such as a scratchpad copy for a test, use that exact path for **every** `--data-root` in the cycle (start, brief, fetch, check, save and the feedback commands) and read the brief, profile, snapshots and results from it. The profile must be in the same data root. Never mix the real `data/` and a test root in one cycle, and say in your report which root you used.

If the user asks for a review before saving, stop after step 8 and present the draft.

## Budget (upper limits, not targets)

- Web searches: at most 8, **standard mode only**. Extended search needs the user's explicit approval in this conversation; without it, do not use it.
- GitHub search API (`https://api.github.com/search/...`, no token): at most 30 requests; the unauthenticated limit is 10 per minute.
- Evidence snapshots: at most 20 (the tool enforces it). Fetch only pages you expect to cite.
- Recommendations: at most 3.

Stop searching when the questions are answered or when more searching is unlikely to help.

## Steps

### 1. Start the cycle and read the brief

```
python -m organizador.research start --data-root <data-root> --project-id <id>
python -m organizador.research brief --data-root <data-root> --cycle-id <cycle> PROJECT
```

If `brief` reports no profile or a profile that is not current (new commit, or a context note added, removed or changed), stop and tell the user to run `/project-understanding` first, saving the new profile into the same data root (for a test root, see that skill's isolated test save).

Read `<data-root>/research/<cycle>/brief.json`, the profile `<data-root>/profiles/<profile_id>.json` and, if the brief lists one, the context note. Number the profile's claims from 1 in their stored order; drafts cite them by number.

### 2. Decide what to look for

Write 0 to 5 research questions, each tied to profile claims (limitations, objectives, capabilities that could improve, or gaps marked missing). Include functional, technical and **analogical** angles: ideas from other domains can count, not only fixes. Use the context note's goals, constraints and "not wanted for now" items to steer; do not propose what the note rules out unless you say why it is still worth knowing.

Read every earlier recommendation in the brief with its whole feedback history. Ratings and decisions are independent: a dislike does not mean discard, and a like does not mean explore. Respect them:

| Latest decision | What you may do |
|---|---|
| none yet (not evaluated) | do not repeat or restate it |
| `pending` | do not repeat it |
| `future` | do not repeat it; reconsider only if the context changed (for example the note now makes it a priority) |
| `explore` | do not repeat it; a follow-up with a clearly different proposal is fine |
| `discard` | do not repeat it; reconsider only with meaningful new evidence or context |

Read the notes on each evaluation and follow them.

### 3. Search and screen

Use `WebSearch` (standard) and the GitHub search API. `WebFetch` may help you explore, but its text is processed by a model and is never evidence. Record every search in the log, and every candidate you looked at and dropped in `log.rejected` with a short reason.

Judge sources by their nature and by the claim they support:

- Official documentation and specifications: good for what a product does, even without a publication date; name it "(official documentation, undated)" when undated.
- Repositories: the README and code show what exists. For maintenance, the GitHub API (`/repos/<owner>/<repo>`: `pushed_at`, `archived`, `license`) helps screening; these facts are not snapshot evidence, so mention them only as interpretation, with the date.
- Articles with an author and date: good for approaches and experience; opinion, not proof.
- Vendor pages: label them as such; never the only support for a claim.
- Issues and forums: signals only.

Label each source in its `name`, for example `"Hypothesis documentation (official documentation, undated)"`.

### 4. Capture evidence

```
python -m organizador.research fetch --data-root <data-root> --cycle-id <cycle> <url> [<url> ...]
```

Only HTTPS public pages with server-rendered text work; for GitHub repositories, fetch the raw README (`https://raw.githubusercontent.com/<owner>/<repo>/<branch>/README.md`). If a page cannot be captured, you may still mention it as interpretation, but not as verified.

### 5. Separate what sources say from what you think

For each recommendation:

- `verified`: what a source **says**, each with an exact `quote` copied from the snapshot text (`<data-root>/research/<cycle>/snapshots/<snap>.json`, field `text`; at least 20 characters) and its `url` (also listed in that source's `evidence_urls`). The `statement` paraphrases the quote and adds nothing. Read the text around the quote: a quote out of context is not support.
- `interpretations`: your conclusions, including why it fits the project.
- `assumptions`: what you take for granted without evidence (effort, compatibility, ...).

A quote that matches only shows where a statement came from; it does not make your interpretation right.

### 6. Relate it to the project

- `relationship` starts with `functional:` (solves the same problem), `technical:` (a technique or tool that applies) or `analogical:` (an idea from another domain that transfers), then explains the link.
- `profile_claims` lists the claim numbers that motivate it; at least one must be observed or inferred.
- Benefits, limitations and tradeoffs must be specific, including costs such as new dependencies (the project currently uses only the standard library at runtime) or effort.

### 7. Write the draft

Write `<scratchpad>/research-draft-<cycle>.json` in the format documented at the top of `src/organizador/research/drafts.py`: `cycle_id`, `project_id`, `profile_id` (from the brief), `log`, `references` (one per external project or repository you recommend; GitHub references must be repository roots) and `recommendations`.

A standalone page that is not a project or repository you recommend (a documentation page, a policy, an article, a standard) needs **no reference**: cite it through the recommendation's `sources` (`name`, `url`) and `evidence_urls`, and leave `reference_urls` empty unless the recommendation is about adopting or studying an external project. A recommendation needs at least one external source: a reference, or a source with a URL. Optional per recommendation: `interpretations`, `assumptions`, `distinct_from`, `reconsiders`, `new_since`.

### 8. Check

```
python -m organizador.research check --data-root <data-root> --draft <draft> PROJECT
```

Fix every problem and run it again. When it reports that a proposal **may duplicate** an earlier recommendation, decide honestly:

- different proposal (even about the same repository): add `distinct_from` with what differs;
- the same idea with meaningful new evidence or context: set `reconsiders` and `new_since`;
- otherwise: drop it.

Then reread each recommendation: would the user learn something useful and know what to do next? If not, drop it.

### 9. Save

Unless the user asked for review first:

```
python -m organizador.research save --data-root <data-root> --draft <draft> PROJECT
```

A cycle is saved once. Saving creates the references and recommendations and keeps the draft and a result summary in the cycle folder.

### 10. Report

Tell the user: the data root used, the cycle ID, the profile used, the questions, how many searches and snapshots were used, the main rejected candidates and why, and each recommendation (title, relationship, verified evidence, interpretation, limitations), or why there were none. Mention how to give feedback:

```
python -m organizador.recommendations show --data-root <data-root> <rec_id>
python -m organizador.recommendations feedback --data-root <data-root> <rec_id> --decision pending|future|explore|discard [--rating like|dislike] [--notes "..."]
```
