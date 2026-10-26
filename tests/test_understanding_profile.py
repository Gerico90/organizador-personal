import copy
import json
import os

import pytest

from organizador.recommendations import common
from organizador.recommendations.common import ConflictError, DataError, NotFoundError
from organizador.recommendations.records import create_recommendation
from organizador.understanding import profile as profile_module
from organizador.understanding.profile import (
    TOPICS,
    check_profile_draft,
    create_profile,
    load_profile,
)
from recommendation_fixtures import draft as recommendation_draft
from understanding_fixtures import NOW

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"

EVIDENCE = {
    "schema_version": 1,
    "project_id": "demo-project",
    "project_dir": "/tmp/project",
    "github_repo": "Octo-Cat/demo",
    "commit_sha": SHA,
    "working_tree_clean": True,
    "collected_at": "2026-10-27T09:59:00Z",
    "notes_checked": "/tmp/vault/notes/projects/demo-project.md",
    "files": [
        {"ref": "README.md", "kind": "markdown", "bytes": 100, "lines": 40, "sha256": "0" * 64, "headings": []},
        {"ref": "src/app.py", "kind": "python", "bytes": 50, "lines": 12, "sha256": "1" * 64, "docstring": "", "definitions": []},
    ],
    "excluded": [{"ref": ".env", "excluded": "secret"}],
    "reports": [
        {"ref": "report:2026-10-26T170604Z.md", "kind": "markdown", "bytes": 10, "lines": 20, "sha256": "2" * 64, "headings": [], "path": "/x", "front_matter": {}}
    ],
    "notes": [{"ref": "note:demo-project.md", "kind": "markdown", "bytes": 10, "lines": 5, "sha256": "3" * 64, "headings": [], "path": "/y"}],
}


def claim(topic, basis="observed", sources=("README.md:1-5",), statement=None):
    return {
        "topic": topic,
        "statement": statement or f"Example {topic} statement",
        "basis": basis,
        "sources": list(sources) if basis != "missing" else [],
    }


def make_draft(**context_overrides):
    context = {
        "github_repo": "octo-cat/demo",
        "commit_sha": SHA,
        "analyzed_at": "2026-10-27T10:00:00Z",
        "analyzed_files": ["README.md", "src/app.py", "report:2026-10-26T170604Z.md", "note:demo-project.md"],
        "claims": [
            claim("purpose", sources=["README.md:1-3"]),
            claim("capability", sources=["src/app.py:7", "README.md"]),
            claim("architecture", basis="inferred", sources=["src/app.py:1-12"]),
            claim("limitation", sources=["report:2026-10-26T170604Z.md:9-11"]),
            claim("objective", basis="missing", statement="No stated plans were found in the README"),
            claim("objective", sources=["note:demo-project.md:3"]),
        ],
    }
    context.update(context_overrides)
    return {"project_id": "demo-project", "context": context}


def evidence(**overrides):
    data = copy.deepcopy(EVIDENCE)
    data.update(overrides)
    return data


# --- check -----------------------------------------------------------------


def test_valid_draft():
    assert check_profile_draft(make_draft(), evidence(), now=NOW) == []


def test_github_repo_comparison_ignores_case():
    assert check_profile_draft(make_draft(github_repo="OCTO-CAT/Demo"), evidence(), now=NOW) == []


def test_project_without_github():
    d = make_draft(github_repo=None)
    assert check_profile_draft(d, evidence(github_repo=None), now=NOW) == []
    assert any("github_repo" in p for p in check_profile_draft(d, evidence(), now=NOW))


def problems_for(d, ev=None):
    return "\n".join(check_profile_draft(d, ev or evidence(), now=NOW))


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"commit_sha": "b" * 40}, "commit_sha"),
        ({"commit_sha": None}, "commit_sha"),
        ({"analyzed_at": None}, "analyzed_at is required"),
        ({"analyzed_at": "2026-10-28T00:00:00Z"}, "later than created_at"),
        ({"analyzed_files": []}, "analyzed_files is empty"),
        ({"analyzed_files": ["README.md", "missing.md"]}, "not in the evidence index"),
        ({"analyzed_files": ["README.md", ".env"]}, "excluded from inspection (secret)"),
        ({"analyzed_files": ["README.md", "README.md"]}, "duplicates"),
    ],
)
def test_context_problems(overrides, message):
    assert message in problems_for(make_draft(**overrides))


@pytest.mark.parametrize(
    "source, message",
    [
        ("missing.md:1-2", "does not name an entry"),
        (".env", "does not name an entry"),
        ("README.md:0", "outside 1-40"),
        ("README.md:41", "outside 1-40"),
        ("README.md:10-5", "outside 1-40"),
        ("README.md:39-41", "outside 1-40"),
        ("report:2026-10-26T170604Z.md:21", "outside 1-20"),
        ("report:other.md", "does not name an entry"),
    ],
)
def test_source_problems(source, message):
    d = make_draft()
    d["context"]["claims"][0]["sources"] = [source]
    assert message in problems_for(d)


def test_source_must_be_in_analyzed_files():
    d = make_draft(analyzed_files=["README.md", "report:2026-10-26T170604Z.md", "note:demo-project.md"])
    assert "not in analyzed_files" in problems_for(d)  # src/app.py is cited but not listed


@pytest.mark.parametrize("valid", ["README.md", "README.md:40", "README.md:1-40", "note:demo-project.md:1-5"])
def test_valid_source_forms(valid):
    d = make_draft()
    d["context"]["claims"][0]["sources"] = [valid]
    assert check_profile_draft(d, evidence(), now=NOW) == []


