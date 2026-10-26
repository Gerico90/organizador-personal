from datetime import datetime, timezone

import pytest

from organizador.github.changes import (
    BASELINE,
    CLOSED,
    HISTORY_REWRITTEN,
    INCREMENTAL,
    MERGED,
    OPENED,
    UPDATED,
    Changes,
    CommitChanges,
    ItemChanges,
    after_cursor,
    baseline_commits,
    classify,
    commits_from_compare,
    detect_items,
    initial_cursor,
    issues_query_since,
    rewritten_commits,
    window_start,
)
from organizador.github.state import Cursor

T = "2026-10-25T10:00:00Z"
T_PLUS_1 = "2026-10-25T10:00:01Z"
T_MINUS_1 = "2026-10-25T09:59:59Z"
OLD = "2026-01-01T00:00:00Z"

PREV = "1" * 40
HEAD = "f" * 40


# --- realistic GitHub shapes -------------------------------------------------


def gh_issue(number, updated_at, created_at=OLD, closed_at=None, **extra):
    return {
        "number": number,
        "title": f"Issue {number}",
        "state": "closed" if closed_at else "open",
        "html_url": f"https://github.com/octo-cat/my.repo/issues/{number}",
        "user": {"login": "alice"},
        "labels": [],
        "created_at": created_at,
        "updated_at": updated_at,
        "closed_at": closed_at,
        **extra,
    }


def gh_pull(number, updated_at, created_at=OLD, closed_at=None, merged_at=None):
    item = gh_issue(number, updated_at, created_at, closed_at)
    item["html_url"] = f"https://github.com/octo-cat/my.repo/pull/{number}"
    item["merged_at"] = merged_at
    item["draft"] = False
    return item


def gh_commit(sha, date=T, message="Fix parser edge case"):
    return {
        "sha": sha,
        "html_url": f"https://github.com/octo-cat/my.repo/commit/{sha}",
        "commit": {
            "message": message,
            "author": {"name": "Alice", "date": date},
            "committer": {"name": "Alice", "date": date},
        },
        "author": {"login": "alice"},
        "parents": [{"sha": "0" * 40}],
    }


def compare_page(status, total, commits=()):
    return {"status": status, "ahead_by": total, "behind_by": 0, "total_commits": total, "commits": list(commits)}


def numbers(result: ItemChanges):
    return [c.item["number"] for c in result.changes]


def activities(result: ItemChanges):
    return {c.item["number"]: c.activity for c in result.changes}


# --- query boundaries --------------------------------------------------------


def test_window_start_is_15_days_back_in_whole_utc_seconds():
    now = datetime(2026, 10, 25, 18, 30, 11, 987654, tzinfo=timezone.utc)
    assert window_start(now, 15) == "2026-10-10T18:30:11Z"


def test_window_start_converts_to_utc():
    from datetime import timedelta
    now = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone(timedelta(hours=-6)))
    assert window_start(now, 1) == "2026-10-24T18:00:00Z"


def test_window_start_requires_aware_datetime():
    with pytest.raises(ValueError):
        window_start(datetime(2026, 10, 25), 15)


def test_initial_cursor_is_window_start_with_no_ids():
    assert initial_cursor("2026-10-10T18:30:11Z") == Cursor("2026-10-10T18:30:11Z", ())


def test_issues_query_since_overlaps_one_second():
    assert issues_query_since(Cursor(T, (1,))) == T_MINUS_1
    assert issues_query_since(Cursor("2026-01-01T00:00:00Z", ())) == "2025-12-31T23:59:59Z"


def test_overlap_items_are_filtered_not_reported():
    # GitHub returns the extra second; those items are older than the cursor.
    cursor = Cursor(T, (5,))
    result = detect_items([gh_issue(5, T), gh_issue(4, T_MINUS_1)], cursor)
    assert result.changes == ()
    assert result.cursor is cursor


# --- 1. initial baseline -----------------------------------------------------


def test_baseline_with_activity():
    start = "2026-10-10T18:30:11Z"
    cursor = initial_cursor(start)
    issues = [
        gh_issue(3, "2026-10-24T09:00:00Z", created_at="2026-10-20T00:00:00Z"),
        gh_issue(2, "2026-10-12T09:00:00Z"),
        gh_issue(1, start),                       # exactly at window start: included
        gh_issue(0, "2026-10-10T18:30:10Z"),      # before window: stops here
    ]
    result = detect_items(issues, cursor)
    assert activities(result) == {3: OPENED, 2: UPDATED, 1: UPDATED}
    assert result.cursor == Cursor("2026-10-24T09:00:00Z", (3,))

    commits = baseline_commits([gh_commit("c" * 40), gh_commit("b" * 40)], "c" * 40)
    assert commits.status == BASELINE
    assert commits.previous_head is None
    assert [c["sha"] for c in commits.commits] == ["c" * 40, "b" * 40]


