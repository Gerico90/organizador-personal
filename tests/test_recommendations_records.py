import json
import os

import pytest

from organizador.recommendations import common, records
from organizador.recommendations.common import ConflictError, DataError, NotFoundError
from organizador.recommendations.records import (
    create_recommendation,
    list_recommendations,
    load_recommendation,
)
from recommendation_fixtures import T0, T1, T2, context, draft, make_reference


def rec_file(root, rec_id):
    return root / "recommendations" / f"{rec_id}.json"


@pytest.fixture
def fixed_suffix(monkeypatch):
    suffixes = iter(["0000000a", "0000000b", "0000000c", "0000000d"])
    monkeypatch.setattr(records, "_random_suffix", lambda: next(suffixes))


# --- create ----------------------------------------------------------------


def test_create_and_load(tmp_path, fixed_suffix):
    ref = make_reference(tmp_path)
    rec = create_recommendation(tmp_path, draft(reference_ids=[ref.id]), now=T0)

    assert rec.id == "rec-20261026T170000Z-0000000a"
    assert rec.created_at == "2026-10-26T17:00:00Z"
    assert rec.reconsiders is None
    assert rec.reference_ids == (ref.id,)
    assert rec.project_context.claims[2].basis == "missing"
    assert load_recommendation(tmp_path, rec.id) == rec

    stored = json.loads(rec_file(tmp_path, rec.id).read_text(encoding="utf-8"))
    assert stored["schema_version"] == 1
    assert stored["id"] == rec.id
    assert stored["reconsiders"] is None
    assert stored["project_context"]["github_repo"] == "octo-cat/example-project"


def test_unicode_is_stored_as_utf8(tmp_path, fixed_suffix):
    rec = create_recommendation(tmp_path, draft(title="Café ñandú ✓"), now=T0)
    raw = rec_file(tmp_path, rec.id).read_bytes().decode("utf-8")
    assert "Café ñandú ✓" in raw


def test_project_without_github_and_with_missing_context(tmp_path, fixed_suffix):
    empty_context = {
        "github_repo": None,
        "commit_sha": None,
        "analyzed_at": None,
        "analyzed_files": [],
        "claims": [
            {"topic": "objective", "statement": "No stated objectives were found", "basis": "missing", "sources": []}
        ],
    }
    rec = create_recommendation(tmp_path, draft(project_context=empty_context), now=T0)
    ctx = rec.project_context
    assert (ctx.github_repo, ctx.commit_sha, ctx.analyzed_at, ctx.analyzed_files) == (None, None, None, ())
    assert load_recommendation(tmp_path, rec.id).project_context == ctx


def test_empty_optional_lists_are_allowed(tmp_path, fixed_suffix):
    rec = create_recommendation(
        tmp_path,
        draft(benefits=[], limitations=[], project_context=context(analyzed_files=[], claims=[])),
        now=T0,
    )
    assert rec.benefits == rec.limitations == () and rec.project_context.claims == ()


# --- external evidence -----------------------------------------------------

NOTE = {"name": "Personal note", "url": None, "evidence_urls": []}


def test_reference_alone_is_enough_external_evidence(tmp_path, fixed_suffix):
    ref = make_reference(tmp_path)
    rec = create_recommendation(tmp_path, draft(reference_ids=[ref.id], sources=[]), now=T0)
    assert rec.reference_ids == (ref.id,) and rec.sources == ()


def test_source_with_url_alone_is_enough_external_evidence(tmp_path, fixed_suffix):
    rec = create_recommendation(tmp_path, draft(reference_ids=[]), now=T0)
    assert rec.reference_ids == () and rec.sources[0].url == "https://example.com/source"


def test_personal_note_is_allowed_next_to_external_evidence(tmp_path, fixed_suffix):
    ref = make_reference(tmp_path)
    with_ref = create_recommendation(tmp_path, draft(reference_ids=[ref.id], sources=[NOTE]), now=T0)
    with_url = create_recommendation(tmp_path, draft(sources=[NOTE, draft()["sources"][0]]), now=T1)
    assert with_ref.sources[0].url is None
    assert with_url.sources[0].url is None


@pytest.mark.parametrize("sources", [[], [NOTE], [NOTE, dict(NOTE, name="Another note")]])
def test_no_verifiable_external_source_is_rejected(tmp_path, sources):
    with pytest.raises(DataError, match="at least one external source"):
        create_recommendation(tmp_path, draft(reference_ids=[], sources=sources), now=T0)
    assert not (tmp_path / "recommendations").exists()


def test_evidence_urls_without_a_source_url_do_not_count(tmp_path):
    only_evidence = [{"name": "Note", "url": None, "evidence_urls": ["https://example.com/page"]}]
    with pytest.raises(DataError, match="at least one external source"):
        create_recommendation(tmp_path, draft(reference_ids=[], sources=only_evidence), now=T0)


