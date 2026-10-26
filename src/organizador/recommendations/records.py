"""Recommendations: immutable records proposed for one personal project.

One JSON file per recommendation: <root>/recommendations/<rec_id>.json.
A file is created once and never written again. A later reconsideration is
a new recommendation whose `reconsiders` field names the original.

Project context keeps facts (commit SHA, analysis time, analyzed files) as
plain fields, null or empty when unknown. Claims about the project
(objectives, capabilities, architecture, ...) carry a basis: "observed" or
"inferred" with at least one source, or "missing" with none.

Every recommendation needs at least one external source: an existing
reference, or a source with a URL. A source without a URL (a personal
note) may be added but never counts as that evidence.
"""

import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from organizador.recommendations import common
from organizador.recommendations.common import (
    RECOMMENDATIONS_DIR,
    SCHEMA_VERSION,
    ConflictError,
    DataError,
    NotFoundError,
)
from organizador.recommendations.references import load_reference

CLAIM_BASES = ("observed", "inferred", "missing")

_DRAFT_KEYS = (
    "project_id",
    "category",
    "title",
    "proposal",
    "justification",
    "benefits",
    "limitations",
    "relationship",
    "reference_ids",
    "sources",
    "project_context",
)
_KEYS = ("schema_version", "id", "created_at") + _DRAFT_KEYS + ("reconsiders",)
_SOURCE_KEYS = ("name", "url", "evidence_urls")
_CONTEXT_KEYS = ("github_repo", "commit_sha", "analyzed_at", "analyzed_files", "claims")
_CLAIM_KEYS = ("topic", "statement", "basis", "sources")


@dataclass(frozen=True)
class Source:
    name: str
    url: str | None
    evidence_urls: tuple[str, ...]


@dataclass(frozen=True)
class Claim:
    topic: str
    statement: str
    basis: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class ProjectContext:
    github_repo: str | None
    commit_sha: str | None
    analyzed_at: str | None
    analyzed_files: tuple[str, ...]
    claims: tuple[Claim, ...]


@dataclass(frozen=True)
class Recommendation:
    id: str
    created_at: str
    project_id: str
    category: str
    title: str
    proposal: str
    justification: str
    benefits: tuple[str, ...]
    limitations: tuple[str, ...]
    relationship: str
    reference_ids: tuple[str, ...]
    sources: tuple[Source, ...]
    project_context: ProjectContext
    reconsiders: str | None


def create_recommendation(root: Path, draft: dict, now: datetime | None = None) -> Recommendation:
    """Validate `draft` and store it as a new recommendation.

    `draft` has every field except schema_version, id and created_at, which
    are set here; `reconsiders` is optional. Every reference ID and the
    reconsidered recommendation must already exist.
    """
    recommendation = _validated(draft, common.utc_now(now), _random_suffix())

    for ref_id in recommendation.reference_ids:
        load_reference(root, ref_id)  # NotFoundError / DataError
    if recommendation.reconsiders is not None:
        original = load_recommendation(root, recommendation.reconsiders)
        if original.project_id != recommendation.project_id:
            raise ConflictError(
                f"Recommendation {original.id} belongs to project {original.project_id!r}, "
                f"not {recommendation.project_id!r}"
            )

    rec_id = recommendation.id
    path = common.record_path(root, RECOMMENDATIONS_DIR, rec_id, ".json")
    try:
        common.create_new_file(path, common.dump_json(_to_dict(recommendation)))
    except FileExistsError:
        raise ConflictError(f"Recommendation {rec_id} already exists; nothing was written") from None
    return recommendation


def check_recommendation_draft(draft: dict, now: datetime | None = None) -> Recommendation:
    """Validate `draft` exactly as create_recommendation would, without storing it.

    Only the record itself is checked; whether its references and the
    reconsidered recommendation exist is checked when it is created.
    """
    return _validated(draft, common.utc_now(now), "00000000")


