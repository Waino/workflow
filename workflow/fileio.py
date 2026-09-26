"""Small filesystem helpers shared across modules."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, data: bytes, *, mode: int | None = None) -> None:
    """Write *data* to *path* atomically via a synced temp file in the same directory.

    *mode* is applied before the rename (``mkstemp`` creates 0o600). On failure the temp
    file is left behind for inspection; the existing *path*, if any, is untouched.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    if mode is not None:
        Path(tmp).chmod(mode)
    Path(tmp).replace(path)