@pytest.mark.parametrize("topic", TOPICS)
def test_every_topic_must_be_covered(topic):
    d = make_draft()
    d["context"]["claims"] = [c for c in d["context"]["claims"] if c["topic"] != topic]
    assert f"no claim for topic {topic!r}" in problems_for(d)


def test_unknown_topic():
    d = make_draft()
    d["context"]["claims"].append(claim("roadmap"))
    assert "topic must be one of" in problems_for(d)


@pytest.mark.parametrize(
    "bad_claim, message",
    [
        (claim("purpose", sources=[]), "needs at least one source"),
        (claim("purpose", basis="inferred", sources=[]), "needs at least one source"),
        ({**claim("purpose", basis="missing"), "sources": ["README.md"]}, "no sources"),
        ({**claim("purpose"), "basis": "planned"}, "basis must be one of"),
    ],
)
def test_claim_rules_from_shared_schema(bad_claim, message):
    d = make_draft()
    d["context"]["claims"].append(bad_claim)
    assert message in problems_for(d)


@pytest.mark.parametrize(
    "bad, message",
    [
        ({"project_id": "demo-project"}, "missing field"),
        ({"project_id": "demo-project", "context": {}, "extra": 1}, "unknown field"),
        ({"project_id": "Bad", "context": {}}, "project_id"),
        ([], "JSON object"),
    ],
)
def test_malformed_drafts(bad, message):
    assert message in "\n".join(check_profile_draft(bad, evidence(), now=NOW))


def test_project_id_must_match_evidence():
    d = make_draft()
    d["project_id"] = "other-project"
    assert "does not match the evidence" in problems_for(d)


def test_all_problems_are_listed_together():
    d = make_draft(commit_sha="b" * 40, analyzed_files=["README.md"])
    d["context"]["claims"] = [claim("purpose", sources=["README.md:99"])]
    problems = check_profile_draft(d, evidence(), now=NOW)
    assert len(problems) >= 1 + 1 + 4  # sha, bad range, four uncovered topics


# --- save and load ---------------------------------------------------------


@pytest.fixture
def fixed_suffix(monkeypatch):
    monkeypatch.setattr(profile_module, "_random_suffix", lambda: "0000000a")


def test_create_and_load(tmp_path, fixed_suffix):
    profile = create_profile(tmp_path, make_draft(), evidence(), now=NOW)
    assert profile.id == "prof-20261027T100000Z-0000000a"
    assert profile.inputs.reports == ("report:2026-10-26T170604Z.md",)
    assert profile.inputs.notes == ("note:demo-project.md",)
    assert profile.inputs.working_tree_clean is True
    assert load_profile(tmp_path, profile.id) == profile

    stored = json.loads((tmp_path / "profiles" / f"{profile.id}.json").read_text(encoding="utf-8"))
    assert set(stored) == {"schema_version", "id", "created_at", "project_id", "context", "inputs"}
    assert stored["context"] == make_draft()["context"]


def test_dirty_tree_is_recorded(tmp_path, fixed_suffix):
    profile = create_profile(tmp_path, make_draft(), evidence(working_tree_clean=False), now=NOW)
    assert profile.inputs.working_tree_clean is False


def test_invalid_draft_is_not_saved(tmp_path):
    with pytest.raises(DataError, match="Invalid profile draft"):
        create_profile(tmp_path, make_draft(commit_sha="b" * 40), evidence(), now=NOW)
    assert not (tmp_path / "profiles").exists()


def test_profiles_are_immutable(tmp_path, fixed_suffix):
    first = create_profile(tmp_path, make_draft(), evidence(), now=NOW)
    path = tmp_path / "profiles" / f"{first.id}.json"
    before = path.read_bytes()
    with pytest.raises(ConflictError, match="already exists"):
        create_profile(tmp_path, make_draft(analyzed_at="2026-10-27T09:00:00Z"), evidence(), now=NOW)
    assert path.read_bytes() == before


def test_write_failure_leaves_no_file(tmp_path, fixed_suffix, monkeypatch):
    def broken_fsync(fd):
        raise OSError("disk full")

    monkeypatch.setattr(common.os, "fsync", broken_fsync)
    with pytest.raises(OSError, match="disk full"):
        create_profile(tmp_path, make_draft(), evidence(), now=NOW)
    assert os.listdir(tmp_path / "profiles") == []


def test_linked_profiles_folder_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "data"
    root.mkdir()
    try:
        os.symlink(outside, root / "profiles", target_is_directory=True)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"cannot create symlinks here: {e}")
    with pytest.raises(DataError, match="link to another location"):
        create_profile(root, make_draft(), evidence(), now=NOW)
    assert os.listdir(outside) == []


def test_load_errors(tmp_path, fixed_suffix):
    with pytest.raises(NotFoundError):
        load_profile(tmp_path, "prof-20261027T100000Z-0000000a")
    for bad in ["../x", "prof-bad", ""]:
        with pytest.raises(DataError):
            load_profile(tmp_path, bad)

    profile = create_profile(tmp_path, make_draft(), evidence(), now=NOW)
    path = tmp_path / "profiles" / f"{profile.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["inputs"]["working_tree_clean"] = "yes"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DataError, match="working_tree_clean"):
        load_profile(tmp_path, profile.id)


def test_profile_context_is_usable_as_recommendation_context(tmp_path, fixed_suffix):
    profile = create_profile(tmp_path, make_draft(), evidence(), now=NOW)
    stored = json.loads((tmp_path / "profiles" / f"{profile.id}.json").read_text(encoding="utf-8"))
    rec = create_recommendation(tmp_path, recommendation_draft(project_context=stored["context"]), now=NOW)
    assert rec.project_context == profile.context
