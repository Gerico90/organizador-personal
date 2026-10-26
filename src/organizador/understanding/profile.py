"""Project profiles: an immutable, evidence-linked description of one project.

One JSON file per profile: <root>/profiles/<profile_id>.json, created once
and never written again. The `context` field has exactly the shape of a
recommendation's `project_context` and is validated by the same code, so a
recommendation can reuse it as is.

A profile draft (written by Claude Code from the evidence index) is checked
against a freshly collected evidence index before it is saved:

- every claim topic is one of TOPICS, and every topic has at least one claim
  (a "missing" claim when nothing was found);
- every source is "<ref>", "<ref>:<line>" or "<ref>:<start>-<end>", where
  <ref> is an inspectable entry of the index that is also listed in
  analyzed_files, and the lines exist;
- commit, repository and project match the index.

The checker cannot tell whether a cited passage supports its claim; that
remains a review question.
"""

import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from organizador.recommendations import common
from organizador.recommendations.common import ConflictError, DataError, NotFoundError, SCHEMA_VERSION
from organizador.recommendations.records import (
    ProjectContext,
    parse_project_context,
    project_context_to_dict,
)
from organizador.understanding.evidence import inspectable_refs

PROFILES_DIR = "profiles"
PROFILE_ID_RE = re.compile(r"prof-\d{8}T\d{6}Z-[0-9a-f]{8}", re.ASCII)
TOPICS = ("purpose", "capability", "architecture", "limitation", "objective")

_DRAFT_KEYS = ("project_id", "context")
_KEYS = ("schema_version", "id", "created_at", "project_id", "context", "inputs")
_INPUT_KEYS = ("working_tree_clean", "reports", "notes")
_SOURCE_RE = re.compile(r"(?P<ref>.+?)(?::(?P<start>\d+)(?:-(?P<end>\d+))?)?")


@dataclass(frozen=True)
class ProfileInputs:
    working_tree_clean: bool
    reports: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class Profile:
    id: str
    created_at: str
    project_id: str
    context: ProjectContext
    inputs: ProfileInputs


def check_profile_draft(draft: dict, evidence: dict, now: datetime | None = None) -> list[str]:
    """Return every problem found in `draft` against `evidence`. Empty means valid."""
    if not isinstance(draft, dict):
        return ["draft must be a JSON object"]
    try:
        common.check_keys(draft, _DRAFT_KEYS, ())
        project_id = common.project_id(draft["project_id"])
        context = parse_project_context(draft["context"], common.utc_now(now), "context")
    except ValueError as e:
        return [str(e)]

    problems = []
    if project_id != evidence["project_id"]:
        problems.append(f"project_id {project_id!r} does not match the evidence ({evidence['project_id']!r})")
    if context.commit_sha != evidence["commit_sha"]:
        problems.append(f"context.commit_sha {context.commit_sha!r} does not match the evidence ({evidence['commit_sha']})")
    if (context.github_repo or "").casefold() != (evidence["github_repo"] or "").casefold():
        problems.append(
            f"context.github_repo {context.github_repo!r} does not match the evidence ({evidence['github_repo']!r})"
        )
    if context.analyzed_at is None:
        problems.append("context.analyzed_at is required")

    refs = inspectable_refs(evidence)
    excluded = {e["ref"]: e["excluded"] for e in evidence["excluded"]}
    if not context.analyzed_files:
        problems.append("context.analyzed_files is empty")
    for ref in context.analyzed_files:
        if ref in excluded:
            problems.append(f"analyzed file {ref!r} is excluded from inspection ({excluded[ref]})")
        elif ref not in refs:
            problems.append(f"analyzed file {ref!r} is not in the evidence index")
    analyzed = set(context.analyzed_files)

    covered = set()
    for i, claim in enumerate(context.claims):
        where = f"context.claims[{i}]"
        if claim.topic not in TOPICS:
            problems.append(f"{where}.topic must be one of {', '.join(TOPICS)}, got {claim.topic!r}")
        covered.add(claim.topic)
        for source in claim.sources:
            problem = _check_source(source, refs, analyzed)
            if problem:
                problems.append(f"{where}: {problem}")
    for topic in TOPICS:
        if topic not in covered:
            problems.append(f"no claim for topic {topic!r} (add a 'missing' claim if nothing was found)")
    return problems


