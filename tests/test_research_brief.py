import json

import pytest

from organizador.recommendations.common import ConflictError
from organizador.recommendations.feedback import append_feedback
from organizador.recommendations.records import create_recommendation
from organizador.research.brief import build_brief, overlap_candidates, write_brief
from organizador.research.snapshots import start_cycle
from research_fixtures import NOTE_SHA, T0, T1, T2, evidence, make_profile, stored_recommendation


@pytest.fixture
def cycle(tmp_path):
    return start_cycle(tmp_path, "demo-project", now=T0)


def test_brief_without_profile_or_recommendations(tmp_path, cycle):
    brief = build_brief(tmp_path, cycle, evidence(), now=T1)
    assert brief["profile"] is None
    assert brief["recommendations"] == []
    assert brief["project"] == {
        "github_repo": "octo-cat/demo",
        "commit_sha": evidence()["commit_sha"],
        "working_tree_clean": True,
        "notes": {"note:demo-project.md": NOTE_SHA},
    }


def test_current_and_stale_profile(tmp_path, cycle, monkeypatch):
    profile = make_profile(tmp_path, monkeypatch)
    brief = build_brief(tmp_path, cycle, evidence(), now=T1)
    assert brief["profile"] == {"id": profile.id, "created_at": profile.created_at, "current": True, "stale_reasons": []}

    changed_note = evidence(notes=[dict(evidence()["notes"][0], sha256="9" * 64)])
    stale = build_brief(tmp_path, cycle, changed_note, now=T1)["profile"]
    assert stale["current"] is False
    assert stale["stale_reasons"] == ["context note note:demo-project.md changed after the profile was made"]


def test_latest_profile_is_used(tmp_path, cycle, monkeypatch):
    make_profile(tmp_path, monkeypatch, now=T0, suffix="0000000a")
    newer = make_profile(tmp_path, monkeypatch, now=T1, suffix="0000000b")
    assert build_brief(tmp_path, cycle, evidence(), now=T2)["profile"]["id"] == newer.id


def test_full_feedback_history_with_independent_rating_and_decision(tmp_path, cycle):
    rec = create_recommendation(tmp_path, stored_recommendation(), now=T0)
    untouched = create_recommendation(tmp_path, stored_recommendation(title="Another idea"), now=T0)
    create_recommendation(tmp_path, stored_recommendation(project_id="other-project"), now=T0)
    append_feedback(tmp_path, rec.id, rating="dislike", decision="explore", notes="Not my style, but worth a look", now=T1)
    append_feedback(tmp_path, rec.id, rating=None, decision="future", now=T2)

    entries = {e["id"]: e for e in build_brief(tmp_path, cycle, evidence(), now=T2)["recommendations"]}
    assert set(entries) == {rec.id, untouched.id}  # other projects are left out

    entry = entries[rec.id]
    assert entry["feedback"] == [
        {"evaluated_at": "2026-10-27T11:00:00Z", "rating": "dislike", "decision": "explore", "notes": "Not my style, but worth a look"},
        {"evaluated_at": "2026-10-27T12:00:00Z", "rating": None, "decision": "future", "notes": None},
    ]
    assert (entry["latest_rating"], entry["latest_decision"]) == (None, "future")
    assert entry["evidence_urls"] == ["https://docs.example/a"]

    assert entries[untouched.id]["feedback"] == []
    assert (entries[untouched.id]["latest_rating"], entries[untouched.id]["latest_decision"]) == (None, None)


def test_brief_is_written_once(tmp_path, cycle):
    brief = build_brief(tmp_path, cycle, evidence(), now=T1)
    path = write_brief(tmp_path, cycle, brief)
    assert json.loads(path.read_text(encoding="utf-8")) == brief
    with pytest.raises(ConflictError, match="already has a brief"):
        write_brief(tmp_path, cycle, brief)


def test_evidence_must_match_the_cycle_project(tmp_path, cycle):
    with pytest.raises(ConflictError):
        build_brief(tmp_path, cycle, evidence(project_id="other-project"), now=T1)


# --- overlap candidates ----------------------------------------------------

EXISTING = {
    "id": "rec-20261027T100000Z-0000000a",
    "category": "testing",
    "title": "Property-based tests for cursor ties",
    "proposal": "Generate random update sequences sharing timestamps and check that no item is lost.",
    "reference_ids": ["ref-0123456789abcdef"],
}


def proposal(**overrides):
    data = {
        "category": "architecture",
        "title": "Unrelated idea",
        "proposal": "Move configuration to environment variables for containers.",
        "reference_ids": [],
    }
    data.update(overrides)
    return data


def test_unrelated_proposal_has_no_candidates():
    assert overlap_candidates(proposal(), [EXISTING]) == []


def test_shared_reference_is_a_signal_only():
    [found] = overlap_candidates(proposal(reference_ids=["ref-0123456789abcdef"]), [EXISTING])
    assert found["id"] == EXISTING["id"]
    assert found["reasons"] == ["shares reference(s) ref-0123456789abcdef"]


def test_similar_wording():
    similar = proposal(
        title="Property tests for timestamp ties",
        proposal="Generate random sequences of updates sharing timestamps; check no item is lost or repeated.",
    )
    [found] = overlap_candidates(similar, [EXISTING])
    assert found["reasons"][0].startswith("similar wording")


def test_same_category_needs_some_shared_wording():
    assert overlap_candidates(proposal(category="Testing"), [EXISTING]) == []
    some = proposal(category="Testing", proposal="Add tests that generate random cases for the cursor.")
    [found] = overlap_candidates(some, [EXISTING])
    assert found["reasons"][0].startswith("same category")


def test_uncommitted_changes_make_the_brief_profile_not_current(tmp_path, cycle, monkeypatch):
    make_profile(tmp_path, monkeypatch)
    profile = build_brief(tmp_path, cycle, evidence(working_tree_clean=False), now=T1)["profile"]
    assert profile["current"] is False
    assert profile["stale_reasons"] == ["the project has uncommitted changes to tracked files that its commit does not describe"]
