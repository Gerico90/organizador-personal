"""Change detection: compare GitHub data with the saved checkpoint.

No network and no files. Functions take plain GitHub response dicts, or any
iterable of them. When the iterable is one of the client's lazy generators,
stopping early here also stops requesting further pages.

Two separate concerns:
- Query boundary: what to ask GitHub for (`window_start`, `issues_query_since`).
  It may include items we have already seen.
- Filtering: what counts as new (`after_cursor`). This alone decides what is
  reported and how the cursor advances.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from organizador.github.state import TIMESTAMP_FORMAT, Cursor

_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", re.ASCII)

# CommitChanges.status values (also used as the report's commits_status).
BASELINE = "baseline"
INCREMENTAL = "incremental"
HISTORY_REWRITTEN = "history_rewritten"

# ItemChange.activity values.
OPENED = "opened"
UPDATED = "updated"
CLOSED = "closed"
MERGED = "merged"


@dataclass(frozen=True)
class CommitChanges:
    status: str                 # BASELINE | INCREMENTAL | HISTORY_REWRITTEN
    commits: tuple[dict, ...]   # GitHub commit objects, newest first
    previous_head: str | None   # None on the baseline
    head_sha: str               # new checkpoint


@dataclass(frozen=True)
class ItemChange:
    activity: str               # OPENED | UPDATED | CLOSED | MERGED
    item: dict                  # GitHub issue or pull request object


@dataclass(frozen=True)
class ItemChanges:
    changes: tuple[ItemChange, ...]  # most recently updated first
    cursor: Cursor                   # new checkpoint (unchanged if nothing new)


@dataclass(frozen=True)
class Changes:
    baseline: bool
    commits: CommitChanges
    issues: ItemChanges
    pulls: ItemChanges

    @property
    def has_changes(self) -> bool:
        """False only when there is nothing to report. A rewrite is always reported."""
        return bool(
            self.commits.status == HISTORY_REWRITTEN
            or self.commits.commits
            or self.issues.changes
            or self.pulls.changes
        )


# --- timestamps and query boundaries -----------------------------------------


def parse_ts(value: str) -> datetime:
    """Parse YYYY-MM-DDTHH:MM:SSZ exactly; strptime alone accepts unpadded fields."""
    try:
        if not (isinstance(value, str) and _TIMESTAMP_RE.fullmatch(value)):
            raise ValueError
        return datetime.strptime(value, TIMESTAMP_FORMAT)
    except ValueError:
        raise ValueError(f"Invalid GitHub timestamp {value!r}") from None


def format_ts(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.strftime(TIMESTAMP_FORMAT)


def window_start(now: datetime, lookback_days: int) -> str:
    """First run only: start of the lookback window, whole seconds, UTC."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return format_ts(now.replace(microsecond=0) - timedelta(days=lookback_days))


def initial_cursor(start: str) -> Cursor:
    """Cursor before anything has been seen: the window start, no ids."""
    parse_ts(start)
    return Cursor(ts=start, ids=())


def issues_query_since(cursor: Cursor) -> str:
    """`since` for the issues request: one second before the cursor.

    This does not rely on whether GitHub's `since` includes items exactly at
    cursor.ts. Items from the extra second are removed by `after_cursor`.
    """
    return format_ts(parse_ts(cursor.ts) - timedelta(seconds=1))


# --- issues and pull requests ------------------------------------------------


def after_cursor(timestamp: str | None, number: int, cursor: Cursor) -> bool:
    """True if `timestamp` is after the checkpoint.

    Tied at cursor.ts counts as after only for numbers not already in cursor.ids.
    A missing or null timestamp is never after.
    """
    if timestamp is None:
        return False
    t, c = parse_ts(timestamp), parse_ts(cursor.ts)
    return t > c or (t == c and number not in cursor.ids)


def classify(item: dict, cursor: Cursor) -> str:
    """merged > closed > opened > updated, each judged against the checkpoint."""
    number = item["number"]
    if after_cursor(item.get("merged_at"), number, cursor):
        return MERGED
    if after_cursor(item.get("closed_at"), number, cursor):
        return CLOSED
    if after_cursor(item.get("created_at"), number, cursor):
        return OPENED
    return UPDATED


