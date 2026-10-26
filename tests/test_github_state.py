import json
import os

import pytest

from organizador.config import Config
from organizador.github import state as state_module
from organizador.github.state import (
    Cursor,
    State,
    StateError,
    load_state,
    save_state,
    state_file_path,
)

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


@pytest.fixture
def config(tmp_path):
    return Config(
        vault_path=tmp_path / "vault",
        state_path=tmp_path / "state",
        github_owner="octo-cat",
        github_repo="my.repo",
        initial_lookback_days=15,
    )


def make_state(**overrides) -> State:
    fields = dict(
        repo="octo-cat/my.repo",
        default_branch="main",
        last_head_sha=SHA,
        issues_cursor=Cursor(ts="2026-10-25T18:21:04Z", ids=(57, 61)),
        pulls_cursor=Cursor(ts="2026-10-25T17:02:55Z", ids=(42,)),
        last_success_at="2026-10-25T18:30:11Z",
        last_report="reports/github/octo-cat__my.repo/2026-10-25T183011Z.md",
    )
    fields.update(overrides)
    return State(**fields)


def valid_dict() -> dict:
    return {
        "repo": "octo-cat/my.repo",
        "default_branch": "main",
        "last_head_sha": SHA,
        "issues_cursor": {"ts": "2026-10-25T18:21:04Z", "ids": [57, 61]},
        "pulls_cursor": {"ts": "2026-10-25T17:02:55Z", "ids": [42]},
        "last_success_at": "2026-10-25T18:30:11Z",
        "last_report": "reports/github/octo-cat__my.repo/2026-10-25T183011Z.md",
    }


def write_raw(config, text: str) -> None:
    path = state_file_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- paths ---------------------------------------------------------------


def test_state_path_is_per_repository_under_state_dir(config, tmp_path):
    path = state_file_path(config)
    assert path == tmp_path / "state" / "github" / "octo-cat__my.repo.json"
    assert config.vault_path not in path.parents


def test_different_repositories_use_different_files(config):
    other = Config(**{**config.__dict__, "github_repo": "other"})
    assert state_file_path(config) != state_file_path(other)


# --- load / save round trip ----------------------------------------------


def test_missing_state_returns_none(config):
    assert load_state(config) is None


def test_save_then_load_round_trip(config):
    original = make_state()
    path = save_state(config, original)
    assert path == state_file_path(config)
    assert load_state(config) == original


def test_timestamps_and_ids_preserved_exactly(config):
    save_state(config, make_state(issues_cursor=Cursor(ts="2026-01-02T03:04:05Z", ids=(9, 3, 7))))
    loaded = load_state(config)
    assert loaded.issues_cursor == Cursor(ts="2026-01-02T03:04:05Z", ids=(9, 3, 7))
    assert loaded.pulls_cursor == Cursor(ts="2026-10-25T17:02:55Z", ids=(42,))
    assert loaded.last_success_at == "2026-10-25T18:30:11Z"


def test_empty_ids_round_trip(config):
    # First run with no issues or PRs: cursor = window start, ids = [].
    save_state(config, make_state(pulls_cursor=Cursor(ts="2026-10-10T18:30:11Z", ids=())))
    assert load_state(config).pulls_cursor == Cursor(ts="2026-10-10T18:30:11Z", ids=())


def test_saved_file_format_matches_architecture(config):
    save_state(config, make_state())
    raw = state_file_path(config).read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"}\n")
    assert json.loads(raw.decode("utf-8")) == valid_dict()
    assert list(json.loads(raw)) == list(valid_dict())  # field order


def test_save_does_not_touch_vault(config):
    save_state(config, make_state())
    assert not config.vault_path.exists()


def test_owner_and_repo_match_is_case_insensitive(config):
    save_state(config, make_state(repo="Octo-Cat/My.Repo"))
    assert load_state(config).repo == "Octo-Cat/My.Repo"


# --- rejecting bad files -------------------------------------------------


def test_malformed_json_is_rejected(config):
    write_raw(config, '{"repo": "octo-cat/my.repo",')
    with pytest.raises(StateError, match="Malformed JSON.*Back up the file before"):
        load_state(config)


def test_invalid_state_message_recommends_backup(config):
    data = valid_dict()
    del data["repo"]
    write_raw(config, json.dumps(data))
    with pytest.raises(StateError, match="Back up the file before"):
        load_state(config)


@pytest.mark.parametrize(
    "ts",
    [
        "2026-1-02T03:04:05Z",
        "2026-01-2T03:04:05Z",
        "2026-01-02T3:04:05Z",
        "2026-01-02T03:4:05Z",
        "2026-01-02T03:04:5Z",
        "2026-01-02T03:04:05.000Z",
        "2026-01-02T03:04:05z",
        "2026-02-30T03:04:05Z",
        "２０２６-01-02T03:04:05Z",  # non-ASCII digits
        "2026-01-02T03:04:05Z\n",
    ],
)
def test_timestamp_must_be_strict_and_zero_padded(config, ts):
    data = valid_dict()
    data["last_success_at"] = ts
    write_raw(config, json.dumps(data))
    with pytest.raises(StateError, match="last_success_at.*UTC timestamp"):
        load_state(config)


