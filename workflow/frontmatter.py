"""YAML frontmatter on plan files: records stacking metadata for ``implement --onto``.

Written only to the *stacked* plan (never the base) by ``implement --onto``. Optional:
plans without frontmatter are simply not part of any stack.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from workflow.exceptions import WorkflowError

if TYPE_CHECKING:
    from pathlib import Path

# Matches a leading '---\n...\n---\n' block, capturing the YAML body between the fences.
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?\n)---\n?", re.DOTALL)


class RepoBranch(BaseModel):
    """One repo's branch and rollback anchor for a stacked plan."""

    branch: str
    base: str
    """The branch tip the plan's own work starts from -- the rollback anchor."""


class PlanFrontmatter(BaseModel):
    """Parsed frontmatter block. ``extra='allow'`` so later plans can add keys without breaking older files."""

    model_config = ConfigDict(extra="allow")

    onto: str | None = None
    branches: dict[str, RepoBranch] = {}


def _malformed(path: Path | None, detail: str) -> WorkflowError:
    where = f" in {path}" if path is not None else ""
    return WorkflowError(f"malformed frontmatter{where}: {detail}")


def strip_frontmatter(text: str, *, path: Path | None = None) -> tuple[PlanFrontmatter | None, str]:
    """Split *text* into its parsed frontmatter (if any) and the remaining body.

    Returns ``(None, text)`` when *text* does not start with a ``---`` fence.
    Raises :class:`WorkflowError` (naming *path*, when given) on a missing closing
    fence, malformed YAML, or a frontmatter block that fails model validation.
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        if text.startswith("---"):
            raise _malformed(path, "no closing '---' fence")
        return None, text
    try:
        raw = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise _malformed(path, str(exc)) from exc
    try:
        fm = PlanFrontmatter.model_validate(raw or {})
    except ValidationError as exc:
        raise _malformed(path, str(exc)) from exc
    return fm, text[match.end() :]


def parse_frontmatter(text: str, *, path: Path | None = None) -> PlanFrontmatter | None:
    """Parse the leading frontmatter block from *text*, or ``None`` when absent."""
    fm, _body = strip_frontmatter(text, path=path)
    return fm


def render_frontmatter(fm: PlanFrontmatter) -> str:
    """Render *fm* as a fenced frontmatter block (never hand-built strings)."""
    dumped = yaml.safe_dump(fm.model_dump(exclude_none=True, mode="json"), sort_keys=False)
    return f"---\n{dumped}---\n"


def write_frontmatter(path: Path, fm: PlanFrontmatter) -> None:
    """Replace (or insert) *path*'s frontmatter block with *fm*, leaving the body byte-identical."""
    text = path.read_text(encoding="utf-8")
    _old_fm, body = strip_frontmatter(text, path=path)
    path.write_text(render_frontmatter(fm) + body, encoding="utf-8")
