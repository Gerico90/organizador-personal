import json
import os

import pytest

from organizador import fsutil
from organizador.recommendations import common
from organizador.recommendations.common import ConflictError, DataError, NotFoundError
from organizador.recommendations.references import (
    canonical_url,
    get_or_create_reference,
    load_reference,
    mark_reference_reviewed,
    reference_id_for,
)
from recommendation_fixtures import T0, T1, T2


def ref_file(root, ref_id):
    return root / "references" / f"{ref_id}.json"


# --- canonical URLs --------------------------------------------------------


@pytest.mark.parametrize(
    "given",
    [
        "https://github.com/Octo-Cat/My.Repo",
        "https://github.com/octo-cat/my.repo",
        "http://github.com/octo-cat/my.repo",
        "https://www.github.com/octo-cat/my.repo",
        "https://GITHUB.com/octo-cat/my.repo/",
        "https://github.com/octo-cat/my.repo.git",
        "https://github.com/octo-cat/my.repo#readme",
        "https://github.com:443/octo-cat/my.repo",
    ],
)
def test_github_urls_normalize_to_one_form(given):
    assert canonical_url(given) == "https://github.com/octo-cat/my.repo"


@pytest.mark.parametrize(
    "given",
    [
        "https://github.com/octo-cat",
        "https://github.com/octo-cat/my.repo/blob/main/README.md",
        "https://github.com/octo-cat/my.repo/issues/3",
        "https://github.com/octo-cat/my.repo?tab=readme",
        "https://github.com/-bad/repo",
        "https://github.com/octo-cat/..",
        "https://github.com:8443/octo-cat/my.repo",
    ],
)
def test_github_urls_must_be_repository_roots(given):
    with pytest.raises(ValueError):
        canonical_url(given)


@pytest.mark.parametrize(
    "given, expected",
    [
        ("HTTPS://Example.COM/Docs/", "https://example.com/Docs"),
        ("https://example.com:443/x", "https://example.com/x"),
        ("http://example.com:80/x", "http://example.com/x"),
        ("https://example.com:8443/x", "https://example.com:8443/x"),
        ("https://example.com/x?b=2#part", "https://example.com/x?b=2"),
        ("https://example.com/", "https://example.com"),
    ],
)
def test_other_urls_light_normalization(given, expected):
    assert canonical_url(given) == expected


# --- create or retrieve ----------------------------------------------------


def test_create_reference(tmp_path):
    result = get_or_create_reference(
        tmp_path, name="My Repo", url="https://github.com/Octo-Cat/My.Repo", description="Désc", now=T0
    )
    ref = result.reference
    assert result.created is True and result.differences == ()
    assert ref.id == reference_id_for("https://github.com/octo-cat/my.repo")
    assert ref.canonical_url == "https://github.com/octo-cat/my.repo"
    assert ref.first_discovered_at == ref.last_reviewed_at == "2026-10-26T17:00:00Z"
    stored = json.loads(ref_file(tmp_path, ref.id).read_text(encoding="utf-8"))
    assert stored == {
        "schema_version": 1,
        "id": ref.id,
        "name": "My Repo",
        "canonical_url": "https://github.com/octo-cat/my.repo",
        "description": "Désc",
        "first_discovered_at": "2026-10-26T17:00:00Z",
        "last_reviewed_at": "2026-10-26T17:00:00Z",
    }
    assert "Désc" in ref_file(tmp_path, ref.id).read_bytes().decode("utf-8")  # UTF-8, not \u escapes


def test_description_is_optional(tmp_path):
    result = get_or_create_reference(tmp_path, name="x", url="https://example.com/x", now=T0)
    assert result.reference.description is None


def test_retrieve_existing_by_equivalent_url_without_changes(tmp_path):
    first = get_or_create_reference(tmp_path, name="My Repo", url="https://github.com/octo-cat/my.repo", now=T0)
    before = ref_file(tmp_path, first.reference.id).read_bytes()

    again = get_or_create_reference(
        tmp_path, name="My Repo", url="https://github.com/OCTO-CAT/MY.REPO.git", now=T1
    )
    assert again.created is False
    assert again.reference == first.reference
    assert again.differences == ()
    assert ref_file(tmp_path, first.reference.id).read_bytes() == before
    assert len(os.listdir(tmp_path / "references")) == 1


def test_metadata_differences_are_reported_not_written(tmp_path):
    first = get_or_create_reference(tmp_path, name="Name A", url="https://example.com/p", description="A", now=T0)
    before = ref_file(tmp_path, first.reference.id).read_bytes()

    again = get_or_create_reference(tmp_path, name="Name B", url="https://example.com/p", description=None, now=T1)
    assert again.created is False
    assert again.differences == ("name", "description")
    assert again.reference.name == "Name A" and again.reference.description == "A"
    assert ref_file(tmp_path, first.reference.id).read_bytes() == before


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "", "url": "https://example.com"},
        {"name": "x", "url": "not a url"},
        {"name": "x", "url": "https://github.com/o/r/blob/main/f.py"},
        {"name": "x", "url": "https://example.com", "description": "  "},
    ],
)
def test_invalid_input_writes_nothing(tmp_path, kwargs):
    with pytest.raises(DataError):
        get_or_create_reference(tmp_path, now=T0, **kwargs)
    assert not (tmp_path / "references").exists()


