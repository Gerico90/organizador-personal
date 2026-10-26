import copy
import json

import pytest

from github_fixtures import HEAD, PREV, REPO, commit, incremental_changes, issue, pull
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
    ItemChange,
    ItemChanges,
)
from organizador.github.report import render_report
from organizador.github.state import Cursor

FROM = "2026-10-18T18:30:11Z"
TO = "2026-10-25T18:30:11Z"
EMPTY = ItemChanges((), Cursor(FROM, ()))


def render(changes, branch="main", period_from=FROM, repo=REPO):
    return render_report(
        changes, repo=repo, branch=branch, period_from=period_from, period_to=TO, generated_at=TO
    )


def frontmatter(text):
    """Parse the frontmatter. Every value must be a JSON scalar (valid YAML)."""
    lines = text.split("\n")
    assert lines[0] == "---"
    end = lines.index("---", 1)
    fields = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(": ")
        assert sep, line
        assert key not in fields
        fields[key] = json.loads(value)
    return fields, "\n".join(lines[end + 1:])


def one_item_changes(item_change=None, commits=()):
    pulls = EMPTY
    issues = ItemChanges((item_change,), Cursor(FROM, ())) if item_change else EMPTY
    return Changes(False, CommitChanges(INCREMENTAL, tuple(commits), PREV, HEAD), issues, pulls)


# --- exact output -----------------------------------------------------------

EXPECTED_INCREMENTAL = """\
---
source: "github"
repo: "octo-cat/my.repo"
branch: "main"
period_from: "2026-10-18T18:30:11Z"
period_to: "2026-10-25T18:30:11Z"
generated_at: "2026-10-25T18:30:11Z"
generated_by: "organizador"
baseline: false
commits_status: "incremental"
previous_head: "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d"
head_sha: "e4f5a6b7c8d9e0f1a2b3c4d5e6f708192a3b4c5d"
---

# octo-cat/my.repo — activity 2026-10-18 → 2026-10-25

Period: 2026-10-18 18:30 UTC → 2026-10-25 18:30 UTC

2 commits · 2 pull requests (1 merged, 1 opened) · 3 issues (1 opened, 1 closed, 1 updated)

## Commits on main

- [`e4f5a6b`](https://github.com/octo-cat/my.repo/commit/e4f5a6b7c8d9e0f1a2b3c4d5e6f708192a3b4c5d) Merge branch 'feature/export' — @alice, 2026-10-24
- [`5b6c7d8`](https://github.com/octo-cat/my.repo/commit/5b6c7d8e9f0a1b2c3d4e5f60718293a4b5c6d7e8) Add CSV export — Bob Builder, 2026-01-05

## Pull requests

- **merged** [#42](https://github.com/octo-cat/my.repo/pull/42) Add export — @bob, 2026-10-24 09:15 UTC
- **opened** [#43](https://github.com/octo-cat/my.repo/pull/43) Draft: faster parser — @carol, 2026-10-23 16:00 UTC

## Issues

- **opened** [#57](https://github.com/octo-cat/my.repo/issues/57) Crash on empty input — @carol, 2026-10-22 11:00 UTC
- **closed** [#51](https://github.com/octo-cat/my.repo/issues/51) Typo in README — @dave, 2026-10-21 08:00 UTC
- **updated** [#12](https://github.com/octo-cat/my.repo/issues/12) Support Windows paths — @erin, 2026-10-19 20:45 UTC
"""


def test_incremental_report_exact_output():
    assert render(incremental_changes()) == EXPECTED_INCREMENTAL


EXPECTED_EMPTY_BASELINE = """\
---
source: "github"
repo: "octo-cat/my.repo"
branch: "main"
period_from: "2026-10-10T18:30:11Z"
period_to: "2026-10-25T18:30:11Z"
generated_at: "2026-10-25T18:30:11Z"
generated_by: "organizador"
baseline: true
commits_status: "baseline"
previous_head: null
head_sha: "e4f5a6b7c8d9e0f1a2b3c4d5e6f708192a3b4c5d"
---

# octo-cat/my.repo — initial baseline 2026-10-10 → 2026-10-25

Period: 2026-10-10 18:30 UTC → 2026-10-25 18:30 UTC

> **Initial baseline.** This is the first report for this repository. It lists activity in the period above, not changes since a previous report.

No activity in the last 15 days.

0 commits · 0 pull requests · 0 issues

## Commits on main

_No commits._

## Pull requests

_No pull request activity._

## Issues

_No issue activity._
"""


def test_baseline_without_activity_exact_output():
    start = "2026-10-10T18:30:11Z"
    empty = ItemChanges((), Cursor(start, ()))
    changes = Changes(True, CommitChanges(BASELINE, (), None, HEAD), empty, empty)
    assert render(changes, period_from=start) == EXPECTED_EMPTY_BASELINE


# --- baseline / incremental distinctions -------------------------------------


