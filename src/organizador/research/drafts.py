"""Research drafts: check one cycle's findings, then save them as recommendations.

A draft (written by Claude Code, see the /project-research skill) holds the
cycle's log, the external references to create, and 0 to
MAX_RECOMMENDATIONS proposals. Zero proposals is a valid result.

    {
      "cycle_id": "cyc-…", "project_id": "…", "profile_id": "prof-…",
      "log": {"questions": [{"question": "…", "profile_claims": [4]}],
              "searches": [{"tool": "web_search", "query": "…"}],
              "rejected": [{"url": "https://…", "reason": "…"}]},
      "references": [{"name": "…", "url": "https://…", "description": "…" or null}],
      "recommendations": [{
        "category": "…", "title": "…", "proposal": "…",
        "benefits": ["…"], "limitations": ["…"],
        "relationship": "functional: …" | "technical: …" | "analogical: …",
        "profile_claims": [4],                 1-based claim numbers in the profile
        "reference_urls": ["https://…"],
        "sources": [{"name": "…", "url": "https://…" or null, "evidence_urls": ["https://…"]}],
        "verified": [{"statement": "…", "quote": "…", "url": "https://…"}],
        "interpretations": ["…"], "assumptions": ["…"],
        "distinct_from": [{"id": "rec-…", "reason": "…"}],
        "reconsiders": "rec-…" or null, "new_since": "…" or null
      }]
    }

Checks, on top of the storage rules of recommendations:

- the profile is current for the project (profile_freshness);
- project-context claims are copied from the profile by number, never retyped;
  at least one of them is not a "missing" claim;
- every verified statement has an exact quote found in a snapshot of this
  cycle, and its URL is one of the recommendation's evidence URLs; every
  evidence URL has a snapshot. A found quote makes a statement traceable;
  it does not prove the interpretation;
- the relationship starts with functional:, technical: or analogical:;
- every existing recommendation flagged by overlap_candidates is either
  reconsidered or explained in distinct_from; flagged cases are never
  rejected automatically;
- a reconsideration names new evidence (a verified URL the original did not
  use) or new context (a profile claim the original did not use), and says
  what is new in new_since;
- search budget: at most MAX_WEB_SEARCHES web searches, all standard.

On save, the justification is written from the structured fields, one
labelled line each: "Verified: <statement> | Quote: "<quote>" | Source: <url>",
"Interpretation: …", "Assumption: …", "Distinct from <id>: …",
"New since <date>: …". The draft and a result summary are kept in the cycle
folder. Existing recommendations, references and feedback are never changed.
"""

import re
from datetime import datetime
from pathlib import Path

from organizador.recommendations import common
from organizador.recommendations.common import SCHEMA_VERSION, ConflictError, DataError, NotFoundError
from organizador.recommendations.feedback import feedback_history
from organizador.recommendations.records import (
    check_recommendation_draft,
    create_recommendation,
    list_recommendations,
    load_recommendation,
)
from organizador.recommendations.references import (
    canonical_url,
    get_or_create_reference,
    load_reference,
    reference_id_for,
)
from organizador.research.brief import overlap_candidates
from organizador.research.snapshots import list_snapshots, load_cycle, quote_problem
from organizador.understanding.profile import list_profiles, load_profile, profile_freshness

MAX_RECOMMENDATIONS = 3
MAX_WEB_SEARCHES = 8
MAX_GITHUB_SEARCHES = 30
RELATIONSHIP_TYPES = ("functional", "technical", "analogical")
SEARCH_TOOLS = ("web_search", "github_search")

_DRAFT_KEYS = ("cycle_id", "project_id", "profile_id", "log", "references", "recommendations")
_LOG_KEYS = ("questions", "searches", "rejected")
_REFERENCE_KEYS = ("name", "url")
_REC_KEYS = (
    "category", "title", "proposal", "benefits", "limitations", "relationship",
    "profile_claims", "reference_urls", "sources", "verified",
)
_REC_OPTIONAL = ("interpretations", "assumptions", "distinct_from", "reconsiders", "new_since")
_LINE_RANGE_RE = re.compile(r":\d+(?:-\d+)?$")


def check_research_draft(root: Path, draft: dict, evidence: dict, now: datetime | None = None) -> list[str]:
    """Every problem found in `draft`. Empty means it can be saved."""
    try:
        return _check(root, draft, evidence, now)[0]
    except (ValueError, DataError) as e:
        return [str(e)]


