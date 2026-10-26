"""End-to-end tests for `python -m organizador github run`, offline.

FakeGitHub serves the real REST URL shapes from in-memory data, with
Link-header pagination, so the real client, change detection, renderer,
file utilities and state module all run unchanged.
"""

import hashlib
import io
import json
import math
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from organizador import cli
from organizador.github import client as client_module

OWNER, REPO = "octo-cat", "my.repo"
SLUG = f"{OWNER}/{REPO}"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"
NOW = datetime(2026, 10, 25, 18, 30, 11, tzinfo=timezone.utc)
NOW_TS = "2026-10-25T18:30:11Z"
WINDOW = "2026-10-10T18:30:11Z"  # NOW - 15 days
TOKEN = "ghp_SECRETtoken1234567890"

CONFIG = f"""\
vault_path = "vault"
state_path = "state"

[github]
owner = "{OWNER}"
repo = "{REPO}"
"""


def sha(n: int) -> str:
    return f"{n:040x}"


def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def at(days_before_now: float = 0, seconds: int = 0) -> str:
    return ts(NOW - timedelta(days=days_before_now) + timedelta(seconds=seconds))


# --- fake GitHub ---------------------------------------------------------------


class FakeGitHub:
    def __init__(self):
        self.default_branch = "main"
        self.head = sha(1)
        self.commits = {}       # sha -> commit dict
        self.history = []       # shas reachable from head, newest first
        self.issues = {}        # number -> issue dict
        self.pulls = {}         # number -> pull dict
        self.compare = {}       # (base, head) -> ("ahead", [shas oldest first]) | (status,) | int
        self.fail = {}          # (path, page) -> HTTP status
        self.calls = []
        self.add_commit(sha(1), at(30), "Initial commit")

    # data helpers
    def add_commit(self, s, date, message):
        self.commits[s] = {
            "sha": s,
            "html_url": f"https://github.com/{SLUG}/commit/{s}",
            "commit": {"message": message, "author": {"name": "Alice", "date": date},
                       "committer": {"name": "Alice", "date": date}},
            "author": {"login": "alice"},
            "parents": [],
        }
        self.history.insert(0, s)
        self.head = s

    def push(self, s, date, message):
        previous = self.head
        self.add_commit(s, date, message)
        self.compare[(previous, s)] = ("ahead", [s])

    def add_issue(self, number, updated_at, created_at=None, closed_at=None, title=None):
        self.issues[number] = {
            "number": number, "title": title or f"Issue {number}",
            "html_url": f"https://github.com/{SLUG}/issues/{number}",
            "user": {"login": "carol"}, "state": "closed" if closed_at else "open",
            "created_at": created_at or updated_at, "updated_at": updated_at, "closed_at": closed_at,
        }

    def add_pull(self, number, updated_at, created_at=None, closed_at=None, merged_at=None):
        self.pulls[number] = {
            "number": number, "title": f"PR {number}",
            "html_url": f"https://github.com/{SLUG}/pull/{number}",
            "user": {"login": "bob"}, "state": "closed" if closed_at else "open",
            "created_at": created_at or updated_at, "updated_at": updated_at,
            "closed_at": closed_at, "merged_at": merged_at,
        }

    # HTTP
    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        parts = urlsplit(url)
        assert parts.scheme == "https" and parts.netloc == "api.github.com"
        path = parts.path
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        page = int(q.get("page", 1))
        if (path, page) in self.fail:
            return self._error(self.fail[(path, page)])
        base = f"/repos/{OWNER}/{REPO}"

        if path == base:
            return self._ok({"default_branch": self.default_branch})
        if path.startswith(base + "/branches/"):
            name = path.rsplit("/", 1)[1]
            if name != self.default_branch or not self.history:
                return self._error(404)
            return self._ok({"name": name, "commit": {"sha": self.head}})
        if path.startswith(base + "/commits/"):
            s = path.rsplit("/", 1)[1]
            return self._ok(self.commits[s]) if s in self.commits else self._error(422, "No commit found for SHA")
        if path == base + "/commits":
            if not self.history:
                return self._error(409, "Git Repository is empty.")
            start = self.history.index(q["sha"])
            items = [self.commits[s] for s in self.history[start:]
                     if "since" not in q or self.commits[s]["commit"]["committer"]["date"] >= q["since"]]
            return self._page(url, items, q, page)
        if path.startswith(base + "/compare/"):
            b, h = path.rsplit("/", 1)[1].split("...")
            result = self.compare.get((b, h), 404)
            if isinstance(result, int):
                return self._error(result)
            status = result[0]
            shas = result[1] if len(result) > 1 else []
            commits = [self.commits[s] for s in shas]
            per = int(q["per_page"])
            body = {"status": status, "ahead_by": len(shas), "behind_by": 0, "total_commits": len(commits),
                    "commits": commits[(page - 1) * per: page * per]}
            return self._with_link(url, body, len(commits), q, page)
        if path == base + "/issues":
            # Real API: issues and PRs together, since is inclusive.
            items = list(self.issues.values()) + [dict(p, pull_request={"url": "x"}) for p in self.pulls.values()]
            items = [i for i in items if "since" not in q or i["updated_at"] >= q["since"]]
            return self._page(url, self._sorted(items), q, page)
        if path == base + "/pulls":
            return self._page(url, self._sorted(list(self.pulls.values())), q, page)
        raise AssertionError(f"unexpected URL {url}")

    @staticmethod
    def _sorted(items):
        return sorted(items, key=lambda i: (i["updated_at"], i["number"]), reverse=True)

    def _page(self, url, items, q, page):
        per = int(q["per_page"])
        return self._with_link(url, items[(page - 1) * per: page * per], len(items), q, page)

    @staticmethod
    def _with_link(url, body, total, q, page):
        per = int(q["per_page"])
        headers = {}
        if page < math.ceil(total / per):
            next_q = dict(q, page=page + 1)
            headers["Link"] = f'<{url.split("?")[0]}?{urlencode(next_q)}>; rel="next"'
        return 200, headers, json.dumps(body).encode()

    @staticmethod
    def _ok(body):
        return 200, {}, json.dumps(body).encode()

    @staticmethod
    def _error(status, message="Error"):
        return status, {}, json.dumps({"message": message}).encode()

    def requested(self, path_suffix):
        return [u for u, _ in self.calls if urlsplit(u).path == f"/repos/{OWNER}/{REPO}{path_suffix}"]


