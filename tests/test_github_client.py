import io
import json
import logging
import socket
import urllib.error
from urllib.parse import parse_qs, urlsplit

import pytest

from organizador.github import client as client_module
from organizador.github.client import (
    API_VERSION,
    GitHubAuthError,
    GitHubClient,
    GitHubError,
    GitHubNetworkError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubResponseError,
    default_fetch,
)

TOKEN = "ghp_SECRETtoken1234567890"
SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
REPO_URL = "https://api.github.com/repos/octo-cat/my.repo"


class FakeFetch:
    """Maps a URL (without query) or a full URL to queued responses; records calls."""

    def __init__(self):
        self.routes = {}
        self.calls = []

    def add(self, url, body=None, status=200, headers=None, raw=None):
        payload = raw if raw is not None else json.dumps(body).encode("utf-8")
        self.routes.setdefault(url, []).append((status, headers or {}, payload))

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        key = url if url in self.routes else url.split("?")[0]
        if key not in self.routes or not self.routes[key]:
            raise AssertionError(f"unexpected request {url}")
        response = self.routes[key].pop(0)
        if isinstance(response[2], BaseException):
            raise response[2]
        return response


@pytest.fixture
def fake():
    return FakeFetch()


@pytest.fixture
def gh(fake):
    return GitHubClient("octo-cat", "my.repo", token=TOKEN, fetch=fake)


