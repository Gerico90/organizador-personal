"""Research brief: what a research cycle must know before searching.

The brief is written once per cycle to <root>/research/<cycle_id>/brief.json:

- the project's current commit and context notes (from a fresh evidence index);
- the latest profile and whether it is still current (profile_freshness);
- every existing recommendation for the project with its full feedback
  history. Ratings and decisions are listed separately and never combined.

`overlap_candidates` flags existing recommendations that may duplicate a
new proposal. It only identifies candidates; deciding whether a proposal is
really the same is left to research, which must explain each flagged case.
"""

import re
from datetime import datetime
from pathlib import Path

from organizador.recommendations import common
from organizador.recommendations.common import SCHEMA_VERSION, ConflictError
from organizador.recommendations.feedback import feedback_history
from organizador.recommendations.records import Recommendation, list_recommendations
from organizador.research.snapshots import load_cycle
from organizador.understanding.profile import list_profiles, profile_freshness

# Thresholds for overlap signals (Jaccard similarity of content words).
SIMILAR_WORDING = 0.25
SAME_CATEGORY_WORDING = 0.10

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "about after also and are because been before being between both but can could each for from have "
    "into its more most not only other over same should such than that the their them then there these "
    "they this those through using very what when where which while will with would your".split()
)


def build_brief(root: Path, cycle_id: str, evidence: dict, now: datetime | None = None) -> dict:
    """Assemble the brief for `cycle_id` from `evidence` and the stored data."""
    cycle = load_cycle(root, cycle_id)
    project_id = cycle["project_id"]
    if evidence["project_id"] != project_id:
        raise ConflictError(f"evidence is for {evidence['project_id']!r}, the cycle for {project_id!r}")

    profiles = list_profiles(root, project_id)
    if profiles:
        latest = profiles[-1]
        reasons = profile_freshness(latest, evidence)
        profile = {"id": latest.id, "created_at": latest.created_at, "current": not reasons, "stale_reasons": reasons}
    else:
        profile = None

    recommendations = [_recommendation_entry(root, rec) for rec in list_recommendations(root, project_id)]
    return {
        "schema_version": SCHEMA_VERSION,
        "cycle_id": cycle_id,
        "project_id": project_id,
        "generated_at": common.utc_now(now),
        "project": {
            "github_repo": evidence["github_repo"],
            "commit_sha": evidence["commit_sha"],
            "working_tree_clean": evidence["working_tree_clean"],
            "notes": {e["ref"]: e["sha256"] for e in evidence["notes"]},
        },
        "profile": profile,
        "recommendations": recommendations,
    }


def write_brief(root: Path, cycle_id: str, brief: dict) -> Path:
    """Store the brief in the cycle folder. A cycle has one brief; it is never replaced."""
    path = common.record_path(root, "research", cycle_id, "") / "brief.json"
    try:
        common.create_new_file(path, common.dump_json(brief))
    except FileExistsError:
        raise ConflictError(f"Cycle {cycle_id} already has a brief ({path})") from None
    return path


def overlap_candidates(proposal: dict, existing: list[dict]) -> list[dict]:
    """Existing recommendations that may duplicate `proposal`, with the reasons.

    `proposal` and each entry of `existing` need `title`, `proposal`,
    `category` and `reference_ids`; entries also need `id`. Signals:
    a shared reference, similar wording of title and proposal, or the same
    category with some shared wording. A shared reference alone does not
    make two proposals the same; it only asks for an explanation.
    """
    words = _content_words(proposal)
    found = []
    for other in existing:
        reasons = []
        shared = sorted(set(proposal["reference_ids"]) & set(other["reference_ids"]))
        if shared:
            reasons.append(f"shares reference(s) {', '.join(shared)}")
        similarity = _jaccard(words, _content_words(other))
        if similarity >= SIMILAR_WORDING:
            reasons.append(f"similar wording ({similarity:.0%} of content words shared)")
        elif (
            proposal["category"].casefold() == other["category"].casefold()
            and similarity >= SAME_CATEGORY_WORDING
        ):
            reasons.append(f"same category with some shared wording ({similarity:.0%})")
        if reasons:
            found.append({"id": other["id"], "reasons": reasons})
    return found


def _recommendation_entry(root: Path, rec: Recommendation) -> dict:
    history = feedback_history(root, rec.id)
    latest = history[-1] if history else None
    return {
        "id": rec.id,
        "created_at": rec.created_at,
        "category": rec.category,
        "title": rec.title,
        "proposal": rec.proposal,
        "relationship": rec.relationship,
        "reference_ids": list(rec.reference_ids),
        "evidence_urls": sorted({u for s in rec.sources for u in s.evidence_urls}),
        "reconsiders": rec.reconsiders,
        "feedback": [
            {"evaluated_at": e.evaluated_at, "rating": e.rating, "decision": e.decision, "notes": e.notes}
            for e in history
        ],
        # Not evaluated yet is reported as such; it is not turned into a decision.
        "latest_rating": latest.rating if latest else None,
        "latest_decision": latest.decision if latest else None,
        "latest_evaluated_at": latest.evaluated_at if latest else None,
    }


def _content_words(item: dict) -> set[str]:
    text = f"{item['title']} {item['proposal']}".casefold()
    return {w for w in _WORD_RE.findall(text) if len(w) >= 3 and w not in _STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0
