"""Operational state for one GitHub repository: load, validate, save.

One JSON file per repository: <state_path>/github/<owner>__<repo>.json.
This module only stores checkpoints. Advancing cursors and detecting changes
belong to changes.py.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from organizador.config import Config
from organizador.fsutil import atomic_write_text

# GitHub's timestamp format, always UTC with a "Z" suffix and whole seconds.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# strptime accepts unpadded fields ("2026-1-2T3:4:5Z"); this regex does not.
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", re.ASCII)
_BACKUP_ADVICE = (
    "Back up the file before changing it. Then fix it by hand, or delete it "
    "to start again from a new baseline."
)

_KEYS = (
    "repo",
    "default_branch",
    "last_head_sha",
    "issues_cursor",
    "pulls_cursor",
    "last_success_at",
    "last_report",
)
_CURSOR_KEYS = ("ts", "ids")
# SHA-1 (40 hex) today; SHA-256 (64 hex) if the repository uses it.
_SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class StateError(Exception):
    """The state file is unreadable, malformed, or belongs to another repository."""


@dataclass(frozen=True)
class Cursor:
    ts: str
    ids: tuple[int, ...]


@dataclass(frozen=True)
class State:
    repo: str
    default_branch: str
    last_head_sha: str
    issues_cursor: Cursor
    pulls_cursor: Cursor
    last_success_at: str
    last_report: str


def repo_slug(config: Config) -> str:
    return f"{config.github_owner}/{config.github_repo}"


def state_file_path(config: Config) -> Path:
    return config.state_path / "github" / f"{config.github_owner}__{config.github_repo}.json"


def load_state(config: Config) -> State | None:
    """Return the saved state, or None if this repository has never been run.

    Any problem with an existing file raises StateError; the file is never
    reset or replaced here.
    """
    path = state_file_path(config)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        raise StateError(f"Cannot read state file {path}: {e}") from None

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise StateError(f"Malformed JSON in state file {path}: {e}. {_BACKUP_ADVICE}") from None

    try:
        return _from_dict(data, repo_slug(config))
    except ValueError as e:
        raise StateError(f"Invalid state file {path}: {e}. {_BACKUP_ADVICE}") from None


def validate_state(config: Config, state: State) -> None:
    """Raise StateError if `state` could not be saved for this repository.

    Lets the caller check the new state before writing the report, so a run
    never leaves a report whose state cannot be saved.
    """
    try:
        _from_dict(_to_dict(state), repo_slug(config))
    except ValueError as e:
        raise StateError(f"Invalid new state: {e}") from None


def save_state(config: Config, state: State) -> Path:
    """Validate `state` and replace the state file atomically. Returns its path."""
    path = state_file_path(config)
    data = _to_dict(state)
    try:
        _from_dict(data, repo_slug(config))
    except ValueError as e:
        raise StateError(f"Refusing to save invalid state to {path}: {e}") from None

    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return path


def _to_dict(state: State) -> dict:
    def cursor(c: Cursor) -> dict:
        return {"ts": c.ts, "ids": list(c.ids)}

    return {
        "repo": state.repo,
        "default_branch": state.default_branch,
        "last_head_sha": state.last_head_sha,
        "issues_cursor": cursor(state.issues_cursor),
        "pulls_cursor": cursor(state.pulls_cursor),
        "last_success_at": state.last_success_at,
        "last_report": state.last_report,
    }


def _from_dict(data, expected_repo: str) -> State:
    """Build a State from parsed JSON. Raises ValueError with a readable message."""
    if not isinstance(data, dict):
        raise ValueError("top level must be a JSON object")
    _check_keys(data, _KEYS, "")

    repo = _string(data, "repo")
    # GitHub owner and repository names are case-insensitive.
    if repo.casefold() != expected_repo.casefold():
        raise ValueError(f"belongs to repository {repo!r}, expected {expected_repo!r}")

    sha = _string(data, "last_head_sha")
    if not _SHA_RE.fullmatch(sha):
        raise ValueError(f"last_head_sha {sha!r} is not a lowercase hex commit SHA")

    last_report = _string(data, "last_report")
    report_path = PurePosixPath(last_report)
    if "\\" in last_report or report_path.is_absolute() or ".." in report_path.parts:
        raise ValueError(
            f"last_report {last_report!r} must be a relative path inside the vault, using '/'"
        )

    return State(
        repo=repo,
        default_branch=_string(data, "default_branch"),
        last_head_sha=sha,
        issues_cursor=_cursor(data, "issues_cursor"),
        pulls_cursor=_cursor(data, "pulls_cursor"),
        last_success_at=_timestamp(data, "last_success_at"),
        last_report=last_report,
    )


def _check_keys(table: dict, required: tuple[str, ...], prefix: str) -> None:
    missing = [k for k in required if k not in table]
    if missing:
        raise ValueError(f"missing field(s): {', '.join(prefix + k for k in missing)}")
    unknown = [k for k in table if k not in required]
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(prefix + k for k in unknown)}")


def _string(table: dict, key: str, name: str | None = None) -> str:
    value = table[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name or key} must be a non-empty string")
    return value


def _timestamp(table: dict, key: str, name: str | None = None) -> str:
    name = name or key
    value = _string(table, key, name)
    try:
        if not _TIMESTAMP_RE.fullmatch(value):
            raise ValueError
        datetime.strptime(value, TIMESTAMP_FORMAT)
    except ValueError:
        raise ValueError(
            f"{name} {value!r} must be a UTC timestamp like 2026-10-25T18:30:11Z"
        ) from None
    return value


def _cursor(table: dict, key: str) -> Cursor:
    value = table[key]
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object with 'ts' and 'ids'")
    _check_keys(value, _CURSOR_KEYS, f"{key}.")

    ids = value["ids"]
    if not isinstance(ids, list):
        raise ValueError(f"{key}.ids must be a list of integers")
    for i in ids:
        # bool is a subclass of int; reject it explicitly.
        if not isinstance(i, int) or isinstance(i, bool) or i < 1:
            raise ValueError(f"{key}.ids must contain positive integers, got {i!r}")
    if len(set(ids)) != len(ids):
        raise ValueError(f"{key}.ids contains duplicates")

    return Cursor(ts=_timestamp(value, "ts", f"{key}.ts"), ids=tuple(ids))
