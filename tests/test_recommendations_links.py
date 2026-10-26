"""Links (symlinks, Windows junctions) must never redirect reads or writes
outside the data root. Tests are skipped only if the OS cannot create the link."""

import os
import sys

import pytest

from organizador.recommendations.check import check_data
from organizador.recommendations.common import DataError
from organizador.recommendations.feedback import append_feedback, feedback_history
from organizador.recommendations.records import create_recommendation, list_recommendations
from organizador.recommendations.references import (
    get_or_create_reference,
    load_reference,
    mark_reference_reviewed,
    reference_id_for,
)
from recommendation_fixtures import T0, T1, T2, draft


def symlink(target, link, is_dir=False):
    try:
        os.symlink(target, link, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"cannot create symlinks here: {e}")


def junction(target, link):
    if sys.platform != "win32":
        pytest.skip("junctions exist only on Windows")
    import _winapi

    try:
        _winapi.CreateJunction(str(target), str(link))
    except OSError as e:
        pytest.skip(f"cannot create junctions here: {e}")


def snapshot(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


@pytest.fixture
def dirs(tmp_path):
    root = tmp_path / "data"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    return root, outside


@pytest.fixture(params=["symlink", "junction"])
def link_dir(request):
    def make(target, link):
        if request.param == "symlink":
            symlink(target, link, is_dir=True)
        else:
            junction(target, link)

    return make


# --- linked folders --------------------------------------------------------


def test_linked_references_folder(dirs, link_dir):
    root, outside = dirs
    link_dir(outside, root / "references")
    with pytest.raises(DataError, match="link to another location"):
        get_or_create_reference(root, name="p", url="https://example.com/p", now=T0)
    with pytest.raises(DataError, match="link to another location"):
        load_reference(root, "ref-0123456789abcdef")
    assert snapshot(outside) == {}


def test_linked_recommendations_folder(dirs, link_dir):
    root, outside = dirs
    link_dir(outside, root / "recommendations")
    with pytest.raises(DataError, match="link to another location"):
        create_recommendation(root, draft(), now=T0)
    with pytest.raises(DataError, match="link to another location"):
        list_recommendations(root, "example-project")
    assert snapshot(outside) == {}


def test_linked_recommendations_folder_with_records_is_not_read(dirs, link_dir, tmp_path):
    root, outside = dirs
    elsewhere = tmp_path / "elsewhere"
    create_recommendation(elsewhere, draft(), now=T0)  # a valid record outside the root
    link_dir(elsewhere / "recommendations", root / "recommendations")
    with pytest.raises(DataError, match="link to another location"):
        list_recommendations(root, "example-project")


def test_linked_feedback_folder(dirs, link_dir):
    root, outside = dirs
    rec = create_recommendation(root, draft(), now=T0)
    link_dir(outside, root / "feedback")
    with pytest.raises(DataError, match="link to another location"):
        append_feedback(root, rec.id, rating="like", decision="explore", now=T1)
    with pytest.raises(DataError, match="link to another location"):
        feedback_history(root, rec.id)
    assert snapshot(outside) == {}


# --- linked files ----------------------------------------------------------


def test_linked_feedback_file_is_not_appended(dirs):
    root, outside = dirs
    rec = create_recommendation(root, draft(), now=T0)
    target = outside / "victim.txt"
    target.write_bytes(b"outside content\n")
    (root / "feedback").mkdir()
    symlink(target, root / "feedback" / f"{rec.id}.jsonl")

    with pytest.raises(DataError, match="link to another location"):
        append_feedback(root, rec.id, rating="like", decision="explore", now=T1)
    with pytest.raises(DataError, match="link to another location"):
        feedback_history(root, rec.id)
    assert target.read_bytes() == b"outside content\n"


def test_dangling_feedback_link_does_not_create_the_target(dirs):
    root, outside = dirs
    rec = create_recommendation(root, draft(), now=T0)
    (root / "feedback").mkdir()
    symlink(outside / "would-be-created.jsonl", root / "feedback" / f"{rec.id}.jsonl")

    with pytest.raises(DataError, match="link to another location"):
        append_feedback(root, rec.id, rating="like", decision="explore", now=T1)
    assert snapshot(outside) == {}


def test_linked_reference_file_is_not_read_or_rewritten(dirs, tmp_path):
    root, outside = dirs
    real = get_or_create_reference(tmp_path / "elsewhere", name="p", url="https://example.com/p", now=T0).reference
    target = tmp_path / "elsewhere" / "references" / f"{real.id}.json"
    before = target.read_bytes()
    (root / "references").mkdir()
    symlink(target, root / "references" / f"{real.id}.json")

    with pytest.raises(DataError, match="link to another location"):
        load_reference(root, real.id)
    with pytest.raises(DataError, match="link to another location"):
        get_or_create_reference(root, name="p", url="https://example.com/p", now=T1)
    with pytest.raises(DataError, match="link to another location"):
        mark_reference_reviewed(root, real.id, now=T2)
    assert target.read_bytes() == before


def test_linked_recommendation_file_is_not_listed(dirs, tmp_path):
    root, _ = dirs
    rec = create_recommendation(tmp_path / "elsewhere", draft(), now=T0)
    (root / "recommendations").mkdir()
    symlink(tmp_path / "elsewhere" / "recommendations" / f"{rec.id}.json", root / "recommendations" / f"{rec.id}.json")
    with pytest.raises(DataError, match="link to another location"):
        list_recommendations(root, "example-project")


# --- check_data ------------------------------------------------------------


def test_check_reports_links_and_does_not_follow_them(dirs, link_dir):
    root, outside = dirs
    rec = create_recommendation(root, draft(), now=T0)
    (outside / "x.jsonl").write_bytes(b"not json\n")
    (root / "feedback").mkdir()
    symlink(outside / "x.jsonl", root / "feedback" / f"{rec.id}.jsonl")
    link_dir(outside, root / "references")
    before = snapshot(outside)

    problems = "\n".join(check_data(root))
    assert "references: it is a link to another location" in problems
    assert f"link to another location): {root.resolve() / 'feedback' / (rec.id + '.jsonl')}" in problems
    assert "malformed JSON" not in problems  # the linked file was not read
    assert snapshot(outside) == before


# --- normal behaviour is unchanged -----------------------------------------


def test_root_given_through_a_link_still_works(tmp_path):
    real_root = tmp_path / "real"
    real_root.mkdir()
    linked_root = tmp_path / "linked"
    symlink(real_root, linked_root, is_dir=True)

    ref = get_or_create_reference(linked_root, name="p", url="https://example.com/p", now=T0).reference
    assert (real_root / "references" / f"{ref.id}.json").is_file()
    assert reference_id_for("https://example.com/p") == ref.id