# --- project and run helpers -----------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.toml").write_text(CONFIG, encoding="utf-8")
    notes = tmp_path / "vault" / "notes"
    notes.mkdir(parents=True)
    (notes / "ideas.md").write_text("# My ideas\n\nHand-written.\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def gh():
    return FakeGitHub()


class Result:
    def __init__(self, code, out, err):
        self.code, self.out, self.err = code, out, err


def run(project, gh, now=NOW):
    out, err = io.StringIO(), io.StringIO()
    code = cli.run_github(project_root=project, fetch=gh, now=now, out=out, err=err)
    return Result(code, out.getvalue(), err.getvalue())


def state_path(project):
    return project / "state" / "github" / f"{OWNER}__{REPO}.json"


def read_state(project):
    return json.loads(state_path(project).read_text(encoding="utf-8"))


def reports_dir(project):
    return project / "vault" / "reports" / "github" / f"{OWNER}__{REPO}"


def reports(project):
    d = reports_dir(project)
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def frontmatter(path):
    lines = path.read_text(encoding="utf-8").split("\n")
    end = lines.index("---", 1)
    return {k: json.loads(v) for k, _, v in (l.partition(": ") for l in lines[1:end])}


def tree_hash(directory):
    h = hashlib.sha256()
    for p in sorted(directory.rglob("*")):
        h.update(str(p.relative_to(directory)).encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def assert_consistent(project):
    """Saved state and the report it points to describe the same run."""
    state = read_state(project)
    report = project / "vault" / state["last_report"]
    assert report.is_file()
    fm = frontmatter(report)
    assert fm["head_sha"] == state["last_head_sha"]
    assert fm["generated_at"] == state["last_success_at"] == fm["period_to"]
    assert fm["branch"] == state["default_branch"]
    assert fm["repo"] == state["repo"] == SLUG
    return state, fm, report.read_text(encoding="utf-8")


def later(days=0, hours=0, seconds=0):
    return NOW + timedelta(days=days, hours=hours, seconds=seconds)


# --- 1./2. first run ---------------------------------------------------------------


def test_first_run_with_activity(project, gh):
    gh.add_commit(sha(2), at(3), "Add parser")              # in window
    gh.add_issue(5, at(2), created_at=at(2))                # opened in window
    gh.add_issue(4, at(20))                                 # before window
    gh.add_pull(7, at(1), created_at=at(10), closed_at=at(1), merged_at=at(1))

    r = run(project, gh)

    assert r.code == 0, r.err
    assert "Baseline report written:" in r.out
    assert "1 commit, 1 pull request, 1 issue" in r.out
    assert reports(project) == ["2026-10-25T183011Z.md"]
    state, fm, text = assert_consistent(project)
    assert fm["baseline"] is True and fm["commits_status"] == "baseline"
    assert fm["period_from"] == WINDOW
    assert state["last_head_sha"] == sha(2)
    assert state["issues_cursor"] == {"ts": at(2), "ids": [5]}
    assert state["pulls_cursor"] == {"ts": at(1), "ids": [7]}
    assert "Add parser" in text and "**opened** [#5]" in text and "**merged** [#7]" in text
    assert "Issue 4" not in text and "Initial commit" not in text
    # Baseline commits are listed from the head SHA, not the branch name.
    (commits_url,) = gh.requested("/commits")
    assert parse_qs(urlsplit(commits_url).query)["sha"] == [sha(2)]
    assert parse_qs(urlsplit(commits_url).query)["since"] == [WINDOW]


def test_first_run_without_activity_still_writes_baseline(project, gh):
    r = run(project, gh)
    assert r.code == 0, r.err
    state, fm, text = assert_consistent(project)
    assert "No activity in the last 15 days." in text
    assert state["issues_cursor"] == state["pulls_cursor"] == {"ts": WINDOW, "ids": []}
    assert state["last_head_sha"] == sha(1)


# --- 3. no changes ------------------------------------------------------------------


def test_second_run_without_changes(project, gh):
    assert run(project, gh).code == 0
    before = state_path(project).read_bytes()

    r = run(project, gh, now=later(hours=1))

    assert r.code == 0
    assert r.out.strip() == f"No changes since {NOW_TS}."
    assert reports(project) == ["2026-10-25T183011Z.md"]
    assert state_path(project).read_bytes() == before
    assert gh.requested(f"/compare/{sha(1)}...{sha(1)}") == []  # same head: no compare call


# --- 4. new activity ----------------------------------------------------------------


def test_new_commits_issues_and_pulls(project, gh):
    gh.add_issue(1, at(5))
    assert run(project, gh).code == 0

    gh.push(sha(2), ts(later(hours=1)), "Fix bug")
    gh.push(sha(3), ts(later(hours=2)), "Add feature")
    gh.compare[(sha(1), sha(3))] = ("ahead", [sha(2), sha(3)])
    gh.add_issue(2, ts(later(hours=3)), created_at=ts(later(hours=3)))
    gh.add_issue(1, ts(later(hours=4)), created_at=at(5), closed_at=ts(later(hours=4)))
    gh.add_pull(9, ts(later(hours=5)), created_at=ts(later(hours=1)), closed_at=ts(later(hours=5)),
                merged_at=ts(later(hours=5)))

    r = run(project, gh, now=later(days=1))

    assert r.code == 0, r.err
    assert "Report written:" in r.out and "2 commits, 1 pull request, 2 issues" in r.out
    state, fm, text = assert_consistent(project)
    assert fm["baseline"] is False and fm["commits_status"] == "incremental"
    assert fm["period_from"] == NOW_TS and fm["previous_head"] == sha(1)
    assert state["last_head_sha"] == sha(3)
    assert state["issues_cursor"] == {"ts": ts(later(hours=4)), "ids": [1]}
    assert state["pulls_cursor"] == {"ts": ts(later(hours=5)), "ids": [9]}
    assert text.index("Add feature") < text.index("Fix bug")  # newest first
    assert "**closed** [#1]" in text and "**opened** [#2]" in text and "**merged** [#9]" in text
    assert len(reports(project)) == 2


# --- 5. ties ----------------------------------------------------------------------


def test_timestamp_ties_across_executions(project, gh):
    t = at(1)
    gh.add_issue(1, t)
    gh.add_issue(2, t)
    assert run(project, gh).code == 0
    assert read_state(project)["issues_cursor"] == {"ts": t, "ids": [1, 2]}

    gh.add_issue(3, t)  # arrives later with the same timestamp
    r = run(project, gh, now=later(hours=1))
    assert r.code == 0
    _, _, text = assert_consistent(project)
    assert "[#3]" in text and "[#1]" not in text and "[#2]" not in text
    assert read_state(project)["issues_cursor"] == {"ts": t, "ids": [1, 2, 3]}
    # Issues were requested with the one-second overlap.
    since = parse_qs(urlsplit(gh.requested("/issues")[-1]).query)["since"][0]
    assert since == at(1, seconds=-1)

    r = run(project, gh, now=later(hours=2))
    assert r.out.strip() == f"No changes since {ts(later(hours=1))}."
    assert len(reports(project)) == 2


# --- 6. long interval -------------------------------------------------------------


def test_long_interval_uses_saved_checkpoint(project, gh):
    assert run(project, gh).code == 0
    gh.add_issue(8, ts(later(days=5)))   # 35 days before the next run, outside any 15-day window
    gh.add_issue(9, ts(later(days=39)))

    r = run(project, gh, now=later(days=40))

    assert r.code == 0, r.err
    _, fm, text = assert_consistent(project)
    assert fm["period_from"] == NOW_TS
    assert "[#8]" in text and "[#9]" in text
    since = parse_qs(urlsplit(gh.requested("/issues")[-1]).query)["since"][0]
    assert since == WINDOW.replace("18:30:11", "18:30:10")  # cursor (window start) - 1s


# --- 7./8. history rewrite ------------------------------------------------------


def force_push(gh, new_sha):
    old = gh.head
    gh.history = [s for s in gh.history if s != old]
    gh.commits.pop(old)
    gh.add_commit(new_sha, ts(later(hours=1)), "Rewritten")
    return old


def test_confirmed_rewrite_after_404(project, gh):
    gh.add_commit(sha(2), at(1), "Will be rewritten")
    assert run(project, gh).code == 0
    old = force_push(gh, sha(3))       # compare -> 404, branch 200, commit -> 422
    gh.add_issue(4, ts(later(hours=2)))

    r = run(project, gh, now=later(days=1))

    assert r.code == 2, r.err
    assert "Warning: the history of 'main' was rewritten" in r.err
    assert old[:7] in r.err and sha(3)[:7] in r.err
    state, fm, text = assert_consistent(project)
    assert fm["commits_status"] == "history_rewritten"
    assert fm["previous_head"] == old and state["last_head_sha"] == sha(3)
    assert "**Warning: history rewritten.**" in text
    assert "Rewritten" not in text            # no invented commit list
    assert "[#4]" in text                     # issues still reported
    assert gh.requested(f"/branches/main") and gh.requested(f"/commits/{old}")

    # Next run is incremental again.
    gh.push(sha(4), ts(later(days=1, hours=1)), "After rewrite")
    r = run(project, gh, now=later(days=2))
    assert r.code == 0
    assert assert_consistent(project)[1]["commits_status"] == "incremental"


@pytest.mark.parametrize("status", ["diverged", "behind"])
def test_rewrite_from_compare_status(project, gh, status):
    assert run(project, gh).code == 0
    old = gh.head
    gh.add_commit(sha(5), ts(later(hours=1)), "Other line")
    gh.compare[(old, sha(5))] = (status,)
    r = run(project, gh, now=later(days=1))
    assert r.code == 2
    assert read_state(project)["last_head_sha"] == sha(5)


@pytest.mark.parametrize(
    "setup, message",
    [
        (lambda gh, old: None, "still exists"),                              # commit lookup 200
        (lambda gh, old: gh.fail.update({(f"/repos/{OWNER}/{REPO}/commits/{old}", 1): 500}), "HTTP 500"),
        (lambda gh, old: gh.fail.update({(f"/repos/{OWNER}/{REPO}/branches/main", 1): 404}), "HTTP 404"),
    ],
)
def test_uncertain_compare_404_does_not_advance(project, gh, setup, message):
    assert run(project, gh).code == 0
    before = state_path(project).read_bytes()
    old = gh.head
    gh.add_commit(sha(6), ts(later(hours=1)), "New")   # compare not configured -> 404
    # The first branch lookup (current head) must succeed; only the confirmation check may fail.
    original_call = gh.__class__.__call__
    seen = {"branch": 0}

    def call(url, headers):
        if urlsplit(url).path.endswith("/branches/main"):
            seen["branch"] += 1
            if seen["branch"] == 1:
                return 200, {}, json.dumps({"commit": {"sha": gh.head}}).encode()
        return original_call(gh, url, headers)

    setup(gh, old)
    out, err = io.StringIO(), io.StringIO()
    code = cli.run_github(project_root=project, fetch=call, now=later(days=1), out=out, err=err)

    assert code == 1
    assert message in err.getvalue()
    assert "checkpoint was not changed" in err.getvalue()
    assert state_path(project).read_bytes() == before
    assert len(reports(project)) == 1


def test_compare_server_error_does_not_advance(project, gh):
    assert run(project, gh).code == 0
    before = state_path(project).read_bytes()
    old = gh.head
    gh.add_commit(sha(7), ts(later(hours=1)), "New")
    gh.compare[(old, sha(7))] = 502
    r = run(project, gh, now=later(days=1))
    assert r.code == 1 and "HTTP 502" in r.err
    assert state_path(project).read_bytes() == before
    assert len(reports(project)) == 1


# --- 9. failure on a later page ---------------------------------------------------


@pytest.mark.parametrize("path", ["/issues", "/pulls", "/commits"])
def test_failure_on_later_page_writes_nothing(project, gh, monkeypatch, path):
    monkeypatch.setattr(client_module, "PER_PAGE", 1)
    for n in range(1, 4):
        gh.add_issue(n, at(n))
        gh.add_pull(10 + n, at(n))
        gh.add_commit(sha(100 + n), at(n), f"c{n}")
    gh.fail[(f"/repos/{OWNER}/{REPO}{path}", 2)] = 502

    r = run(project, gh)

    assert r.code == 1
    assert "HTTP 502" in r.err and "Nothing was written" in r.err
    assert not state_path(project).exists()
    assert reports(project) == []


def test_pagination_without_hidden_limits(project, gh, monkeypatch):
    monkeypatch.setattr(client_module, "PER_PAGE", 2)
    for n in range(1, 8):
        gh.add_issue(n, at(n / 10))
    assert run(project, gh).code == 0
    assert read_state(project)["issues_cursor"]["ids"] == [1]
    assert len(gh.requested("/issues")) == 4  # 7 issues + 0 PRs in pages of 2


def test_compare_across_pages(project, gh, monkeypatch):
    assert run(project, gh).code == 0
    monkeypatch.setattr(client_module, "PER_PAGE", 2)
    start = gh.head
    new = [sha(200 + i) for i in range(5)]
    for i, s in enumerate(new):
        gh.add_commit(s, ts(later(hours=i + 1)), f"m{i}")
    gh.compare[(start, new[-1])] = ("ahead", new)
    r = run(project, gh, now=later(days=1))
    assert r.code == 0 and "5 commits" in r.out
    assert len(gh.requested(f"/compare/{start}...{new[-1]}")) == 3


# --- 10. filename collision -------------------------------------------------------


def test_report_name_collision(project, gh):
    reports_dir(project).mkdir(parents=True)
    existing = reports_dir(project) / "2026-10-25T183011Z.md"
    existing.write_text("human edits\n", encoding="utf-8")

    r = run(project, gh)

    assert r.code == 0
    assert reports(project) == ["2026-10-25T183011Z-2.md", "2026-10-25T183011Z.md"]
    assert existing.read_text(encoding="utf-8") == "human edits\n"
    assert read_state(project)["last_report"].endswith("/2026-10-25T183011Z-2.md")
    assert_consistent(project)


def test_two_runs_in_the_same_second(project, gh):
    assert run(project, gh).code == 0
    gh.add_issue(1, NOW_TS)
    r = run(project, gh, now=NOW)  # same second as the first run
    assert r.code == 0
    assert reports(project) == ["2026-10-25T183011Z-2.md", "2026-10-25T183011Z.md"]


# --- 11. state save failure ---------------------------------------------------------


def test_state_save_failure_keeps_report_and_old_state(project, gh, monkeypatch):
    assert run(project, gh).code == 0
    before = state_path(project).read_bytes()
    gh.add_issue(1, ts(later(hours=1)))

    def fail(src, dst):
        raise PermissionError(13, "simulated", str(dst))

    monkeypatch.setattr(os, "replace", fail)
    r = run(project, gh, now=later(days=1))
    monkeypatch.undo()

    assert r.code == 1
    assert "the report was written to" in r.err and "next run will report this period again" in r.err
    assert state_path(project).read_bytes() == before
    first_retry_report = reports_dir(project) / "2026-10-26T183011Z.md"
    first_bytes = first_retry_report.read_bytes()

    # Retry: a second, differently named report for the same period; the first is untouched.
    r = run(project, gh, now=later(days=1, seconds=5))
    assert r.code == 0
    assert len(reports(project)) == 3
    assert first_retry_report.read_bytes() == first_bytes
    _, fm, text = assert_consistent(project)
    assert fm["period_from"] == NOW_TS and "[#1]" in text


def test_report_creation_failure_leaves_state(project, gh, monkeypatch):
    assert run(project, gh).code == 0
    before = state_path(project).read_bytes()
    gh.add_issue(1, ts(later(hours=1)))

    def fail(*a, **k):
        raise OSError(28, "No space left on device (simulated)")

    monkeypatch.setattr(cli, "create_exclusive", fail)
    r = run(project, gh, now=later(days=1))
    assert r.code == 1 and "Could not create the report" in r.err
    assert state_path(project).read_bytes() == before


# --- 12. configuration and state errors -------------------------------------------


def test_missing_config(project, gh):
    (project / "config" / "config.toml").unlink()
    r = run(project, gh)
    assert r.code == 1 and "Config file not found" in r.err
    assert gh.calls == []


def test_invalid_config(project, gh):
    (project / "config" / "config.toml").write_text(CONFIG.replace(OWNER, "OWNER"), encoding="utf-8")
    r = run(project, gh)
    assert r.code == 1 and "placeholder" in r.err
    assert gh.calls == []


def test_corrupt_state_is_not_reset(project, gh):
    state_path(project).parent.mkdir(parents=True)
    state_path(project).write_text("{broken", encoding="utf-8")
    r = run(project, gh)
    assert r.code == 1 and "Malformed JSON" in r.err and "Back up the file" in r.err
    assert state_path(project).read_text(encoding="utf-8") == "{broken"
    assert gh.calls == []


def test_default_branch_change_stops(project, gh):
    assert run(project, gh).code == 0
    before = state_path(project).read_bytes()
    gh.default_branch = "trunk"
    r = run(project, gh, now=later(days=1))
    assert r.code == 1 and "default branch changed from 'main' to 'trunk'" in r.err
    assert state_path(project).read_bytes() == before


# --- 13. empty repository --------------------------------------------------------


def test_empty_repository(project, gh):
    gh.history, gh.commits = [], {}
    r = run(project, gh)
    assert r.code == 1
    assert "may be empty" in r.err
    assert not state_path(project).exists()
    assert reports(project) == []


# --- 14. human notes ----------------------------------------------------------------


def test_human_notes_never_modified(project, gh, monkeypatch):
    notes = project / "vault" / "notes"
    before = tree_hash(notes)
    run(project, gh)
    gh.add_issue(1, ts(later(hours=1)))
    run(project, gh, now=later(days=1))
    force_push(gh, sha(9))
    run(project, gh, now=later(days=2))
    gh.fail[(f"/repos/{OWNER}/{REPO}/issues", 1)] = 500
    run(project, gh, now=later(days=3))
    assert tree_hash(notes) == before
    written = {p.relative_to(project).parts[:2] for p in project.rglob("*") if p.is_file()}
    assert written <= {("config", "config.toml"), ("vault", "notes"), ("vault", "reports"), ("state", "github")}


# --- 15. messages, tokens and console encoding -----------------------------------


def test_token_used_but_never_printed(project, gh, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)

    def leaky(url, headers):
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        return 401, {}, json.dumps({"message": f"Bad credentials {TOKEN}"}).encode()

    out, err = io.StringIO(), io.StringIO()
    code = cli.run_github(project_root=project, fetch=leaky, now=NOW, out=out, err=err)
    assert code == 1
    assert "HTTP 401" in err.getvalue()
    assert TOKEN not in out.getvalue() + err.getvalue()
    assert not state_path(project).exists()


def test_messages_survive_windows_console_encoding(project, gh):
    gh.fail[(f"/repos/{OWNER}/{REPO}", 1)] = 500
    original = gh._error
    gh._error = lambda status, message="x": original(status, "Fallo → ñ 🚀")
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    err = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    code = cli.run_github(project_root=project, fetch=gh, now=NOW, out=out, err=err)
    err.flush()
    assert code == 1
    text = err.buffer.getvalue().decode("cp1252")
    assert "HTTP 500" in text and "Fallo ? ñ ?" in text


def test_success_output_is_ascii(project, gh):
    gh.add_issue(1, at(1), title="Título → 🚀")
    r = run(project, gh)
    assert r.code == 0
    assert (r.out + r.err).isascii()


def test_main_dispatches_github_run(monkeypatch):
    called = {}
    monkeypatch.setattr(cli, "run_github", lambda: called.setdefault("yes", 7))
    assert cli.main(["github", "run"]) == 7
