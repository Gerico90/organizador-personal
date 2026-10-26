import json
import os

import pytest

from organizador.recommendations import feedback as feedback_module
from organizador.recommendations.common import ConflictError, DataError, NotFoundError
from organizador.recommendations.feedback import (
    FeedbackEvent,
    append_feedback,
    feedback_history,
    latest_feedback,
)
from organizador.recommendations.records import create_recommendation
from recommendation_fixtures import T0, T1, T2, draft


@pytest.fixture
def rec(tmp_path):
    return create_recommendation(tmp_path, draft(), now=T0)


def history_file(root, rec_id):
    return root / "feedback" / f"{rec_id}.jsonl"


def test_no_feedback_yet(tmp_path, rec):
    assert feedback_history(tmp_path, rec.id) == ()
    assert latest_feedback(tmp_path, rec.id) is None
    assert not (tmp_path / "feedback").exists()


def test_append_and_read(tmp_path, rec):
    event = append_feedback(tmp_path, rec.id, rating="like", decision="explore", notes="Nota ✓", now=T1)
    assert event == FeedbackEvent(rec.id, "2026-10-26T18:00:00Z", "like", "explore", "Nota ✓")
    assert feedback_history(tmp_path, rec.id) == (event,)
    assert latest_feedback(tmp_path, rec.id) == event

    raw = history_file(tmp_path, rec.id).read_bytes()
    assert raw.endswith(b"\n") and b"\r\n" not in raw
    assert json.loads(raw.decode("utf-8")) == {
        "schema_version": 1,
        "recommendation_id": rec.id,
        "evaluated_at": "2026-10-26T18:00:00Z",
        "rating": "like",
        "decision": "explore",
        "notes": "Nota ✓",
    }


def test_no_rating_and_no_notes(tmp_path, rec):
    event = append_feedback(tmp_path, rec.id, rating=None, decision="pending", now=T1)
    assert event.rating is None and event.notes is None


def test_change_of_opinion_keeps_history(tmp_path, rec):
    first = append_feedback(tmp_path, rec.id, rating="like", decision="future", now=T1)
    first_bytes = history_file(tmp_path, rec.id).read_bytes()
    second = append_feedback(tmp_path, rec.id, rating="dislike", decision="discard", notes="Changed my mind", now=T2)

    assert feedback_history(tmp_path, rec.id) == (first, second)
    assert latest_feedback(tmp_path, rec.id) == second
    assert history_file(tmp_path, rec.id).read_bytes().startswith(first_bytes)


def test_same_second_keeps_file_order(tmp_path, rec):
    a = append_feedback(tmp_path, rec.id, rating=None, decision="pending", now=T1)
    b = append_feedback(tmp_path, rec.id, rating="like", decision="explore", now=T1)
    assert feedback_history(tmp_path, rec.id) == (a, b)


def test_earlier_evaluation_is_rejected(tmp_path, rec):
    append_feedback(tmp_path, rec.id, rating="like", decision="explore", now=T2)
    before = history_file(tmp_path, rec.id).read_bytes()
    with pytest.raises(ConflictError, match="earlier"):
        append_feedback(tmp_path, rec.id, rating="dislike", decision="discard", now=T1)
    assert history_file(tmp_path, rec.id).read_bytes() == before


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"rating": "love", "decision": "explore"}, "rating"),
        ({"rating": "", "decision": "explore"}, "rating"),
        ({"rating": "like", "decision": "maybe"}, "decision"),
        ({"rating": "like", "decision": None}, "decision"),
        ({"rating": "like", "decision": "explore", "notes": "  "}, "notes"),
    ],
)
def test_invalid_values_write_nothing(tmp_path, rec, kwargs, message):
    with pytest.raises(DataError, match=message):
        append_feedback(tmp_path, rec.id, now=T1, **kwargs)
    assert not history_file(tmp_path, rec.id).exists()


def test_recommendation_must_exist(tmp_path):
    missing = "rec-20261026T170000Z-0000000a"
    with pytest.raises(NotFoundError):
        append_feedback(tmp_path, missing, rating="like", decision="explore", now=T1)
    with pytest.raises(NotFoundError):
        feedback_history(tmp_path, missing)
    assert not (tmp_path / "feedback").exists()


