=======
History
=======

Unreleased
----------

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
