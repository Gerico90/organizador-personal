import os

import pytest

from organizador import fsutil
from organizador.fsutil import atomic_write_text, create_exclusive


def read(path):
    return path.read_bytes().decode("utf-8")


# --- atomic_write_text ---------------------------------------------------


def test_atomic_write_creates_file_and_parents(tmp_path):
    target = tmp_path / "state" / "github" / "o__r.json"
    atomic_write_text(target, '{"a": 1}\n')
    assert read(target) == '{"a": 1}\n'
    assert os.listdir(target.parent) == ["o__r.json"]


def test_atomic_write_replaces_existing(tmp_path):
    target = tmp_path / "s.json"
    target.write_text("old", encoding="utf-8")
    atomic_write_text(target, "new\nline")
    assert target.read_bytes() == b"new\nline"  # no \r\n on Windows
    assert os.listdir(tmp_path) == ["s.json"]


def test_atomic_write_temp_file_is_in_target_directory(tmp_path, monkeypatch):
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append((os.path.dirname(src), os.path.dirname(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(fsutil.os, "replace", spy)
    atomic_write_text(tmp_path / "s.json", "x")
    assert seen == [(str(tmp_path), str(tmp_path))]


def test_atomic_write_failed_replace_keeps_original(tmp_path, monkeypatch):
    target = tmp_path / "s.json"
    target.write_text("original", encoding="utf-8")

    def fail(src, dst):
        raise PermissionError(13, "simulated", str(dst))

    monkeypatch.setattr(fsutil.os, "replace", fail)
    with pytest.raises(PermissionError):
        atomic_write_text(target, "new")
    assert read(target) == "original"
    assert os.listdir(tmp_path) == ["s.json"]  # temp file cleaned up


def test_atomic_write_failed_write_keeps_original(tmp_path, monkeypatch):
    target = tmp_path / "s.json"
    target.write_text("original", encoding="utf-8")

    def fail(fd):
        raise OSError(28, "No space left on device (simulated)")

    monkeypatch.setattr(fsutil.os, "fsync", fail)
    with pytest.raises(OSError, match="simulated"):
        atomic_write_text(target, "new")
    assert read(target) == "original"
    assert os.listdir(tmp_path) == ["s.json"]


# --- create_exclusive ----------------------------------------------------


def test_create_exclusive_creates_file_and_parents(tmp_path):
    directory = tmp_path / "reports" / "github" / "o__r"
    path = create_exclusive(directory, "2026-10-25T183011Z", ".md", "# report\n")
    assert path == directory / "2026-10-25T183011Z.md"
    assert path.read_bytes() == b"# report\n"


def test_same_second_runs_get_suffixes(tmp_path):
    stem = "2026-10-25T183011Z"
    paths = [create_exclusive(tmp_path, stem, ".md", f"run {i}") for i in range(1, 4)]
    assert [p.name for p in paths] == [f"{stem}.md", f"{stem}-2.md", f"{stem}-3.md"]
    assert [read(p) for p in paths] == ["run 1", "run 2", "run 3"]


def test_existing_report_is_never_overwritten(tmp_path):
    existing = tmp_path / "r.md"
    existing.write_bytes(b"human edits \xc3\xa9\n")
    before = existing.stat().st_mtime_ns

    path = create_exclusive(tmp_path, "r", ".md", "generated")

    assert path.name == "r-2.md"
    assert existing.read_bytes() == b"human edits \xc3\xa9\n"
    assert existing.stat().st_mtime_ns == before


def test_gap_in_suffixes_is_filled_without_touching_others(tmp_path):
    (tmp_path / "r.md").write_text("1", encoding="utf-8")
    (tmp_path / "r-3.md").write_text("3", encoding="utf-8")
    path = create_exclusive(tmp_path, "r", ".md", "new")
    assert path.name == "r-2.md"
    assert read(tmp_path / "r-3.md") == "3"


def test_all_suffixes_taken_raises_and_changes_nothing(tmp_path):
    names = ["r.md"] + [f"r-{n}.md" for n in range(2, 100)]
    for name in names:
        (tmp_path / name).write_text(name, encoding="utf-8")

    with pytest.raises(FileExistsError, match="-2 to -99"):
        create_exclusive(tmp_path, "r", ".md", "new")

    assert sorted(os.listdir(tmp_path)) == sorted(names)
    assert all(read(tmp_path / name) == name for name in names)


def test_failed_write_removes_partial_report(tmp_path):
    # A lone surrogate cannot be encoded as UTF-8, so the write fails after creation.
    with pytest.raises(UnicodeEncodeError):
        create_exclusive(tmp_path, "r", ".md", "bad \ud800")
    assert os.listdir(tmp_path) == []


def test_failed_write_on_collision_keeps_existing(tmp_path):
    (tmp_path / "r.md").write_text("keep", encoding="utf-8")
    with pytest.raises(UnicodeEncodeError):
        create_exclusive(tmp_path, "r", ".md", "bad \ud800")
    assert os.listdir(tmp_path) == ["r.md"]
    assert read(tmp_path / "r.md") == "keep"
