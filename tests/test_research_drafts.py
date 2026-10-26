import json
import os

import pytest

from organizador.recommendations.common import ConflictError, DataError
from organizador.recommendations.feedback import append_feedback
from organizador.recommendations.records import create_recommendation, list_recommendations, load_recommendation
from organizador.recommendations.references import get_or_create_reference, reference_id_for
from organizador.research.drafts import check_research_draft, save_research_draft
from organizador.research.snapshots import start_cycle
from research_fixtures import (
    DOC_HTML,
    DOC_URL,
    OTHER_HTML,
    OTHER_URL,
    PROFILE_CLAIMS,
    T0,
    T1,
    T2,
    add_snapshot,
    evidence,
    make_profile,
    research_draft,
    research_recommendation,
    stored_recommendation,
)

TOOL_REF = reference_id_for("https://github.com/octo-cat/tool")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A data root with a current profile, a cycle and one evidence snapshot."""
    profile = make_profile(tmp_path, monkeypatch)
    cycle = start_cycle(tmp_path, "demo-project", now=T0)
    add_snapshot(tmp_path, cycle, DOC_URL, DOC_HTML)
    return tmp_path, profile, cycle


def problems(env, draft, ev=None):
    root, _, _ = env
    return "\n".join(check_research_draft(root, draft, ev or evidence(), now=T1))


def draft_for(env, recs=None, **overrides):
    _, profile, cycle = env
    return research_draft(cycle, profile.id, recs, **overrides)


# --- valid drafts and saving -----------------------------------------------


def test_valid_draft_is_saved(env):
    root, profile, cycle = env
    draft = draft_for(env)
    assert problems(env, draft) == ""

    result = save_research_draft(root, draft, evidence(), now=T1)
    [rec_id] = result["recommendations"]
    assert result["references"] == [{"id": TOOL_REF, "created": True, "differences": []}]
    rec = load_recommendation(root, rec_id)

    assert rec.reference_ids == (TOOL_REF,)
    assert rec.relationship.startswith("technical:")
    assert rec.justification.splitlines() == [
        f'Verified: The tool shrinks failing inputs to a minimal example. | Quote: "shrinks failing cases to a minimal example" | Source: {DOC_URL}',
        "Interpretation: Tie handling is the kind of edge case generated inputs explore.",
        "Assumption: The existing fake API can be driven by generated data.",
    ]
    assert rec.sources[-1].name == f"Project profile {profile.id}" and rec.sources[-1].url is None
    # Profile claims are copied verbatim, with the profile's commit and only the files they cite.
    [claim] = rec.project_context.claims
    assert (claim.topic, claim.statement, claim.basis, claim.sources) == (
        PROFILE_CLAIMS[3]["topic"], PROFILE_CLAIMS[3]["statement"], PROFILE_CLAIMS[3]["basis"], tuple(PROFILE_CLAIMS[3]["sources"]),
    )
    assert rec.project_context.commit_sha == profile.context.commit_sha
    assert rec.project_context.analyzed_files == ("README.md",)

    cycle_dir = root / "research" / cycle
    assert json.loads((cycle_dir / "draft.json").read_text(encoding="utf-8")) == draft
    assert json.loads((cycle_dir / "result.json").read_text(encoding="utf-8"))["recommendations"] == [rec_id]


def test_zero_recommendations_is_a_valid_outcome(env):
    root, _, cycle = env
    draft = draft_for(env, [], references=[])
    assert problems(env, draft) == ""
    result = save_research_draft(root, draft, evidence(), now=T1)
    assert result["recommendations"] == [] and result["references"] == []
    assert list_recommendations(root, "demo-project") == ()
    assert not (root / "references").exists()


def test_a_cycle_is_saved_once(env):
    root, _, cycle = env
    save_research_draft(root, draft_for(env), evidence(), now=T1)
    with pytest.raises(ConflictError, match="already saved"):
        save_research_draft(root, draft_for(env), evidence(), now=T2)


def test_interrupted_save_is_not_repeated_blindly(env):
    root, _, cycle = env
    (root / "research" / cycle / "draft.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ConflictError, match="interrupted"):
        save_research_draft(root, draft_for(env), evidence(), now=T1)
    assert list_recommendations(root, "demo-project") == ()


def test_invalid_draft_saves_nothing(env):
    root, _, cycle = env
    bad = draft_for(env, [research_recommendation(relationship="related somehow")])
    with pytest.raises(DataError, match="Invalid research draft"):
        save_research_draft(root, bad, evidence(), now=T1)
    assert not (root / "references").exists()
    assert not (root / "research" / cycle / "draft.json").exists()


# --- profile ---------------------------------------------------------------


def test_profile_must_be_current(env):
    stale = evidence(commit_sha="b" * 40)
    assert "is not current" in problems(env, draft_for(env), stale)
    changed_note = evidence(notes=[dict(evidence()["notes"][0], sha256="9" * 64)])
    assert "context note note:demo-project.md changed" in problems(env, draft_for(env), changed_note)


@pytest.mark.parametrize(
    "claims, message",
    [
        ([], "non-empty list"),
        ([0], "numbers from 1 to 6"),
        ([7], "numbers from 1 to 6"),
        ([4, 4], "duplicates"),
        (["4"], "numbers from 1 to 6"),
        ([6], "only on 'missing' profile claims"),
    ],
)
def test_profile_claim_numbers(env, claims, message):
    assert message in problems(env, draft_for(env, [research_recommendation(profile_claims=claims)]))


def test_missing_claim_alongside_a_real_one_is_fine(env):
    assert problems(env, draft_for(env, [research_recommendation(profile_claims=[4, 6])])) == ""


# --- evidence --------------------------------------------------------------


def verified(quote="shrinks failing cases to a minimal example", url=DOC_URL):
    return [{"statement": "The tool shrinks failing inputs.", "quote": quote, "url": url}]


@pytest.mark.parametrize(
    "rec, message",
    [
        (research_recommendation(verified=verified(quote="shrinks failing cases to the smallest one")), "not found in snapshot"),
        (research_recommendation(verified=verified(quote="minimal")), "shorter than"),
        (research_recommendation(verified=verified(url=OTHER_URL)), "has no snapshot"),
        (research_recommendation(verified=[]), "at least one verified statement"),
        (
            research_recommendation(sources=[{"name": "Other", "url": OTHER_URL, "evidence_urls": [OTHER_URL]}]),
            "has no snapshot",
        ),
    ],
)
def test_evidence_problems(env, rec, message):
    assert message in problems(env, draft_for(env, [rec]))


def test_verified_url_must_be_an_evidence_url_of_the_recommendation(env):
    root, _, cycle = env
    add_snapshot(root, cycle, OTHER_URL, OTHER_HTML)
    rec = research_recommendation(
        verified=verified(quote="models a system as a sequence of operations", url=OTHER_URL)
    )
    assert "not one of the recommendation's evidence URLs" in problems(env, draft_for(env, [rec]))


def test_quote_traceability_does_not_require_the_statement_to_repeat_it(env):
    # The statement is our paraphrase; only the quote is matched against the page.
    rec = research_recommendation(
        verified=[{"statement": "Failing examples are reduced automatically.", "quote": "shrinks failing cases to a minimal example", "url": DOC_URL}]
    )
    assert problems(env, draft_for(env, [rec])) == ""


# --- relationship, references, budget --------------------------------------


@pytest.mark.parametrize("relationship", ["related", "Technical: x", "inspirational: x", "functional:", "functional:   "])
def test_relationship_type(env, relationship):
    assert "relationship must start with" in problems(env, draft_for(env, [research_recommendation(relationship=relationship)]))


@pytest.mark.parametrize("relationship", ["functional: same problem", "technical: x", "analogical: idea from logistics"])
def test_valid_relationship_types(env, relationship):
    assert problems(env, draft_for(env, [research_recommendation(relationship=relationship)])) == ""


def test_reference_must_be_in_draft_or_stored(env):
    root, _, _ = env
    rec = research_recommendation(reference_urls=["https://github.com/octo-cat/elsewhere"])
    assert "neither in this draft's references nor stored" in problems(env, draft_for(env, [rec]))
    get_or_create_reference(root, name="elsewhere", url="https://github.com/octo-cat/elsewhere", now=T0)
    assert "neither in this draft" not in problems(env, draft_for(env, [rec]))


def test_reference_problems(env):
    unused = draft_for(env, [research_recommendation(reference_urls=[])])
    assert "is not used by any recommendation" in problems(env, unused)
    deep_link = draft_for(env, references=[{"name": "x", "url": "https://github.com/octo-cat/tool/blob/main/a.py"}])
    assert "repository root" in problems(env, deep_link)
    twice = draft_for(env, references=[{"name": "a", "url": "https://github.com/octo-cat/tool"}, {"name": "b", "url": "https://github.com/Octo-Cat/Tool.git"}])
    assert "listed twice" in problems(env, twice)
    assert "references are listed but there are no recommendations" in problems(env, draft_for(env, []))


def test_budget_and_tools(env):
    many = draft_for(env)
    many["log"]["searches"] = [{"tool": "web_search", "query": f"q{i}"} for i in range(9)]
    assert "at most 8 web searches" in problems(env, many)

    extended = draft_for(env)
    extended["log"]["searches"] = [{"tool": "web_search_extended", "query": "q"}]
    assert "needs the user's approval" in problems(env, extended)

    too_many = draft_for(env, [research_recommendation(title=f"Idea {i}", proposal=f"Distinct proposal number {i} about topic {i}") for i in range(4)])
    assert "at most 3 recommendations" in problems(env, too_many)


def test_malformed_drafts(env):
    root, profile, cycle = env
    assert "missing field" in problems(env, {"cycle_id": cycle})
    assert "unknown field" in problems(env, dict(draft_for(env), extra=1))
    assert "missing field" in problems(env, draft_for(env, [{"title": "x"}]))
    assert "not valid" in problems(env, dict(draft_for(env), profile_id="prof-bad"))


# --- duplicates and feedback -----------------------------------------------


def existing_rec(root, **overrides):
    get_or_create_reference(root, name="tool", url="https://github.com/octo-cat/tool", now=T0)
    return create_recommendation(root, stored_recommendation(reference_ids=[TOOL_REF], **overrides), now=T0)


def test_overlap_must_be_explained_not_rejected(env):
    root, _, _ = env
    old = existing_rec(root)
    assert f"may duplicate {old.id}" in problems(env, draft_for(env))

    explained = research_recommendation(
        title="Stateful tests for the checkpoint",
        proposal="Model runs as sequences of operations and check checkpoint invariants after each step.",
        distinct_from=[{"id": old.id, "reason": "tests run sequences, not timestamp ties"}],
    )
    draft = draft_for(env, [explained])
    assert problems(env, draft) == ""
    [rec_id] = save_research_draft(root, draft, evidence(), now=T1)["recommendations"]
    assert f"Distinct from {old.id}: tests run sequences, not timestamp ties" in load_recommendation(root, rec_id).justification


def test_discarded_recommendation_does_not_block_a_distinct_proposal(env):
    root, _, _ = env
    old = existing_rec(root)
    append_feedback(root, old.id, rating="dislike", decision="discard", notes="not now", now=T0)
    rec = research_recommendation(distinct_from=[{"id": old.id, "reason": "different scope"}])
    assert problems(env, draft_for(env, [rec])) == ""


def test_distinct_from_must_name_a_recommendation_of_the_project(env):
    rec = research_recommendation(distinct_from=[{"id": "rec-20261027T100000Z-ffffffff", "reason": "x"}])
    assert "is not a recommendation of this project" in problems(env, draft_for(env, [rec]))


def test_two_similar_proposals_in_one_draft(env):
    same = draft_for(env, [research_recommendation(), research_recommendation()])
    assert "look alike" in problems(env, same)
    shared_reference_only = draft_for(
        env,
        [
            research_recommendation(),
            research_recommendation(
                category="documentation",
                title="Document cursor guarantees",
                proposal="Write down which tie cases the checkpoint covers and which it cannot detect.",
            ),
        ],
    )
    assert problems(env, shared_reference_only) == ""


def test_reconsideration_rules(env):
    root, _, cycle = env
    old = existing_rec(root, sources=[{"name": "Tool documentation", "url": DOC_URL, "evidence_urls": [DOC_URL]}])
    append_feedback(root, old.id, rating="like", decision="future", now=T0)
    original_bytes = (root / "recommendations" / f"{old.id}.json").read_bytes()
    feedback_bytes = (root / "feedback" / f"{old.id}.jsonl").read_bytes()

    no_note = research_recommendation(reconsiders=old.id)
    assert "needs new_since" in problems(env, draft_for(env, [no_note]))

    nothing_new = research_recommendation(reconsiders=old.id, new_since="looked again")
    assert "needs new evidence" in problems(env, draft_for(env, [nothing_new]))

    add_snapshot(root, cycle, OTHER_URL, OTHER_HTML)
    with_new_evidence = research_recommendation(
        reconsiders=old.id,
        new_since="a guide on stateful testing was found",
        sources=[
            {"name": "Tool documentation", "url": DOC_URL, "evidence_urls": [DOC_URL]},
            {"name": "Guide", "url": OTHER_URL, "evidence_urls": [OTHER_URL]},
        ],
        verified=verified() + verified(quote="models a system as a sequence of operations", url=OTHER_URL),
    )
    draft = draft_for(env, [with_new_evidence])
    assert problems(env, draft) == ""
    [rec_id] = save_research_draft(root, draft, evidence(), now=T1)["recommendations"]
    rec = load_recommendation(root, rec_id)
    assert rec.reconsiders == old.id
    assert rec.justification.splitlines()[-1] == "New since 2026-10-27T10:00:00Z: a guide on stateful testing was found"
    # The original recommendation and its feedback are untouched.
    assert (root / "recommendations" / f"{old.id}.json").read_bytes() == original_bytes
    assert (root / "feedback" / f"{old.id}.jsonl").read_bytes() == feedback_bytes


def test_new_context_also_justifies_a_reconsideration(env):
    root, _, _ = env
    old = existing_rec(root, sources=[{"name": "Tool documentation", "url": DOC_URL, "evidence_urls": [DOC_URL]}])
    rec = research_recommendation(reconsiders=old.id, new_since="the note now plans scheduled runs", profile_claims=[4, 5])
    assert problems(env, draft_for(env, [rec])) == ""


def test_new_since_only_for_reconsiderations_and_same_project(env):
    root, _, _ = env
    assert "only for reconsiderations" in problems(env, draft_for(env, [research_recommendation(new_since="x")]))
    other = create_recommendation(root, stored_recommendation(project_id="other-project"), now=T0)
    rec = research_recommendation(reconsiders=other.id, new_since="x")
    assert "belongs to another project" in problems(env, draft_for(env, [rec]))
    missing = research_recommendation(reconsiders="rec-20261027T100000Z-ffffffff", new_since="x")
    assert "does not exist" in problems(env, draft_for(env, [missing]))


def test_cycle_and_project_must_match(env):
    root, profile, _ = env
    other_cycle = start_cycle(root, "other-project", now=T0)
    assert "does not match the cycle" in problems(env, research_draft(other_cycle, profile.id))
