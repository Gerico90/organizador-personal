"""Command-line entry point.

Exit codes: 0 = success, 1 = failure (nothing written), 2 = completed with warning.

`github run` order (docs/mvp1-architecture-proposal.md, section 3):
load config and state -> read everything from GitHub -> detect changes ->
render -> create report (never overwrite) -> save state (atomic).
Nothing is written until every GitHub read has succeeded. State is saved
only after the report exists.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from organizador.config import PROJECT_ROOT, ConfigError, load_config
from organizador.fsutil import create_exclusive
from organizador.github.changes import (
    HISTORY_REWRITTEN,
    INCREMENTAL,
    Changes,
    CommitChanges,
    baseline_commits,
    commits_from_compare,
    detect_items,
    format_ts,
    initial_cursor,
    issues_query_since,
    rewritten_commits,
    window_start,
)
from organizador.github.client import (
    Fetch,
    GitHubClient,
    GitHubError,
    GitHubNotFoundError,
    default_fetch,
)
from organizador.github.report import render_report
from organizador.github.state import (
    State,
    StateError,
    load_state,
    repo_slug,
    save_state,
    state_file_path,
    validate_state,
)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_WARNING = 2


class RunError(Exception):
    """A failure with a message already written for the user."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m organizador",
        description="Personal local-first backend.",
    )
    parser.set_defaults(print_help=parser.print_help, command=None)
    modules = parser.add_subparsers(metavar="MODULE")

    github = modules.add_parser("github", help="GitHub repository activity reports")
    github.set_defaults(print_help=github.print_help)
    github_commands = github.add_subparsers(metavar="COMMAND")
    github_run = github_commands.add_parser(
        "run", help="Report activity since the last successful run"
    )
    github_run.set_defaults(command="github run")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "github run":
        return run_github()

    args.print_help()
    return 0


