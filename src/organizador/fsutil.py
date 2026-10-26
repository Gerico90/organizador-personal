"""File writes that never corrupt state and never overwrite reports.

Text is written as UTF-8 with "\\n" line endings on every platform.
Filesystem errors are raised as OSError subclasses; the message includes the path.
"""

import os
import tempfile
from pathlib import Path

MAX_SUFFIX = 99


def atomic_write_text(path: Path, text: str) -> None:
    """Replace `path` with `text`, all or nothing.

    The text goes to a temporary file in the same directory, is flushed to
    disk, and is then renamed over the target with os.replace (atomic on
    Windows and POSIX within one volume). On any failure the temporary file
    is removed and the original file is left untouched.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        # On Windows the handle must be closed before os.replace.
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


def create_exclusive(directory: Path, stem: str, suffix: str, text: str) -> Path:
    """Create a new file `<stem><suffix>` in `directory` and write `text`.

    Never opens an existing file. If the name is taken, tries
    `<stem>-2<suffix>` up to `<stem>-99<suffix>`, then raises FileExistsError.
    If writing fails after creation, the partial file is removed.
    Returns the path of the created file.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    for n in range(1, MAX_SUFFIX + 1):
        name = f"{stem}{suffix}" if n == 1 else f"{stem}-{n}{suffix}"
        path = directory / name
        try:
            f = open(path, "x", encoding="utf-8", newline="\n")
        except FileExistsError:
            continue
        try:
            with f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            try:
                os.remove(path)
            except OSError:
                pass
            raise
        return path

    raise FileExistsError(
        f"Cannot create {stem}{suffix} in {directory}: "
        f"that name and suffixes -2 to -{MAX_SUFFIX} are all taken"
    )
