import json

from organizador.recommendations import common
from organizador.research import snapshots as snap_module
from organizador.research.__main__ import main
from organizador.understanding.evidence import collect_evidence
from organizador.understanding.profile import create_profile
from research_fixtures import DOC_HTML, DOC_URL, FakeNet, research_draft
from understanding_fixtures import git, make_repo, make_vault


def setup_project(tmp_path):
    """A real git repository, its vault, and a current profile in `data`."""
    repo, vault, data = make_repo(tmp_path), make_vault(tmp_path), tmp_path / "data"
    ev = collect_evidence(repo, project_id="demo-project", github_repo="octo-cat/demo", vault_dir=vault)
    draft = {
        "project_id": "demo-project",
        "context": {
            "github_repo": "octo-cat/demo",
            "commit_sha": ev["commit_sha"],
            "analyzed_at": common.utc_now(),
            "analyzed_files": ["README.md", "note:demo-project.md"],
            "claims": [
                {"topic": t, "statement": f"Example {t}", "basis": "observed", "sources": ["README.md:1"]}
                for t in ("purpose", "capability", "architecture")
            ]
            + [
                {"topic": "limitation", "statement": "Only one thing.", "basis": "observed", "sources": ["README.md:12-13"]},
                {"topic": "objective", "statement": "Goal: example.", "basis": "observed", "sources": ["note:demo-project.md:3"]},
            ],
        },
    }
    profile = create_profile(data, draft, ev)
    return repo, vault, data, profile


def project_args(repo, vault):
    return ["--project-dir", str(repo), "--github-repo", "octo-cat/demo", "--vault-dir", str(vault)]


def test_full_cycle_from_the_command_line(tmp_path, capsys, monkeypatch):
    repo, vault, data, profile = setup_project(tmp_path)
    net = FakeNet({DOC_URL: DOC_HTML})
    monkeypatch.setattr(snap_module, "default_resolver", net.resolver)
    monkeypatch.setattr(snap_module, "default_transport", net.transport)

    assert main(["start", "--data-root", str(data), "--project-id", "demo-project"]) == 0
    cycle = capsys.readouterr().out.strip()

    assert main(["brief", "--data-root", str(data), "--cycle-id", cycle, *project_args(repo, vault)]) == 0
    assert f"Profile {profile.id} is current" in capsys.readouterr().out
    brief = json.loads((data / "research" / cycle / "brief.json").read_text(encoding="utf-8"))
    assert brief["profile"]["current"] is True

    assert main(["fetch", "--data-root", str(data), "--cycle-id", cycle, DOC_URL]) == 0
    capsys.readouterr()

    draft = research_draft(cycle, profile.id)
    draft["recommendations"][0]["profile_claims"] = [4]
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(draft), encoding="utf-8")

    assert main(["check", "--data-root", str(data), "--draft", str(path), *project_args(repo, vault)]) == 0
    assert "Draft is valid" in capsys.readouterr().out
    assert main(["save", "--data-root", str(data), "--draft", str(path), *project_args(repo, vault)]) == 0
    out = capsys.readouterr().out
    assert "1 recommendation(s)" in out
    assert len(list((data / "recommendations").iterdir())) == 1


def test_brief_stops_on_a_stale_profile(tmp_path, capsys):
    repo, vault, data, profile = setup_project(tmp_path)
    (vault / "notes" / "projects" / "demo-project.md").write_text("# Context\n\nGoal: changed.\n", encoding="utf-8")
    assert main(["start", "--data-root", str(data), "--project-id", "demo-project"]) == 0
    cycle = capsys.readouterr().out.strip()
    assert main(["brief", "--data-root", str(data), "--cycle-id", cycle, *project_args(repo, vault)]) == 1
    out = capsys.readouterr().out
    assert "is not current" in out and "changed after the profile was made" in out


def test_brief_without_profile(tmp_path, capsys):
    repo, vault = make_repo(tmp_path), make_vault(tmp_path)
    data = tmp_path / "data"
    assert main(["start", "--data-root", str(data), "--project-id", "demo-project"]) == 0
    cycle = capsys.readouterr().out.strip()
    assert main(["brief", "--data-root", str(data), "--cycle-id", cycle, *project_args(repo, vault)]) == 1
    assert "No profile exists" in capsys.readouterr().out


def test_check_reports_problems_and_bad_drafts(tmp_path, capsys):
    repo, vault, data, profile = setup_project(tmp_path)
    git(repo, "commit", "-q", "--allow-empty", "-m", "newer commit")
    assert main(["start", "--data-root", str(data), "--project-id", "demo-project"]) == 0
    cycle = capsys.readouterr().out.strip()
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(research_draft(cycle, profile.id, [], references=[])), encoding="utf-8")
    assert main(["check", "--data-root", str(data), "--draft", str(path), *project_args(repo, vault)]) == 1
    assert "is not current" in capsys.readouterr().err

    path.write_text("{oops", encoding="utf-8")
    assert main(["check", "--data-root", str(data), "--draft", str(path), *project_args(repo, vault)]) == 1
    assert "cannot read draft" in capsys.readouterr().err