def test_baseline_without_activity_initializes_cursors():
    start = "2026-10-10T18:30:11Z"
    issues = detect_items([], initial_cursor(start))
    pulls = detect_items([], initial_cursor(start))
    commits = baseline_commits([], HEAD)

    assert issues.cursor == pulls.cursor == Cursor(start, ())
    assert commits == CommitChanges(BASELINE, (), None, HEAD)
    changes = Changes(baseline=True, commits=commits, issues=issues, pulls=pulls)
    assert changes.baseline is True
    # Nothing to list, but Step 7 still writes the baseline because baseline=True.
    assert changes.has_changes is False


def test_baseline_commits_consumes_iterator():
    result = baseline_commits(iter([gh_commit(HEAD)]), HEAD)
    assert result.commits == (gh_commit(HEAD),)


# --- 2./3. incremental and no changes ---------------------------------------


def test_incremental_issues_and_pulls():
    issues_cursor = Cursor(T, (1,))
    pulls_cursor = Cursor(T_MINUS_1, (9,))
    issues = detect_items(
        [gh_issue(2, "2026-10-26T08:00:00Z", created_at="2026-10-26T07:00:00Z"), gh_issue(1, T)],
        issues_cursor,
    )
    pulls = detect_items(
        [gh_pull(10, T_PLUS_1, merged_at=T_PLUS_1, closed_at=T_PLUS_1), gh_pull(9, T_MINUS_1)],
        pulls_cursor,
    )
    assert activities(issues) == {2: OPENED}
    assert issues.cursor == Cursor("2026-10-26T08:00:00Z", (2,))
    assert activities(pulls) == {10: MERGED}
    assert pulls.cursor == Cursor(T_PLUS_1, (10,))


def test_no_changes_keeps_cursors_and_commit_head():
    cursor = Cursor(T, (1, 2))
    result = detect_items([gh_issue(2, T), gh_issue(1, T), gh_issue(0, T_MINUS_1)], cursor)
    assert result.changes == ()
    assert result.cursor is cursor

    commits = commits_from_compare([compare_page("identical", 0)], PREV, PREV)
    assert commits == CommitChanges(INCREMENTAL, (), PREV, PREV)

    changes = Changes(False, commits, result, detect_items([], Cursor(T, ())))
    assert changes.has_changes is False


def test_empty_collections():
    cursor = Cursor(T, (3,))
    assert detect_items([], cursor) == ItemChanges((), cursor)


# --- 4./5./6. ties across repeated runs and cursor advancement -------------


@pytest.mark.parametrize("make", [gh_issue, gh_pull])
def test_timestamp_ties_across_four_runs(make):
    # Run 1: #1 and #2 share T.
    run1 = detect_items([make(2, T), make(1, T)], Cursor(T_MINUS_1, ()))
    assert sorted(numbers(run1)) == [1, 2]
    assert run1.cursor == Cursor(T, (1, 2))

    # Run 2: #3 appears, also at T. Only #3 is new.
    run2 = detect_items([make(3, T), make(2, T), make(1, T)], run1.cursor)
    assert numbers(run2) == [3]
    assert run2.cursor == Cursor(T, (1, 2, 3))

    # Run 3: same data. Nothing new; cursor unchanged.
    run3 = detect_items([make(3, T), make(2, T), make(1, T)], run2.cursor)
    assert run3.changes == ()
    assert run3.cursor == run2.cursor

    # Run 4: #2 updated one second later. Only #2, as updated.
    run4 = detect_items([make(2, T_PLUS_1), make(3, T), make(1, T)], run3.cursor)
    assert activities(run4) == {2: UPDATED}
    assert run4.cursor == Cursor(T_PLUS_1, (2,))


def test_several_items_share_newest_timestamp():
    result = detect_items(
        [gh_issue(7, T_PLUS_1), gh_issue(5, T_PLUS_1), gh_issue(6, T_PLUS_1), gh_issue(1, T)],
        Cursor(T_MINUS_1, ()),
    )
    assert numbers(result) == [7, 5, 6, 1]
    assert result.cursor == Cursor(T_PLUS_1, (5, 6, 7))