def save_research_draft(root: Path, draft: dict, evidence: dict, now: datetime | None = None) -> dict:
    """Check `draft` and save it: references, recommendations, and the cycle result."""
    if not isinstance(draft, dict) or not isinstance(draft.get("cycle_id"), str):
        raise DataError("Invalid research draft: it must be an object with a cycle_id")
    load_cycle(root, draft["cycle_id"])
    cycle_dir = common.record_path(root, "research", draft["cycle_id"], "")
    draft_path, result_path = cycle_dir / "draft.json", cycle_dir / "result.json"
    if result_path.exists():
        raise ConflictError(f"Cycle {draft['cycle_id']} was already saved ({result_path})")
    if draft_path.exists():
        raise ConflictError(
            f"Cycle {draft['cycle_id']} has a saved draft but no result: a previous save was interrupted. "
            "Inspect the recommendations created at that time before trying again."
        )

    try:
        problems, built = _check(root, draft, evidence, now)
    except ValueError as e:
        problems, built = [str(e)], []
    if problems:
        raise DataError("Invalid research draft:\n- " + "\n- ".join(problems))
    common.create_new_file(draft_path, common.dump_json(draft))

    references = []
    for ref in draft["references"]:
        outcome = get_or_create_reference(
            root, name=ref["name"], url=ref["url"], description=ref.get("description"), now=now
        )
        references.append(
            {"id": outcome.reference.id, "created": outcome.created, "differences": list(outcome.differences)}
        )
    created = [create_recommendation(root, rec, now=now).id for rec in built]

    result = {
        "schema_version": SCHEMA_VERSION,
        "cycle_id": draft["cycle_id"],
        "project_id": draft["project_id"],
        "profile_id": draft["profile_id"],
        "saved_at": common.utc_now(now),
        "recommendations": created,
        "references": references,
        "searches": len(draft["log"]["searches"]),
        "rejected_candidates": len(draft["log"]["rejected"]),
    }
    common.create_new_file(result_path, common.dump_json(result))
    return result


# --- checking --------------------------------------------------------------


def _check(root, draft, evidence, now):
    """(problems, recommendations in storage format). Structural errors raise ValueError."""
    common.check_keys(draft, _DRAFT_KEYS, ())
    cycle = load_cycle(root, draft["cycle_id"])
    problems = []
    if draft["project_id"] != cycle["project_id"]:
        problems.append(f"project_id {draft['project_id']!r} does not match the cycle ({cycle['project_id']!r})")
    if evidence["project_id"] != cycle["project_id"]:
        problems.append("the evidence index is for another project")

    profile = load_profile(root, draft["profile_id"])
    if profile.project_id != cycle["project_id"]:
        problems.append(f"profile {profile.id} belongs to project {profile.project_id!r}")
    for reason in profile_freshness(profile, evidence):
        problems.append(f"profile {profile.id} is not current: {reason}")

    problems += _check_log(draft["log"])

    snapshots = {}
    for snapshot in list_snapshots(root, draft["cycle_id"]):
        snapshots[snapshot.url] = snapshot
        snapshots.setdefault(snapshot.final_url, snapshot)

    draft_refs = _draft_references(draft["references"], problems)

    recs = draft["recommendations"]
    if not isinstance(recs, list):
        raise ValueError("recommendations must be a list")
    if len(recs) > MAX_RECOMMENDATIONS:
        problems.append(f"at most {MAX_RECOMMENDATIONS} recommendations per cycle, got {len(recs)}")

    existing = [
        {
            "id": r.id,
            "category": r.category,
            "title": r.title,
            "proposal": r.proposal,
            "reference_ids": list(r.reference_ids),
        }
        for r in list_recommendations(root, cycle["project_id"])
    ]

    built = []
    for i, rec in enumerate(recs):
        where = f"recommendations[{i}]"
        try:
            rec_problems, storage = _check_recommendation(
                root, rec, where, profile, snapshots, draft_refs, existing, cycle["project_id"], now
            )
        except ValueError as e:
            problems.append(f"{where}: {e}")
            continue
        problems += rec_problems
        if not rec_problems:
            built.append(storage)

    # Within one draft: every reference is used, and no two proposals say the same.
    used = {r for b in built for r in b["reference_ids"]}
    for canonical, ref_id in draft_refs.items():
        if built and len(built) == len(recs) and ref_id not in used:
            problems.append(f"reference {canonical} is not used by any recommendation")
    for i, rec in enumerate(built):
        earlier = [dict(b, id=f"recommendations[{j}]") for j, b in enumerate(built[:i])]
        for candidate in overlap_candidates(rec, earlier):
            if any(r.startswith("similar wording") for r in candidate["reasons"]):
                problems.append(f"recommendations[{i}] and {candidate['id']} look alike; merge them or make them distinct")
    if not recs and draft_refs:
        problems.append("references are listed but there are no recommendations")
    return problems, built


