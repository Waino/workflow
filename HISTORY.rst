=======
History
=======

Unreleased
----------

* ``todo`` and ``implement`` now delete the planner branch they merged (locally and on bare).
  When the plan is not found, they name the branch the planner clone is on. ``doctor`` notes
  a planner ``plan/*`` branch with nothing ahead of ``main``.
  One-off cleanup of already merged planner branches: list them with
  ``git branch --merged main --list 'plan/*'`` in the planner clone, delete them with
  ``git branch -d``, and delete them on bare too.
* Planner prompts and ``AGENTS.md`` now state the planner branch convention (``plan/<name>``).
* The ``noqa-nosec`` validate check is renamed ``suppressions`` and covers many more markers
  (case-insensitive; plan files are ignored).
* In-band ``init`` now requires a committed ``.git-pre-commit-hook`` and a committed
  ``pyproject.toml`` or executable ``docs/bootstrap_venv.sh``. ``init`` and ``add-repo`` report
  all pre-flight problems at once.
* Dry-run renders the builder/reviewer ``{{ branch }}`` as ``<branch> (expected)``.

* The tmux session is now named after the project and tagged with the project directory.
  Existing sessions lack the tag: restart them once (``tmux kill-session -t workflow``,
  then ``workflow start``). Remove a pinned ``tmux.session_name: workflow`` from ``config.yaml``
  to allow parallel projects.

0.1.0 (FUTURE)
-----------------------------------

* First release on PyPI.

0.0.0 (2026-03-17)
----------------------------------------------

* Development begins.