def test_cursor_unchanged_when_only_old_items():
    cursor = Cursor(T, ())
    assert detect_items([gh_issue(1, T_MINUS_1)], cursor).cursor is cursor


# --- 7. duplicates -----------------------------------------------------------


def test_same_item_twice_in_one_run_is_reported_once():
    # Page shift: #4 appears at the end of page 1 and the start of page 2.
    items = [gh_issue(5, T_PLUS_1), gh_issue(4, T), gh_issue(4, T), gh_issue(3, T)]
    result = detect_items(items, Cursor(T_MINUS_1, ()))
    assert numbers(result) == [5, 4, 3]


def test_already_reported_item_is_not_reported_again():
    result = detect_items([gh_issue(4, T)], Cursor(T, (4,)))
    assert result.changes == ()


# --- lazy iteration ---------------------------------------------------------


def test_stops_reading_at_first_older_item():
    def items():
        yield gh_pull(2, T_PLUS_1)
        yield gh_pull(1, T_MINUS_1)
        raise AssertionError("next page must not be requested")

    result = detect_items(items(), Cursor(T, ()))
    assert numbers(result) == [2]


def test_unsorted_input_is_rejected():
    with pytest.raises(ValueError, match="not sorted"):
        detect_items([gh_issue(1, T), gh_issue(2, T_PLUS_1)], Cursor(T_MINUS_1, ()))


def test_invalid_timestamp_is_rejected():
    with pytest.raises(ValueError, match="Invalid GitHub timestamp"):
        detect_items([gh_issue(1, "yesterday")], Cursor(T, ()))


@pytest.mark.parametrize(
    "value",
    [
        "2026-1-02T03:04:05Z",
        "2026-01-2T03:04:05Z",
        "2026-01-02T3:04:05Z",
        "2026-01-02T03:4:05Z",
        "2026-01-02T03:04:5Z",
        "2026-01-02T03:04:05.123Z",
        "2026-01-02T03:04:05+00:00",
        "2026-01-02T03:04:05Z\n",
        "2026-02-30T03:04:05Z",
        "２０２６-01-02T03:04:05Z",
        None,
        1793000000,
    ],
)
def test_parse_ts_is_strict(value):
    from organizador.github.changes import parse_ts
    with pytest.raises(ValueError, match="Invalid GitHub timestamp"):
        parse_ts(value)


def test_parse_ts_accepts_exact_format():
    from organizador.github.changes import parse_ts
    assert parse_ts("2026-01-02T03:04:05Z") == datetime(2026, 1, 2, 3, 4, 5)


# --- 8. commits: old dates, new position -----------------------------------


def test_incremental_commits_include_merged_commits_with_old_dates():
    # main: PREV -> M (merge of side commit S, dated in January).
    side = gh_commit("5" * 40, date=OLD, message="Old work on a branch")
    merge = gh_commit(HEAD, date=T, message="Merge branch 'feature'")
    result = commits_from_compare([compare_page("ahead", 2, [side, merge])], PREV, HEAD)

    assert result.status == INCREMENTAL
    assert [c["sha"] for c in result.commits] == [HEAD, "5" * 40]  # newest first
    assert result.previous_head == PREV
    assert result.head_sha == HEAD


def test_incremental_commits_across_pages():
    shas = [f"{i:040x}" for i in range(1, 4)] + [HEAD]
    pages = [
        compare_page("ahead", 4, [gh_commit(s) for s in shas[:2]]),
        compare_page("ahead", 4, [gh_commit(s) for s in shas[2:]]),
    ]
    result = commits_from_compare(pages, PREV, HEAD)
    assert [c["sha"] for c in result.commits] == list(reversed(shas))


def test_incremental_commits_head_not_last_by_date_is_accepted():
    # Clock skew: head commit dated earlier than its parent.
    a = gh_commit(HEAD, date=OLD)
    b = gh_commit("b" * 40, date=T)
    result = commits_from_compare([compare_page("ahead", 2, [a, b])], PREV, HEAD)
    assert {c["sha"] for c in result.commits} == {HEAD, "b" * 40}