def _check_log(log) -> list[str]:
    common.check_keys(log, _LOG_KEYS, (), "log.")
    problems = []
    for name in _LOG_KEYS:
        if not isinstance(log[name], list):
            raise ValueError(f"log.{name} must be a list")
    for i, q in enumerate(log["questions"]):
        common.check_keys(q, ("question", "profile_claims"), (), f"log.questions[{i}].")
        common.text(q["question"], f"log.questions[{i}].question")
    counts = {tool: 0 for tool in SEARCH_TOOLS}
    for i, s in enumerate(log["searches"]):
        common.check_keys(s, ("tool", "query"), (), f"log.searches[{i}].")
        common.text(s["query"], f"log.searches[{i}].query")
        if s["tool"] not in SEARCH_TOOLS:
            problems.append(
                f"log.searches[{i}].tool must be one of {', '.join(SEARCH_TOOLS)} "
                f"(extended web search needs the user's approval and is not supported here), got {s['tool']!r}"
            )
            continue
        counts[s["tool"]] += 1
    if counts["web_search"] > MAX_WEB_SEARCHES:
        problems.append(f"at most {MAX_WEB_SEARCHES} web searches per cycle, the log lists {counts['web_search']}")
    if counts["github_search"] > MAX_GITHUB_SEARCHES:
        problems.append(f"at most {MAX_GITHUB_SEARCHES} GitHub searches per cycle, the log lists {counts['github_search']}")
    for i, r in enumerate(log["rejected"]):
        common.check_keys(r, ("url", "reason"), (), f"log.rejected[{i}].")
        common.url(r["url"], f"log.rejected[{i}].url")
        common.text(r["reason"], f"log.rejected[{i}].reason")
    return problems


def _reconsideration_problem(decision: str | None, new_evidence: bool, new_context: bool) -> str | None:
    """Why the latest decision does not allow this reconsideration, or None.

    Follows the /project-research rules: an unevaluated or pending
    recommendation is not reconsidered; "future" needs changed project
    context (new evidence alone is not enough); "explore" takes distinct
    follow-ups, not reconsiderations; "discard" needs new evidence or context.
    """
    if decision is None:
        return "has not been evaluated yet; it cannot be reconsidered or restated"
    if decision == "pending":
        return "is pending evaluation; it cannot be reconsidered or restated"
    if decision == "future":
        if not new_context:
            return (
                "was marked 'future'; reconsider it only when the project context changed after that "
                "decision (a cited profile claim that was not in the profile at the time), not only "
                "because of new evidence or a claim it did not use"
            )
        return None
    if decision == "explore":
        return (
            "was marked 'explore'; propose a clearly distinct follow-up (with distinct_from) "
            "instead of reconsidering it"
        )
    if not new_evidence and not new_context:  # discard
        return (
            "was discarded; reconsidering it needs new evidence (a verified URL it did not use) "
            "or project context that changed after that decision"
        )
    return None


def _context_changed_since(root: Path, project_id: str, decided_at: str, chosen) -> bool:
    """True if a cited profile claim was not in the profile the user had when deciding.

    That profile is the latest one created at or before `decided_at`; profiles
    are immutable, so it shows exactly what was known then. A claim counts as
    the same when its topic, statement and basis are unchanged. Without such a
    profile, the earlier context cannot be established, and no change is assumed.
    """
    decided = common.parse_ts(decided_at)
    earlier = [p for p in list_profiles(root, project_id) if common.parse_ts(p.created_at) <= decided]
    if not earlier:
        return False
    known = {(c.topic, c.statement, c.basis) for c in earlier[-1].context.claims}
    return any((c.topic, c.statement, c.basis) not in known for c in chosen)


