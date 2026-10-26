"""Test helpers for project understanding. Builds small, synthetic git repositories."""

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

NOW = datetime(2026, 10, 27, 10, 0, 0, tzinfo=timezone.utc)

README = """\
# Demo

Intro line.

## Usage

```bash
# not a heading
run demo
```

## Limits
Only one thing.
"""

APP = '''\
"""Demo application.

Second paragraph is not indexed.
"""


class Store:
    pass


def main():
    return 0
'''


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, check=True, text=True).stdout


def make_repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "project"
    (repo / "src").mkdir(parents=True)
    (repo / "README.md").write_text(README, encoding="utf-8", newline="\n")
    (repo / "src" / "app.py").write_text(APP, encoding="utf-8", newline="\n")
    (repo / "src" / "broken.py").write_text("def (:\n", encoding="utf-8", newline="\n")
    (repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0binary")
    (repo / ".env").write_text("API_TOKEN=do-not-read\n", encoding="utf-8")
    (repo / "api_token.txt").write_text("do-not-read\n", encoding="utf-8")
    (repo / "keep").mkdir()
    (repo / "keep" / ".gitkeep").write_bytes(b"")
    (repo / "package-lock.json").write_text("{}\n", encoding="utf-8")
    (repo / "big.txt").write_bytes(b"x" * 1_000_001)
    (repo / "latin1.txt").write_bytes("caf\xe9\n".encode("latin-1"))
    (repo / ".gitignore").write_text("local.toml\n", encoding="utf-8", newline="\n")
    (repo / "local.toml").write_text("ignored = true\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def make_vault(tmp_path: Path, project_id="demo-project", repo="octo-cat/demo", note=True) -> Path:
    vault = tmp_path / "vault"
    reports = vault / "reports" / "github" / repo.replace("/", "__")
    reports.mkdir(parents=True)
    (reports / "2026-10-26T170604Z.md").write_text(
        '---\nrepo: "octo-cat/demo"\nperiod_from: "2026-10-26T16:55:05Z"\nperiod_to: "2026-10-26T17:06:04Z"\n'
        'baseline: false\nhead_sha: "abc"\n---\n\n# Demo activity\n\n## Commits on main\n',
        encoding="utf-8",
        newline="\n",
    )
    (vault / "notes" / "projects").mkdir(parents=True)
    if note:
        (vault / "notes" / "projects" / f"{project_id}.md").write_text(
            "# Context\n\nGoal: example.\n", encoding="utf-8", newline="\n"
        )
    return vault


def snapshot(folder: Path) -> dict:
    return {
        p.relative_to(folder).as_posix(): p.read_bytes()
        for p in folder.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(folder).parts
    }