def test_baseline_with_activity():
    inc = incremental_changes()
    changes = Changes(True, CommitChanges(BASELINE, inc.commits.commits, None, HEAD), inc.issues, inc.pulls)
    text = render(changes)
    fields, body = frontmatter(text)
    assert fields["baseline"] is True
    assert fields["commits_status"] == "baseline"
    assert fields["previous_head"] is None
    assert "initial baseline 2026-10-18" in body
    assert "**Initial baseline.**" in body
    assert "No activity" not in body
    assert "2 commits · 2 pull requests (1 merged, 1 opened)" in body


def test_incremental_report_has_no_baseline_note():
    body = render(incremental_changes())
    assert "Initial baseline" not in body
    assert "— activity 2026-10-18 → 2026-10-25" in body


def test_incremental_without_activity_says_so():
    text = render(one_item_changes())
    assert "No activity since the previous report." in text
    assert "0 commits · 0 pull requests · 0 issues" in text


# --- counts and labels -------------------------------------------------------


def test_activity_labels_and_counts():
    changes = Changes(
        False,
        CommitChanges(INCREMENTAL, (commit(HEAD, "One"),), PREV, HEAD),
        ItemChanges(
            tuple(ItemChange(a, issue(n, f"I{n}", "x", FROM, TO)) for n, a in
                  [(1, OPENED), (2, OPENED), (3, CLOSED), (4, UPDATED)]),
            Cursor(TO, ()),
        ),
        ItemChanges((ItemChange(MERGED, pull(9, "P", "x", FROM, TO, TO, TO)),), Cursor(TO, ())),
    )
    text = render(changes)
    assert "1 commit · 1 pull request (1 merged) · 4 issues (2 opened, 1 closed, 1 updated)" in text
    assert text.count("**opened**") == 2
    assert text.count("**closed**") == 1
    assert text.count("**updated**") == 1
    assert text.count("**merged**") == 1


def test_item_timestamp_matches_activity():
    pr = pull(5, "T", "x", "2026-10-01T01:00:00Z", "2026-10-20T03:00:00Z",
              closed_at="2026-10-19T02:00:00Z", merged_at="2026-10-19T02:00:00Z")
    for activity, expected in [(MERGED, "2026-10-19 02:00"), (CLOSED, "2026-10-19 02:00"),
                               (OPENED, "2026-10-01 01:00"), (UPDATED, "2026-10-20 03:00")]:
        assert f"@x, {expected} UTC" in render(one_item_changes(ItemChange(activity, pr)))


# --- history rewrite ----------------------------------------------------------


def test_history_rewrite_report():
    inc = incremental_changes()
    changes = Changes(False, CommitChanges(HISTORY_REWRITTEN, (), PREV, HEAD), inc.issues, inc.pulls)
    text = render(changes)
    fields, body = frontmatter(text)

    assert fields["commits_status"] == "history_rewritten"
    assert fields["previous_head"] == PREV
    assert fields["head_sha"] == HEAD
    assert "**Warning: history rewritten.**" in body
    assert f"[`1a2b3c4`](https://github.com/{REPO}/commit/{PREV})" in body
    assert f"[`e4f5a6b`](https://github.com/{REPO}/commit/{HEAD})" in body
    assert "_Commit comparison unavailable (history rewritten)._" in body
    assert "commits: comparison unavailable · 2 pull requests" in body
    assert "/commit/5b6c7d8" not in body  # no fabricated commit list
    assert "**merged** [#42]" in body and "**opened** [#57]" in body
    assert "No activity" not in body


# --- missing optional fields -------------------------------------------------


def test_missing_optional_fields_are_omitted_not_invented():
    bare_commit = {"sha": HEAD}
    bare_issue = {"number": 7, "updated_at": TO}
    text = render(one_item_changes(ItemChange(UPDATED, bare_issue), [bare_commit]))
    assert "- `e4f5a6b`\n" in text  # no link, message, author, or date
    assert "- **updated** #7 — 2026-10-25 18:30 UTC\n" in text


def test_null_author_and_bad_dates():
    c = commit(HEAD, "Msg", login=None, name=None, date=None)
    i = issue(7, None, None, None, "not-a-date")
    text = render(one_item_changes(ItemChange(OPENED, i), [c]))
    assert f"/commit/{HEAD}) Msg\n" in text
    assert "/issues/7)\n" in text


def test_commit_message_uses_first_non_blank_line():
    text = render(one_item_changes(commits=[commit(HEAD, "\n\n  Real subject  \nBody line")]))
    assert ") Real subject — @alice" in text
    assert "Body line" not in text


# --- links -------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "http://github.com/octo-cat/my.repo/issues/1",
        "https://evil.example.com/x",
        "https://github.com.evil.com/x",
        "https://github.com/x) [click](https://evil.example.com",
        "https://github.com/x\n# Injected",
        None,
        42,
    ],
)
def test_untrusted_urls_are_not_linked(url):
    i = issue(1, "T", "x", FROM, TO)
    i["html_url"] = url
    text = render(one_item_changes(ItemChange(OPENED, i)))
    assert "- **opened** #1 T — @x" in text
    assert "evil" not in text
    assert "Injected" not in text