def create_profile(root: Path, draft: dict, evidence: dict, now: datetime | None = None) -> Profile:
    """Check `draft` against `evidence` and store it as a new, immutable profile."""
    problems = check_profile_draft(draft, evidence, now)
    if problems:
        raise DataError("Invalid profile draft:\n- " + "\n- ".join(problems))

    created_at = common.utc_now(now)
    profile_id = f"prof-{created_at.replace('-', '').replace(':', '')}-{_random_suffix()}"
    data = {
        "schema_version": SCHEMA_VERSION,
        "id": profile_id,
        "created_at": created_at,
        "project_id": draft["project_id"],
        "context": draft["context"],
        "inputs": {
            "working_tree_clean": evidence["working_tree_clean"],
            "reports": [e["ref"] for e in evidence["reports"]],
            "notes": [e["ref"] for e in evidence["notes"]],
        },
    }
    try:
        profile = _from_dict(data, profile_id)
    except ValueError as e:
        raise DataError(f"Invalid profile: {e}") from None

    path = common.record_path(root, PROFILES_DIR, profile_id, ".json")
    try:
        common.create_new_file(path, common.dump_json(_to_dict(profile)))
    except FileExistsError:
        raise ConflictError(f"Profile {profile_id} already exists; nothing was written") from None
    return profile


def load_profile(root: Path, profile_id: str) -> Profile:
    if not isinstance(profile_id, str) or not PROFILE_ID_RE.fullmatch(profile_id):
        raise DataError(f"profile id {profile_id!r} is not valid")
    path = common.record_path(root, PROFILES_DIR, profile_id, ".json")
    try:
        data = common.read_json_object(path)
    except FileNotFoundError:
        raise NotFoundError(f"Profile {profile_id} not found ({path})") from None
    try:
        return _from_dict(data, profile_id)
    except ValueError as e:
        raise DataError(f"Invalid profile {path}: {e}") from None


def _check_source(source: str, refs: dict, analyzed: set) -> str | None:
    m = _SOURCE_RE.fullmatch(source)
    ref = m.group("ref")
    if ref not in refs:
        return f"source {source!r} does not name an entry of the evidence index"
    if ref not in analyzed:
        return f"source {source!r} cites a file that is not in analyzed_files"
    if m.group("start") is None:
        return None
    start = int(m.group("start"))
    end = int(m.group("end") or start)
    if not 1 <= start <= end <= refs[ref]["lines"]:
        return f"source {source!r} cites lines outside 1-{refs[ref]['lines']}"
    return None


def _random_suffix() -> str:
    return secrets.token_hex(4)


def _to_dict(p: Profile) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "id": p.id,
        "created_at": p.created_at,
        "project_id": p.project_id,
        "context": project_context_to_dict(p.context),
        "inputs": {
            "working_tree_clean": p.inputs.working_tree_clean,
            "reports": list(p.inputs.reports),
            "notes": list(p.inputs.notes),
        },
    }


def _from_dict(data, expected_id: str) -> Profile:
    common.check_keys(data, _KEYS, ())
    common.check_schema_version(data)
    profile_id = data["id"]
    if profile_id != expected_id or not PROFILE_ID_RE.fullmatch(profile_id):
        raise ValueError(f"id {profile_id!r} does not match the file name ({expected_id})")
    created_at = common.timestamp(data["created_at"], "created_at")
    if profile_id[5:21] != created_at.replace("-", "").replace(":", ""):
        raise ValueError(f"id {profile_id!r} does not match created_at {created_at!r}")

    inputs = data["inputs"]
    common.check_keys(inputs, _INPUT_KEYS, (), "inputs.")
    if not isinstance(inputs["working_tree_clean"], bool):
        raise ValueError("inputs.working_tree_clean must be true or false")

    return Profile(
        id=profile_id,
        created_at=created_at,
        project_id=common.project_id(data["project_id"]),
        context=parse_project_context(data["context"], created_at, "context"),
        inputs=ProfileInputs(
            working_tree_clean=inputs["working_tree_clean"],
            reports=common.text_list(inputs["reports"], "inputs.reports"),
            notes=common.text_list(inputs["notes"], "inputs.notes"),
        ),
    )
