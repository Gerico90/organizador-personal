"""Render a GitHub activity report as Markdown. Pure: no I/O, no clock.

All GitHub text (titles, messages, names, branch) is treated as untrusted:
it is collapsed to one line, stripped of control characters, and has
Markdown/Obsidian syntax characters backslash-escaped. Links are emitted only
for https://github.com URLs made of safe characters. Frontmatter values are
written as JSON strings, which are valid YAML double-quoted scalars.
"""

import json
import re
import unicodedata

from organizador.github.changes import (
    CLOSED,
    HISTORY_REWRITTEN,
    MERGED,
    OPENED,
    UPDATED,
    Changes,
    CommitChanges,
    ItemChange,
    ItemChanges,
    parse_ts,
)

GENERATOR = "organizador"

_ACTIVITY_ORDER = (MERGED, OPENED, CLOSED, UPDATED)
_ACTIVITY_TIMESTAMP = {
    MERGED: "merged_at",
    CLOSED: "closed_at",
    OPENED: "created_at",
    UPDATED: "updated_at",
}

_REPO_RE = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
_SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_SAFE_URL_RE = re.compile(r"https://github\.com/[A-Za-z0-9/._~%#?=&+-]+")
# Characters with meaning in CommonMark, GFM, or Obsidian inline syntax
# (emphasis, code, links, wikilinks, HTML, tables, strikethrough, highlight,
# comments, math, tags, block references, entities).
_MD_SPECIAL_RE = re.compile(r"([\\`*_{}\[\]<>()!|~=%$#^&])")
_LINE_BREAKS_RE = re.compile(r"[\r\n\v\f\x85  ]+")
# Bidirectional overrides can make text display differently from what it is.
_BIDI = set("‪‫‬‭‮⁦⁧⁨⁩")


def render_report(
    changes: Changes,
    *,
    repo: str,
    branch: str,
    period_from: str,
    period_to: str,
    generated_at: str,
) -> str:
    """Return the full Markdown report (frontmatter + body).

    `repo` is "owner/name". Timestamps are GitHub-format UTC strings
    (YYYY-MM-DDTHH:MM:SSZ). For a baseline, `period_from` is the window start;
    otherwise it is the previous successful run.
    """
    if not _REPO_RE.fullmatch(repo):
        raise ValueError(f"Invalid repository {repo!r}")
    for value in (period_from, period_to, generated_at):
        parse_ts(value)
    commits = changes.commits

    lines = _frontmatter(changes, repo, branch, period_from, period_to, generated_at)
    lines.append("")

    kind = "initial baseline" if changes.baseline else "activity"
    lines.append(f"# {_md(repo)} — {kind} {period_from[:10]} → {period_to[:10]}")
    lines.append("")
    lines.append(f"Period: {_human_ts(period_from)} → {_human_ts(period_to)}")
    lines.append("")

    if changes.baseline:
        lines.append(
            "> **Initial baseline.** This is the first report for this repository. "
            "It lists activity in the period above, not changes since a previous report."
        )
        lines.append("")
    if commits.status == HISTORY_REWRITTEN:
        lines.extend(_rewrite_warning(commits, repo, branch))
        lines.append("")

    if not changes.has_changes:
        if changes.baseline:
            days = (parse_ts(period_to) - parse_ts(period_from)).days
            lines.append(f"No activity in the last {days} days.")
        else:
            lines.append("No activity since the previous report.")
        lines.append("")

    lines.append(_summary(commits, changes.pulls, changes.issues))
    lines.append("")

    lines.append(f"## Commits on {_md(_one_line(branch))}")
    lines.append("")
    if commits.status == HISTORY_REWRITTEN:
        lines.append("_Commit comparison unavailable (history rewritten)._")
    elif not commits.commits:
        lines.append("_No commits._")
    else:
        lines.extend(_commit_line(c) for c in commits.commits)
    lines.append("")

    for title, items, empty in (
        ("Pull requests", changes.pulls, "_No pull request activity._"),
        ("Issues", changes.issues, "_No issue activity._"),
    ):
        lines.append(f"## {title}")
        lines.append("")
        if items.changes:
            lines.extend(_item_line(c) for c in items.changes)
        else:
            lines.append(empty)
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


# --- sections ----------------------------------------------------------------


