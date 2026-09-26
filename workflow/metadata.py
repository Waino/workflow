"""Project metadata (``.workflow.json``): tool-owned state, never hand-edited.

Distinct from ``config.yaml`` (hand-authored, operator-edited configuration). The
presence of this file marks the project root; see ``workflow.config.find_project_root``.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ValidationError, field_validator, model_validator

from workflow.exceptions import WorkflowError
from workflow.fileio import atomic_write
from workflow.names import DOCS_REPO

if TYPE_CHECKING:
    from pathlib import Path

METADATA_FILENAME = ".workflow.json"
LEGACY_ROOT_MARKER = ".workflow_root"
# A single path component: code repo names become directory names under every worktree and bare.
_CODE_REPO_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class ProjectMode(StrEnum):
    """Whether a project's plans are implemented in-band (canon repo) or out-of-band."""

    in_band = "in_band"
    oob = "oob"


class CodeRepoMeta(BaseModel):
    """One code repo tracked by an out-of-band project."""

    name: str
    """Directory name of the repo under ``canon/``, ``bare/`` and every agent worktree."""
    run_hook: bool = True
    """Whether the pre-commit hook is installed and run for this repo (``--skip-hook`` clears it)."""

    @field_validator("name")
    @classmethod
    def validate_name(cls, name: str) -> str:
        """Reject names that are not a single safe path component, or that collide with the docs repo."""
        if not _CODE_REPO_NAME_RE.fullmatch(name) or name == DOCS_REPO:
            msg = (
                f"invalid code repo name {name!r}: use letters, digits, '.', '_' and '-', "
                f"starting with a letter or digit, and not {DOCS_REPO!r}."
            )
            raise ValueError(msg)
        return name


class ProjectMetadata(BaseModel):
    """Tool-owned project state, persisted as ``.workflow.json``."""

    version: Literal[1] = 1
    """Schema version of ``.workflow.json``."""
    mode: ProjectMode = ProjectMode.in_band
    """In-band or out-of-band; fixed at ``init``."""
    code_repos: list[CodeRepoMeta] = []
    """Code repos of an oob project, in registration order; empty in-band."""

    @model_validator(mode="after")
    def validate_code_repos_matches_mode(self) -> ProjectMetadata:
        """Enforce that ``code_repos`` is non-empty iff the project is oob.

        In-band projects have no separate code repos (the single canon repo holds
        everything); oob projects always have at least one (a docs-only project is
        out of scope).
        """
        if self.mode == ProjectMode.in_band and self.code_repos:
            msg = "in-band projects must not have code_repos."
            raise ValueError(msg)
        if self.mode == ProjectMode.oob and not self.code_repos:
            msg = "oob projects must have at least one code repo."
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def validate_code_repo_names_unique(self) -> ProjectMetadata:
        """Reject duplicate code repo names (they would share one directory)."""
        names = [repo.name for repo in self.code_repos]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            msg = f"duplicate code repo names: {duplicates}"
            raise ValueError(msg)
        return self

    def runs_hook(self, repo: str) -> bool:
        """Return whether the pre-commit hook applies to *repo* (always true for the docs repo)."""
        return next((code_repo.run_hook for code_repo in self.code_repos if code_repo.name == repo), True)


def read_metadata(project_dir: Path) -> ProjectMetadata:
    """Read and validate ``.workflow.json`` from *project_dir*.

    Raises WorkflowError (naming the file path) on a missing file, invalid JSON,
    an unknown ``version``, or any other validation failure.
    """
    path = project_dir / METADATA_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        msg = f"{path} not found."
        raise WorkflowError(msg) from exc
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"{path} could not be read: {exc}"
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
    """Atomically write *metadata* to ``.workflow.json`` in *project_dir*."""
    data = json.dumps(metadata.model_dump(mode="json"), indent=2) + "\n"
    atomic_write(project_dir / METADATA_FILENAME, data.encode("utf-8"), mode=0o644)
