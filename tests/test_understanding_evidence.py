import os
from pathlib import Path

import pytest

from organizador.understanding import evidence as evidence_module
from organizador.understanding.evidence import EvidenceError, collect_evidence, inspectable_refs
from understanding_fixtures import NOW, git, make_repo, make_vault, snapshot


def collect(repo, **kwargs):
    kwargs.setdefault("project_id", "demo-project")
    return collect_evidence(repo, now=NOW, **kwargs)


def by_ref(entries):
    return {e["ref"]: e for e in entries}


def test_basic_index(tmp_path):
    repo = make_repo(tmp_path)
    ev = collect(repo, github_repo="octo-cat/demo")

    assert ev["commit_sha"] == git(repo, "rev-parse", "HEAD").strip()
    assert ev["working_tree_clean"] is True
    assert ev["collected_at"] == "2026-10-27T10:00:00Z"
    assert ev["github_repo"] == "octo-cat/demo"
    assert ev["reports"] == [] and ev["notes"] == [] and ev["notes_checked"] is None

    files = by_ref(ev["files"])
    assert set(files) == {".gitignore", "README.md", "src/app.py", "src/broken.py"}
    readme = files["README.md"]
    assert readme["kind"] == "markdown" and readme["lines"] == 13
    assert readme["headings"] == [[1, 1, "Demo"], [5, 2, "Usage"], [12, 2, "Limits"]]  # not the fenced "#"

    app = files["src/app.py"]
    assert app["docstring"] == "Demo application."
    assert app["definitions"] == [[7, "class", "Store"], [11, "def", "main"]]
    assert files["src/broken.py"]["parse_error"] is True


def test_exclusions(tmp_path):
    repo = make_repo(tmp_path)
    excluded = {e["ref"]: e["excluded"] for e in collect(repo)["excluded"]}
    assert excluded == {
        ".env": "secret",
        "api_token.txt": "secret",
        "big.txt": "too_large",
        "keep/.gitkeep": "irrelevant",
        "latin1.txt": "binary",
        "logo.png": "binary",
        "package-lock.json": "irrelevant",
    }


def test_secret_files_are_never_opened(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    opened = []
    real = Path.read_bytes

    def spy(self):
        opened.append(self.name)
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", spy)
    collect(repo)
    assert ".env" not in opened and "api_token.txt" not in opened
    assert "local.toml" not in opened  # git-ignored


def test_ignored_and_untracked_files_are_not_indexed(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "new_untracked.md").write_text("# New\n", encoding="utf-8")
    ev = collect(repo)
    refs = set(inspectable_refs(ev)) | {e["ref"] for e in ev["excluded"]}
    assert "local.toml" not in refs and "new_untracked.md" not in refs
    assert ev["working_tree_clean"] is True  # untracked files do not count


def test_modified_tracked_file_marks_tree_dirty(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "README.md").write_text("# Changed\n", encoding="utf-8")
    ev = collect(repo)
    assert ev["working_tree_clean"] is False
    assert by_ref(ev["files"])["README.md"]["lines"] == 1  # working tree content is indexed


def test_deleted_tracked_file(tmp_path):
    repo = make_repo(tmp_path)
    os.remove(repo / "src" / "app.py")
    ev = collect(repo)
    assert {"ref": "src/app.py", "excluded": "deleted"} in ev["excluded"]


def test_tracked_symlink_is_excluded_not_followed(tmp_path):
    repo = make_repo(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    try:
        os.symlink(outside, repo / "link.md")
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"cannot create symlinks here: {e}")
    git(repo, "add", "link.md")
    git(repo, "commit", "-q", "-m", "link")
    ev = collect(repo)
    assert {"ref": "link.md", "excluded": "link"} in ev["excluded"]


def test_vault_reports_and_note(tmp_path):
    repo = make_repo(tmp_path)
    vault = make_vault(tmp_path)
    ev = collect(repo, github_repo="octo-cat/demo", vault_dir=vault)

    assert [r["ref"] for r in ev["reports"]] == ["report:2026-10-26T170604Z.md"]
    report = ev["reports"][0]
    assert report["front_matter"] == {
        "period_from": "2026-10-26T16:55:05Z",
        "period_to": "2026-10-26T17:06:04Z",
        "baseline": "false",
        "head_sha": "abc",
    }
    assert report["headings"] == [[9, 1, "Demo activity"], [11, 2, "Commits on main"]]
    assert [n["ref"] for n in ev["notes"]] == ["note:demo-project.md"]
    assert ev["notes_checked"].endswith("vault/notes/projects/demo-project.md")
    assert set(inspectable_refs(ev)) >= {"report:2026-10-26T170604Z.md", "note:demo-project.md", "README.md"}


def test_vault_without_note_or_github_repo(tmp_path):
    repo = make_repo(tmp_path)
    vault = make_vault(tmp_path, note=False)
    ev = collect(repo, vault_dir=vault)  # no github_repo: reports are not looked up
    assert ev["notes"] == [] and ev["reports"] == []
    assert ev["notes_checked"].endswith("demo-project.md")


def test_reports_and_notes_need_explicit_vault(tmp_path):
    repo = make_repo(tmp_path)
    make_vault(tmp_path)
    ev = collect(repo, github_repo="octo-cat/demo")
    assert ev["reports"] == [] and ev["notes"] == []


def test_linked_note_is_ignored(tmp_path):
    repo = make_repo(tmp_path)
    vault = make_vault(tmp_path, note=False)
    other = tmp_path / "other.md"
    other.write_text("# Other\n", encoding="utf-8")
    try:
        os.symlink(other, vault / "notes" / "projects" / "demo-project.md")
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"cannot create symlinks here: {e}")
    assert collect(repo, vault_dir=vault)["notes"] == []


def test_collection_is_read_only_and_deterministic(tmp_path):
    repo = make_repo(tmp_path)
    vault = make_vault(tmp_path)
    git_index = (repo / ".git" / "index").read_bytes()
    before = (snapshot(repo), snapshot(vault))

    first = collect(repo, github_repo="octo-cat/demo", vault_dir=vault)
    second = collect(repo, github_repo="octo-cat/demo", vault_dir=vault)

    assert first == second
    assert (snapshot(repo), snapshot(vault)) == before
    assert (repo / ".git" / "index").read_bytes() == git_index


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"project_id": "Bad Id"}, "project_id"),
        ({"github_repo": "no-slash"}, "owner/repo"),
    ],
)
def test_invalid_arguments(tmp_path, kwargs, message):
    repo = make_repo(tmp_path)
    with pytest.raises(EvidenceError, match=message):
        collect(repo, **kwargs)


def test_not_a_repository_or_not_its_top(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(EvidenceError, match="not found"):
        collect(tmp_path / "missing")
    with pytest.raises(EvidenceError, match="top folder"):
        collect(repo / "src")
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(EvidenceError):
        collect(plain)


def test_missing_vault_folder(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(EvidenceError, match="Vault folder not found"):
        collect(repo, vault_dir=tmp_path / "nope")


def test_git_not_installed(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(evidence_module.subprocess, "run", no_git)
    with pytest.raises(EvidenceError, match="git is not installed"):
        collect(repo)
