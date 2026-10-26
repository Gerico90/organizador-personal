import pytest

from organizador.recommendations.__main__ import main
from organizador.recommendations.feedback import feedback_history
from organizador.recommendations.records import create_recommendation
from recommendation_fixtures import T0, draft, make_reference


@pytest.fixture
def recs(tmp_path):
    ref = make_reference(tmp_path)
    a = create_recommendation(tmp_path, draft(title="First idea", reference_ids=[ref.id]), now=T0)
    b = create_recommendation(tmp_path, draft(title="Second idea"), now=T0)
    return a, b


def run(capsys, *args):
    code = main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


def test_list(tmp_path, recs, capsys):
    a, b = recs
    code, out, _ = run(capsys, "list", "--data-root", str(tmp_path), "--project-id", "example-project")
    assert code == 0
    assert f"{a.id}  [not evaluated]  First idea" in out
    assert f"{b.id}  [not evaluated]  Second idea" in out
    code, out, _ = run(capsys, "list", "--data-root", str(tmp_path), "--project-id", "nothing-here")
    assert code == 0 and "No recommendations" in out


def test_feedback_keeps_rating_and_decision_independent(tmp_path, recs, capsys):
    a, _ = recs
    code, out, _ = run(capsys, "feedback", "--data-root", str(tmp_path), a.id, "--decision", "explore", "--rating", "dislike")
    assert code == 0 and "decision explore, rating dislike" in out
    code, out, _ = run(capsys, "feedback", "--data-root", str(tmp_path), a.id, "--decision", "future", "--notes", "Later")
    assert code == 0 and "decision future, rating none" in out

    history = feedback_history(tmp_path, a.id)
    assert [(e.rating, e.decision, e.notes) for e in history] == [("dislike", "explore", None), (None, "future", "Later")]
    _, out, _ = run(capsys, "list", "--data-root", str(tmp_path), "--project-id", "example-project")
    assert f"{a.id}  [future, rating none]" in out


def test_show(tmp_path, recs, capsys):
    a, b = recs
    run(capsys, "feedback", "--data-root", str(tmp_path), a.id, "--decision", "pending", "--rating", "like", "--notes", "Nota ✓")
    code, out, _ = run(capsys, "show", "--data-root", str(tmp_path), a.id)
    assert code == 0
    assert out.startswith("First idea\n")
    assert "Relationship: Example relationship" in out
    assert "tool <https://github.com/octo-cat/tool>" in out
    assert "evidence: https://example.com/source/evidence" in out
    assert "[capability, missing] No documentation of deployment was found" in out
    assert "decision pending, rating like" in out
    _, out, _ = run(capsys, "show", "--data-root", str(tmp_path), b.id)
    assert "not evaluated yet" in out


@pytest.mark.parametrize(
    "args, message",
    [
        (["feedback", "rec-20261026T170000Z-ffffffff", "--decision", "explore"], "not found"),
        (["feedback", "../x", "--decision", "explore"], "not valid"),
        (["show", "rec-20261026T170000Z-ffffffff"], "not found"),
    ],
)
def test_errors(tmp_path, recs, capsys, args, message):
    code, _, err = run(capsys, args[0], "--data-root", str(tmp_path), *args[1:])
    assert code == 1 and message in err


@pytest.mark.parametrize("args", [["--decision", "maybe"], ["--decision", "explore", "--rating", "love"], []])
def test_invalid_choices_are_rejected_by_the_parser(tmp_path, recs, args):
    with pytest.raises(SystemExit) as exc:
        main(["feedback", "--data-root", str(tmp_path), recs[0].id, *args])
    assert exc.value.code == 2
    assert feedback_history(tmp_path, recs[0].id) == ()


def test_blank_notes_are_rejected(tmp_path, recs, capsys):
    code, _, err = run(capsys, "feedback", "--data-root", str(tmp_path), recs[0].id, "--decision", "explore", "--notes", "  ")
    assert code == 1 and "notes" in err
    assert feedback_history(tmp_path, recs[0].id) == ()
