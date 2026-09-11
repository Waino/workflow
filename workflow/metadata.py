"""Project metadata (``.workflow.json``): tool-owned state, never hand-edited.

Distinct from ``config.yaml`` (hand-authored, operator-edited configuration). The
presence of this file marks the project root; see ``workflow.config.find_project_root``.
"""

from __future__ import annotations

import json
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from workflow.exceptions import WorkflowError

METADATA_FILENAME = ".workflow.json"
LEGACY_ROOT_MARKER = ".workflow_root"


class ProjectMode(StrEnum):
    """Whether a project's plans are implemented in-band (canon repo) or out-of-band."""

    in_band = "in_band"
    oob = "oob"


class CodeRepoMeta(BaseModel):
    """One code repo tracked by an out-of-band project."""

    name: str
    run_hook: bool = True


class ProjectMetadata(BaseModel):
    """Tool-owned project state, persisted as ``.workflow.json``."""

    version: Literal[1] = 1
    mode: ProjectMode = ProjectMode.in_band
    code_repos: list[CodeRepoMeta] = []


def read_metadata(project_dir: Path) -> ProjectMetadata:
    """Read and validate ``.workflow.json`` from *project_dir*.

    Raises WorkflowError (naming the file path) on a missing file, invalid JSON,
    an unknown ``version``, or any other validation failure.
    """
    path = project_dir / METADATA_FILENAME
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError as exc:
        msg = f"{path} not found."
        raise WorkflowError(msg) from exc
    except json.JSONDecodeError as exc:
        msg = f"{path} is not valid JSON: {exc}"
        raise WorkflowError(msg) from exc

    version = raw.get("version") if isinstance(raw, dict) else None
    if version != 1:
        msg = f"{path} has unknown or missing version (expected 1): {version!r}"
        raise WorkflowError(msg)

    try:
        return ProjectMetadata.model_validate(raw)
    except ValidationError as exc:
        msg = f"{path} is malformed: {exc}"
        raise WorkflowError(msg) from exc


def write_metadata(project_dir: Path, metadata: ProjectMetadata) -> None:
    """Atomically write *metadata* to ``.workflow.json`` in *project_dir*.

    Writes to a temp file in the same directory (so ``os.replace`` stays on one
    filesystem), then replaces the target. On any error the temp file is removed
    and the existing file, if any, is left untouched.
    """
    path = project_dir / METADATA_FILENAME
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=project_dir,
        prefix=".workflow.json.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp_path = Path(tmp.name)
        try:
            json.dump(metadata.model_dump(mode="json"), tmp, indent=2)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
    tmp_path.replace(path)
