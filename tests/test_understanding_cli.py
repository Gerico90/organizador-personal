import json

import pytest

from organizador.recommendations import common
from organizador.understanding.__main__ import main
from understanding_fixtures import git, make_repo, make_vault, snapshot


def base_args(repo, vault=None):
    args = ["--project-dir", str(repo), "--project-id", "demo-project", "--github-repo", "octo-cat/demo"]
    return args + (["--vault-dir", str(vault)] if vault else [])


def valid_draft(repo) -> dict:
    claims = [
        {"topic": t, "statement": f"Example {t}", "basis": "observed", "sources": ["README.md:1"]}
        for t in ("purpose", "capability", "architecture", "limitation")
    ] + [{"topic": "objective", "statement": "No plans were found", "basis": "missing", "sources": []}]
    return {
        "project_id": "demo-project",
        "context": {
            "github_repo": "octo-cat/demo",
            "commit_sha": git(repo, "rev-parse", "HEAD").strip(),
            "analyzed_at": common.utc_now(),  # the CLI checks against the real clock
            "analyzed_files": ["README.md"],
            "claims": claims,
        },
    }


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_collect_to_stdout(tmp_path, capsysbinary):
    repo = make_repo(tmp_path)
    assert main(["collect", *base_args(repo)]) == 0
    evidence = json.loads(capsysbinary.readouterr().out.decode("utf-8"))
    assert evidence["project_id"] == "demo-project"


def test_collect_to_new_file_never_overwrites(tmp_path, capsys):
    repo = make_repo(tmp_path)
    out = tmp_path / "scratch" / "evidence.json"
    assert main(["collect", *base_args(repo), "--out", str(out)]) == 0
    before = out.read_bytes()
    assert json.loads(before.decode("utf-8"))["github_repo"] == "octo-cat/demo"
    assert main(["collect", *base_args(repo), "--out", str(out)]) == 1
    assert "Error" in capsys.readouterr().err
    assert out.read_bytes() == before


def test_collect_error_exit_code(tmp_path, capsys):
    assert main(["collect", "--project-dir", str(tmp_path / "missing"), "--project-id", "demo-project"]) == 1
    assert "not found" in capsys.readouterr().err


def test_check_valid_draft_without_saving(tmp_path, capsys):
    repo = make_repo(tmp_path)
    draft = write_json(tmp_path / "draft.json", valid_draft(repo))
    assert main(["check", *base_args(repo), "--draft", str(draft)]) == 0
    assert "Draft is valid" in capsys.readouterr().out
    assert not (tmp_path / "data").exists()


def test_check_reports_problems(tmp_path, capsys):
    repo = make_repo(tmp_path)
    data = valid_draft(repo)
    data["context"]["claims"][0]["sources"] = ["README.md:999"]
    draft = write_json(tmp_path / "draft.json", data)
    assert main(["check", *base_args(repo), "--draft", str(draft), "--save", "--data-root", str(tmp_path / "data")]) == 1
    err = capsys.readouterr().err
    assert "1 problem(s)" in err and "outside 1-13" in err
    assert not (tmp_path / "data").exists()


def test_check_malformed_draft(tmp_path, capsys):
    repo = make_repo(tmp_path)
    draft = tmp_path / "draft.json"
    draft.write_text("{oops", encoding="utf-8")
    assert main(["check", *base_args(repo), "--draft", str(draft)]) == 1
    assert "cannot read draft" in capsys.readouterr().err


def test_save_needs_data_root(tmp_path, capsys):
    repo = make_repo(tmp_path)
    draft = write_json(tmp_path / "draft.json", valid_draft(repo))
    assert main(["check", *base_args(repo), "--draft", str(draft), "--save"]) == 1
    assert "--data-root" in capsys.readouterr().err


def test_check_and_save(tmp_path, capsys):
    repo = make_repo(tmp_path)
    vault = make_vault(tmp_path)
    before = (snapshot(repo), snapshot(vault))
    draft = write_json(tmp_path / "draft.json", valid_draft(repo))
    data_root = tmp_path / "data"

    assert main(["check", *base_args(repo, vault), "--draft", str(draft), "--save", "--data-root", str(data_root)]) == 0
    assert "Profile saved" in capsys.readouterr().out
    [saved] = list((data_root / "profiles").iterdir())
    stored = json.loads(saved.read_text(encoding="utf-8"))
    assert stored["inputs"] == {
        "working_tree_clean": True,
        "reports": ["report:2026-10-26T170604Z.md"],
        "notes": ["note:demo-project.md"],
    }
    assert (snapshot(repo), snapshot(vault)) == before


def test_check_on_changed_commit_fails(tmp_path, capsys):
    repo = make_repo(tmp_path)
    draft = write_json(tmp_path / "draft.json", valid_draft(repo))
    (repo / "README.md").write_text("# Changed\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "change")
    assert main(["check", *base_args(repo), "--draft", str(draft)]) == 1
    assert "commit_sha" in capsys.readouterr().err


def test_dirty_tree_note(tmp_path, capsys):
    repo = make_repo(tmp_path)
    draft = write_json(tmp_path / "draft.json", valid_draft(repo))
    with open(repo / "src" / "app.py", "a", encoding="utf-8") as f:
        f.write("# local edit\n")
    assert main(["check", *base_args(repo), "--draft", str(draft)]) == 0
    assert "uncommitted changes" in capsys.readouterr().out


def test_unknown_command_exits_with_usage():
    with pytest.raises(SystemExit) as exc:
        main(["explain"])
    assert exc.value.code == 2
