"""Evidence index for one project: what exists and where, without interpreting it.

Read-only. Project content comes only from files tracked by git, read from
the working tree. Context notes and GitHub reports are read only when the
caller passes a vault folder (that is the explicit authorization):

    <vault>/notes/projects/<project_id>.md          optional context note
    <vault>/reports/github/<owner>__<repo>/*.md      GitHub activity reports

Files that look like secrets are never opened. Binary, very large and
clearly irrelevant files are listed as excluded and their content is not
indexed. The index is regenerated on demand and not stored.

Every inspectable entry has a `ref`, the string a profile cites as a source:
the project-relative path ("README.md"), "report:<file name>" or
"note:<file name>", optionally followed by ":<line>" or ":<start>-<end>".
"""

import ast
import fnmatch
import hashlib
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path, PurePosixPath

from organizador.recommendations import common

EVIDENCE_SCHEMA_VERSION = 1
MAX_FILE_BYTES = 1_000_000
DOCSTRING_MAX_CHARS = 600
GIT_TIMEOUT_SECONDS = 30

# Matched against the file name, case-insensitively. Never opened.
SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.keystore",
    "*.jks",
    "*.kdbx",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    ".netrc",
    ".pypirc",
    ".npmrc",
    "credentials*",
    "*secret*",
    "*token*",
)
# Tracked, readable, but not useful as evidence of what the project does.
IRRELEVANT_PATTERNS = (
    ".gitkeep",
    "*.lock",
    "package-lock.json",
    "*.min.js",
    "*.min.css",
    "*.map",
)

