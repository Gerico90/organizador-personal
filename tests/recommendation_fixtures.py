"""Test helpers for the local recommendation data. All data here is synthetic."""

import copy
from datetime import datetime, timezone

from organizador.recommendations.references import get_or_create_reference

T0 = datetime(2026, 10, 26, 17, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 10, 26, 18, 0, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 10, 27, 9, 30, 0, tzinfo=timezone.utc)

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"

BASE_DRAFT = {
    "project_id": "example-project",
    "category": "testing",
    "title": "Example title",
    "proposal": "Example proposal",
    "justification": "Example justification",
    "benefits": ["Example benefit"],
    "limitations": ["Example limitation"],
    "relationship": "Example relationship",
    "reference_ids": [],
    "sources": [
        {
            "name": "Example source",
            "url": "https://example.com/source",
            "evidence_urls": ["https://example.com/source/evidence"],
        }
    ],
    "project_context": {
        "github_repo": "octo-cat/example-project",
        "commit_sha": SHA,
        "analyzed_at": "2026-10-26T16:00:00Z",
        "analyzed_files": ["README.md", "src/app.py"],
        "claims": [
            {
                "topic": "objective",
                "statement": "Example observed objective",
                "basis": "observed",
                "sources": ["README.md"],
            },
            {
                "topic": "architecture",
                "statement": "Example inferred decision",
                "basis": "inferred",
                "sources": ["src/app.py"],
            },
            {
                "topic": "capability",
                "statement": "No documentation of deployment was found",
                "basis": "missing",
                "sources": [],
            },
        ],
    },
}


def draft(**overrides) -> dict:
    data = copy.deepcopy(BASE_DRAFT)
    data.update(overrides)
    return data


def context(**overrides) -> dict:
    data = copy.deepcopy(BASE_DRAFT["project_context"])
    data.update(overrides)
    return data


def make_reference(root, url="https://github.com/octo-cat/tool", name="tool", now=T0):
    return get_or_create_reference(root, name=name, url=url, description="Example tool", now=now).reference
