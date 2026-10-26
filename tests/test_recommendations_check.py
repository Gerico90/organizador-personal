import json
import os
from pathlib import Path

from organizador.recommendations.check import check_data
from organizador.recommendations.feedback import append_feedback
from organizador.recommendations.records import create_recommendation
from recommendation_fixtures import T0, T1, T2, draft, make_reference

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def build_valid_data(root):
    ref = make_reference(root)
    a = create_recommendation(root, draft(reference_ids=[ref.id]), now=T0)
    b = create_recommendation(root, draft(reference_ids=[ref.id], reconsiders=a.id), now=T1)
    append_feedback(root, a.id, rating="like", decision="explore", now=T1)
    append_feedback(root, a.id, rating=None, decision="future", now=T2)
    return ref, a, b


def test_empty_or_missing_root_is_clean(tmp_path):
    assert check_data(tmp_path / "absent") == []
    assert check_data(tmp_path) == []


def test_valid_data_has_no_problems_and_is_not_changed(tmp_path):
    build_valid_data(tmp_path)
    before = snapshot(tmp_path)
    assert check_data(tmp_path) == []
    assert snapshot(tmp_path) == before


def test_reports_every_problem_without_changing_anything(tmp_path):
    ref, a, b = build_valid_data(tmp_path)
    # Break the shared reference, add an orphan feedback file, a stray temp
    # file, a malformed recommendation, and an incomplete feedback line.
    (tmp_path / "references" / f"{ref.id}.json").write_text("{", encoding="utf-8")
    orphan = "rec-20991231T000000Z-ffffffff"
    (tmp_path / "feedback" / f"{orphan}.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "references" / ".x.json.123.tmp").write_text("", encoding="utf-8")
    broken = tmp_path / "recommendations" / "rec-20261027T000000Z-00000000.json"
    broken.write_text("[]", encoding="utf-8")
    with open(tmp_path / "feedback" / f"{a.id}.jsonl", "ab") as f:
        f.write(b'{"partial": ')
    before = snapshot(tmp_path)

    problems = check_data(tmp_path)
    text = "\n".join(problems)

    assert "Malformed JSON" in text and ref.id in text
    assert f"Recommendation {a.id} uses reference {ref.id}" in text
    assert f"Recommendation {b.id} uses reference {ref.id}" in text
    assert "Unexpected entry" in text and ".tmp" in text
    assert "rec-20261027T000000Z-00000000" in text
    assert f"belongs to recommendation {orphan}" in text
    assert "incomplete" in text
    assert snapshot(tmp_path) == before


def test_reports_dangling_and_cross_project_reconsiderations(tmp_path):
    other = create_recommendation(tmp_path, draft(project_id="other-project"), now=T0)
    rec = create_recommendation(tmp_path, draft(reconsiders=None), now=T1)
    path = tmp_path / "recommendations" / f"{rec.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    data["reconsiders"] = other.id
    path.write_text(json.dumps(data), encoding="utf-8")
    assert any("from a different project" in p for p in check_data(tmp_path))

    os.remove(tmp_path / "recommendations" / f"{other.id}.json")
    assert any(f"reconsiders {other.id}, which is missing" in p for p in check_data(tmp_path))


def test_folder_in_place_of_a_record_is_reported(tmp_path):
    (tmp_path / "references" / "ref-0123456789abcdef.json").mkdir(parents=True)
    assert any("Unexpected entry" in p for p in check_data(tmp_path))


# --- MVP 1 compatibility and data ownership --------------------------------


def test_data_folder_is_git_ignored():
    lines = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "data/" in lines


def test_storage_does_not_read_config_or_github_state():
    source = "\n".join(
        p.read_text(encoding="utf-8") for p in (PROJECT_ROOT / "src" / "organizador" / "recommendations").glob("*.py")
    )
    assert "organizador.config" not in source
    assert "organizador.github" not in source
