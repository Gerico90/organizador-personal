"""Shared validation and file helpers for the local recommendation data.

Layout under the data root (passed in by the caller, never read from config):

    <root>/references/<ref_id>.json
    <root>/recommendations/<rec_id>.json
    <root>/feedback/<rec_id>.jsonl

File names are built only from validated IDs, and every path is checked to
stay inside its folder. Nothing here repairs a bad file: problems raise
DataError (or a subclass) with the path in the message.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA_VERSION = 1

REFERENCES_DIR = "references"
RECOMMENDATIONS_DIR = "recommendations"
FEEDBACK_DIR = "feedback"

# Same format as the GitHub checkpoint: UTC, whole seconds, "Z" suffix.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", re.ASCII)

PROJECT_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*", re.ASCII)
PROJECT_ID_MAX = 64
REFERENCE_ID_RE = re.compile(r"ref-[0-9a-f]{16}", re.ASCII)
RECOMMENDATION_ID_RE = re.compile(r"rec-\d{8}T\d{6}Z-[0-9a-f]{8}", re.ASCII)
# GitHub naming rules (same as config.py): owners are alphanumeric or hyphen,
# not starting or ending with a hyphen; repositories also allow "." and "_".
GITHUB_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", re.ASCII)
GITHUB_REPO_RE = re.compile(r"[A-Za-z0-9._-]{1,100}", re.ASCII)
# SHA-1 (40 hex) or SHA-256 (64 hex).
COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}", re.ASCII)

URL_MAX = 2048
_URL_FORBIDDEN_RE = re.compile(r"[\s\x00-\x1f\x7f]")


class DataError(Exception):
    """A record or file is missing, unreadable, malformed, or invalid."""


class NotFoundError(DataError):
    """A referenced record does not exist."""


class ConflictError(DataError):
    """The operation would overwrite or contradict an existing record."""


# --- timestamps --------------------------------------------------------------


def format_ts(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT)


def utc_now(now: datetime | None = None) -> str:
    return format_ts(now or datetime.now(timezone.utc))


def parse_ts(value: str) -> datetime:
    return datetime.strptime(value, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)


# --- field checks (raise ValueError; callers add the path) --------------------


def check_keys(table, required: tuple[str, ...], optional: tuple[str, ...], prefix: str = "") -> None:
    if not isinstance(table, dict):
        raise ValueError(f"{prefix.rstrip('.') or 'top level'} must be a JSON object")
    missing = [k for k in required if k not in table]
    if missing:
        raise ValueError(f"missing field(s): {', '.join(prefix + k for k in missing)}")
    allowed = set(required) | set(optional)
    unknown = [k for k in table if k not in allowed]
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(prefix + k for k in unknown)}")


def check_schema_version(table: dict) -> None:
    value = table["schema_version"]
    # bool is a subclass of int; reject it explicitly.
    if isinstance(value, bool) or value != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}, got {value!r}")


def text(value, name: str) -> str:
    """A non-empty string that is not only whitespace. Stored as given."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def optional_text(value, name: str) -> str | None:
    return None if value is None else text(value, name)


def text_list(value, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list of strings")
    return tuple(text(v, f"{name}[{i}]") for i, v in enumerate(value))


def timestamp(value, name: str) -> str:
    if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
        raise ValueError(f"{name} {value!r} must be a UTC timestamp like 2026-10-26T17:06:04Z")
    try:
        parse_ts(value)
    except ValueError:
        raise ValueError(f"{name} {value!r} is not a real date and time") from None
    return value


def optional_timestamp(value, name: str) -> str | None:
    return None if value is None else timestamp(value, name)


def matching(value, pattern: re.Pattern, name: str, example: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{name} {value!r} is not valid (expected something like {example!r})")
    return value


def project_id(value, name: str = "project_id") -> str:
    matching(value, PROJECT_ID_RE, name, "organizador-personal")
    if len(value) > PROJECT_ID_MAX:
        raise ValueError(f"{name} is longer than {PROJECT_ID_MAX} characters")
    return value


def reference_id(value, name: str = "reference id") -> str:
    return matching(value, REFERENCE_ID_RE, name, "ref-0123456789abcdef")


def recommendation_id(value, name: str = "recommendation id") -> str:
    return matching(value, RECOMMENDATION_ID_RE, name, "rec-20261026T170604Z-a1b2c3d4")


def url(value, name: str) -> str:
    """An absolute http(s) URL with a host, no credentials, no spaces."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty URL string")
    if len(value) > URL_MAX:
        raise ValueError(f"{name} is longer than {URL_MAX} characters")
    if _URL_FORBIDDEN_RE.search(value):
        raise ValueError(f"{name} {value!r} contains spaces or control characters")
    try:
        parts = urlsplit(value)
        parts.port  # raises ValueError for a malformed port
    except ValueError:
        raise ValueError(f"{name} {value!r} is not a valid URL") from None
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"{name} {value!r} must start with http:// or https://")
    if not parts.hostname:
        raise ValueError(f"{name} {value!r} has no host")
    if parts.username is not None or parts.password is not None:
        raise ValueError(f"{name} must not contain credentials")
    return value


def url_list(value, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list of URLs")
    return tuple(url(v, f"{name}[{i}]") for i, v in enumerate(value))


def no_duplicates(values: tuple, name: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{name} contains duplicates")


# --- paths and files -------------------------------------------------------


def record_path(root: Path, folder: str, record_id: str, suffix: str) -> Path:
    """`<root>/<folder>/<record_id><suffix>`, refusing anything that escapes the folder.

    `record_id` must already be validated; this is a second line of defence
    (for example against a folder that is a link to somewhere else).
    """
    directory = Path(root) / folder
    path = directory / f"{record_id}{suffix}"
    if path.parent.resolve() != directory.resolve() or path.name != f"{record_id}{suffix}":
        raise DataError(f"Unsafe path for {record_id!r} in {directory}")
    return path


def read_json_object(path: Path) -> dict:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise
    except OSError as e:
        raise DataError(f"Cannot read {path}: {e}") from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as e:
        raise DataError(f"{path} is not valid UTF-8: {e}") from None
    except json.JSONDecodeError as e:
        raise DataError(f"Malformed JSON in {path}: {e}") from None
    if not isinstance(data, dict):
        raise DataError(f"Invalid record {path}: top level must be a JSON object")
    return data


def dump_json(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def create_new_file(path: Path, text_: str) -> None:
    """Create `path` and write `text_`. Never opens an existing file.

    Raises FileExistsError if the name is taken. If writing fails after the
    file was created, the partial file is removed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "x", encoding="utf-8", newline="\n")
    try:
        with f:
            f.write(text_)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