@pytest.mark.parametrize("bad", ["../x", "rec-bad", ""])
def test_unsafe_ids_are_rejected(tmp_path, bad):
    with pytest.raises(DataError):
        append_feedback(tmp_path, bad, rating="like", decision="explore", now=T1)


# --- corrupted or incomplete files -----------------------------------------


def valid_line(rec_id, when="2026-10-26T18:00:00Z", **overrides):
    data = {
        "schema_version": 1,
        "recommendation_id": rec_id,
        "evaluated_at": when,
        "rating": "like",
        "decision": "explore",
        "notes": None,
    }
    data.update(overrides)
    return json.dumps(data)


@pytest.mark.parametrize(
    "make_content, message",
    [
        (lambda r: valid_line(r), "incomplete"),  # no final newline: interrupted write
        (lambda r: valid_line(r) + "\n\n", "empty line"),
        (lambda r: "{oops\n", "malformed JSON"),
        (lambda r: "[1, 2]\n", "JSON object"),
        (lambda r: valid_line(r, decision="maybe") + "\n", "decision"),
        (lambda r: valid_line(r, recommendation_id="rec-20991231T000000Z-ffffffff") + "\n", "does not match the file"),
        (lambda r: valid_line(r, extra=1) + "\n", "unknown field"),
        (lambda r: valid_line(r, schema_version=2) + "\n", "schema_version"),
        (
            lambda r: valid_line(r, "2026-10-27T00:00:00Z") + "\n" + valid_line(r, "2026-10-26T00:00:00Z") + "\n",
            "earlier than the previous line",
        ),
    ],
)
def test_corrupted_history_is_reported_not_repaired(tmp_path, rec, make_content, message):
    path = history_file(tmp_path, rec.id)
    path.parent.mkdir(parents=True)
    content = make_content(rec.id).encode("utf-8")
    path.write_bytes(content)

    with pytest.raises(DataError, match=message):
        feedback_history(tmp_path, rec.id)
    with pytest.raises(DataError, match=message):
        append_feedback(tmp_path, rec.id, rating="like", decision="explore", now=T2)
    assert path.read_bytes() == content


def test_invalid_utf8_is_reported(tmp_path, rec):
    path = history_file(tmp_path, rec.id)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe\n")
    with pytest.raises(DataError, match="UTF-8"):
        feedback_history(tmp_path, rec.id)


def test_empty_file_means_no_feedback(tmp_path, rec):
    path = history_file(tmp_path, rec.id)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"")
    assert latest_feedback(tmp_path, rec.id) is None


def test_failed_write_removes_only_the_partial_line(tmp_path, rec, monkeypatch):
    append_feedback(tmp_path, rec.id, rating="like", decision="explore", now=T1)
    before = history_file(tmp_path, rec.id).read_bytes()

    def broken_fsync(fd):
        raise OSError("disk full")

    monkeypatch.setattr(feedback_module.os, "fsync", broken_fsync)
    with pytest.raises(OSError, match="disk full"):
        append_feedback(tmp_path, rec.id, rating="dislike", decision="discard", now=T2)
    assert history_file(tmp_path, rec.id).read_bytes() == before
    monkeypatch.undo()
    assert len(feedback_history(tmp_path, rec.id)) == 1


def test_feedback_does_not_touch_the_recommendation(tmp_path, rec):
    rec_path = tmp_path / "recommendations" / f"{rec.id}.json"
    before = rec_path.read_bytes()
    append_feedback(tmp_path, rec.id, rating="dislike", decision="discard", now=T1)
    assert rec_path.read_bytes() == before
    assert sorted(os.listdir(tmp_path)) == ["feedback", "recommendations"]


def test_no_edit_or_delete_functions_exist():
    public = {name for name in dir(feedback_module) if not name.startswith("_")}
    assert not {n for n in public if any(w in n for w in ("edit", "update", "delete", "remove", "replace"))}
