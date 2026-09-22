r"""Path layout math for a project's on-disk shape, covering both in-band and oob modes.

Completion criterion for the refactor that introduced this module: every remaining
``project_dir / <worktree-or-repo>`` join outside this file is a bug.

    rg 'project_dir\s*/\s*(repo|worktree|agent|RepoName|WorktreeName|"canon")'

should only match here.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict

from workflow.exceptions import WorkflowError
from workflow.metadata import ProjectMetadata, ProjectMode
from workflow.names import DOCS_REPO, WorktreeName

__all__ = ["DOCS_REPO", "Layout"]


class Layout(BaseModel):
    """Path math for one project, covering both in-band and out-of-band (oob) modes.

    In-band: a single git repo (``docs``) holds both the plans and the package
    source, and lives directly at the worktree root. Oob: ``docs`` and every
    code repo are separate git repos, each in its own subdirectory of the
    worktree.
    """

    model_config = ConfigDict(frozen=True)

    project_dir: Path
    metadata: ProjectMetadata

    def worktree_root(self, worktree: WorktreeName) -> Path:
        """Root directory of one agent worktree (canon/planner/builder/reviewer)."""
        return self.project_dir / worktree.value

    def git_repo_root(self, worktree: WorktreeName, repo: str = DOCS_REPO) -> Path:
        """Root of *repo*'s git working tree inside *worktree*."""
        if self.metadata.mode == ProjectMode.in_band:
            self._require_docs_repo(repo)
            return self.worktree_root(worktree)
        return self.worktree_root(worktree) / repo

    def plans_root(self, worktree: WorktreeName) -> Path:
        """Root of the plans tree inside *worktree* (identical in both modes)."""
        return self.worktree_root(worktree) / "docs" / "plans"

    def venv_root(self, worktree: WorktreeName) -> Path:
        """Root holding ``.venv`` for *worktree* (identical in both modes)."""
        return self.worktree_root(worktree)

    def hook_root(self, worktree: WorktreeName, repo: str = DOCS_REPO) -> Path:
        """Directory the pre-commit hook runs in for *repo* inside *worktree*."""
        return self.git_repo_root(worktree, repo)

    def bare_repo(self, repo: str = DOCS_REPO) -> Path:
        """Root of the bare mirror for *repo*."""
        if self.metadata.mode == ProjectMode.in_band:
            self._require_docs_repo(repo)
            return self.project_dir / "bare"
        return self.project_dir / "bare" / repo

    def repos(self) -> list[str]:
        """All git repos in the project: ``docs`` plus, in oob mode, every code repo."""
        if self.metadata.mode == ProjectMode.in_band:
            return [DOCS_REPO]
        return [DOCS_REPO, *self.code_repos()]

    def code_repos(self) -> list[str]:
        """Code repos only (excludes ``docs``); empty in in-band mode."""
        if self.metadata.mode == ProjectMode.in_band:
            return []
        return [repo.name for repo in self.metadata.code_repos]

    @property
    def plans_prefix(self) -> PurePosixPath:
        """A plan's path relative to its git repo: ``docs/plans`` in-band, ``plans`` in oob.

        Every ``git add``/``git mv``/pathspec/``--name-only`` consumer must use this,
        never a hardcoded ``"docs/plans"``, since that only exists in-band.
        """
        if self.metadata.mode == ProjectMode.in_band:
            return PurePosixPath("docs", "plans")
        return PurePosixPath("plans")

    def _require_docs_repo(self, repo: str) -> None:
        if repo != DOCS_REPO:
            msg = f"in-band mode has a single repo ({DOCS_REPO!r}); got {repo!r}."
            raise WorkflowError(msg)
