"""User feedback on recommendations: an append-only history per recommendation.

One JSON Lines file per recommendation: <root>/feedback/<rec_id>.jsonl, one
evaluation per line, oldest first. Lines are only ever added at the end;
there is no function to edit or delete one. A change of opinion is a new line.

Reading is strict: an empty line, malformed JSON, a missing final newline
(an interrupted write), or an out-of-order timestamp raises DataError. The
file is never repaired here.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from organizador.recommendations import common
from organizador.recommendations.common import (
    FEEDBACK_DIR,
    SCHEMA_VERSION,
    ConflictError,
    DataError,
)
from organizador.recommendations.records import load_recommendation

RATINGS = ("like", "dislike")  # or null: no rating
DECISIONS = ("pending", "future", "explore", "discard")

_KEYS = ("schema_version", "recommendation_id", "evaluated_at", "rating", "decision", "notes")


@dataclass(frozen=True)
class FeedbackEvent:
    recommendation_id: str
    evaluated_at: str
    rating: str | None
    decision: str
    notes: str | None


def append_feedback(
    root: Path,
    rec_id: str,
    *,
    rating: str | None,
    decision: str,
    notes: str | None = None,
    now: datetime | None = None,
) -> FeedbackEvent:
    """Add one evaluation to the end of the recommendation's history.

    The recommendation must exist, the existing history must be valid, and
    the new evaluation cannot be dated before the last one.
    """
    load_recommendation(root, rec_id)  # NotFoundError / DataError
    event_data = {
        "schema_version": SCHEMA_VERSION,
        "recommendation_id": rec_id,
        "evaluated_at": common.utc_now(now),
        "rating": rating,
        "decision": decision,
        "notes": notes,
    }
    try:
        event = _from_dict(event_data, rec_id)
    except ValueError as e:
        raise DataError(f"Invalid feedback: {e}") from None

    path = common.record_path(root, FEEDBACK_DIR, rec_id, ".jsonl")
    history = _read(path, rec_id)
    if history and common.parse_ts(event.evaluated_at) < common.parse_ts(history[-1].evaluated_at):
        raise ConflictError(
            f"Feedback for {rec_id}: evaluation time {event.evaluated_at} is earlier than "
            f"the last recorded one ({history[-1].evaluated_at}); nothing was written"
        )

    line = (json.dumps(_to_dict(event), ensure_ascii=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as f:
        size_before = f.tell()
        try:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            # Undo only our own partial line, so earlier lines stay intact.
            try:
                f.truncate(size_before)
            except OSError:
                pass
            raise
    return event


def feedback_history(root: Path, rec_id: str) -> tuple[FeedbackEvent, ...]:
    """Every evaluation of the recommendation, oldest first. Empty if none."""
    load_recommendation(root, rec_id)
    return _read(common.record_path(root, FEEDBACK_DIR, rec_id, ".jsonl"), rec_id)


def latest_feedback(root: Path, rec_id: str) -> FeedbackEvent | None:
    history = feedback_history(root, rec_id)
    return history[-1] if history else None


def read_history_file(path: Path, rec_id: str) -> tuple[FeedbackEvent, ...]:
    """Validate one history file without checking that the recommendation exists."""
    return _read(path, rec_id)


def _read(path: Path, rec_id: str) -> tuple[FeedbackEvent, ...]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return ()
    except OSError as e:
        raise DataError(f"Cannot read {path}: {e}") from None
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise DataError(f"{path} is not valid UTF-8: {e}") from None
    if not content:
        return ()
    if not content.endswith("\n"):
        raise DataError(f"{path}: the last line is incomplete (no final newline)")

    events: list[FeedbackEvent] = []
    for number, line in enumerate(content[:-1].split("\n"), start=1):
        where = f"{path}, line {number}"
        if not line.strip():
            raise DataError(f"{where}: empty line")
        try:
            data = json.loads(line)
        except json.JSONDecodeError as e:
            raise DataError(f"{where}: malformed JSON: {e}") from None
        try:
            event = _from_dict(data, rec_id)
        except ValueError as e:
            raise DataError(f"{where}: {e}") from None
        if events and common.parse_ts(event.evaluated_at) < common.parse_ts(events[-1].evaluated_at):
            raise DataError(f"{where}: evaluated_at is earlier than the previous line")
        events.append(event)
    return tuple(events)


def _to_dict(event: FeedbackEvent) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "recommendation_id": event.recommendation_id,
        "evaluated_at": event.evaluated_at,
        "rating": event.rating,
        "decision": event.decision,
        "notes": event.notes,
    }


def _from_dict(data, rec_id: str) -> FeedbackEvent:
    common.check_keys(data, _KEYS, ())
    common.check_schema_version(data)
    stored_id = common.recommendation_id(data["recommendation_id"], "recommendation_id")
    if stored_id != rec_id:
        raise ValueError(f"recommendation_id {stored_id!r} does not match the file ({rec_id})")
    rating = data["rating"]
    if rating is not None and rating not in RATINGS:
        raise ValueError(f"rating must be one of {', '.join(RATINGS)} or null, got {rating!r}")
    decision = data["decision"]
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {', '.join(DECISIONS)}, got {decision!r}")
    return FeedbackEvent(
        recommendation_id=stored_id,
        evaluated_at=common.timestamp(data["evaluated_at"], "evaluated_at"),
        rating=rating,
        decision=decision,
        notes=common.optional_text(data["notes"], "notes"),
    )