@pytest.mark.parametrize(
    "pages, message",
    [
        ([compare_page("ahead", 3, [gh_commit("a" * 40), gh_commit(HEAD)])], "expected 3 commits, received 2"),
        ([compare_page("ahead", 2, [gh_commit(HEAD), gh_commit(HEAD)])], "duplicates"),
        ([compare_page("ahead", 2, [gh_commit(PREV), gh_commit(HEAD)])], "previous head"),
        ([compare_page("ahead", 1, [gh_commit("a" * 40)])], "expected head SHA"),
        ([compare_page("ahead", 0)], "expected head SHA"),
        ([], "no pages"),
        ([compare_page("weird", 0)], "Unexpected comparison status"),
    ],
)
def test_incomplete_or_inconsistent_comparison_is_rejected(pages, message):
    with pytest.raises(ValueError, match=message):
        commits_from_compare(pages, PREV, HEAD)


def test_identical_with_different_head_is_rejected():
    with pytest.raises(ValueError, match="identical"):
        commits_from_compare([compare_page("identical", 0)], PREV, HEAD)


# --- 9. history rewrite ------------------------------------------------------


@pytest.mark.parametrize("status", ["behind", "diverged"])
def test_history_rewrite(status):
    def pages():
        yield compare_page(status, 5, [gh_commit("a" * 40)])
        raise AssertionError("later pages must not be requested")

    result = commits_from_compare(pages(), PREV, HEAD)
    assert result == CommitChanges(HISTORY_REWRITTEN, (), PREV, HEAD)

    changes = Changes(False, result, detect_items([], Cursor(T, ())), detect_items([], Cursor(T, ())))
    assert changes.has_changes is True  # a rewrite is always reported


def test_rewritten_commits_after_confirmed_404():
    assert rewritten_commits(PREV, HEAD) == CommitChanges(HISTORY_REWRITTEN, (), PREV, HEAD)


# --- 10./11. classification and missing fields ------------------------------


@pytest.mark.parametrize(
    "item, expected",
    [
        (gh_issue(1, T_PLUS_1, created_at=T_PLUS_1), OPENED),
        (gh_issue(1, T_PLUS_1, closed_at=T_PLUS_1), CLOSED),
        (gh_issue(1, T_PLUS_1, created_at=T_PLUS_1, closed_at=T_PLUS_1), CLOSED),  # opened and closed
        (gh_issue(1, T_PLUS_1), UPDATED),
        (gh_issue(1, T_PLUS_1, closed_at=OLD), UPDATED),  # comment on an old closed issue
        (gh_pull(1, T_PLUS_1, closed_at=T_PLUS_1, merged_at=T_PLUS_1), MERGED),
        (gh_pull(1, T_PLUS_1, closed_at=T_PLUS_1), CLOSED),  # closed without merge
        (gh_pull(1, T_PLUS_1, created_at=T_PLUS_1), OPENED),
        (gh_pull(1, T_PLUS_1, closed_at=OLD, merged_at=OLD), UPDATED),
    ],
)
def test_classification(item, expected):
    assert classify(item, Cursor(T, ())) == expected


def test_classification_at_cursor_tie_uses_ids():
    # Created exactly at cursor.ts but not seen yet: opened.
    assert classify(gh_issue(8, T, created_at=T), Cursor(T, (1,))) == OPENED


def test_missing_and_null_optional_fields():
    minimal = {"number": 1, "updated_at": T_PLUS_1}  # no created_at, closed_at, merged_at
    assert classify(minimal, Cursor(T, ())) == UPDATED
    null_fields = {"number": 2, "updated_at": T_PLUS_1, "created_at": None, "closed_at": None, "merged_at": None}
    result = detect_items([null_fields, minimal], Cursor(T, ()))
    assert activities(result) == {2: UPDATED, 1: UPDATED}


def test_after_cursor_rules():
    cursor = Cursor(T, (1,))
    assert after_cursor(T_PLUS_1, 1, cursor)
    assert after_cursor(T, 2, cursor)
    assert not after_cursor(T, 1, cursor)
    assert not after_cursor(T_MINUS_1, 2, cursor)
    assert not after_cursor(None, 2, cursor)


# --- 12. later run more than 15 days after the last one --------------------


def test_run_40_days_later_uses_saved_cursor_not_window():
    saved = Cursor("2026-09-15T12:00:00Z", (3,))
    items = [
        gh_issue(9, T, created_at="2026-09-20T00:00:00Z"),   # 35 days after cursor
        gh_issue(8, "2026-09-16T00:00:00Z"),                 # 39 days before T: still included
        gh_issue(3, "2026-09-15T12:00:00Z"),                 # already reported
    ]
    result = detect_items(items, saved)
    assert activities(result) == {9: OPENED, 8: UPDATED}
    assert issues_query_since(saved) == "2026-09-15T11:59:59Z"
