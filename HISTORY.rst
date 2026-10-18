=======
History
=======

Unreleased
----------

* ``todo`` and ``implement`` now delete the planner branch they merged (locally and on bare).
  When the plan is not found, they name the branch the planner clone is on. ``doctor`` notes
  a planner ``plan/*`` branch with nothing ahead of ``main``.
* Planner prompts and ``AGENTS.md`` now state the planner branch convention (``plan/<name>``).

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
