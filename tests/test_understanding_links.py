"""Links in folders on the way to a file must not let the collector read
outside the project or the vault. Tests are skipped only if the OS cannot
create the link."""

import os
import shutil
import sys
from pathlib import Path

import pytest

from organizador.understanding.evidence import collect_evidence
from understanding_fixtures import APP, NOW, git, make_repo, make_vault

OUTSIDE_TEXT = "# Outside\n\nmust not be read\n"


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


@pytest.fixture(params=["symlink", "junction"])
def link_dir(request):
    def make(target, link):
        if request.param == "symlink":
            symlink(target, link, is_dir=True)
        else:
            junction(target, link)

    return make


@pytest.fixture
def opened(monkeypatch):
    """Names of every file whose bytes are read during the test."""
    names = []
    real = Path.read_bytes

    def spy(self):
        names.append(self.resolve().as_posix())
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", spy)
    return names


def outside_dir(tmp_path, *files):
    folder = tmp_path / "outside"
    folder.mkdir(exist_ok=True)
    for name in files:
        (folder / name).write_text(OUTSIDE_TEXT, encoding="utf-8")
    return folder


def collect(repo, **kwargs):
    return collect_evidence(repo, project_id="demo-project", now=NOW, **kwargs)


def refs(entries):
    return {e["ref"] for e in entries}


def read_outside(opened, folder):
    return [p for p in opened if p.startswith(folder.resolve().as_posix())]


# --- project files ---------------------------------------------------------


def test_tracked_file_under_a_linked_folder_is_not_read(tmp_path, link_dir, opened):
    repo = make_repo(tmp_path)
    outside = outside_dir(tmp_path, "app.py", "broken.py")
    shutil.rmtree(repo / "src")
    link_dir(outside, repo / "src")  # git still tracks src/app.py and src/broken.py

    ev = collect(repo)
    assert {"ref": "src/app.py", "excluded": "link"} in ev["excluded"]
    assert {"ref": "src/broken.py", "excluded": "link"} in ev["excluded"]
    assert "src/app.py" not in refs(ev["files"])
    assert read_outside(opened, outside) == []
    assert "README.md" in refs(ev["files"])  # unaffected files are still indexed


def test_link_deeper_in_the_path(tmp_path, link_dir, opened):
    repo = make_repo(tmp_path)
    (repo / "pkg" / "inner").mkdir(parents=True)
    (repo / "pkg" / "inner" / "mod.py").write_text(APP, encoding="utf-8", newline="\n")
    git(repo, "add", "pkg")
    git(repo, "commit", "-q", "-m", "nested")
    outside = outside_dir(tmp_path, "mod.py")
    shutil.rmtree(repo / "pkg" / "inner")
    link_dir(outside, repo / "pkg" / "inner")

    ev = collect(repo)
    assert {"ref": "pkg/inner/mod.py", "excluded": "link"} in ev["excluded"]
    assert read_outside(opened, outside) == []


def test_nested_normal_folders_are_indexed(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "pkg" / "inner").mkdir(parents=True)
    (repo / "pkg" / "inner" / "mod.py").write_text(APP, encoding="utf-8", newline="\n")
    git(repo, "add", "pkg")
    git(repo, "commit", "-q", "-m", "nested")

    ev = collect(repo)
    assert "pkg/inner/mod.py" in refs(ev["files"])
    assert not [e for e in ev["excluded"] if e["excluded"] == "link"]


def test_secret_rule_still_wins_under_a_linked_folder(tmp_path, link_dir, opened):
    repo = make_repo(tmp_path)
    (repo / "conf").mkdir()
    (repo / "conf" / ".env").write_text("X=1\n", encoding="utf-8")
    git(repo, "add", "-f", "conf/.env")
    git(repo, "commit", "-q", "-m", "env")
    outside = outside_dir(tmp_path, ".env")
    shutil.rmtree(repo / "conf")
    link_dir(outside, repo / "conf")

    assert {"ref": "conf/.env", "excluded": "secret"} in collect(repo)["excluded"]
    assert read_outside(opened, outside) == []


def test_project_root_given_through_a_link_still_works(tmp_path, link_dir):
    repo = make_repo(tmp_path)
    linked_root = tmp_path / "linked-project"
    link_dir(repo, linked_root)

    ev = collect(linked_root)
    assert "src/app.py" in refs(ev["files"])
    assert not [e for e in ev["excluded"] if e["excluded"] == "link"]


# --- vault notes and reports -----------------------------------------------


def test_note_under_a_linked_folder_is_not_read(tmp_path, link_dir, opened):
    repo = make_repo(tmp_path)
    vault = make_vault(tmp_path, note=False)
    outside = outside_dir(tmp_path, "demo-project.md")
    (vault / "notes" / "projects").rmdir()
    link_dir(outside, vault / "notes" / "projects")

    ev = collect(repo, vault_dir=vault)
    assert ev["notes"] == []
    assert read_outside(opened, outside) == []


def test_linked_notes_folder_higher_up(tmp_path, link_dir, opened):
    repo = make_repo(tmp_path)
    vault = make_vault(tmp_path, note=False)
    outside = tmp_path / "outside-notes"
    (outside / "projects").mkdir(parents=True)
    (outside / "projects" / "demo-project.md").write_text(OUTSIDE_TEXT, encoding="utf-8")
    shutil.rmtree(vault / "notes")
    link_dir(outside, vault / "notes")

    assert collect(repo, vault_dir=vault)["notes"] == []
    assert read_outside(opened, outside) == []


@pytest.mark.parametrize("linked", ["reports", "reports/github", "reports/github/octo-cat__demo"])
def test_reports_under_a_linked_folder_are_not_read(tmp_path, link_dir, opened, linked):
    repo = make_repo(tmp_path)
    vault = make_vault(tmp_path)
    original = vault / "reports" / "github" / "octo-cat__demo"
    outside = tmp_path / "outside-reports"
    shutil.copytree(vault / linked, outside)  # same layout, outside the vault
    shutil.rmtree(vault / linked)
    link_dir(outside, vault / linked)
    assert original.joinpath("2026-10-26T170604Z.md").is_file()  # reachable through the link

    assert collect(repo, github_repo="octo-cat/demo", vault_dir=vault)["reports"] == []
    assert read_outside(opened, outside) == []


def test_linked_report_file_is_skipped_others_kept(tmp_path, opened):
    repo = make_repo(tmp_path)
    vault = make_vault(tmp_path)
    reports_dir = vault / "reports" / "github" / "octo-cat__demo"
    outside = outside_dir(tmp_path, "elsewhere.md")
    symlink(outside / "elsewhere.md", reports_dir / "2026-10-27T000000Z.md")

    ev = collect(repo, github_repo="octo-cat/demo", vault_dir=vault)
    assert refs(ev["reports"]) == {"report:2026-10-26T170604Z.md"}
    assert read_outside(opened, outside) == []


def test_vault_root_given_through_a_link_still_works(tmp_path, link_dir):
    repo = make_repo(tmp_path)
    vault = make_vault(tmp_path)
    linked_vault = tmp_path / "linked-vault"
    link_dir(vault, linked_vault)

    ev = collect(repo, github_repo="octo-cat/demo", vault_dir=linked_vault)
    assert refs(ev["notes"]) == {"note:demo-project.md"}
    assert refs(ev["reports"]) == {"report:2026-10-26T170604Z.md"}