_HEADING_RE = re.compile(r"(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"\s{0,3}(```|~~~)")
_FRONT_MATTER_KEYS = ("period_from", "period_to", "head_sha", "baseline", "commits_status")


class EvidenceError(Exception):
    """The project folder or an authorized input cannot be indexed."""


def collect_evidence(
    project_dir: Path,
    *,
    project_id: str,
    github_repo: str | None = None,
    vault_dir: Path | None = None,
    now: datetime | None = None,
) -> dict:
    try:
        common.project_id(project_id)
    except ValueError as e:
        raise EvidenceError(str(e)) from None
    if github_repo is not None and not _valid_repo(github_repo):
        raise EvidenceError(f"github_repo {github_repo!r} must look like 'owner/repo'")

    project_dir = Path(project_dir).resolve()
    if not project_dir.is_dir():
        raise EvidenceError(f"Project folder not found: {project_dir}")
    if Path(_git(project_dir, "rev-parse", "--show-toplevel").strip()).resolve() != project_dir:
        raise EvidenceError(f"{project_dir} is not the top folder of a git repository")

    commit_sha = _git(project_dir, "rev-parse", "HEAD").strip()
    status = _git(project_dir, "status", "--porcelain", "--untracked-files=no")
    tracked = sorted(p for p in _git(project_dir, "ls-files", "-z").split("\0") if p)

    files, excluded = [], []
    for rel in tracked:
        entry = _index_file(project_dir / PurePosixPath(rel), rel)
        (excluded if "excluded" in entry else files).append(entry)

    reports, notes, notes_checked = [], [], None
    if vault_dir is not None:
        vault_dir = Path(vault_dir).resolve()
        if not vault_dir.is_dir():
            raise EvidenceError(f"Vault folder not found: {vault_dir}")
        note = vault_dir / "notes" / "projects" / f"{project_id}.md"
        notes_checked = note.as_posix()
        if note.is_file() and _no_links(note):
            notes.append(_index_external(note, f"note:{note.name}"))
        if github_repo is not None:
            owner, repo = github_repo.split("/")
            reports_dir = vault_dir / "reports" / "github" / f"{owner}__{repo}"
            if reports_dir.is_dir() and _no_links(reports_dir):
                for path in sorted(reports_dir.glob("*.md")):
                    if path.is_file() and _no_links(path):
                        reports.append(_index_external(path, f"report:{path.name}"))

    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "project_id": project_id,
        "project_dir": project_dir.as_posix(),
        "github_repo": github_repo,
        "commit_sha": commit_sha,
        "working_tree_clean": status == "",
        "collected_at": common.utc_now(now),
        "notes_checked": notes_checked,
        "files": files,
        "excluded": excluded,
        "reports": reports,
        "notes": notes,
    }


def inspectable_refs(evidence: dict) -> dict[str, dict]:
    """Every citable entry by ref: project files, reports and notes."""
    return {e["ref"]: e for group in ("files", "reports", "notes") for e in evidence[group]}


# --- project files ---------------------------------------------------------


def _index_file(path: Path, rel: str) -> dict:
    name = PurePosixPath(rel).name
    if _matches(name, SECRET_PATTERNS):
        return {"ref": rel, "excluded": "secret"}  # never opened
    if not _no_links(path):
        return {"ref": rel, "excluded": "link"}  # the file or a folder on its path is a link
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return {"ref": rel, "excluded": "deleted"}  # tracked but removed from the working tree
    if _matches(name, IRRELEVANT_PATTERNS) or size == 0:
        return {"ref": rel, "excluded": "irrelevant"}
    if size > MAX_FILE_BYTES:
        return {"ref": rel, "excluded": "too_large"}
    raw = path.read_bytes()
    text = _decode_text(raw)
    if text is None:
        return {"ref": rel, "excluded": "binary"}
    return _describe(rel, raw, text, _kind(name))


def _index_external(path: Path, ref: str) -> dict:
    raw = path.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        raise EvidenceError(f"{path} is larger than {MAX_FILE_BYTES} bytes")
    text = _decode_text(raw)
    if text is None:
        raise EvidenceError(f"{path} is not UTF-8 text")
    entry = _describe(ref, raw, text, "markdown")
    entry["path"] = path.as_posix()
    if ref.startswith("report:"):
        entry["front_matter"] = _front_matter(text)
    return entry


def _describe(ref: str, raw: bytes, text: str, kind: str) -> dict:
    entry = {
        "ref": ref,
        "kind": kind,
        "bytes": len(raw),
        "lines": len(text.splitlines()),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    if kind == "markdown":
        entry["headings"] = _headings(text)
    elif kind == "python":
        entry.update(_python_outline(text))
    return entry


def _kind(name: str) -> str:
    suffix = PurePosixPath(name).suffix.lower()
    return {".md": "markdown", ".markdown": "markdown", ".py": "python"}.get(suffix, "text")


def _decode_text(raw: bytes) -> str | None:
    if b"\0" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _headings(text: str) -> list[list]:
    """[[line, level, title], ...], ignoring '#' lines inside fenced code blocks."""
    headings, fence = [], None
    for number, line in enumerate(text.splitlines(), start=1):
        m = _FENCE_RE.match(line)
        if m:
            fence = None if fence == m.group(1) else (fence or m.group(1))
            continue
        if fence:
            continue
        h = _HEADING_RE.fullmatch(line)
        if h:
            headings.append([number, len(h.group(1)), h.group(2)])
    return headings


def _python_outline(text: str) -> dict:
    """Module docstring and top-level definitions. The code is parsed, never run."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return {"parse_error": True}
    docstring = ast.get_docstring(tree) or ""
    first_paragraph = docstring.split("\n\n")[0].strip()
    definitions = [
        [node.lineno, "class" if isinstance(node, ast.ClassDef) else "def", node.name]
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    return {"docstring": first_paragraph[:DOCSTRING_MAX_CHARS], "definitions": definitions}


def _front_matter(text: str) -> dict:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    found = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep and key.strip() in _FRONT_MATTER_KEYS:
            found[key.strip()] = value.strip().strip('"')
    return found


def _no_links(path: Path) -> bool:
    """True if no part of `path` below its (already resolved) root is a symlink or junction.

    `path` is built from a resolved root, so it resolves to itself unless a
    folder on the way, or the file itself, points somewhere else. Case is
    ignored where the filesystem ignores it (Windows).
    """
    return os.path.normcase(str(path.resolve())) == os.path.normcase(str(path))


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatchcase(lowered, p) for p in patterns)


def _valid_repo(value) -> bool:
    if not isinstance(value, str):
        return False
    owner, _, repo = value.partition("/")
    return bool(
        common.GITHUB_OWNER_RE.fullmatch(owner) and common.GITHUB_REPO_RE.fullmatch(repo) and repo not in {".", ".."}
    )


def _git(project_dir: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            # --no-optional-locks: `git status` must not refresh .git/index (read-only).
            ["git", "--no-optional-locks", "-C", str(project_dir), *args],
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        raise EvidenceError("git is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise EvidenceError(f"git {' '.join(args)} timed out") from None
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise EvidenceError(f"git {' '.join(args)} failed in {project_dir}: {message}")
    return result.stdout.decode("utf-8")
