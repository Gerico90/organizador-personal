"""External references: projects or repositories that recommendations point to.

One JSON file per reference: <root>/references/<ref_id>.json.

The ID is derived from the canonical URL, so the same external project
always gets the same ID and is stored once, however many recommendations
use it. Only `last_reviewed_at` ever changes after creation.
"""

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from organizador.fsutil import atomic_write_text
from organizador.recommendations import common
from organizador.recommendations.common import (
    REFERENCES_DIR,
    SCHEMA_VERSION,
    ConflictError,
    DataError,
    NotFoundError,
)

_GITHUB_HOSTS = ("github.com", "www.github.com")
_DEFAULT_PORTS = {"http": 80, "https": 443}
_KEYS = (
    "schema_version",
    "id",
    "name",
    "canonical_url",
    "description",
    "first_discovered_at",
    "last_reviewed_at",
)


@dataclass(frozen=True)
class Reference:
    id: str
    name: str
    canonical_url: str
    description: str | None
    first_discovered_at: str
    last_reviewed_at: str


@dataclass(frozen=True)
class ReferenceResult:
    reference: Reference
    created: bool
    # Fields whose given value differs from the stored one. The stored record
    # is never changed to match; the caller decides what to do.
    differences: tuple[str, ...]


def canonical_url(value: str) -> str:
    """Normalize a project URL so the same project gives the same string.

    - scheme and host in lowercase, default port removed, fragment removed,
      trailing "/" removed
    - GitHub: must be a repository root, https://github.com/<owner>/<repo>;
      owner and name in lowercase (GitHub treats them case-insensitively)
      and a trailing ".git" removed. Deeper links (files, issues, commits)
      are evidence URLs, not references, and are rejected here.

    Raises ValueError.
    """
    common.url(value, "url")
    parts = urlsplit(value)
    host = parts.hostname
    if host in _GITHUB_HOSTS:
        return _github_canonical(value, parts)

    port = parts.port
    netloc = f"[{host}]" if ":" in host else host  # IPv6 literal
    if port is not None and port != _DEFAULT_PORTS[parts.scheme]:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), parts.query, ""))


def _github_canonical(value, parts) -> str:
    segments = [s for s in parts.path.split("/") if s]
    if parts.query or len(segments) != 2 or parts.port not in (None, 443):
        raise ValueError(
            f"GitHub URL {value!r} must be a repository root like https://github.com/owner/repo; "
            "links to files, issues or commits belong in evidence_urls"
        )
    owner, repo = segments
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    if not common.GITHUB_OWNER_RE.fullmatch(owner):
        raise ValueError(f"GitHub URL {value!r} has an invalid owner name")
    if repo in {".", ".."} or not common.GITHUB_REPO_RE.fullmatch(repo):
        raise ValueError(f"GitHub URL {value!r} has an invalid repository name")
    return f"https://github.com/{owner.lower()}/{repo.lower()}"


def reference_id_for(canonical: str) -> str:
    return "ref-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def get_or_create_reference(
    root: Path,
    *,
    name: str,
    url: str,
    description: str | None = None,
    now: datetime | None = None,
) -> ReferenceResult:
    """Return the reference for `url`, creating it if it does not exist yet.

    An existing record is returned unchanged. If `name` or `description`
    differ from it, they are listed in `differences`.
    """
    try:
        name = common.text(name, "name")
        description = common.optional_text(description, "description")
        canonical = canonical_url(url)
    except ValueError as e:
        raise DataError(f"Invalid reference: {e}") from None
    ref_id = reference_id_for(canonical)
    path = common.record_path(root, REFERENCES_DIR, ref_id, ".json")

    try:
        existing = _load(path, ref_id)
    except FileNotFoundError:
        created_at = common.utc_now(now)
        reference = Reference(
            id=ref_id,
            name=name,
            canonical_url=canonical,
            description=description,
            first_discovered_at=created_at,
            last_reviewed_at=created_at,
        )
        try:
            common.create_new_file(path, common.dump_json(_to_dict(reference)))
            return ReferenceResult(reference, created=True, differences=())
        except FileExistsError:
            # Created by someone else in the meantime; use theirs.
            existing = _load(path, ref_id)

    differences = tuple(
        field
        for field, given in (("name", name), ("description", description))
        if getattr(existing, field) != given
    )
    return ReferenceResult(existing, created=False, differences=differences)


def load_reference(root: Path, ref_id: str) -> Reference:
    try:
        common.reference_id(ref_id)
    except ValueError as e:
        raise DataError(str(e)) from None
    path = common.record_path(root, REFERENCES_DIR, ref_id, ".json")
    try:
        return _load(path, ref_id)
    except FileNotFoundError:
        raise NotFoundError(f"Reference {ref_id} not found ({path})") from None


def mark_reference_reviewed(root: Path, ref_id: str, now: datetime | None = None) -> Reference:
    """Set `last_reviewed_at` to now. The only change ever made to a reference."""
    existing = load_reference(root, ref_id)
    reviewed_at = common.utc_now(now)
    if common.parse_ts(reviewed_at) < common.parse_ts(existing.last_reviewed_at):
        raise ConflictError(
            f"Reference {ref_id}: review time {reviewed_at} is earlier than the "
            f"stored last_reviewed_at {existing.last_reviewed_at}"
        )
    updated = replace(existing, last_reviewed_at=reviewed_at)
    path = common.record_path(root, REFERENCES_DIR, ref_id, ".json")
    atomic_write_text(path, common.dump_json(_to_dict(updated)))
    return updated


def _load(path: Path, expected_id: str) -> Reference:
    """Raises FileNotFoundError if the file does not exist, DataError if it is invalid."""
    data = common.read_json_object(path)
    try:
        return _from_dict(data, expected_id)
    except ValueError as e:
        raise DataError(f"Invalid reference {path}: {e}") from None


def _to_dict(reference: Reference) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "id": reference.id,
        "name": reference.name,
        "canonical_url": reference.canonical_url,
        "description": reference.description,
        "first_discovered_at": reference.first_discovered_at,
        "last_reviewed_at": reference.last_reviewed_at,
    }


def _from_dict(data, expected_id: str) -> Reference:
    common.check_keys(data, _KEYS, ())
    common.check_schema_version(data)

    ref_id = common.reference_id(data["id"], "id")
    if ref_id != expected_id:
        raise ValueError(f"id {ref_id!r} does not match the file name ({expected_id})")

    stored_url = data["canonical_url"]
    canonical = canonical_url(stored_url)
    if canonical != stored_url:
        raise ValueError(f"canonical_url {stored_url!r} is not normalized (expected {canonical!r})")
    if reference_id_for(canonical) != ref_id:
        raise ValueError(f"id {ref_id!r} does not match canonical_url {canonical!r}")

    first = common.timestamp(data["first_discovered_at"], "first_discovered_at")
    last = common.timestamp(data["last_reviewed_at"], "last_reviewed_at")
    if common.parse_ts(last) < common.parse_ts(first):
        raise ValueError("last_reviewed_at is earlier than first_discovered_at")

    return Reference(
        id=ref_id,
        name=common.text(data["name"], "name"),
        canonical_url=canonical,
        description=common.optional_text(data["description"], "description"),
        first_discovered_at=first,
        last_reviewed_at=last,
    )
