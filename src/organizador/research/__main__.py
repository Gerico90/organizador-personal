"""Manual entry point for external research.

    python -m organizador.research start --data-root R --project-id ID
    python -m organizador.research brief --data-root R --cycle-id C PROJECT
    python -m organizador.research fetch --data-root R --cycle-id C URL [URL ...]
    python -m organizador.research check --data-root R --draft FILE PROJECT
    python -m organizador.research save  --data-root R --draft FILE PROJECT

PROJECT is --project-dir D [--github-repo OWNER/REPO] [--vault-dir V]; the
project ID comes from the cycle. `brief` writes the cycle's brief and prints
its path; `check` and `save` collect the evidence again and validate the
draft against it (see drafts.py). Exit codes: 0 = done or valid, 1 = problems
or error. Nothing reads config/config.toml.
"""

import argparse
import json
import sys
from pathlib import Path

from organizador.recommendations.common import DataError
from organizador.research.brief import build_brief, write_brief
from organizador.research.drafts import check_research_draft, save_research_draft
from organizador.research.snapshots import FetchError, fetch_snapshot, load_cycle, start_cycle
from organizador.understanding.evidence import EvidenceError, collect_evidence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m organizador.research")
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start", help="create a new research cycle")
    start.add_argument("--data-root", required=True, type=Path)
    start.add_argument("--project-id", required=True)

    fetch = commands.add_parser("fetch", help="store text snapshots of evidence pages")
    fetch.add_argument("--data-root", required=True, type=Path)
    fetch.add_argument("--cycle-id", required=True)
    fetch.add_argument("urls", nargs="+", metavar="URL")

    brief = commands.add_parser("brief", help="write the cycle's research brief")
    brief.add_argument("--data-root", required=True, type=Path)
    brief.add_argument("--cycle-id", required=True)
    _project_arguments(brief)

    for name, text in (("check", "validate a research draft"), ("save", "validate and save a research draft")):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("--data-root", required=True, type=Path)
        sub.add_argument("--draft", required=True, type=Path)
        _project_arguments(sub)
    return parser


def _project_arguments(parser) -> None:
    parser.add_argument("--project-dir", required=True, type=Path)
    parser.add_argument("--github-repo")
    parser.add_argument("--vault-dir", type=Path, help="authorizes reading its notes and GitHub reports")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "start":
            _out(start_cycle(args.data_root, args.project_id))
            return 0
        if args.command == "fetch":
            return _fetch(args)
        if args.command == "brief":
            return _brief(args)
        return _check_or_save(args)
    except (DataError, EvidenceError, OSError) as e:
        _err(f"Error: {e}")
        return 1


def _fetch(args) -> int:
    failed = 0
    for url in args.urls:
        try:
            snapshot, created = fetch_snapshot(args.data_root, args.cycle_id, url)
        except (FetchError, DataError, OSError) as e:
            _err(f"Error: {url}: {e}")
            failed += 1
            continue
        state = "stored" if created else "already stored"
        truncated = " (text truncated)" if snapshot.text_truncated else ""
        _out(f"{snapshot.id} {state}: {snapshot.url} -> {snapshot.final_url}, {len(snapshot.text)} chars{truncated}")
    return 1 if failed else 0


def _evidence(args, project_id: str) -> dict:
    return collect_evidence(
        args.project_dir, project_id=project_id, github_repo=args.github_repo, vault_dir=args.vault_dir
    )


def _brief(args) -> int:
    cycle = load_cycle(args.data_root, args.cycle_id)
    brief = build_brief(args.data_root, args.cycle_id, _evidence(args, cycle["project_id"]))
    path = write_brief(args.data_root, args.cycle_id, brief)
    profile = brief["profile"]
    _out(f"Brief written: {path}")
    if profile is None:
        _out("No profile exists for this project. Run /project-understanding first.")
        return 1
    if not profile["current"]:
        _out(f"Profile {profile['id']} is not current:")
        for reason in profile["stale_reasons"]:
            _out(f"- {reason}")
        return 1
    _out(f"Profile {profile['id']} is current. Existing recommendations: {len(brief['recommendations'])}.")
    return 0


def _check_or_save(args) -> int:
    try:
        draft = json.loads(args.draft.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        _err(f"Error: cannot read draft {args.draft}: {e}")
        return 1
    if not isinstance(draft, dict) or not isinstance(draft.get("cycle_id"), str):
        _err("Error: the draft must be a JSON object with a cycle_id")
        return 1
    cycle = load_cycle(args.data_root, draft["cycle_id"])
    evidence = _evidence(args, cycle["project_id"])
    if args.command == "check":
        problems = check_research_draft(args.data_root, draft, evidence)
        if problems:
            _err(f"{len(problems)} problem(s) in {args.draft}:")
            for problem in problems:
                _err(f"- {problem}")
            return 1
        _out(f"Draft is valid: {args.draft} ({len(draft['recommendations'])} recommendation(s))")
        return 0
    result = save_research_draft(args.data_root, draft, evidence)
    _out(f"Saved cycle {result['cycle_id']}: {len(result['recommendations'])} recommendation(s)")
    for rec_id in result["recommendations"]:
        _out(f"- {rec_id}")
    for ref in result["references"]:
        if ref["differences"]:
            _out(f"Note: reference {ref['id']} already existed; differing fields not changed: {', '.join(ref['differences'])}")
    return 0


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