def test_non_utf8_file_is_rejected(config):
    path = state_file_path(config)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(StateError, match="Cannot read state file"):
        load_state(config)


def test_top_level_not_object_is_rejected(config):
    write_raw(config, "[]")
    with pytest.raises(StateError, match="JSON object"):
        load_state(config)


def test_wrong_repository_is_rejected(config):
    data = valid_dict()
    data["repo"] = "someone/else"
    write_raw(config, json.dumps(data))
    with pytest.raises(StateError, match="belongs to repository 'someone/else'"):
        load_state(config)


def test_invalid_file_is_not_reset(config):
    write_raw(config, "not json")
    with pytest.raises(StateError):
        load_state(config)
    assert state_file_path(config).read_text(encoding="utf-8") == "not json"


def _without(key):
    def change(d):
        del d[key]
    return change


def _set(key, value):
    def change(d):
        d[key] = value
    return change


def _set_cursor(key, field, value):
    def change(d):
        d[key][field] = value
    return change


@pytest.mark.parametrize(
    "change, message",
    [
        (_without("repo"), "missing field.*repo"),
        (_without("last_head_sha"), "missing field.*last_head_sha"),
        (_without("issues_cursor"), "missing field.*issues_cursor"),
        (_without("last_report"), "missing field.*last_report"),
        (_set("extra", 1), "unknown field.*extra"),
        (_set("default_branch", ""), "default_branch must be a non-empty string"),
        (_set("default_branch", None), "default_branch must be a non-empty string"),
        (_set("last_head_sha", "abc123"), "last_head_sha.*hex commit SHA"),
        (_set("last_head_sha", SHA.upper()), "last_head_sha.*hex commit SHA"),
        (_set("last_head_sha", SHA + "\n"), "last_head_sha.*hex commit SHA"),
        (_set("last_head_sha", SHA + "0"), "last_head_sha.*hex commit SHA"),
        (_set("last_success_at", "2026-10-25 18:30:11"), "last_success_at.*UTC timestamp"),
        (_set("last_success_at", "2026-10-25T18:30:11+00:00"), "last_success_at.*UTC timestamp"),
        (_set("last_report", "/abs/report.md"), "last_report.*relative path"),
        (_set("last_report", "reports\\x.md"), "last_report.*relative path"),
        (_set("last_report", "../notes/x.md"), "last_report.*relative path"),
        (_set("pulls_cursor", "2026-10-25T17:02:55Z"), "pulls_cursor must be an object"),
        (_set("pulls_cursor", {"ts": "2026-10-25T17:02:55Z"}), r"missing field.*pulls_cursor\.ids"),
        (_set_cursor("issues_cursor", "ts", "yesterday"), r"issues_cursor\.ts.*UTC timestamp"),
        (_set_cursor("issues_cursor", "ids", "57"), r"issues_cursor\.ids must be a list"),
        (_set_cursor("issues_cursor", "ids", [57, "61"]), "positive integers"),
        (_set_cursor("issues_cursor", "ids", [57, 1.0]), "positive integers"),
        (_set_cursor("issues_cursor", "ids", [True]), "positive integers"),
        (_set_cursor("issues_cursor", "ids", [0]), "positive integers"),
        (_set_cursor("issues_cursor", "ids", [57, 57]), "duplicates"),
        (_set_cursor("pulls_cursor", "extra", 1), r"unknown field.*pulls_cursor\.extra"),
    ],
)
def test_invalid_fields_are_rejected(config, change, message):
    data = valid_dict()
    change(data)
    write_raw(config, json.dumps(data))
    with pytest.raises(StateError, match=message):
        load_state(config)


def test_validate_state_without_writing(config):
    from organizador.github.state import validate_state
    validate_state(config, make_state())
    with pytest.raises(StateError, match="last_head_sha"):
        validate_state(config, make_state(last_head_sha="not-a-sha"))
    assert not state_file_path(config).exists()


def test_saving_invalid_state_is_refused_and_keeps_previous(config):
    save_state(config, make_state())
    before = state_file_path(config).read_bytes()
    with pytest.raises(StateError, match="Refusing to save"):
        save_state(config, make_state(repo="someone/else"))
    assert state_file_path(config).read_bytes() == before


# --- write failures ------------------------------------------------------


def test_failed_write_preserves_previous_state(config, monkeypatch):
    save_state(config, make_state())
    path = state_file_path(config)
    before = path.read_bytes()

    def fail(src, dst):
        raise PermissionError(13, "simulated", str(dst))

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(PermissionError):
        save_state(config, make_state(last_success_at="2026-10-26T09:00:00Z"))

    assert path.read_bytes() == before
    assert os.listdir(path.parent) == [path.name]
    monkeypatch.undo()
    assert load_state(config) == make_state()


def test_module_uses_atomic_write(monkeypatch, config):
    calls = []
    monkeypatch.setattr(state_module, "atomic_write_text", lambda p, t: calls.append(p))
    save_state(config, make_state())
    assert calls == [state_file_path(config)]