def _draft_references(refs, problems) -> dict[str, str]:
    """canonical URL -> reference ID for the references this draft would create."""
    if not isinstance(refs, list):
        raise ValueError("references must be a list")
    found = {}
    for i, ref in enumerate(refs):
        common.check_keys(ref, _REFERENCE_KEYS, ("description",), f"references[{i}].")
        try:
            common.text(ref["name"], f"references[{i}].name")
            common.optional_text(ref.get("description"), f"references[{i}].description")
            canonical = canonical_url(ref["url"])
        except ValueError as e:
            problems.append(f"references[{i}]: {e}")
            continue
        if canonical in found:
            problems.append(f"references[{i}]: {canonical} is listed twice")
        found[canonical] = reference_id_for(canonical)
    return found


def _check_recommendation(root, rec, where, profile, snapshots, draft_refs, existing, project_id, now):
    common.check_keys(rec, _REC_KEYS, _REC_OPTIONAL, f"{where}.")
    problems = []
    interpretations = common.text_list(rec.get("interpretations", []), f"{where}.interpretations")
    assumptions = common.text_list(rec.get("assumptions", []), f"{where}.assumptions")

    relationship = common.text(rec["relationship"], f"{where}.relationship")
    kind, sep, rest = relationship.partition(":")
    if not sep or kind not in RELATIONSHIP_TYPES or not rest.strip():
        problems.append(f"{where}.relationship must start with one of {', '.join(t + ':' for t in RELATIONSHIP_TYPES)}")

    # Profile claims, copied by number.
    numbers = rec["profile_claims"]
    claims = profile.context.claims
    if not isinstance(numbers, list) or not numbers:
        raise ValueError("profile_claims must be a non-empty list of claim numbers")
    if any(not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= len(claims) for n in numbers):
        raise ValueError(f"profile_claims must be numbers from 1 to {len(claims)}")
    if len(set(numbers)) != len(numbers):
        raise ValueError("profile_claims contains duplicates")
    chosen = [claims[n - 1] for n in numbers]
    if all(c.basis == "missing" for c in chosen):
        problems.append(f"{where}: rests only on 'missing' profile claims; cite at least one observed or inferred claim")

    # References: from this draft or already stored.
    reference_ids = []
    urls = rec["reference_urls"]
    if not isinstance(urls, list):
        raise ValueError("reference_urls must be a list")
    for url in urls:
        try:
            canonical = canonical_url(url)
        except ValueError as e:
            problems.append(f"{where}.reference_urls: {e}")
            continue
        ref_id = reference_id_for(canonical)
        if canonical not in draft_refs:
            try:
                load_reference(root, ref_id)
            except NotFoundError:
                problems.append(f"{where}.reference_urls: {canonical} is neither in this draft's references nor stored")
                continue
        reference_ids.append(ref_id)

    # Evidence: every evidence URL has a snapshot; every verified quote is in one.
    sources = rec["sources"]
    if not isinstance(sources, list):
        raise ValueError("sources must be a list")
    evidence_urls = set()
    for j, source in enumerate(sources):
        for url in (source.get("evidence_urls") or []) if isinstance(source, dict) else []:
            evidence_urls.add(url)
            if url not in snapshots:
                problems.append(f"{where}.sources[{j}]: evidence URL {url} has no snapshot in this cycle")

    verified = rec["verified"]
    if not isinstance(verified, list) or not verified:
        problems.append(f"{where}: needs at least one verified statement with a quote from a snapshot")
        verified = verified if isinstance(verified, list) else []
    verified_urls = set()
    for j, item in enumerate(verified):
        common.check_keys(item, ("statement", "quote", "url"), (), f"{where}.verified[{j}].")
        common.text(item["statement"], f"{where}.verified[{j}].statement")
        url = item["url"]
        verified_urls.add(url)
        snapshot = snapshots.get(url)
        if snapshot is None:
            problems.append(f"{where}.verified[{j}]: {url} has no snapshot in this cycle")
            continue
        if url not in evidence_urls:
            problems.append(f"{where}.verified[{j}]: {url} is not one of the recommendation's evidence URLs")
        problem = quote_problem(snapshot, item["quote"])
        if problem:
            problems.append(f"{where}.verified[{j}]: {problem}")

    # Overlap with existing recommendations: explain or reconsider, never auto-reject.
    reconsiders = rec.get("reconsiders")
    distinct = rec.get("distinct_from", [])
    if not isinstance(distinct, list):
        raise ValueError("distinct_from must be a list")
    explained = {}
    for j, d in enumerate(distinct):
        common.check_keys(d, ("id", "reason"), (), f"{where}.distinct_from[{j}].")
        common.recommendation_id(d["id"], f"{where}.distinct_from[{j}].id")
        explained[d["id"]] = common.text(d["reason"], f"{where}.distinct_from[{j}].reason")
    known_ids = {e["id"] for e in existing}
    for rec_id in explained:
        if rec_id not in known_ids:
            problems.append(f"{where}.distinct_from: {rec_id} is not a recommendation of this project")
    candidate_view = {
        "category": common.text(rec["category"], f"{where}.category"),
        "title": common.text(rec["title"], f"{where}.title"),
        "proposal": common.text(rec["proposal"], f"{where}.proposal"),
        "reference_ids": reference_ids,
    }
    for candidate in overlap_candidates(candidate_view, existing):
        if candidate["id"] != reconsiders and candidate["id"] not in explained:
            problems.append(
                f"{where}: may duplicate {candidate['id']} ({'; '.join(candidate['reasons'])}); "
                "explain the difference in distinct_from, reconsider it, or drop the proposal"
            )

    # Reconsiderations need something new.
    new_since = rec.get("new_since")
    new_since_line = None
    if reconsiders is None:
        if new_since is not None:
            problems.append(f"{where}: new_since is only for reconsiderations")
    else:
        common.recommendation_id(reconsiders, f"{where}.reconsiders")
        try:
            original = load_recommendation(root, reconsiders)
        except NotFoundError:
            problems.append(f"{where}: reconsidered recommendation {reconsiders} does not exist")
            original = None
        if original is not None:
            if original.project_id != project_id:
                problems.append(f"{where}: {reconsiders} belongs to another project")
            if new_since is None:
                problems.append(f"{where}: a reconsideration needs new_since (what is new since it was evaluated)")
            else:
                common.text(new_since, f"{where}.new_since")
            old_urls = {u for s in original.sources for u in s.evidence_urls}
            new_evidence = verified_urls - old_urls
            history = feedback_history(root, reconsiders)
            new_context = bool(history) and _context_changed_since(root, project_id, history[-1].evaluated_at, chosen)
            # Only the latest decision matters here; the rating never does.
            decision = history[-1].decision if history else None
            problem = _reconsideration_problem(decision, bool(new_evidence), bool(new_context))
            if problem:
                problems.append(f"{where}: {reconsiders} {problem}")
            since = history[-1].evaluated_at if history else original.created_at
            new_since_line = f"New since {since}: {new_since}"

    justification = "\n".join(
        [f'Verified: {v["statement"]} | Quote: "{v["quote"]}" | Source: {v["url"]}' for v in verified if isinstance(v, dict)]
        + [f"Interpretation: {t}" for t in interpretations]
        + [f"Assumption: {t}" for t in assumptions]
        + [f"Distinct from {rec_id}: {reason}" for rec_id, reason in explained.items()]
        + ([new_since_line] if new_since_line else [])
    )
    storage = {
        "project_id": project_id,
        "category": candidate_view["category"],
        "title": candidate_view["title"],
        "proposal": candidate_view["proposal"],
        "justification": justification or "(none)",
        "benefits": rec["benefits"],
        "limitations": rec["limitations"],
        "relationship": relationship,
        "reference_ids": reference_ids,
        "sources": list(sources) + [{"name": f"Project profile {profile.id}", "url": None, "evidence_urls": []}],
        "project_context": {
            "github_repo": profile.context.github_repo,
            "commit_sha": profile.context.commit_sha,
            "analyzed_at": profile.context.analyzed_at,
            "analyzed_files": sorted({_LINE_RANGE_RE.sub("", s) for c in chosen for s in c.sources}),
            "claims": [{"topic": c.topic, "statement": c.statement, "basis": c.basis, "sources": list(c.sources)} for c in chosen],
        },
        "reconsiders": reconsiders,
    }
    if not problems:
        try:
            check_recommendation_draft(storage, now)
        except DataError as e:
            problems.append(f"{where}: {e}")
    return problems, storage