def detect_items(items: Iterable[dict], cursor: Cursor) -> ItemChanges:
    """Issues or pull requests changed after `cursor`, and the advanced cursor.

    `items` must be sorted by updated_at, newest first (the client requests
    sort=updated&direction=desc). Iteration stops at the first item older
    than cursor.ts. A number seen twice in one run (page shift) is reported
    once, using its first, newest copy.
    """
    cursor_time = parse_ts(cursor.ts)
    changes: list[ItemChange] = []
    seen: set[int] = set()
    previous_time = None

    for item in items:
        updated = parse_ts(item["updated_at"])
        if previous_time is not None and updated > previous_time:
            raise ValueError(
                f"Items are not sorted by updated_at (newest first) at #{item['number']}"
            )
        previous_time = updated
        if updated < cursor_time:
            break
        if item["number"] in seen:
            continue
        seen.add(item["number"])
        if after_cursor(item["updated_at"], item["number"], cursor):
            changes.append(ItemChange(classify(item, cursor), item))

    return ItemChanges(changes=tuple(changes), cursor=advance_cursor(cursor, changes))


def advance_cursor(cursor: Cursor, changes: list[ItemChange]) -> Cursor:
    """Move the cursor to the newest reported timestamp, keeping tied ids."""
    if not changes:
        return cursor
    newest = max(changes, key=lambda c: parse_ts(c.item["updated_at"])).item["updated_at"]
    newest_time = parse_ts(newest)
    at_newest = {c.item["number"] for c in changes if parse_ts(c.item["updated_at"]) == newest_time}
    if newest_time == parse_ts(cursor.ts):
        at_newest |= set(cursor.ids)
        newest = cursor.ts
    return Cursor(ts=newest, ids=tuple(sorted(at_newest)))


# --- commits -----------------------------------------------------------------


def baseline_commits(commits: Iterable[dict], head_sha: str) -> CommitChanges:
    """First run: commits listed from `head_sha` within the lookback window, newest first.

    Pass commits listed from the head SHA (not the branch name), so the list
    and the saved checkpoint describe the same history.
    """
    return CommitChanges(BASELINE, tuple(commits), None, head_sha)


def rewritten_commits(previous_head: str, head_sha: str) -> CommitChanges:
    """History rewritten: no commit list, explicit status, new baseline at `head_sha`."""
    return CommitChanges(HISTORY_REWRITTEN, (), previous_head, head_sha)


def commits_from_compare(pages: Iterable[dict], previous_head: str, head_sha: str) -> CommitChanges:
    """Incremental commits from compare/{previous_head}...{head_sha} pages.

    - identical: no new commits.
    - ahead: every commit in the comparison (git log previous..head, so
      merged-in commits with old dates are included), newest first. The
      count must equal total_commits; otherwise the result is incomplete
      and ValueError is raised.
    - behind / diverged: history rewritten; remaining pages are not read.

    A 404 from compare is not handled here; once confirmed, the caller uses
    `rewritten_commits`.
    """
    collected: list[dict] = []
    status = total = None

    for page in pages:
        if status is None:
            status, total = page["status"], page["total_commits"]
            if status in ("behind", "diverged"):
                return rewritten_commits(previous_head, head_sha)
            if status not in ("identical", "ahead"):
                raise ValueError(f"Unexpected comparison status {status!r}")
            if status == "identical":
                break
        collected.extend(page["commits"])

    if status is None:
        raise ValueError("Comparison returned no pages")
    if status == "identical":
        if head_sha != previous_head:
            raise ValueError("Comparison reported 'identical' but the head SHA changed")
        return CommitChanges(INCREMENTAL, (), previous_head, head_sha)

    shas = [c["sha"] for c in collected]
    if len(collected) != total or len(set(shas)) != len(shas):
        raise ValueError(
            f"Incomplete comparison: expected {total} commits, received {len(collected)}"
            + (" with duplicates" if len(set(shas)) != len(shas) else "")
        )
    if previous_head in shas:
        raise ValueError("Comparison unexpectedly includes the previous head")
    # Membership, not position: compare orders by date, which can disagree with
    # history order when commit clocks are skewed.
    if head_sha not in shas:
        raise ValueError("Comparison does not include the expected head SHA")
    return CommitChanges(INCREMENTAL, tuple(reversed(collected)), previous_head, head_sha)