def test_existing_invalid_file_is_reported_not_replaced(tmp_path):
    ref_id = reference_id_for("https://example.com/p")
    path = ref_file(tmp_path, ref_id)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"{broken")
    with pytest.raises(DataError, match="Malformed JSON"):
        get_or_create_reference(tmp_path, name="p", url="https://example.com/p", now=T0)
    assert path.read_bytes() == b"{broken"


def test_existing_file_for_other_url_is_a_conflict(tmp_path):
    good = get_or_create_reference(tmp_path, name="p", url="https://example.com/p", now=T0).reference
    path = ref_file(tmp_path, good.id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["canonical_url"] = "https://example.com/other"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DataError, match="does not match canonical_url"):
        get_or_create_reference(tmp_path, name="p", url="https://example.com/p", now=T1)


def test_write_failure_leaves_no_file(tmp_path, monkeypatch):
    def broken_fsync(fd):
        raise OSError("disk full")

    monkeypatch.setattr(common.os, "fsync", broken_fsync)
    with pytest.raises(OSError, match="disk full"):
        get_or_create_reference(tmp_path, name="p", url="https://example.com/p", now=T0)
    assert os.listdir(tmp_path / "references") == []


# --- load ------------------------------------------------------------------


def test_load_reference(tmp_path):
    ref = get_or_create_reference(tmp_path, name="p", url="https://example.com/p", now=T0).reference
    assert load_reference(tmp_path, ref.id) == ref


def test_load_missing_reference(tmp_path):
    with pytest.raises(NotFoundError):
        load_reference(tmp_path, "ref-0123456789abcdef")


@pytest.mark.parametrize("bad", ["../ref-0123456789abcdef", "ref-x", ""])
def test_load_rejects_unsafe_ids(tmp_path, bad):
    with pytest.raises(DataError):
        load_reference(tmp_path, bad)


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d.pop("name"), "missing field"),
        (lambda d: d.update(extra=1), "unknown field"),
        (lambda d: d.update(schema_version=2), "schema_version"),
        (lambda d: d.update(id="ref-0000000000000000"), "does not match the file name"),
        (lambda d: d.update(canonical_url="https://EXAMPLE.com/p"), "not normalized"),
        (lambda d: d.update(last_reviewed_at="2026-10-25T00:00:00Z"), "earlier than first_discovered_at"),
        (lambda d: d.update(first_discovered_at="yesterday"), "UTC timestamp"),
        (lambda d: d.update(description=""), "description"),
    ],
)
def test_invalid_stored_reference(tmp_path, change, message):
    ref = get_or_create_reference(tmp_path, name="p", url="https://example.com/p", now=T0).reference
    path = ref_file(tmp_path, ref.id)
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DataError, match=message):
        load_reference(tmp_path, ref.id)


# --- last_reviewed_at ------------------------------------------------------


def test_mark_reviewed_changes_only_last_reviewed_at(tmp_path):
    ref = get_or_create_reference(tmp_path, name="p", url="https://example.com/p", description="d", now=T0).reference
    path = ref_file(tmp_path, ref.id)
    before = json.loads(path.read_text(encoding="utf-8"))

    updated = mark_reference_reviewed(tmp_path, ref.id, now=T2)
    after = json.loads(path.read_text(encoding="utf-8"))
    assert updated.last_reviewed_at == after["last_reviewed_at"] == "2026-10-27T09:30:00Z"
    before.pop("last_reviewed_at")
    after.pop("last_reviewed_at")
    assert before == after
    assert os.listdir(tmp_path / "references") == [path.name]  # no temp file left


def test_mark_reviewed_cannot_go_back_in_time(tmp_path):
    ref = get_or_create_reference(tmp_path, name="p", url="https://example.com/p", now=T1).reference
    before = ref_file(tmp_path, ref.id).read_bytes()
    with pytest.raises(ConflictError):
        mark_reference_reviewed(tmp_path, ref.id, now=T0)
    assert ref_file(tmp_path, ref.id).read_bytes() == before


def test_mark_reviewed_missing_reference(tmp_path):
    with pytest.raises(NotFoundError):
        mark_reference_reviewed(tmp_path, "ref-0123456789abcdef", now=T0)


def test_mark_reviewed_write_failure_keeps_original(tmp_path, monkeypatch):
    ref = get_or_create_reference(tmp_path, name="p", url="https://example.com/p", now=T0).reference
    before = ref_file(tmp_path, ref.id).read_bytes()

    def broken_replace(src, dst):
        raise OSError("locked")

    monkeypatch.setattr(fsutil.os, "replace", broken_replace)
    with pytest.raises(OSError, match="locked"):
        mark_reference_reviewed(tmp_path, ref.id, now=T2)
    assert ref_file(tmp_path, ref.id).read_bytes() == before
    assert os.listdir(tmp_path / "references") == [f"{ref.id}.json"]
