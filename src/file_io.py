"""Replace generated files atomically; never expose a partially written catalogue."""

import os
from contextlib import suppress
import tempfile
from pathlib import Path
import time


# Windows readers/scanners may briefly hold a file without FILE_SHARE_DELETE.
# Retry the SAME atomic replacement; never remove/truncate the old destination.
_REPLACE_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.0, 1.0)


def _replace_with_retry(source, destination):
    for attempt in range(len(_REPLACE_DELAYS) + 1):
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            if (
                getattr(exc, "winerror", None) not in (5, 32, 33)
                or attempt == len(_REPLACE_DELAYS)
            ):
                raise
            time.sleep(_REPLACE_DELAYS[attempt])


def atomic_write_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        _replace_with_retry(temp, path)
    finally:
        # A locked temporary file must not hide the original write/replace error.
        with suppress(OSError):
            os.unlink(temp)