def run_github(
    project_root: Path = PROJECT_ROOT,
    fetch: Fetch = default_fetch,
    now: datetime | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    try:
        return _run_github(project_root, fetch, now, out, err)
    except (ConfigError, StateError, RunError) as e:
        _say(err, f"Error: {e}")
    except GitHubError as e:
        _say(err, f"Error: {e}")
    except ValueError as e:
        # Inconsistent or malformed GitHub data found during change detection.
        _say(err, f"Error: unexpected GitHub data: {e}")
    _say(err, "Nothing was written. The saved checkpoint was not changed.")
    return EXIT_FAILURE


def _run_github(project_root, fetch, now, out, err) -> int:
    generated_at = format_ts(now)

    # 1-3. Configuration, previous checkpoint, read-only client.
    config = load_config(project_root)
    state = load_state(config)
    slug = repo_slug(config)
    client = GitHubClient.from_env(config.github_owner, config.github_repo, fetch=fetch)

    # 4. Repository metadata and current head.
    branch = client.get_repository()["default_branch"]
    if state is not None and state.default_branch != branch:
        raise RunError(
            f"The default branch changed from '{state.default_branch}' to '{branch}'. "
            f"Back up {state_file_path(config)} and delete it to start a new baseline."
        )
    try:
        head_sha = client.get_branch(branch)["commit"]["sha"]
    except GitHubNotFoundError:
        raise RunError(
            f"Branch '{branch}' of {slug} was not found. "
            "The repository may be empty (no commits yet)."
        ) from None

    # 5-6. Read everything and detect changes. Nothing is written yet.
    if state is None:
        period_from = window_start(now, config.initial_lookback_days)
        issues_cursor = pulls_cursor = initial_cursor(period_from)
        commits = baseline_commits(client.iter_commits(head_sha, since=period_from), head_sha)
    else:
        period_from = state.last_success_at
        issues_cursor, pulls_cursor = state.issues_cursor, state.pulls_cursor
        commits = _incremental_commits(client, branch, state.last_head_sha, head_sha)

    issues = detect_items(client.iter_issues(since=issues_query_since(issues_cursor)), issues_cursor)
    pulls = detect_items(client.iter_pulls(), pulls_cursor)
    changes = Changes(baseline=state is None, commits=commits, issues=issues, pulls=pulls)

    if state is not None and not changes.has_changes:
        _say(out, f"No changes since {state.last_success_at}.")
        return EXIT_OK

    # 7. Render and check the new state before writing anything.
    report_text = render_report(
        changes,
        repo=slug,
        branch=branch,
        period_from=period_from,
        period_to=generated_at,
        generated_at=generated_at,
    )
    report_dir_rel = f"reports/github/{config.github_owner}__{config.github_repo}"
    stem = generated_at.replace(":", "")

    def new_state(report_name: str) -> State:
        return State(
            repo=slug,
            default_branch=branch,
            last_head_sha=head_sha,
            issues_cursor=issues.cursor,
            pulls_cursor=pulls.cursor,
            last_success_at=generated_at,
            last_report=f"{report_dir_rel}/{report_name}",
        )

    validate_state(config, new_state(f"{stem}.md"))

    # 8. Create the report. Never overwrites; picks -2..-99 on collision.
    try:
        report_path = create_exclusive(config.vault_path / report_dir_rel, stem, ".md", report_text)
    except OSError as e:
        raise RunError(f"Could not create the report: {e}") from None

    # 9. Save the checkpoint only after the report exists.
    try:
        save_state(config, new_state(report_path.name))
    except (OSError, StateError) as e:
        _say(err, f"Error: the report was written to {report_path}")
        _say(err, f"but the checkpoint could not be saved: {e}")
        _say(err, "The saved checkpoint was not changed. The next run will report this period again.")
        return EXIT_FAILURE

    # 10. Result.
    kind = "Baseline report" if changes.baseline else "Report"
    _say(out, f"{kind} written: {report_path}")
    _say(out, f"  {_counts(changes)}")
    if commits.status == HISTORY_REWRITTEN:
        _say(
            err,
            f"Warning: the history of '{branch}' was rewritten. Previous head "
            f"{commits.previous_head[:7]} is no longer reachable; commit comparison was "
            f"skipped. New commit baseline: {head_sha[:7]}.",
        )
        return EXIT_WARNING
    return EXIT_OK


def _incremental_commits(client: GitHubClient, branch: str, previous: str, head: str) -> CommitChanges:
    if previous == head:
        return CommitChanges(INCREMENTAL, (), previous, head)
    try:
        return commits_from_compare(client.iter_compare(previous, head), previous, head)
    except GitHubNotFoundError:
        pass

    # Compare returned 404. Treat it as a rewrite only if the branch exists
    # and the previous commit is confirmed missing (404 or 422). Any other
    # result, including another error, stops the run.
    client.get_branch(branch)
    try:
        client.get_commit(previous)
    except GitHubError as e:
        if e.status in (404, 422):
            return rewritten_commits(previous, head)
        raise
    raise RunError(
        f"Comparing {previous[:7]}...{head[:7]} returned 404, but commit {previous[:7]} "
        "still exists. The cause is unclear, so the checkpoint was not advanced."
    )


def _counts(changes: Changes) -> str:
    def plural(n, noun):
        return f"{n} {noun}" + ("" if n == 1 else "s")

    commits = (
        "commits: comparison unavailable"
        if changes.commits.status == HISTORY_REWRITTEN
        else plural(len(changes.commits.commits), "commit")
    )
    return ", ".join(
        [commits, plural(len(changes.pulls.changes), "pull request"), plural(len(changes.issues.changes), "issue")]
    )


def _say(stream: TextIO, text: str) -> None:
    """Print without failing on consoles that cannot encode some characters (Windows cp1252)."""
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        text = text.encode(encoding, "replace").decode(encoding)
    except LookupError:
        text = text.encode("ascii", "replace").decode("ascii")
    print(text, file=stream)