def test_missing_reference_is_rejected_even_with_a_note(tmp_path):
    with pytest.raises(NotFoundError):
        create_recommendation(tmp_path, draft(reference_ids=["ref-0123456789abcdef"], sources=[NOTE]), now=T0)
    assert not (tmp_path / "recommendations").exists()


def test_invalid_reference_file_is_rejected(tmp_path):
    ref = make_reference(tmp_path)
    (tmp_path / "references" / f"{ref.id}.json").write_text("{", encoding="utf-8")
    with pytest.raises(DataError, match="Malformed JSON"):
        create_recommendation(tmp_path, draft(reference_ids=[ref.id], sources=[NOTE]), now=T0)
    assert not (tmp_path / "recommendations").exists()


def test_stored_recommendation_without_external_source_is_invalid(tmp_path, fixed_suffix):
    rec = create_recommendation(tmp_path, draft(), now=T0)
    path = rec_file(tmp_path, rec.id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["sources"] = [NOTE]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DataError, match="at least one external source"):
        load_recommendation(tmp_path, rec.id)


def test_reference_shared_by_several_recommendations(tmp_path, fixed_suffix):
    ref = make_reference(tmp_path)
    a = create_recommendation(tmp_path, draft(reference_ids=[ref.id]), now=T0)
    b = create_recommendation(tmp_path, draft(project_id="other-project", reference_ids=[ref.id]), now=T1)
    assert a.reference_ids == b.reference_ids == (ref.id,)
    assert len(os.listdir(tmp_path / "references")) == 1


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"title": ""}, "title"),
        ({"category": None}, "category"),
        ({"project_id": "Bad Project"}, "project_id"),
        ({"project_id": "../escape"}, "project_id"),
        ({"benefits": "one"}, "benefits"),
        ({"benefits": ["ok", ""]}, r"benefits\[1\]"),
        ({"reference_ids": ["ref-bad"]}, "reference_ids"),
        ({"reference_ids": "ref-0123456789abcdef"}, "reference_ids"),
        ({"sources": [{"name": "s", "url": "ftp://x", "evidence_urls": []}]}, r"sources\[0\]\.url"),
        ({"sources": [{"name": "s", "url": None, "evidence_urls": ["nope"]}]}, "evidence_urls"),
        ({"sources": [{"name": "s", "url": None}]}, "missing field"),
        ({"reconsiders": "rec-bad"}, "reconsiders"),
        ({"extra": 1}, "unknown field"),
        ({"project_context": context(github_repo="no-slash")}, "github_repo"),
        ({"project_context": context(github_repo="a/b/c")}, "github_repo"),
        ({"project_context": context(commit_sha="ABC")}, "commit_sha"),
        ({"project_context": context(analyzed_at="2026-10-27T00:00:00Z")}, "later than created_at"),
        ({"project_context": context(analyzed_files=["a.py", "a.py"])}, "duplicates"),
        ({"project_context": context(confidence=0.9)}, "unknown field"),
        (
            {"project_context": context(claims=[{"topic": "t", "statement": "s", "basis": "guessed", "sources": ["x"]}])},
            "basis",
        ),
        (
            {"project_context": context(claims=[{"topic": "t", "statement": "s", "basis": "observed", "sources": []}])},
            "needs at least one source",
        ),
        (
            {"project_context": context(claims=[{"topic": "t", "statement": "s", "basis": "inferred", "sources": []}])},
            "needs at least one source",
        ),
        (
            {"project_context": context(claims=[{"topic": "t", "statement": "s", "basis": "missing", "sources": ["x"]}])},
            "no sources",
        ),
    ],
)
def test_invalid_drafts_write_nothing(tmp_path, overrides, message):
    with pytest.raises(DataError, match=message):
        create_recommendation(tmp_path, draft(**overrides), now=T0)
    assert not (tmp_path / "recommendations").exists()


@pytest.mark.parametrize("key", ["project_id", "title", "project_context", "sources"])
def test_missing_required_field(tmp_path, key):
    data = draft()
    del data[key]
    with pytest.raises(DataError, match="missing field"):
        create_recommendation(tmp_path, data, now=T0)


@pytest.mark.parametrize("key, value", [("id", "rec-20261026T170000Z-ffffffff"), ("created_at", "2026-01-01T00:00:00Z"), ("schema_version", 1)])
def test_draft_cannot_set_automatic_fields(tmp_path, key, value):
    with pytest.raises(DataError, match="set automatically"):
        create_recommendation(tmp_path, draft(**{key: value}), now=T0)


def test_draft_must_be_a_dict(tmp_path):
    with pytest.raises(DataError):
        create_recommendation(tmp_path, ["not", "a", "dict"], now=T0)


def test_unknown_reference_is_rejected(tmp_path):
    with pytest.raises(NotFoundError):
        create_recommendation(tmp_path, draft(reference_ids=["ref-0123456789abcdef"]), now=T0)
    assert not (tmp_path / "recommendations").exists()