def _frontmatter(changes, repo, branch, period_from, period_to, generated_at) -> list[str]:
    commits = changes.commits
    fields = [
        ("source", "github"),
        ("repo", repo),
        ("branch", branch),
        ("period_from", period_from),
        ("period_to", period_to),
        ("generated_at", generated_at),
        ("generated_by", GENERATOR),
        ("baseline", changes.baseline),
        ("commits_status", commits.status),
        ("previous_head", commits.previous_head),
        ("head_sha", commits.head_sha),
    ]
    # json.dumps gives "quoted", true/false, and null: all valid YAML, and
    # newlines or quotes in a value cannot break out of the line.
    return ["---", *(f"{key}: {json.dumps(value)}" for key, value in fields), "---"]


def _rewrite_warning(commits: CommitChanges, repo: str, branch: str) -> list[str]:
    previous = _sha_link(commits.previous_head, repo)
    current = _sha_link(commits.head_sha, repo)
    return [
        f"> **Warning: history rewritten.** The history of {_md(_one_line(branch))} was rewritten "
        f"since the last run. Previous head {previous} is no longer reachable. "
        f"Commit comparison was skipped. New commit baseline: {current}."
    ]


def _summary(commits: CommitChanges, pulls: ItemChanges, issues: ItemChanges) -> str:
    if commits.status == HISTORY_REWRITTEN:
        commit_part = "commits: comparison unavailable"
    else:
        commit_part = _plural(len(commits.commits), "commit")
    return " · ".join([commit_part, _items_summary(pulls, "pull request"), _items_summary(issues, "issue")])


def _items_summary(items: ItemChanges, noun: str) -> str:
    text = _plural(len(items.changes), noun)
    counts = [(a, sum(1 for c in items.changes if c.activity == a)) for a in _ACTIVITY_ORDER]
    detail = ", ".join(f"{n} {a}" for a, n in counts if n)
    return f"{text} ({detail})" if detail else text


def _commit_line(commit: dict) -> str:
    sha = commit.get("sha")
    url = commit.get("html_url")
    label = f"`{sha[:7]}`" if isinstance(sha, str) and _SHA_RE.fullmatch(sha) else _md(str(sha))
    parts = [_link(label, url)]

    data = commit.get("commit") if isinstance(commit.get("commit"), dict) else {}
    message = _first_line(data.get("message"))
    if message:
        parts.append(_md(message))

    line = "- " + " ".join(parts)
    meta = [m for m in (_commit_author(commit, data), _date(_get(data, "author", "date"))) if m]
    return line + (" — " + ", ".join(meta) if meta else "")


def _item_line(change: ItemChange) -> str:
    item = change.item
    number = item.get("number")
    parts = [f"**{change.activity}**", _link(f"#{number}", item.get("html_url"))]
    title = _one_line(item.get("title"))
    if title:
        parts.append(_md(title))

    line = "- " + " ".join(parts)
    login = _get(item, "user", "login")
    meta = [m for m in (_login(login), _human_ts(item.get(_ACTIVITY_TIMESTAMP[change.activity]))) if m]
    return line + (" — " + ", ".join(meta) if meta else "")


# --- small helpers -----------------------------------------------------------


def _commit_author(commit: dict, data: dict) -> str:
    # Prefer the GitHub account; fall back to the git author name. Never guess.
    login = _login(_get(commit, "author", "login"))
    if login:
        return login
    name = _one_line(_get(data, "author", "name"))
    return _md(name) if name else ""


def _login(login) -> str:
    text = _one_line(login)
    return "@" + _md(text) if text else ""


def _get(data, *keys):
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _one_line(value) -> str:
    """Untrusted text as a single clean line; "" for missing or non-text values."""
    if not isinstance(value, str):
        return ""
    value = _LINE_BREAKS_RE.sub(" ", value)
    value = "".join(
        " " if ch == "\t" else ch
        for ch in value
        if ch == "\t" or (unicodedata.category(ch) != "Cc" and ch not in _BIDI)
    )
    return " ".join(value.split())


def _first_line(message) -> str:
    if not isinstance(message, str):
        return ""
    for line in _LINE_BREAKS_RE.split(message):
        if line.strip():
            return _one_line(line)
    return ""


def _md(text: str) -> str:
    return _MD_SPECIAL_RE.sub(r"\\\1", text)


def _link(label: str, url) -> str:
    if isinstance(url, str) and _SAFE_URL_RE.fullmatch(url):
        return f"[{label}]({url})"
    return label


def _sha_link(sha, repo: str) -> str:
    if isinstance(sha, str) and _SHA_RE.fullmatch(sha):
        return _link(f"`{sha[:7]}`", f"https://github.com/{repo}/commit/{sha}")
    return _md(_one_line(sha)) or "unknown"


def _human_ts(value) -> str:
    try:
        return parse_ts(value).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return ""


def _date(value) -> str:
    try:
        return parse_ts(value).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")