def _validated(draft, created_at: str, suffix: str) -> Recommendation:
    if not isinstance(draft, dict):
        raise DataError("Invalid recommendation: draft must be a dict")
    rec_id = f"rec-{created_at.replace('-', '').replace(':', '')}-{suffix}"
    data = {"schema_version": SCHEMA_VERSION, "id": rec_id, "created_at": created_at}
    data.update(draft)
    data.setdefault("reconsiders", None)
    for key in ("schema_version", "id", "created_at"):
        if key in draft:
            raise DataError(f"Invalid recommendation: {key} is set automatically, not by the draft")
    try:
        return _from_dict(data, rec_id)
    except ValueError as e:
        raise DataError(f"Invalid recommendation: {e}") from None


def load_recommendation(root: Path, rec_id: str) -> Recommendation:
    try:
        common.recommendation_id(rec_id)
    except ValueError as e:
        raise DataError(str(e)) from None
    path = common.record_path(root, RECOMMENDATIONS_DIR, rec_id, ".json")
    try:
        return _load(path, rec_id)
    except FileNotFoundError:
        raise NotFoundError(f"Recommendation {rec_id} not found ({path})") from None


def list_recommendations(root: Path, project_id: str) -> tuple[Recommendation, ...]:
    """All recommendations for `project_id`, oldest first.

    Every `.json` file in the folder is read and validated; one invalid file
    stops the listing with DataError rather than being skipped.
    """
    try:
        common.project_id(project_id)
    except ValueError as e:
        raise DataError(str(e)) from None
    found = [r for r in iter_recommendations(root) if r.project_id == project_id]
    return tuple(sorted(found, key=lambda r: (r.created_at, r.id)))


def iter_recommendations(root: Path):
    directory = common.data_folder(root, RECOMMENDATIONS_DIR)
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.json")):
        stem = path.name[: -len(".json")]
        if not common.RECOMMENDATION_ID_RE.fullmatch(stem):
            raise DataError(f"Unexpected file name in {directory}: {path.name}")
        yield _load(common.record_path(root, RECOMMENDATIONS_DIR, stem, ".json"), stem)


def _random_suffix() -> str:
    return secrets.token_hex(4)


def _load(path: Path, expected_id: str) -> Recommendation:
    """Raises FileNotFoundError if the file does not exist, DataError if it is invalid."""
    data = common.read_json_object(path)
    try:
        return _from_dict(data, expected_id)
    except ValueError as e:
        raise DataError(f"Invalid recommendation {path}: {e}") from None


def project_context_to_dict(c: ProjectContext) -> dict:
    return {
        "github_repo": c.github_repo,
        "commit_sha": c.commit_sha,
        "analyzed_at": c.analyzed_at,
        "analyzed_files": list(c.analyzed_files),
        "claims": [
            {"topic": k.topic, "statement": k.statement, "basis": k.basis, "sources": list(k.sources)}
            for k in c.claims
        ],
    }


def _to_dict(r: Recommendation) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "id": r.id,
        "created_at": r.created_at,
        "project_id": r.project_id,
        "category": r.category,
        "title": r.title,
        "proposal": r.proposal,
        "justification": r.justification,
        "benefits": list(r.benefits),
        "limitations": list(r.limitations),
        "relationship": r.relationship,
        "reference_ids": list(r.reference_ids),
        "sources": [
            {"name": s.name, "url": s.url, "evidence_urls": list(s.evidence_urls)} for s in r.sources
        ],
        "project_context": project_context_to_dict(r.project_context),
        "reconsiders": r.reconsiders,
    }