def test_duplicate_reference_ids_are_rejected(tmp_path):
    ref = make_reference(tmp_path)
    with pytest.raises(DataError, match="duplicates"):
        create_recommendation(tmp_path, draft(reference_ids=[ref.id, ref.id]), now=T0)


# --- immutability and reconsiderations -------------------------------------


def test_never_overwrites_an_existing_id(tmp_path, monkeypatch):
    monkeypatch.setattr(records, "_random_suffix", lambda: "0000000a")
    first = create_recommendation(tmp_path, draft(title="Original"), now=T0)
    before = rec_file(tmp_path, first.id).read_bytes()

    with pytest.raises(ConflictError, match="already exists"):
        create_recommendation(tmp_path, draft(title="Replacement"), now=T0)
    assert rec_file(tmp_path, first.id).read_bytes() == before


def test_reconsideration_keeps_the_original(tmp_path, fixed_suffix):
    original = create_recommendation(tmp_path, draft(title="Original"), now=T0)
    before = rec_file(tmp_path, original.id).read_bytes()

    revised = create_recommendation(tmp_path, draft(title="Revised", reconsiders=original.id), now=T1)
    assert revised.reconsiders == original.id
    assert revised.id != original.id
    assert rec_file(tmp_path, original.id).read_bytes() == before
    assert [r.title for r in list_recommendations(tmp_path, "example-project")] == ["Original", "Revised"]


def test_reconsidering_a_missing_recommendation(tmp_path):
    with pytest.raises(NotFoundError):
        create_recommendation(tmp_path, draft(reconsiders="rec-20261026T170000Z-0000000a"), now=T0)


def test_reconsidering_another_projects_recommendation(tmp_path, fixed_suffix):
    other = create_recommendation(tmp_path, draft(project_id="other-project"), now=T0)
    with pytest.raises(ConflictError, match="belongs to project"):
        create_recommendation(tmp_path, draft(reconsiders=other.id), now=T1)


def test_write_failure_leaves_no_file(tmp_path, monkeypatch, fixed_suffix):
    def broken_fsync(fd):
        raise OSError("disk full")

    monkeypatch.setattr(common.os, "fsync", broken_fsync)
    with pytest.raises(OSError, match="disk full"):
        create_recommendation(tmp_path, draft(), now=T0)
    assert os.listdir(tmp_path / "recommendations") == []


# --- load and list ---------------------------------------------------------


def test_list_by_project_in_creation_order(tmp_path, fixed_suffix):
    late = create_recommendation(tmp_path, draft(title="late"), now=T2)
    other = create_recommendation(tmp_path, draft(project_id="other-project"), now=T1)
    early = create_recommendation(tmp_path, draft(title="early"), now=T0)

    assert list_recommendations(tmp_path, "example-project") == (early, late)
    assert list_recommendations(tmp_path, "other-project") == (other,)
    assert list_recommendations(tmp_path, "nothing-here") == ()


def test_list_with_no_data_folder(tmp_path):
    assert list_recommendations(tmp_path / "absent", "example-project") == ()


def test_list_rejects_invalid_project_id(tmp_path):
    with pytest.raises(DataError):
        list_recommendations(tmp_path, "../x")


def test_list_stops_on_an_invalid_file(tmp_path, fixed_suffix):
    rec = create_recommendation(tmp_path, draft(), now=T0)
    rec_file(tmp_path, rec.id).write_text("{", encoding="utf-8")
    with pytest.raises(DataError, match="Malformed JSON"):
        list_recommendations(tmp_path, "example-project")


def test_list_stops_on_an_unexpected_json_name(tmp_path, fixed_suffix):
    create_recommendation(tmp_path, draft(), now=T0)
    (tmp_path / "recommendations" / "notes.json").write_text("{}", encoding="utf-8")
    with pytest.raises(DataError, match="Unexpected file name"):
        list_recommendations(tmp_path, "example-project")


def test_load_missing(tmp_path):
    with pytest.raises(NotFoundError):
        load_recommendation(tmp_path, "rec-20261026T170000Z-0000000a")


@pytest.mark.parametrize("bad", ["rec-x", "../rec-20261026T170000Z-0000000a", ""])
def test_load_rejects_unsafe_ids(tmp_path, bad):
    with pytest.raises(DataError):
        load_recommendation(tmp_path, bad)


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d.pop("justification"), "missing field"),
        (lambda d: d.update(id="rec-20261026T170000Z-ffffffff"), "does not match the file name"),
        (lambda d: d.update(created_at="2026-10-26T17:00:01Z"), "does not match created_at"),
        (lambda d: d.update(schema_version=99), "schema_version"),
        (lambda d: d["project_context"].pop("claims"), "missing field"),
    ],
)
def test_invalid_stored_recommendation(tmp_path, fixed_suffix, change, message):
    rec = create_recommendation(tmp_path, draft(), now=T0)
    path = rec_file(tmp_path, rec.id)
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DataError, match=message):
        load_recommendation(tmp_path, rec.id)
