"""Manual entry point for project understanding.

    python -m organizador.understanding collect --project-dir D --project-id ID
        [--github-repo OWNER/REPO] [--vault-dir V] [--out FILE]
    python -m organizador.understanding check --project-dir D --project-id ID
        [--github-repo OWNER/REPO] [--vault-dir V] --draft FILE [--save --data-root R]

`collect` prints the evidence index (or writes it to a new file with --out).
`check` collects the evidence again, checks the draft against it, and with
--save stores it as a new profile. Exit codes: 0 = valid, 1 = problems or error.
Nothing reads config/config.toml; every location is given explicitly.
"""

import argparse
import json
import sys
from pathlib import Path

from organizador.recommendations import common
from organizador.recommendations.common import DataError
from organizador.understanding.evidence import EvidenceError, collect_evidence
from organizador.understanding.profile import check_profile_draft, create_profile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m organizador.understanding")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("collect", "check"):
        sub = commands.add_parser(name)
        sub.add_argument("--project-dir", required=True, type=Path)
        sub.add_argument("--project-id", required=True)
        sub.add_argument("--github-repo")
        sub.add_argument("--vault-dir", type=Path, help="authorizes reading its notes and GitHub reports")
    commands.choices["collect"].add_argument("--out", type=Path, help="new file to write (never overwritten)")
    check = commands.choices["check"]
    check.add_argument("--draft", required=True, type=Path)
    check.add_argument("--save", action="store_true")
    check.add_argument("--data-root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check" and args.save and args.data_root is None:
        _err("Error: --save needs --data-root")
        return 1
    try:
        evidence = collect_evidence(
            args.project_dir,
            project_id=args.project_id,
            github_repo=args.github_repo,
            vault_dir=args.vault_dir,
        )
        if args.command == "collect":
            return _collect(evidence, args.out)
        return _check(evidence, args.draft, args.save, args.data_root)
    except (EvidenceError, DataError, OSError) as e:
        _err(f"Error: {e}")
        return 1


def _collect(evidence: dict, out: Path | None) -> int:
    text = json.dumps(evidence, indent=2, ensure_ascii=False) + "\n"
    if out is None:
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.flush()
    else:
        common.create_new_file(out, text)
        _out(f"Evidence written: {out}")
    return 0


def _check(evidence: dict, draft_path: Path, save: bool, data_root: Path | None) -> int:
    try:
        draft = json.loads(draft_path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        _err(f"Error: cannot read draft {draft_path}: {e}")
        return 1
    problems = check_profile_draft(draft, evidence)
    if problems:
        _err(f"{len(problems)} problem(s) in {draft_path}:")
        for problem in problems:
            _err(f"- {problem}")
        return 1
    if not evidence["working_tree_clean"]:
        _out("Note: the working tree has uncommitted changes; the profile records working_tree_clean = false.")
    if not save:
        _out(f"Draft is valid: {draft_path}")
        return 0
    profile = create_profile(data_root, draft, evidence)
    _out(f"Profile saved: {common.record_path(data_root, 'profiles', profile.id, '.json')}")
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