def _from_dict(data, expected_id: str) -> Recommendation:
    common.check_keys(data, _KEYS, ())
    common.check_schema_version(data)

    rec_id = common.recommendation_id(data["id"], "id")
    if rec_id != expected_id:
        raise ValueError(f"id {rec_id!r} does not match the file name ({expected_id})")
    created_at = common.timestamp(data["created_at"], "created_at")
    if rec_id[4:20] != created_at.replace("-", "").replace(":", ""):
        raise ValueError(f"id {rec_id!r} does not match created_at {created_at!r}")

    reference_ids = data["reference_ids"]
    if not isinstance(reference_ids, list):
        raise ValueError("reference_ids must be a list")
    reference_ids = tuple(common.reference_id(v, f"reference_ids[{i}]") for i, v in enumerate(reference_ids))
    common.no_duplicates(reference_ids, "reference_ids")

    sources = data["sources"]
    if not isinstance(sources, list):
        raise ValueError("sources must be a list")
    sources = tuple(_source(s, f"sources[{i}]") for i, s in enumerate(sources))
    # A source without a URL (a personal note) is not external evidence.
    if not reference_ids and not any(s.url for s in sources):
        raise ValueError(
            "at least one external source is required: a reference ID, or a source with a name and URL"
        )

    reconsiders = data["reconsiders"]
    if reconsiders is not None:
        common.recommendation_id(reconsiders, "reconsiders")

    context = parse_project_context(data["project_context"], created_at)

    return Recommendation(
        id=rec_id,
        created_at=created_at,
        project_id=common.project_id(data["project_id"]),
        category=common.text(data["category"], "category"),
        title=common.text(data["title"], "title"),
        proposal=common.text(data["proposal"], "proposal"),
        justification=common.text(data["justification"], "justification"),
        benefits=common.text_list(data["benefits"], "benefits"),
        limitations=common.text_list(data["limitations"], "limitations"),
        relationship=common.text(data["relationship"], "relationship"),
        reference_ids=reference_ids,
        sources=sources,
        project_context=context,
        reconsiders=reconsiders,
    )


def _source(data, name: str) -> Source:
    common.check_keys(data, _SOURCE_KEYS, (), f"{name}.")
    url = data["url"]
    return Source(
        name=common.text(data["name"], f"{name}.name"),
        url=None if url is None else common.url(url, f"{name}.url"),
        evidence_urls=common.url_list(data["evidence_urls"], f"{name}.evidence_urls"),
    )


def parse_project_context(data, created_at: str, name: str = "project_context") -> ProjectContext:
    """Validate a project context object. Raises ValueError; `name` prefixes messages."""
    common.check_keys(data, _CONTEXT_KEYS, (), f"{name}.")

    repo = data["github_repo"]
    if repo is not None:
        owner, _, repo_name = repo.partition("/") if isinstance(repo, str) else ("", "", "")
        if not (
            common.GITHUB_OWNER_RE.fullmatch(owner)
            and common.GITHUB_REPO_RE.fullmatch(repo_name)
            and repo_name not in {".", ".."}
        ):
            raise ValueError(f"{name}.github_repo {repo!r} must look like 'owner/repo'")

    sha = data["commit_sha"]
    if sha is not None and (not isinstance(sha, str) or not common.COMMIT_SHA_RE.fullmatch(sha)):
        raise ValueError(f"{name}.commit_sha {sha!r} must be a lowercase hex commit SHA")

    analyzed_at = common.optional_timestamp(data["analyzed_at"], f"{name}.analyzed_at")
    if analyzed_at is not None and common.parse_ts(analyzed_at) > common.parse_ts(created_at):
        raise ValueError(f"{name}.analyzed_at is later than created_at")

    files = common.text_list(data["analyzed_files"], f"{name}.analyzed_files")
    common.no_duplicates(files, f"{name}.analyzed_files")

    claims = data["claims"]
    if not isinstance(claims, list):
        raise ValueError(f"{name}.claims must be a list")

    return ProjectContext(
        github_repo=repo,
        commit_sha=sha,
        analyzed_at=analyzed_at,
        analyzed_files=files,
        claims=tuple(_claim(c, f"{name}.claims[{i}]") for i, c in enumerate(claims)),
    )


def _claim(data, name: str) -> Claim:
    common.check_keys(data, _CLAIM_KEYS, (), f"{name}.")
    basis = data["basis"]
    if basis not in CLAIM_BASES:
        raise ValueError(f"{name}.basis must be one of {', '.join(CLAIM_BASES)}, got {basis!r}")
    sources = common.text_list(data["sources"], f"{name}.sources")
    if basis == "missing" and sources:
        raise ValueError(f"{name}: a 'missing' claim must have no sources")
    if basis != "missing" and not sources:
        raise ValueError(f"{name}: an '{basis}' claim needs at least one source")
    return Claim(
        topic=common.text(data["topic"], f"{name}.topic"),
        statement=common.text(data["statement"], f"{name}.statement"),
        basis=basis,
        sources=sources,
    )
