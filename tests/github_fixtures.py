"""Realistic GitHub API response shapes shared by report tests."""

from organizador.github.changes import (
    CLOSED,
    INCREMENTAL,
    MERGED,
    OPENED,
    UPDATED,
    Changes,
    CommitChanges,
    ItemChange,
    ItemChanges,
)
from organizador.github.state import Cursor

REPO = "octo-cat/my.repo"
PREV = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d"
HEAD = "e4f5a6b7c8d9e0f1a2b3c4d5e6f708192a3b4c5d"
SIDE = "5b6c7d8e9f0a1b2c3d4e5f60718293a4b5c6d7e8"


def commit(sha, message, login="alice", name="Alice Example", date="2026-10-24T09:15:00Z"):
    return {
        "sha": sha,
        "html_url": f"https://github.com/{REPO}/commit/{sha}",
        "commit": {
            "message": message,
            "author": {"name": name, "email": "a@example.com", "date": date},
            "committer": {"name": name, "email": "a@example.com", "date": date},
        },
        "author": {"login": login} if login else None,
        "parents": [{"sha": PREV}],
    }


def issue(number, title, login, created_at, updated_at, closed_at=None):
    return {
        "number": number,
        "title": title,
        "state": "closed" if closed_at else "open",
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "user": {"login": login},
        "labels": [{"name": "bug"}],
        "created_at": created_at,
        "updated_at": updated_at,
        "closed_at": closed_at,
        "body": "Steps to reproduce:\n1. run\n2. crash",
    }


def pull(number, title, login, created_at, updated_at, closed_at=None, merged_at=None):
    item = issue(number, title, login, created_at, updated_at, closed_at)
    item["html_url"] = f"https://github.com/{REPO}/pull/{number}"
    item["merged_at"] = merged_at
    item["draft"] = False
    return item


def incremental_changes() -> Changes:
    commits = CommitChanges(
        INCREMENTAL,
        (
            commit(HEAD, "Merge branch 'feature/export'\n\nLong description."),
            commit(SIDE, "Add CSV export", login=None, name="Bob Builder", date="2026-01-05T08:00:00Z"),
        ),
        PREV,
        HEAD,
    )
    pulls = ItemChanges(
        (
            ItemChange(MERGED, pull(42, "Add export", "bob", "2026-10-20T10:00:00Z", "2026-10-24T09:15:05Z",
                                    closed_at="2026-10-24T09:15:00Z", merged_at="2026-10-24T09:15:00Z")),
            ItemChange(OPENED, pull(43, "Draft: faster parser", "carol", "2026-10-23T16:00:00Z", "2026-10-23T16:30:00Z")),
        ),
        Cursor("2026-10-24T09:15:05Z", (42,)),
    )
    issues = ItemChanges(
        (
            ItemChange(OPENED, issue(57, "Crash on empty input", "carol", "2026-10-22T11:00:00Z", "2026-10-22T11:00:00Z")),
            ItemChange(CLOSED, issue(51, "Typo in README", "dave", "2026-09-01T00:00:00Z", "2026-10-21T08:00:00Z",
                                     closed_at="2026-10-21T08:00:00Z")),
            ItemChange(UPDATED, issue(12, "Support Windows paths", "erin", "2026-03-03T00:00:00Z", "2026-10-19T20:45:00Z")),
        ),
        Cursor("2026-10-22T11:00:00Z", (57,)),
    )
    return Changes(baseline=False, commits=commits, issues=issues, pulls=pulls)
