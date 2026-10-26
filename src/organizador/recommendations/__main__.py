"""Read recommendations and record your feedback on them.

    python -m organizador.recommendations list --data-root R --project-id ID
    python -m organizador.recommendations show --data-root R REC_ID
    python -m organizador.recommendations feedback --data-root R REC_ID --decision D [--rating like|dislike] [--notes TEXT]

`feedback` appends one evaluation; earlier ones are kept. The rating and the
decision are independent: leave out --rating to record no rating.
Exit codes: 0 = done, 1 = error. Nothing reads config/config.toml.
"""

import argparse
import sys
from pathlib import Path

from organizador.recommendations.common import DataError
from organizador.recommendations.feedback import DECISIONS, RATINGS, append_feedback, feedback_history
from organizador.recommendations.records import list_recommendations, load_recommendation
from organizador.recommendations.references import load_reference


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m organizador.recommendations")
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="recommendations of a project with their latest feedback")
    listing.add_argument("--data-root", required=True, type=Path)
    listing.add_argument("--project-id", required=True)

    show = commands.add_parser("show", help="one recommendation and its feedback history")
    show.add_argument("--data-root", required=True, type=Path)
    show.add_argument("rec_id")

    feedback = commands.add_parser("feedback", help="add an evaluation (earlier ones are kept)")
    feedback.add_argument("--data-root", required=True, type=Path)
    feedback.add_argument("rec_id")
    feedback.add_argument("--decision", required=True, choices=DECISIONS)
    feedback.add_argument("--rating", choices=RATINGS, help="omit for no rating")
    feedback.add_argument("--notes")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "list":
            _list(args.data_root, args.project_id)
        elif args.command == "show":
            _show(args.data_root, args.rec_id)
        else:
            event = append_feedback(
                args.data_root, args.rec_id, rating=args.rating, decision=args.decision, notes=args.notes
            )
            _out(
                f"Recorded for {event.recommendation_id} at {event.evaluated_at}: "
                f"decision {event.decision}, rating {event.rating or 'none'}"
            )
    except (DataError, OSError) as e:
        _err(f"Error: {e}")
        return 1
    return 0


def _list(root: Path, project_id: str) -> None:
    recs = list_recommendations(root, project_id)
    if not recs:
        _out(f"No recommendations for {project_id}.")
        return
    for rec in recs:
        history = feedback_history(root, rec.id)
        latest = history[-1] if history else None
        state = f"{latest.decision}, rating {latest.rating or 'none'}" if latest else "not evaluated"
        _out(f"{rec.id}  [{state}]  {rec.title}")


def _show(root: Path, rec_id: str) -> None:
    rec = load_recommendation(root, rec_id)
    lines = [
        rec.title,
        f"ID: {rec.id}    created: {rec.created_at}    project: {rec.project_id}",
        f"Category: {rec.category}",
        f"Relationship: {rec.relationship}",
    ]
    if rec.reconsiders:
        lines.append(f"Reconsiders: {rec.reconsiders}")
    lines += ["", "Proposal:", f"  {rec.proposal}", "", "Justification:"]
    lines += [f"  {line}" for line in rec.justification.splitlines()]
    lines += _section("Benefits", rec.benefits) + _section("Limitations", rec.limitations)
    refs = []
    for ref_id in rec.reference_ids:
        ref = load_reference(root, ref_id)
        refs.append(f"{ref.name} <{ref.canonical_url}> ({ref.id})")
    lines += _section("References", refs)
    lines += _section(
        "Sources",
        [
            f"{s.name}" + (f" <{s.url}>" if s.url else " (no URL)")
            + "".join(f"\n      evidence: {u}" for u in s.evidence_urls)
            for s in rec.sources
        ],
    )
    ctx = rec.project_context
    lines += _section(
        f"Project context (commit {(ctx.commit_sha or 'unknown')[:7]})",
        [f"[{c.topic}, {c.basis}] {c.statement}" + (f"  ({', '.join(c.sources)})" if c.sources else "") for c in ctx.claims],
    )
    history = feedback_history(root, rec.id)
    lines += _section(
        "Feedback",
        [
            f"{e.evaluated_at}  decision {e.decision}, rating {e.rating or 'none'}" + (f"  - {e.notes}" if e.notes else "")
            for e in history
        ]
        or ["not evaluated yet"],
    )
    _out("\n".join(lines))


def _section(title: str, items) -> list[str]:
    return ["", f"{title}:"] + ([f"  - {item}" for item in items] if items else ["  (none)"])


def _out(text: str) -> None:
    _write(sys.stdout, text)


def _err(text: str) -> None:
    _write(sys.stderr, text)


def _write(stream, text: str) -> None:
    """Print without failing on consoles that cannot encode some characters (Windows cp1252)."""
    encoding = getattr(stream, "encoding", None) or "ascii"
    print(text.encode(encoding, "replace").decode(encoding), file=stream)


if __name__ == "__main__":
    raise SystemExit(main())
