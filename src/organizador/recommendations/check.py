"""Read-only check of the whole data folder.

Lists every problem found instead of stopping at the first one, and never
changes, moves or deletes anything.
"""

from pathlib import Path

from organizador.recommendations import common
from organizador.recommendations.common import (
    FEEDBACK_DIR,
    RECOMMENDATIONS_DIR,
    REFERENCES_DIR,
    DataError,
)
from organizador.recommendations.feedback import read_history_file
from organizador.recommendations.records import Recommendation, load_recommendation
from organizador.recommendations.references import load_reference


def check_data(root: Path) -> list[str]:
    """Return a list of problems in `root`. An empty list means none were found."""
    root = Path(root)
    problems: list[str] = []
    reference_ids = _check_references(root, problems)
    recommendations = _check_recommendations(root, reference_ids, problems)
    _check_feedback(root, recommendations, problems)
    return problems


def _entries(root: Path, folder: str, suffix: str, id_re, problems: list[str]):
    """Yield (path, id) for well-named files; report everything else."""
    directory = root / folder
    if not directory.exists():
        return
    if not directory.is_dir():
        problems.append(f"{directory} is not a folder")
        return
    for path in sorted(directory.iterdir()):
        stem = path.name[: -len(suffix)] if path.name.endswith(suffix) else None
        if not path.is_file() or stem is None or not id_re.fullmatch(stem):
            problems.append(f"Unexpected entry: {path}")
            continue
        yield path, stem


def _check_references(root: Path, problems: list[str]) -> set[str]:
    valid = set()
    for _, ref_id in _entries(root, REFERENCES_DIR, ".json", common.REFERENCE_ID_RE, problems):
        try:
            load_reference(root, ref_id)
        except DataError as e:
            problems.append(str(e))
            continue
        valid.add(ref_id)
    return valid


def _check_recommendations(root: Path, reference_ids: set[str], problems: list[str]) -> dict[str, Recommendation]:
    loaded: dict[str, Recommendation] = {}
    for _, rec_id in _entries(root, RECOMMENDATIONS_DIR, ".json", common.RECOMMENDATION_ID_RE, problems):
        try:
            loaded[rec_id] = load_recommendation(root, rec_id)
        except DataError as e:
            problems.append(str(e))

    for rec in loaded.values():
        for ref_id in rec.reference_ids:
            if ref_id not in reference_ids:
                problems.append(f"Recommendation {rec.id} uses reference {ref_id}, which is missing or invalid")
        if rec.reconsiders is None:
            continue
        original = loaded.get(rec.reconsiders)
        if original is None:
            problems.append(
                f"Recommendation {rec.id} reconsiders {rec.reconsiders}, which is missing or invalid"
            )
        elif original.project_id != rec.project_id:
            problems.append(
                f"Recommendation {rec.id} reconsiders {original.id} from a different project "
                f"({original.project_id!r})"
            )
    return loaded


def _check_feedback(root: Path, recommendations: dict[str, Recommendation], problems: list[str]) -> None:
    for path, rec_id in _entries(root, FEEDBACK_DIR, ".jsonl", common.RECOMMENDATION_ID_RE, problems):
        if rec_id not in recommendations:
            problems.append(f"Feedback {path} belongs to recommendation {rec_id}, which is missing or invalid")
        try:
            read_history_file(path, rec_id)
        except DataError as e:
            problems.append(str(e))
