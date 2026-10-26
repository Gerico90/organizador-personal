"""Test helpers for external research. All data here is synthetic."""

import copy
from datetime import datetime, timezone

from organizador.understanding import profile as profile_module
from organizador.understanding.profile import create_profile

T0 = datetime(2026, 10, 27, 10, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 10, 27, 11, 0, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone.utc)
SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
NOTE_SHA = "3" * 64

EVIDENCE = {
    "schema_version": 1,
    "project_id": "demo-project",
    "project_dir": "/tmp/project",
    "github_repo": "octo-cat/demo",
    "commit_sha": SHA,
    "working_tree_clean": True,
    "collected_at": "2026-10-27T09:00:00Z",
    "notes_checked": "/tmp/vault/notes/projects/demo-project.md",
    "files": [
        {"ref": "README.md", "kind": "markdown", "bytes": 100, "lines": 40, "sha256": "0" * 64, "headings": []},
        {"ref": "src/app.py", "kind": "python", "bytes": 50, "lines": 12, "sha256": "1" * 64},
    ],
    "excluded": [],
    "reports": [],
    "notes": [{"ref": "note:demo-project.md", "kind": "markdown", "bytes": 10, "lines": 5, "sha256": NOTE_SHA, "headings": []}],
}

PROFILE_CLAIMS = [
    {"topic": "purpose", "statement": "A demo tool.", "basis": "observed", "sources": ["README.md:1-3"]},
    {"topic": "capability", "statement": "Syncs items.", "basis": "observed", "sources": ["src/app.py:1-5"]},
    {"topic": "architecture", "statement": "Single module.", "basis": "inferred", "sources": ["src/app.py"]},
    {"topic": "limitation", "statement": "Items in the same second can be missed.", "basis": "observed", "sources": ["README.md:10-12"]},
    {"topic": "objective", "statement": "Planned: scheduled runs.", "basis": "observed", "sources": ["note:demo-project.md:3"]},
    {"topic": "objective", "statement": "A timeline is not documented in the inspected sources.", "basis": "missing", "sources": []},
]


def evidence(**overrides) -> dict:
    data = copy.deepcopy(EVIDENCE)
    data.update(overrides)
    return data


def make_profile(root, monkeypatch, now=T0, suffix="0000000a", ev=None):
    monkeypatch.setattr(profile_module, "_random_suffix", lambda: suffix)
    draft = {
        "project_id": "demo-project",
        "context": {
            "github_repo": "octo-cat/demo",
            "commit_sha": SHA,
            "analyzed_at": "2026-10-27T09:30:00Z",
            "analyzed_files": ["README.md", "src/app.py", "note:demo-project.md"],
            "claims": copy.deepcopy(PROFILE_CLAIMS),
        },
    }
    return create_profile(root, draft, ev or evidence(), now=now)


def stored_recommendation(**overrides) -> dict:
    """A recommendation in storage format, for creating existing records directly."""
    data = {
        "project_id": "demo-project",
        "category": "testing",
        "title": "Property-based tests for cursor ties",
        "proposal": "Generate random update sequences sharing timestamps and check no item is lost.",
        "justification": "Example justification",
        "benefits": [],
        "limitations": [],
        "relationship": "technical: example",
        "reference_ids": [],
        "sources": [{"name": "Docs", "url": "https://docs.example/a", "evidence_urls": ["https://docs.example/a"]}],
        "project_context": {
            "github_repo": "octo-cat/demo",
            "commit_sha": SHA,
            "analyzed_at": "2026-10-27T09:30:00Z",
            "analyzed_files": ["README.md"],
            "claims": [copy.deepcopy(PROFILE_CLAIMS[3])],
        },
    }
    data.update(overrides)
    return data


DOC_URL = "https://docs.example/tool"
DOC_HTML = (
    b"<html><body><h1>Tool</h1><p>The tool generates random inputs and shrinks failing cases "
    b"to a minimal example.</p><p>It also records a database of past failures.</p></body></html>"
)
OTHER_URL = "https://other.example/guide"
OTHER_HTML = b"<html><body><p>Stateful testing models a system as a sequence of operations on it.</p></body></html>"


class FakeNet:
    """Resolver and transport for offline tests: every host is public, pages come from `pages`."""

    def __init__(self, pages):
        self.pages = pages  # url -> HTML bytes

    def resolver(self, host, port):
        return ["93.184.216.34"]

    def transport(self, host, ip, port, target, headers):
        body = self.pages[f"https://{host}{target}"]
        return 200, {"Content-Type": "text/html; charset=utf-8"}, body


def add_snapshot(root, cycle_id, url, html, now=T0):
    from organizador.research.snapshots import fetch_snapshot

    net = FakeNet({url: html})
    return fetch_snapshot(root, cycle_id, url, now=now, resolver=net.resolver, transport=net.transport)[0]


def research_recommendation(**overrides) -> dict:
    data = {
        "category": "testing",
        "title": "Property-based tests for cursor ties",
        "proposal": "Generate random update sequences that share timestamps and check that no distinct item is lost.",
        "benefits": ["Explores tie cases beyond hand-written fixtures"],
        "limitations": ["Adds a test-only dependency"],
        "relationship": "technical: a testing technique applied to the cursor logic",
        "profile_claims": [4],
        "reference_urls": ["https://github.com/octo-cat/tool"],
        "sources": [{"name": "Tool documentation", "url": DOC_URL, "evidence_urls": [DOC_URL]}],
        "verified": [
            {
                "statement": "The tool shrinks failing inputs to a minimal example.",
                "quote": "shrinks failing cases to a minimal example",
                "url": DOC_URL,
            }
        ],
        "interpretations": ["Tie handling is the kind of edge case generated inputs explore."],
        "assumptions": ["The existing fake API can be driven by generated data."],
    }
    data.update(overrides)
    return data


def research_draft(cycle_id, profile_id, recommendations=None, **overrides) -> dict:
    data = {
        "cycle_id": cycle_id,
        "project_id": "demo-project",
        "profile_id": profile_id,
        "log": {
            "questions": [{"question": "How do others test timestamp ties?", "profile_claims": [4]}],
            "searches": [{"tool": "web_search", "query": "property based testing python"}],
            "rejected": [{"url": "https://spam.example/top-10", "reason": "generic list without technical detail"}],
        },
        "references": [{"name": "tool", "url": "https://github.com/octo-cat/tool", "description": "Example tool"}],
        "recommendations": [research_recommendation()] if recommendations is None else recommendations,
    }
    data.update(overrides)
    return data
