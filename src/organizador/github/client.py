"""Read-only GitHub REST client for one repository.

All HTTP goes through one injectable function:

    fetch(url, headers) -> (status, headers, body)

It returns any HTTP response (including 4xx/5xx) as a tuple and raises only
for network failures. Tests pass a fake; production uses `default_fetch`.

List methods are generators: each page is requested only when the caller
iterates past the previous one, so a caller can stop early with `break`.
An error on any page raises; callers must then discard what they collected.

The token is read from GITHUB_TOKEN, sent only to api.github.com, and never
included in error messages. This module does not log.
"""

import http.client
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Iterator
from urllib.parse import quote, urlencode, urlsplit

API_ROOT = "https://api.github.com"
API_HOST = "api.github.com"
API_VERSION = "2022-11-28"
TOKEN_ENV_VAR = "GITHUB_TOKEN"
PER_PAGE = 100
TIMEOUT_SECONDS = 30
COMPARE_STATUSES = frozenset({"identical", "ahead", "behind", "diverged"})

Fetch = Callable[[str, dict[str, str]], tuple[int, dict[str, str], bytes]]

_NEXT_LINK_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


class GitHubError(Exception):
    """A GitHub request failed. `status` is the HTTP status, or None for network errors."""

    def __init__(self, message: str, status: int | None = None, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url


class GitHubAuthError(GitHubError):
    """401, or 403 that is not a rate limit (bad token or missing permission)."""


class GitHubNotFoundError(GitHubError):
    """404: the repository, branch, commit, or resource does not exist or is not visible."""


class GitHubRateLimitError(GitHubError):
    """429, or 403 caused by a primary or secondary rate limit."""


class GitHubNetworkError(GitHubError):
    """No HTTP response: timeout, DNS failure, connection reset."""


class GitHubResponseError(GitHubError):
    """The response was not what the API documents (bad JSON, wrong shape, bad Link URL)."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # Redirects are not followed, so the token never travels to another URL.
    # A 3xx becomes an HTTPError and is reported by the client.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def default_fetch(url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with _opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:
            body = b""
        finally:
            e.close()
        return e.code, dict(e.headers or {}), body


class GitHubClient:
    def __init__(self, owner: str, repo: str, token: str | None = None, fetch: Fetch = default_fetch):
        self.owner = owner
        self.repo = repo
        self._token = token.strip() if token and token.strip() else None
        self._fetch = fetch

    @classmethod
    def from_env(cls, owner: str, repo: str, fetch: Fetch = default_fetch) -> "GitHubClient":
        """Use GITHUB_TOKEN if set; otherwise make unauthenticated requests."""
        return cls(owner, repo, token=os.environ.get(TOKEN_ENV_VAR), fetch=fetch)

    def __repr__(self) -> str:
        return f"GitHubClient({self.owner}/{self.repo}, authenticated={self._token is not None})"

    # --- single resources -------------------------------------------------

    def get_repository(self) -> dict:
        """GET /repos/{owner}/{repo}. Guarantees `default_branch` is a string."""
        data = self._get_object(self._repo_url())
        _require(data, "default_branch", str, "repository")
        return data

    def get_branch(self, branch: str) -> dict:
        """GET /repos/{owner}/{repo}/branches/{branch}. Guarantees `commit.sha`."""
        data = self._get_object(self._repo_url("branches", branch))
        commit = _require(data, "commit", dict, "branch")
        _require(commit, "sha", str, "branch commit")
        return data

    def get_commit(self, sha: str) -> dict:
        """GET /repos/{owner}/{repo}/commits/{sha}. Guarantees `sha`."""
        data = self._get_object(self._repo_url("commits", sha))
        _require(data, "sha", str, "commit")
        return data

    def iter_compare(self, base: str, head: str) -> Iterator[dict]:
        """GET /repos/{owner}/{repo}/compare/{base}...{head}, one page at a time.

        Each page is the comparison object. Its `commits` list is the part of
        `git log base..head` on that page, oldest first; merged-in commits with
        old dates are included. Guarantees on every page: `status` is one of
        identical/ahead/behind/diverged, `total_commits` is an int, and
        `commits` is a list of objects with a string `sha`.
        """
        url = self._repo_url("compare", f"{base}...{head}") + "?" + urlencode({"per_page": PER_PAGE})
        for page in self._paginate(url, list_key="commits"):
            status = _require(page, "status", str, "comparison")
            if status not in COMPARE_STATUSES:
                raise GitHubResponseError(f"Unexpected comparison status {status!r}", url=url)
            total = page.get("total_commits")
            if not isinstance(total, int) or isinstance(total, bool):
                raise GitHubResponseError("Unexpected comparison response: missing or invalid 'total_commits'", url=url)
            for commit in page["commits"]:
                _require(commit, "sha", str, "compared commit")
            yield page

    # --- lists (lazy, page by page) ---------------------------------------

    def iter_commits(self, branch: str, since: str | None = None) -> Iterator[dict]:
        """Commits on `branch`, newest first. Each has a string `sha`."""
        params = {"sha": branch, "per_page": PER_PAGE}
        if since is not None:
            params["since"] = since
        for item in self._paginate(self._repo_url("commits") + "?" + urlencode(params)):
            _require(item, "sha", str, "commit")
            yield item

    def iter_issues(self, since: str | None = None) -> Iterator[dict]:
        """Open and closed issues, most recently updated first. Pull requests are excluded.

        Each has integer `number` and string `updated_at`. `since` is passed to
        GitHub as is; any overlap is the caller's decision.
        """
        params = {"state": "all", "sort": "updated", "direction": "desc", "per_page": PER_PAGE}
        if since is not None:
            params["since"] = since
        for item in self._paginate(self._repo_url("issues") + "?" + urlencode(params)):
            _require_item(item, "issue")
            if "pull_request" not in item:
                yield item

    def iter_pulls(self) -> Iterator[dict]:
        """Open and closed pull requests, most recently updated first.

        Each has integer `number` and string `updated_at`. GitHub has no
        `since` filter here; the caller stops iterating when items get too old.
        """
        params = {"state": "all", "sort": "updated", "direction": "desc", "per_page": PER_PAGE}
        for item in self._paginate(self._repo_url("pulls") + "?" + urlencode(params)):
            _require_item(item, "pull request")
            yield item

    # --- internals --------------------------------------------------------

    def _repo_url(self, *segments: str) -> str:
        parts = ["repos", self.owner, self.repo, *segments]
        return API_ROOT + "/" + "/".join(quote(p, safe="") for p in parts)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "organizador",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(self, url: str) -> tuple[dict[str, str], object]:
        """One GET. Returns (lower-cased headers, parsed JSON) or raises GitHubError."""
        if not _is_trusted(url):
            raise GitHubResponseError(f"Refusing to request untrusted URL {url!r}", url=url)
        try:
            status, raw_headers, body = self._fetch(url, self._headers())
        except (OSError, http.client.HTTPException) as e:
            raise GitHubNetworkError(self._scrub(f"Network error for {url}: {e}"), url=url) from None

        headers = {k.lower(): v for k, v in (raw_headers or {}).items()}
        if not 200 <= status < 300:
            raise self._http_error(status, headers, body, url)
        try:
            return headers, json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise GitHubResponseError(f"Response from {url} is not valid JSON", status, url) from None

    def _get_object(self, url: str) -> dict:
        _, data = self._request(url)
        if not isinstance(data, dict):
            raise GitHubResponseError(f"Expected a JSON object from {url}", url=url)
        return data

    def _paginate(self, url: str, list_key: str | None = None) -> Iterator[dict]:
        """Follow Link rel="next" pages.

        Without `list_key`, each page is a JSON list and its items are yielded.
        With `list_key`, each page is a JSON object whose `list_key` is a list
        of objects, and the page object itself is yielded.
        """
        seen = set()
        while url:
            seen.add(url)
            headers, data = self._request(url)
            if list_key:
                items = data.get(list_key) if isinstance(data, dict) else None
            else:
                items = data
            if not isinstance(items, list):
                expected = f"a JSON object with a '{list_key}' list" if list_key else "a JSON list"
                raise GitHubResponseError(f"Expected {expected} from {url}", url=url)
            for item in items:
                if not isinstance(item, dict):
                    raise GitHubResponseError(f"Expected JSON objects in list from {url}", url=url)
            if list_key:
                yield data
            else:
                yield from items

            match = _NEXT_LINK_RE.search(headers.get("link", ""))
            next_url = match.group(1) if match else None
            if next_url is not None:
                if not _is_trusted(next_url):
                    raise GitHubResponseError(
                        f"Refusing to follow untrusted pagination URL {next_url!r}", url=url
                    )
                if next_url in seen:
                    raise GitHubResponseError(f"Pagination loop at {next_url!r}", url=url)
            url = next_url

    def _http_error(self, status: int, headers: dict[str, str], body: bytes, url: str) -> GitHubError:
        detail = _api_message(body)
        base = f"GitHub returned HTTP {status} for {url}" + (f": {detail}" if detail else "")

        if status == 429 or (status == 403 and _is_rate_limited(headers, detail)):
            cls, hint = GitHubRateLimitError, _rate_limit_hint(headers, self._token is not None)
        elif status == 401:
            cls, hint = GitHubAuthError, f"Check the {TOKEN_ENV_VAR} environment variable."
        elif status == 403:
            cls, hint = GitHubAuthError, "The token may lack read access to this repository."
        elif status == 404:
            cls, hint = GitHubNotFoundError, (
                "It does not exist, or it is private and the token cannot see it."
            )
        elif 300 <= status < 400:
            cls, hint = GitHubError, "The repository may have been renamed or transferred; update the config."
        else:
            cls, hint = GitHubError, ""
        return cls(self._scrub(f"{base}. {hint}".strip()), status, url)

    def _scrub(self, message: str) -> str:
        return message.replace(self._token, "***") if self._token else message


def _is_trusted(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.netloc == API_HOST and not parts.username


def _api_message(body: bytes) -> str:
    try:
        message = json.loads(body).get("message", "")
    except (ValueError, UnicodeDecodeError, AttributeError):
        return ""
    return str(message)[:200] if message else ""


def _is_rate_limited(headers: dict[str, str], detail: str) -> bool:
    return (
        headers.get("x-ratelimit-remaining") == "0"
        or "retry-after" in headers
        or "rate limit" in detail.lower()
    )


def _rate_limit_hint(headers: dict[str, str], authenticated: bool) -> str:
    if "retry-after" in headers:
        hint = f"Rate limited; retry after {headers['retry-after']} seconds."
    elif headers.get("x-ratelimit-reset", "").isdigit():
        reset = datetime.fromtimestamp(int(headers["x-ratelimit-reset"]), tz=timezone.utc)
        hint = f"Rate limited until {reset:%Y-%m-%dT%H:%M:%SZ}."
    else:
        hint = "Rate limited."
    if not authenticated:
        hint += f" Unauthenticated requests are limited to 60/hour; set {TOKEN_ENV_VAR}."
    return hint


def _require(data: dict, key: str, kind: type, what: str):
    value = data.get(key)
    if not isinstance(value, kind) or (kind is str and not value):
        raise GitHubResponseError(f"Unexpected {what} response: missing or invalid {key!r}")
    return value


def _require_item(item: dict, what: str) -> None:
    number = item.get("number")
    if not isinstance(number, int) or isinstance(number, bool):
        raise GitHubResponseError(f"Unexpected {what} in list: missing or invalid 'number'")
    _require(item, "updated_at", str, what)
