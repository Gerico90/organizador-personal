import os
from datetime import datetime, timedelta, timezone

import pytest

from organizador.recommendations import common
from organizador.recommendations.common import DataError, create_new_file, record_path

# --- URLs ------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com",
        "http://example.com/a/b?x=1#frag",
        "https://sub.example.com:8443/path",
        "https://[::1]/x",
    ],
)
def test_valid_urls(value):
    assert common.url(value, "u") == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        None,
        42,
        "example.com",
        "ftp://example.com/file",
        "javascript:alert(1)",
        "file:///C:/Windows",
        "https://",
        "https:///path",
        "https://exa mple.com",
        "https://example.com/a b",
        "https://example.com/\n",
        "https://user:pass@example.com",
        "https://user@example.com",
        "https://example.com:notaport/",
        "https://example.com/" + "a" * 2048,
    ],
)
def test_invalid_urls(value):
    with pytest.raises(ValueError):
        common.url(value, "u")


# --- IDs and timestamps ----------------------------------------------------


@pytest.mark.parametrize("value", ["organizador-personal", "a", "p2", "my-project-3"])
def test_valid_project_ids(value):
    assert common.project_id(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "Organizador",
        "-lead",
        "trail-",
        "double--hyphen",
        "with space",
        "dot.name",
        "slash/name",
        "back\\slash",
        "..",
        "a" * 65,
        None,
    ],
)
def test_invalid_project_ids(value):
    with pytest.raises(ValueError):
        common.project_id(value)


@pytest.mark.parametrize(
    "value",
    ["ref-0123456789abcdeF", "ref-0123", "REF-0123456789abcdef", "../ref-0123456789abcdef", "ref-0123456789abcdef.json"],
)
def test_invalid_reference_ids(value):
    with pytest.raises(ValueError):
        common.reference_id(value)


@pytest.mark.parametrize(
    "value",
    [
        "rec-20261026T170604Z-a1b2c3d",
        "rec-20261026T170604-a1b2c3d4",
        "rec-20261026T170604Z-A1B2C3D4",
        "rec-20261026T170604Z-a1b2c3d4/../x",
        "rec-2026-10-26T17:06:04Z-a1b2c3d4",
    ],
)
def test_invalid_recommendation_ids(value):
    with pytest.raises(ValueError):
        common.recommendation_id(value)


@pytest.mark.parametrize(
    "value", ["2026-10-26 17:06:04Z", "2026-10-26T17:06:04", "2026-10-26T17:06:04.5Z", "2026-02-30T00:00:00Z", 5]
)
def test_invalid_timestamps(value):
    with pytest.raises(ValueError):
        common.timestamp(value, "t")


def test_format_ts_converts_to_utc_and_rejects_naive():
    plus_two = timezone(timedelta(hours=2))
    assert common.format_ts(datetime(2026, 10, 26, 19, 6, 4, 999, tzinfo=plus_two)) == "2026-10-26T17:06:04Z"
    with pytest.raises(ValueError):
        common.format_ts(datetime(2026, 10, 26))


def test_text_rejects_blank_and_non_strings():
    for bad in ["", "   ", "\n", None, 3, ["x"]]:
        with pytest.raises(ValueError):
            common.text(bad, "f")


def test_check_keys_reports_missing_and_unknown():
    with pytest.raises(ValueError, match="missing field"):
        common.check_keys({"a": 1}, ("a", "b"), ())
    with pytest.raises(ValueError, match="unknown field"):
        common.check_keys({"a": 1, "z": 2}, ("a",), ())
    with pytest.raises(ValueError, match="JSON object"):
        common.check_keys([1], ("a",), ())


@pytest.mark.parametrize("value", [2, 0, "1", True, None])
def test_schema_version_must_be_1(value):
    with pytest.raises(ValueError):
        common.check_schema_version({"schema_version": value})


# --- paths and files -------------------------------------------------------


def test_record_path_inside_folder(tmp_path):
    assert record_path(tmp_path, "references", "ref-0123456789abcdef", ".json") == (
        tmp_path / "references" / "ref-0123456789abcdef.json"
    )


@pytest.mark.parametrize("bad_id", ["../escape", "..\\escape", "sub/dir", "/abs"])
def test_record_path_refuses_escapes(tmp_path, bad_id):
    with pytest.raises(DataError, match="Unsafe path"):
        record_path(tmp_path, "references", bad_id, ".json")


def test_create_new_file_never_overwrites(tmp_path):
    target = tmp_path / "d" / "x.json"
    create_new_file(target, "first\n")
    with pytest.raises(FileExistsError):
        create_new_file(target, "second\n")
    assert target.read_bytes() == b"first\n"


def test_create_new_file_removes_partial_file_on_failure(tmp_path, monkeypatch):
    def broken_fsync(fd):
        raise OSError("disk full")

    monkeypatch.setattr(common.os, "fsync", broken_fsync)
    with pytest.raises(OSError, match="disk full"):
        create_new_file(tmp_path / "x.json", "data\n")
    assert os.listdir(tmp_path) == []


def test_read_json_object_errors(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"{not json")
    with pytest.raises(DataError, match="Malformed JSON"):
        common.read_json_object(bad)
    bad.write_bytes(b"\xff\xfe{}")
    with pytest.raises(DataError, match="UTF-8"):
        common.read_json_object(bad)
    bad.write_bytes(b"[1, 2]")
    with pytest.raises(DataError, match="JSON object"):
        common.read_json_object(bad)
    with pytest.raises(FileNotFoundError):
        common.read_json_object(tmp_path / "missing.json")