def test_valid_links_are_well_formed():
    import re
    body = frontmatter(render(incremental_changes()))[1]
    links = re.findall(r"\]\(([^)]*)\)", body)
    assert len(links) == 7
    assert all(re.fullmatch(r"https://github\.com/octo-cat/my\.repo/(commit|pull|issues)/[0-9a-f]+|https://github\.com/octo-cat/my\.repo/(pull|issues)/\d+", u) for u in links)


# --- untrusted text ---------------------------------------------------------


def test_multiline_and_markdown_injection_in_titles():
    title = "Real title\n\n## Injected heading\n---\nfoo: bar\n[x](https://evil.example.com) <script>"
    text = render(one_item_changes(ItemChange(OPENED, issue(1, title, "x", FROM, TO))))
    fields, body = frontmatter(text)
    assert set(fields) == set(frontmatter(render(incremental_changes()))[0])
    assert "\n## Injected" not in body
    assert "\n---" not in body
    lines = [l for l in body.split("\n") if l.startswith("- **opened**")]
    assert len(lines) == 1
    assert "\\#\\# Injected heading" in lines[0]
    assert "\\[x\\]\\(https://evil.example.com\\)" in lines[0]
    assert "\\<script\\>" in lines[0]
    assert "<script>" not in body


@pytest.mark.parametrize(
    "raw, escaped",
    [
        ("*bold* _it_ `code`", "\\*bold\\* \\_it\\_ \\`code\\`"),
        ("[[Wiki link]] ![[embed]]", "\\[\\[Wiki link\\]\\] \\!\\[\\[embed\\]\\]"),
        ("a | b ~~s~~ ==hl== %%c%%", "a \\| b \\~\\~s\\~\\~ \\=\\=hl\\=\\= \\%\\%c\\%\\%"),
        ("#tag $x$ ^block &amp; \\", "\\#tag \\$x\\$ \\^block \\&amp; \\\\"),
        ("tab\tand\x00null\x1b[31m", "tab andnull\\[31m"),  # control chars removed
        ("line sep para\rcr", "line sep para cr"),
        ("rtl‮override", "rtloverride"),
        ("ñandú 🚀 ✓", "ñandú 🚀 ✓"),
    ],
)
def test_title_escaping(raw, escaped):
    text = render(one_item_changes(ItemChange(OPENED, issue(1, raw, "x", FROM, TO))))
    assert f"/issues/1) {escaped} — @x" in text


def test_login_branch_and_author_name_are_escaped():
    c = commit(HEAD, "M", login=None, name="Eve *\n# hdr")
    i = issue(1, "T", "dependabot[bot]", FROM, TO)
    text = render(one_item_changes(ItemChange(OPENED, i), [c]), branch="feat/`x`*")
    assert "— Eve \\* \\# hdr," in text
    assert "@dependabot\\[bot\\]" in text
    assert "## Commits on feat/\\`x\\`\\*" in text
    fields, _ = frontmatter(text)
    assert fields["branch"] == "feat/`x`*"


def test_frontmatter_values_cannot_break_out():
    branch = 'main"\n---\nevil: true\n'
    text = render(one_item_changes(), branch=branch)
    fields, body = frontmatter(text)
    assert fields["branch"] == branch
    assert "evil" not in fields
    assert text.split("\n").count("---") == 2
    assert "\nevil: true" not in body


def test_branch_newline_in_rewrite_warning_stays_on_one_line():
    changes = Changes(False, CommitChanges(HISTORY_REWRITTEN, (), PREV, HEAD), EMPTY, EMPTY)
    body = frontmatter(render(changes, branch="main\n# Injected"))[1]
    assert "\n# Injected" not in body
    assert body.count("main \\# Injected") == 2  # warning and section heading


def test_non_hex_sha_is_escaped_not_code_span():
    text = render(one_item_changes(commits=[{"sha": "abc`]) <x>"}]))
    assert "- abc\\`\\]\\) \\<x\\>\n" in text


# --- input validation ----------------------------------------------------------


@pytest.mark.parametrize("repo", ["octo-cat", "a/b/c", "a/b\n# x", ""])
def test_invalid_repo_is_rejected(repo):
    with pytest.raises(ValueError):
        render(one_item_changes(), repo=repo)


def test_invalid_period_is_rejected():
    with pytest.raises(ValueError):
        render(one_item_changes(), period_from="last week")


# --- purity --------------------------------------------------------------------


def test_deterministic():
    assert render(incremental_changes()) == render(incremental_changes())


def test_inputs_not_mutated():
    changes = incremental_changes()
    snapshot = copy.deepcopy(changes)
    render(changes)
    assert changes == snapshot


def test_output_uses_lf_and_ends_with_single_newline():
    text = render(incremental_changes())
    assert "\r" not in text
    assert text.endswith("\n") and not text.endswith("\n\n")