def query(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def link(next_url):
    return {"Link": f'<{next_url}>; rel="next", <{next_url}&x=last>; rel="last"'}


def issue(n, **extra):
    return {"number": n, "updated_at": "2026-10-25T10:00:00Z", **extra}


# --- headers and authentication -------------------------------------------


def test_authenticated_request_headers(gh, fake):
    fake.add(REPO_URL, {"default_branch": "main"})
    assert gh.get_repository()["default_branch"] == "main"
    url, headers = fake.calls[0]
    assert url == REPO_URL
    assert headers["Accept"] == "application/vnd.github+json"
    assert headers["X-GitHub-Api-Version"] == API_VERSION
    assert headers["User-Agent"]
    assert headers["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize("token", [None, "", "   "])
def test_unauthenticated_request_has_no_authorization(fake, token):
    fake.add(REPO_URL, {"default_branch": "main"})
    GitHubClient("octo-cat", "my.repo", token=token, fetch=fake).get_repository()
    assert "Authorization" not in fake.calls[0][1]


def test_from_env_reads_github_token(fake, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    fake.add(REPO_URL, {"default_branch": "main"})
    GitHubClient.from_env("octo-cat", "my.repo", fetch=fake).get_repository()
    assert fake.calls[0][1]["Authorization"] == f"Bearer {TOKEN}"


def test_from_env_without_token_is_unauthenticated(fake, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    fake.add(REPO_URL, {"default_branch": "main"})
    GitHubClient.from_env("octo-cat", "my.repo", fetch=fake).get_repository()
    assert "Authorization" not in fake.calls[0][1]


def test_repr_does_not_show_token(gh):
    assert TOKEN not in repr(gh)
    assert "authenticated=True" in repr(gh)


# --- single resources -------------------------------------------------------


def test_get_branch(gh, fake):
    fake.add(f"{REPO_URL}/branches/main", {"name": "main", "commit": {"sha": SHA}})
    assert gh.get_branch("main")["commit"]["sha"] == SHA


def test_branch_name_with_slash_is_encoded(gh, fake):
    fake.add(f"{REPO_URL}/branches/feature%2Fx", {"commit": {"sha": SHA}})
    gh.get_branch("feature/x")


def test_get_commit(gh, fake):
    fake.add(f"{REPO_URL}/commits/{SHA}", {"sha": SHA})
    assert gh.get_commit(SHA)["sha"] == SHA


def compare_page(status="ahead", total=0, shas=()):
    return {"status": status, "ahead_by": total, "total_commits": total, "commits": [{"sha": s} for s in shas]}


@pytest.mark.parametrize("status", ["identical", "ahead", "behind", "diverged"])
def test_compare(gh, fake, status):
    fake.add(f"{REPO_URL}/compare/{SHA}...main", compare_page(status))
    pages = list(gh.iter_compare(SHA, "main"))
    assert [p["status"] for p in pages] == [status]
    assert query(fake.calls[0][0]) == {"per_page": "100"}


def test_compare_follows_pages(gh, fake):
    page2 = f"{REPO_URL}/compare/{SHA}...main?per_page=100&page=2"
    fake.add(f"{REPO_URL}/compare/{SHA}...main", compare_page(total=3, shas=["c1", "c2"]), headers=link(page2))
    fake.add(page2, compare_page(total=3, shas=["c3"]))
    pages = list(gh.iter_compare(SHA, "main"))
    assert [c["sha"] for p in pages for c in p["commits"]] == ["c1", "c2", "c3"]


def test_compare_stops_early(gh, fake):
    page2 = f"{REPO_URL}/compare/{SHA}...main?per_page=100&page=2"
    fake.add(f"{REPO_URL}/compare/{SHA}...main", compare_page("diverged", total=3), headers=link(page2))
    next(gh.iter_compare(SHA, "main"))
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "method, args, url, body",
    [
        ("get_repository", (), REPO_URL, {"name": "x"}),
        ("get_repository", (), REPO_URL, {"default_branch": ""}),
        ("get_branch", ("main",), f"{REPO_URL}/branches/main", {"commit": "abc"}),
        ("get_branch", ("main",), f"{REPO_URL}/branches/main", {"commit": {}}),
        ("get_commit", (SHA,), f"{REPO_URL}/commits/{SHA}", {"sha": 5}),
        ("get_repository", (), REPO_URL, ["not", "an", "object"]),
    ],
)
def test_unexpected_shapes_are_rejected(gh, fake, method, args, url, body):
    fake.add(url, body)
    with pytest.raises(GitHubResponseError):
        getattr(gh, method)(*args)


@pytest.mark.parametrize(
    "body",
    [
        {"status": "weird", "total_commits": 0, "commits": []},
        {"total_commits": 0, "commits": []},
        {"status": "ahead", "commits": []},
        {"status": "ahead", "total_commits": "1", "commits": []},
        {"status": "ahead", "total_commits": 0},
        {"status": "ahead", "total_commits": 1, "commits": ["x"]},
        {"status": "ahead", "total_commits": 1, "commits": [{"no_sha": 1}]},
        [],
    ],
)
def test_unexpected_compare_shapes_are_rejected(gh, fake, body):
    fake.add(f"{REPO_URL}/compare/{SHA}...main", body)
    with pytest.raises(GitHubResponseError):
        list(gh.iter_compare(SHA, "main"))


def test_invalid_json_is_rejected(gh, fake):
    fake.add(REPO_URL, raw=b"<html>oops</html>")
    with pytest.raises(GitHubResponseError, match="not valid JSON"):
        gh.get_repository()


def test_non_utf8_body_is_rejected(gh, fake):
    fake.add(REPO_URL, raw=b"\xff\xfe")
    with pytest.raises(GitHubResponseError):
        gh.get_repository()


# --- lists and pagination ---------------------------------------------------


def test_commits_query_parameters(gh, fake):
    fake.add(f"{REPO_URL}/commits", [{"sha": SHA}])
    assert [c["sha"] for c in gh.iter_commits("main", since="2026-10-10T00:00:00Z")] == [SHA]
    assert query(fake.calls[0][0]) == {"sha": "main", "per_page": "100", "since": "2026-10-10T00:00:00Z"}


def test_commits_without_since(gh, fake):
    fake.add(f"{REPO_URL}/commits", [])
    assert list(gh.iter_commits("main")) == []
    assert "since" not in query(fake.calls[0][0])


def test_issues_query_and_pull_requests_excluded(gh, fake):
    fake.add(f"{REPO_URL}/issues", [issue(1), issue(2, pull_request={"url": "x"}), issue(3)])
    assert [i["number"] for i in gh.iter_issues(since="2026-10-25T10:00:00Z")] == [1, 3]
    assert query(fake.calls[0][0]) == {
        "state": "all", "sort": "updated", "direction": "desc",
        "per_page": "100", "since": "2026-10-25T10:00:00Z",
    }


def test_pulls_query(gh, fake):
    fake.add(f"{REPO_URL}/pulls", [issue(42)])
    assert [p["number"] for p in gh.iter_pulls()] == [42]
    assert query(fake.calls[0][0]) == {"state": "all", "sort": "updated", "direction": "desc", "per_page": "100"}


def test_pagination_follows_all_pages(gh, fake):
    page2 = f"{REPO_URL}/issues?page=2"
    page3 = f"{REPO_URL}/issues?page=3"
    fake.add(f"{REPO_URL}/issues", [issue(1), issue(2)], headers=link(page2))
    fake.add(page2, [issue(3)], headers={"link": f'<{page3}>; rel="next"'})  # lower-case header
    fake.add(page3, [issue(4)])
    assert [i["number"] for i in gh.iter_issues()] == [1, 2, 3, 4]
    assert len(fake.calls) == 3


def test_pagination_stops_early_without_fetching_next_page(gh, fake):
    page2 = f"{REPO_URL}/pulls?page=2"
    fake.add(f"{REPO_URL}/pulls", [issue(1), issue(2)], headers=link(page2))
    fake.add(page2, [issue(3)])
    for pr in gh.iter_pulls():
        if pr["number"] == 2:
            break
    assert len(fake.calls) == 1


def test_error_on_later_page_raises(gh, fake):
    page2 = f"{REPO_URL}/commits?page=2"
    fake.add(f"{REPO_URL}/commits", [{"sha": SHA}], headers=link(page2))
    fake.add(page2, {"message": "Server Error"}, status=502)
    got = []
    with pytest.raises(GitHubError) as info:
        for c in gh.iter_commits("main"):
            got.append(c)
    assert info.value.status == 502
    assert len(got) == 1  # caller sees the failure and must discard partial results


@pytest.mark.parametrize(
    "next_url",
    [
        "https://evil.example.com/repos/octo-cat/my.repo/issues?page=2",
        "http://api.github.com/repos/octo-cat/my.repo/issues?page=2",
        "https://api.github.com.evil.com/x",
        "https://user:pass@api.github.com/x",
        "https://api.github.com:8443/x",
    ],
)
def test_untrusted_pagination_url_is_not_followed(gh, fake, next_url):
    fake.add(f"{REPO_URL}/issues", [issue(1)], headers={"Link": f'<{next_url}>; rel="next"'})
    with pytest.raises(GitHubResponseError, match="untrusted pagination URL"):
        list(gh.iter_issues())
    assert len(fake.calls) == 1


def test_pagination_loop_is_detected(gh, fake):
    first = f"{REPO_URL}/issues?state=all&sort=updated&direction=desc&per_page=100"
    fake.add(first, [issue(1)], headers={"Link": f'<{first}>; rel="next"'})
    with pytest.raises(GitHubResponseError, match="loop"):
        list(gh.iter_issues())


@pytest.mark.parametrize(
    "body",
    [
        {"message": "not a list"},
        ["not an object"],
        [{"updated_at": "2026-10-25T10:00:00Z"}],
        [{"number": "1", "updated_at": "2026-10-25T10:00:00Z"}],
        [{"number": True, "updated_at": "2026-10-25T10:00:00Z"}],
        [{"number": 1}],
    ],
)
def test_malformed_list_items_are_rejected(gh, fake, body):
    fake.add(f"{REPO_URL}/pulls", body)
    with pytest.raises(GitHubResponseError):
        list(gh.iter_pulls())


def test_malformed_commit_item_is_rejected(gh, fake):
    fake.add(f"{REPO_URL}/commits", [{"no_sha": 1}])
    with pytest.raises(GitHubResponseError):
        list(gh.iter_commits("main"))


# --- HTTP errors ------------------------------------------------------------


@pytest.mark.parametrize(
    "status, headers, message, error_class",
    [
        (401, {}, "Bad credentials", GitHubAuthError),
        (403, {}, "Resource not accessible by personal access token", GitHubAuthError),
        (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1793000000"}, "API rate limit exceeded", GitHubRateLimitError),
        (403, {"Retry-After": "60"}, "You have exceeded a secondary rate limit", GitHubRateLimitError),
        (429, {"Retry-After": "30"}, "Too many requests", GitHubRateLimitError),
        (404, {}, "Not Found", GitHubNotFoundError),
        (409, {}, "Git Repository is empty.", GitHubError),
        (422, {}, "No commit found for SHA: abc", GitHubError),
        (500, {}, "Server Error", GitHubError),
        (503, {}, "Service Unavailable", GitHubError),
        (301, {"Location": "https://api.github.com/repositories/1"}, "Moved Permanently", GitHubError),
    ],
)
def test_http_errors(gh, fake, status, headers, message, error_class):
    fake.add(REPO_URL, {"message": message}, status=status, headers=headers)
    with pytest.raises(error_class) as info:
        gh.get_repository()
    assert type(info.value) is error_class
    assert info.value.status == status
    assert info.value.url == REPO_URL
    assert str(status) in str(info.value)
    assert message in str(info.value)


def test_not_found_is_distinguishable_from_unprocessable(gh, fake):
    fake.add(f"{REPO_URL}/commits/{SHA}", {"message": "Not Found"}, status=404)
    fake.add(f"{REPO_URL}/commits/{SHA}", {"message": "No commit found"}, status=422)
    with pytest.raises(GitHubNotFoundError):
        gh.get_commit(SHA)
    with pytest.raises(GitHubError) as info:
        gh.get_commit(SHA)
    assert not isinstance(info.value, GitHubNotFoundError)
    assert info.value.status == 422


def test_rate_limit_message_includes_reset_time(gh, fake):
    fake.add(REPO_URL, {"message": "API rate limit exceeded"}, status=403,
             headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1793000000"})
    with pytest.raises(GitHubRateLimitError, match="until 2026-10-26T"):
        gh.get_repository()


def test_unauthenticated_rate_limit_suggests_token(fake):
    fake.add(REPO_URL, {"message": "API rate limit exceeded"}, status=403,
             headers={"X-RateLimit-Remaining": "0"})
    with pytest.raises(GitHubRateLimitError, match="set GITHUB_TOKEN"):
        GitHubClient("octo-cat", "my.repo", fetch=fake).get_repository()


def test_error_body_that_is_not_json(gh, fake):
    fake.add(REPO_URL, raw=b"<html>Bad gateway</html>", status=502)
    with pytest.raises(GitHubError, match="HTTP 502"):
        gh.get_repository()


# --- network failures -------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError("timed out"),
        socket.timeout("timed out"),
        urllib.error.URLError("getaddrinfo failed"),
        ConnectionResetError("reset"),
    ],
)
def test_network_errors(gh, fake, exc):
    fake.add(REPO_URL, raw=exc)
    with pytest.raises(GitHubNetworkError) as info:
        gh.get_repository()
    assert info.value.status is None


def test_initial_untrusted_url_is_refused(fake):
    gh = GitHubClient("octo-cat", "my.repo", token=TOKEN, fetch=fake)
    with pytest.raises(GitHubResponseError):
        list(gh._paginate("https://evil.example.com/x"))
    assert fake.calls == []


# --- no token leakage -------------------------------------------------------


def test_token_never_appears_in_errors_or_logs(gh, fake, caplog):
    caplog.set_level(logging.DEBUG)
    fake.add(REPO_URL, {"message": f"echo {TOKEN}"}, status=401)
    fake.add(REPO_URL, raw=OSError(f"proxy said {TOKEN}"))
    for _ in range(2):
        with pytest.raises(GitHubError) as info:
            gh.get_repository()
        assert TOKEN not in str(info.value)
        assert TOKEN not in repr(info.value.args)
        assert info.value.__cause__ is None
    assert TOKEN not in caplog.text


# --- default_fetch (no network: opener is replaced) -------------------------


class FakeResponse:
    status = 200
    headers = {"Link": "x"}

    def read(self):
        return b"[]"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_default_fetch_success(monkeypatch):
    seen = {}

    def open_(request, timeout):
        seen["auth"] = request.get_header("Authorization")
        seen["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(client_module._opener, "open", open_)
    assert default_fetch(REPO_URL, {"Authorization": "Bearer x"}) == (200, {"Link": "x"}, b"[]")
    assert seen == {"auth": "Bearer x", "timeout": client_module.TIMEOUT_SECONDS}


def test_default_fetch_returns_http_errors_as_responses(monkeypatch):
    def open_(request, timeout):
        raise urllib.error.HTTPError(REPO_URL, 404, "Not Found", {"X-A": "1"}, io.BytesIO(b'{"message":"Not Found"}'))

    monkeypatch.setattr(client_module._opener, "open", open_)
    assert default_fetch(REPO_URL, {}) == (404, {"X-A": "1"}, b'{"message":"Not Found"}')


def test_default_fetch_propagates_timeouts(monkeypatch):
    def open_(request, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(client_module._opener, "open", open_)
    with pytest.raises(TimeoutError):
        default_fetch(REPO_URL, {})


def test_default_fetch_does_not_follow_redirects():
    handler = next(h for h in client_module._opener.handlers if isinstance(h, client_module._NoRedirect))
    assert handler.redirect_request(None, None, 301, "Moved", {}, "https://elsewhere") is None
